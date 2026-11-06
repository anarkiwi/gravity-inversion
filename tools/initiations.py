"""Sequence reads of a translated player, found statically in the LLVM IR of its generated C.

Song data is read from bytes no store writes at addresses derived from cursors the player
advances; a note read carries a value that may reach a voice's frequency registers
(docs/eventlog.md)."""

import heapq
import itertools
from itertools import product

from tqdm import tqdm

from irmodule import CASTS, offset, signed, successors

EMPTY = frozenset()
INF = float("inf")
BYTE = 256
CMP = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "gt": lambda a, b: a > b,
    "ge": lambda a, b: a >= b,
    "lt": lambda a, b: a < b,
    "le": lambda a, b: a <= b,
}
BIN = {
    "add": lambda a, b, w: a + b,
    "sub": lambda a, b, w: a - b,
    "mul": lambda a, b, w: a * b,
    "and": lambda a, b, w: a & b,
    "or": lambda a, b, w: a | b,
    "xor": lambda a, b, w: a ^ b,
    "shl": lambda a, b, w: a << b if b < w else 0,
    "lshr": lambda a, b, w: a >> b if b < w else 0,
    "ashr": lambda a, b, w: signed(a, w) >> min(b, w - 1),
    "udiv": lambda a, b, w: a // b if b else 0,
    "urem": lambda a, b, w: a % b if b else 0,
}


class Reach:
    """Which locations may reach a frequency register write of which voices, with the
    fewest play-call boundaries crossed: ``{location: {voice: delay}}``."""

    __slots__ = ("may",)

    def __init__(self, may=None):
        self.may = may or {}

    def copy(self):
        """An independent copy."""
        return Reach({l: dict(d) for l, d in self.may.items()})

    def __eq__(self, other):
        return isinstance(other, Reach) and self.may == other.may

    def gen(self, loc, delays):
        """``loc`` reaches ``delays``' voices so soon."""
        if loc is None or not delays:
            return
        d0 = self.may.setdefault(loc, {})
        for v, d in delays.items():
            if d < d0.get(v, INF):
                d0[v] = d

    def back(self, eff):
        """Step backward over effect ``eff``."""
        kind, out, mayi, _, mustc, mayc, _, mayv = eff
        if kind == "w":
            if mayi:
                self.gen(mayi[0], dict.fromkeys(mayv, 0))
            return
        srcs, kill = (mayc, mustc) if kind == "s" else ((out,), (out,))
        hit = {}
        for l in srcs:
            for v, d in self.may.get(l, {}).items():
                if d < hit.get(v, INF):
                    hit[v] = d
        for l in kill:
            self.may.pop(l, None)
        for l in mayi if kind == "s" else (*mayi, *mayc):
            self.gen(l, hit)

    def through(self, copies, cycle):
        """The reach at the end of an edge's source from that entering its target (that
        itself when the edge assigns nothing)."""
        if cycle:
            return Reach(
                {
                    l: {v: d + 1 for v, d in vs.items()}
                    for l, vs in self.may.items()
                    if l[0] == "m"
                }
            )
        if not copies:
            return self
        out = self.copy()
        moved = [(src, self.may.get(d, {})) for d, src in copies]
        for d, _ in copies:
            out.may.pop(d, None)
        for src, delays in moved:
            out.gen(src, dict(delays))
        return out

    @staticmethod
    def meet(reaches):
        """United reach of successors, at the least delay."""
        out = Reach()
        for r in reaches:
            for l, vs in r.may.items():
                out.gen(l, vs)
        return out


class Analysis:  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    """Sequence reads of IR ``Module`` ``m`` whose functions ``entries`` the host calls
    repeatedly after ``init`` once; ``freq`` maps frequency register addresses to voices.
    A read is ``(instance, load, partition)``."""

    def __init__(self, m, freq, entries, init=(), sink="io_write"):
        self.m, self.freq, self.sink = m, dict(freq), sink
        self.entries, self.init = list(entries), list(init)
        self.insts, self.inst_of = [], {}
        self.taint, self.stored, self.advancing = {}, set(), set()
        self.states, self.graph, self.heads = {}, {}, []
        self.recs, self.reach, self.roots = {}, {}, set()
        self.callers, self.writes = {}, {}
        self.subordinate, self.resets, self.flows = set(), set(), {}

    def default(self, loc):
        """``(values, taint, cells)`` of a location no state holds: an unstored byte's
        initial value, a stored byte's taint left by earlier calls, else unknown."""
        if loc[0] != "m":
            return None, EMPTY, EMPTY
        init = self.m.globals.get(loc[1])
        t = self.taint.get(loc[1:], EMPTY)
        if loc[1:] in self.stored or init is None or not 0 <= loc[2] < len(init):
            return None, t, EMPTY
        v = init[loc[2]]
        return (None if v is None else frozenset((v,))), t, EMPTY

    def get(self, s, loc):
        """``(values, taint, cells)`` of location ``loc`` in state ``s``."""
        return s[loc] if loc in s else self.default(loc)

    def val(self, s, o, inst):
        """``(values, taint, cells)`` of operand ``o``."""
        if o[0] in "cp":
            return (None if o[1] is None else frozenset((o[1],))), EMPTY, EMPTY
        if o[0] == "v":
            return s.get((inst, o[1]), (None, EMPTY, EMPTY))
        return None, EMPTY, EMPTY

    def key(self, s, inst, fn, blk):
        """The partition at a block: the singleton values of its phis when it is a loop
        latch and of the header phis of the loops holding it."""

        def one(names):
            out = []
            for p in names:
                v = s.get((inst, p), (None,))[0]
                out.append(next(iter(v)) if v is not None and len(v) == 1 else None)
            return tuple(out)

        here = (
            op[1]
            for op in self.m.funcs[fn][1][blk]
            if op[0] == "phi" and blk in self.m.latches[fn]
        )
        return one(here), one(self.m.loops[fn][blk])

    def keep(self, s, node):
        """``s`` with its instance's values dead at ``node`` dropped; the header phis of
        the loops holding it, which partition the state, are kept."""
        inst, fn, blk, i = node
        live = {(inst, n) for n in (*self.m.live(fn, blk, i), *self.m.loops[fn][blk])}
        return {k: v for k, v in s.items() if k[0] == "m" or k in live}

    def instance(self, fn, site, args, caller=None):
        """The instance of ``fn`` called at ``site`` with the singleton values ``args`` of
        its arguments, from a caller with arguments and partition ``caller``."""
        k = (fn, site, args, caller)
        if k not in self.inst_of:
            self.inst_of[k] = len(self.insts)
            self.insts.append(k)
        return self.inst_of[k]

    def compute(self, s, op, inst):
        """``(values, taint, cells)`` of a pure op; a set of more values than a byte
        holds is unknown."""
        vals, taint, cells = self._compute(s, op, inst)
        return (None if vals is not None and len(vals) >= BYTE else vals), taint, cells

    def _compute(  # pylint: disable=too-many-return-statements,too-many-locals
        self, s, op, inst
    ):
        code, _, w, ops, extra = op
        ins = [self.val(s, o, inst) for o in ops]
        if code == "select":
            c = ins[0][0]
            ins = (
                [ins[1], ins[2]]
                if c is None or len(c) > 1
                else [ins[1 if 1 in c else 2]]
            )
        elif code == "phi":
            return None, EMPTY, EMPTY
        taint = EMPTY.union(*(x[1] for x in ins))
        cells = EMPTY.union(*(x[2] for x in ins))
        vals = [x[0] for x in ins]
        if None in vals:
            return None, taint, cells
        if code == "select":
            return frozenset().union(*vals), taint, cells
        if code == "getelementptr":
            out = set()
            for base, *idx in product(*vals):
                if not isinstance(base, tuple) or any(
                    isinstance(i, tuple) for i in idx
                ):
                    return None, taint, cells
                off = offset(extra, [signed(i, 64) for i in idx]) if idx else 0
                out.add((base[0], base[1] + off))
            return frozenset(out), taint, cells
        if w is None or any(isinstance(x, tuple) for v in vals for x in v):
            return None, taint, cells
        mask = (1 << w) - 1
        if code in CASTS:
            f = (lambda a: signed(a, extra)) if code == "sext" else (lambda a: a)
            return frozenset(f(a) & mask for a in vals[0]), taint, cells
        if code == "icmp":
            pred, iw = extra
            sign = pred[0] == "s"
            f = CMP[pred if pred in CMP else pred[1:]]
            conv = (lambda a: signed(a, iw)) if sign else (lambda a: a)
            return (
                frozenset(int(f(conv(a), conv(b))) for a, b in product(*vals)),
                EMPTY,
                cells,
            )
        if code in BIN:
            return (
                frozenset(BIN[code](a, b, w) & mask for a, b in product(*vals)),
                taint,
                cells,
            )
        return None, taint, cells

    def load(self, s, op, nk):
        """``((values, taint, cells), bytes it may read or None, read label or None)``: a
        sequence read loads unstored bytes at an address derived from an advanced cell.
        """
        inst = nk[0][0]
        addrs, at, acells = self.val(s, op[3][0], inst)
        n = max(1, (op[2] or 64) // 8)
        known = addrs is not None and all(isinstance(a, tuple) for a in addrs)
        unstored = not known or all(
            (g, off + k) not in self.stored for g, off in addrs for k in range(n)
        )
        cursors = (acells & self.advancing) - self.subordinate
        label = (inst, op[1], nk[1]) if unstored and cursors else None
        t = frozenset((label,)) if label else at
        if not known:
            return (None, t, EMPTY), None, label
        vals, cells, read = set(), set(), []
        for g, off in sorted(addrs):
            parts = [self.get(s, ("m", g, off + k)) for k in range(n)]
            read += [("m", g, off + k) for k in range(n)]
            cells.update((g, off + k) for k in range(n))
            if not label and not at:
                t = t.union(*(p[1] for p in parts))
            if vals is None or any(p[0] is None for p in parts):
                vals = None
            elif op[2] is None:
                vals.update(p for p in parts[0][0] if isinstance(p, tuple))
            else:
                vals.update(
                    sum(b << 8 * k for k, b in enumerate(bs))
                    for bs in product(*(p[0] for p in parts))
                )
        vals = frozenset(vals) if vals and len(vals) < BYTE else None
        return (vals, t, frozenset(cells)), tuple(read), label

    def pure_call(self, s, op, nk):  # pylint: disable=too-many-locals
        """``((values, taint, cells), bytes it may read, read label or None)`` of a call of
        a pure function, run on the caller's memory: a sequence read when a load in it is.
        """
        inst, fn = nk[0][0], op[4]
        here = ("pure", fn)
        start = {
            (here, a): self.val(s, o, inst) for a, o in zip(self.m.funcs[fn][2], op[3])
        }
        start.update((k, v) for k, v in s.items() if k[0] == "m")
        seen, work, out, read = (
            {},
            [((here, fn, self.m.funcs[fn][0][0], 0), start)],
            [],
            set(),
        )
        while work:
            node, st = work.pop()
            if node in seen:
                st, changed = self.join(seen[node], st, False)
                if not changed:
                    continue
            seen[node] = st
            st, rec = dict(st), []
            for _, nxt, copies in self.run((node, ()), st, rec):
                if nxt is None:
                    out.append(
                        self.val(st, copies[0], here)
                        if copies
                        else (None, EMPTY, EMPTY)
                    )
                    continue
                s2 = dict(st)
                for dst, srcop in copies:
                    s2[dst] = self.val(st, srcop, here)
                work.append((nxt, s2))
            read.update(c for _, eff, _, _ in rec for c in eff[5])
        vals = (
            None
            if any(o[0] is None for o in out)
            else frozenset().union(*(o[0] for o in out))
        )
        taint = EMPTY.union(*(o[1] for o in out))
        cells = EMPTY.union(*(o[2] for o in out))
        label = None
        if any(e[0] == here for e in taint):
            label = (inst, op[1], nk[1])
            taint = frozenset((label,))
        return (vals, taint, cells), tuple(sorted(read)), label

    def store(self, s, op, inst, blk):
        """Apply a store to ``s``; the bytes it may write (none when unknown)."""
        vals, t, cells = self.val(s, op[3][0], inst)
        addrs = self.val(s, op[3][1], inst)[0]
        if addrs is None or any(not isinstance(a, tuple) for a in addrs):
            return ()
        out = []
        for k in range(max(1, op[4] // 8)):
            byte = None
            if vals is not None and all(isinstance(v, int) for v in vals):
                byte = frozenset((v >> 8 * k) & 0xFF for v in vals)
            for g, off in sorted(addrs):
                loc = ("m", g, off + k)
                out.append(loc)
                if loc[1:] in cells:
                    self.advancing.add(loc[1:])
                elif len(addrs) == 1:
                    self.resets.add((self.insts[inst][0], blk, loc[1:]))
                if len(addrs) == 1:
                    s[loc] = (byte, t, EMPTY)
                else:
                    old = self.get(s, loc)
                    u = None if byte is None or old[0] is None else old[0] | byte
                    s[loc] = (u, old[1] | t, EMPTY)
        self.stored.update(c[1:] for c in out)
        self.writes.setdefault(inst, set()).update(out)
        return tuple(out)

    def edge(self, inst, fn, src, dst):
        """``(node, copies)`` of the edge from block ``src`` to ``dst``: its phis'
        incoming operands."""
        copies = []
        for op in self.m.funcs[fn][1][dst]:
            if op[0] != "phi":
                break
            copies.append(((inst, op[1]), op[3][op[4].index(src)]))
        return (inst, fn, dst, 0), copies

    def branch(self, s, nk, i, op):
        """Successors of a ``br``."""
        inst, fn, blk, _ = nk[0]
        args = op[3]
        if len(args) == 1:
            return [((fn, blk, i, "next"), *self.edge(inst, fn, blk, args[0][1]))]
        cond = self.val(s, args[0], inst)[0]
        succ = []
        if cond is None or 1 in cond:
            succ.append(((fn, blk, i, "taken"), *self.edge(inst, fn, blk, args[2][1])))
        if cond is None or 0 in cond:
            succ.append(((fn, blk, i, "fall"), *self.edge(inst, fn, blk, args[1][1])))
        return succ

    def run(  # pylint: disable=too-many-branches,too-many-locals,too-many-statements
        self, nk, s, rec=None
    ):
        """Run segment ``nk`` on ``s`` in place; its successors ``[(label, node,
        copies)]`` (node None: leaving an entry). ``rec`` collects ``(op index, effect,
        {input: carries a read}, read label or None)``."""
        inst, fn, blk, start = nk[0]
        ops = self.m.funcs[fn][1][blk]
        for i in range(start, len(ops)):
            op = ops[i]
            code, name, _, args, _ = op
            if code == "phi":
                continue
            if code == "br":
                return self.branch(s, nk, i, op)
            if code == "ret":
                return [((fn, blk, i, "next"), None, args)]
            if code == "unreachable":
                return []
            locs = tuple((inst, a[1]) for a in args if a[0] == "v")
            fresh = {l: bool(self.get(s, l)[1]) for l in locs}
            eff, src = None, None
            if code == "call" and op[4] in self.m.pure:
                got, read, src = self.pure_call(s, op, nk)
                if name:
                    s[(inst, name)] = got
                    eff = ("l", (inst, name), locs, locs, (), read, (), ())
            elif code == "call":
                callee = op[4]
                if callee in self.m.funcs:
                    vals = [self.val(s, o, inst)[0] for o in args]
                    key = tuple(
                        next(iter(v)) if v is not None and len(v) == 1 else None
                        for v in vals
                    )
                    child = self.instance(
                        callee, (fn, blk, i), key, (self.insts[inst][2], nk[1])
                    )
                    copies = [
                        ((child, a), o) for a, o in zip(self.m.funcs[callee][2], args)
                    ]
                    node = (child, callee, self.m.funcs[callee][0][0], 0)
                    return [((fn, blk, i, "next"), node, copies)]
                if callee == self.sink:
                    addrs = self.val(s, args[0], inst)[0] or ()
                    voices = frozenset(self.freq[a] for a in addrs if a in self.freq)
                    v = locs[-1:] if args[1][0] == "v" else ()
                    eff = (
                        "w",
                        None,
                        v,
                        v,
                        (),
                        (),
                        voices if len(addrs) == 1 else EMPTY,
                        voices,
                    )
                elif callee == "trap":
                    return []
                elif name:
                    got = [self.val(s, a, inst) for a in args]
                    s[(inst, name)] = (None, EMPTY.union(*(g[1] for g in got)), EMPTY)
                    eff = ("v", (inst, name), locs, locs, (), (), (), ())
            elif code == "load":
                got, read, src = self.load(s, op, nk)
                s[(inst, name)] = got
                n = max(1, (op[2] or 64) // 8)
                must = read if read is not None and len(read) == n else ()
                eff = ("l", (inst, name), locs[:1], locs[:1], must, read or (), (), ())
            elif code == "store":
                cells = self.store(s, op, inst, blk)
                v = locs[:1] if args[0][0] == "v" else ()
                full = len(cells) == max(1, op[4] // 8)
                eff = ("s", None, v, v, cells if full else (), cells, (), ())
            elif name:
                s[(inst, name)] = self.compute(s, op, inst)
                flow = (
                    ()
                    if code == "icmp"
                    else self.flow(
                        s, locs[1:] if code == "select" and args[0][0] == "v" else locs
                    )
                )
                if rec is not None:
                    self.flows.setdefault((fn, name), set()).update(l[1] for l in flow)
                eff = (
                    "v",
                    (inst, name),
                    flow,
                    () if code == "select" else flow,
                    (),
                    (),
                    (),
                    (),
                )
            if rec is not None and eff is not None:
                rec.append((i, eff, fresh, src))
        raise ValueError(f"{fn}:{blk} has no terminator")

    def flow(self, s, locs):
        """The inputs whose values flow into an op's result: those derived from an
        advanced cell when some are (the others modify that accumulated value). A
        comparison decides; no value flows into it."""
        acc = [l for l in locs if self.get(s, l)[2] & self.advancing]
        return tuple(acc) if acc else locs

    def join(self, old, new, head):
        """``(old joined with new, changed)``: value sets united, those at a loop header
        (``head``) or of 16 bits or more that grow made unknown; taints and cells united.
        """
        out, changed = dict(old), False
        for loc in old.keys() | new.keys():
            ov, ot, oc = self.get(old, loc)
            nv, nt, nc = self.get(new, loc)
            if ov is None or nv is None:
                u = None
            else:
                u = ov | nv
                grew = u - ov
                if grew and (
                    head or any(not isinstance(x, int) or x > 0xFF for x in grew)
                ):
                    u = None
            t, c = ot | nt, oc | nc
            if loc not in old or u != ov or t != ot or c != oc:
                out[loc], changed = (u, t, c), True
        return out, changed

    def _round(  # pylint: disable=too-many-locals,too-many-statements,too-many-branches,too-many-nested-blocks
        self, progress
    ):
        """One forward pass over init and the entries; the memory taint the calls leave.
        A callee instance's returns go to every call registered with it, each with the
        caller's state at the call and the bytes the callee may write. Segments are taken
        in reverse postorder of their function's blocks."""
        self.states, self.graph, self.heads, exits = {}, {}, [], []
        self.callers, self.writes, rets, retedges = {}, {}, {}, {}
        work, queued, seq = [], set(), itertools.count()

        def queue(k):
            if k not in queued:
                queued.add(k)
                heapq.heappush(
                    work, (self.m.rpo[k[0][1]][k[0][2]], k[0][3], next(seq), k)
                )

        for fn in self.init + self.entries:
            inst = self.instance(fn, None, (None,) * len(self.m.funcs[fn][2]))
            self.roots.add(inst)
            node = (inst, fn, self.m.funcs[fn][0][0], 0)
            nk = (node, self.key({}, *node[:3]))
            self.states[nk] = {
                (inst, a): (None, EMPTY, EMPTY) for a in self.m.funcs[fn][2]
            }
            queue(nk)
            if fn in self.entries:
                self.heads.append(nk)

        def push(nxt, s2):
            s2 = self.keep(s2, nxt)
            k2 = (nxt, self.key(s2, *nxt[:3]))
            old = self.states.get(k2)
            head = nxt[3] == 0 and nxt[2] in self.m.headers[nxt[1]]
            joined, changed = (s2, True) if old is None else self.join(old, s2, head)
            if changed:
                self.states[k2] = joined
                queue(k2)
            return k2

        def deliver(rk, caller, sc):
            """Return the exit of ret segment ``rk`` to the call ``caller``."""
            lab, s, copies = rets[rk]
            inst, (pk, i) = rk[0][0], caller
            pinst, pfn, pblk, _ = pk[0]
            wrote = self.writes.get(inst, set())
            self.writes.setdefault(pinst, set()).update(wrote)
            s2 = dict(sc) | {k: self.get(s, k) for k in wrote}
            call = self.m.funcs[pfn][1][pblk][i]
            back = ()
            if copies and call[1]:
                o = copies[0]
                s2[(pinst, call[1])] = self.val(s, o, inst)
                back = (((pinst, call[1]), (inst, o[1]) if o[0] == "v" else None),)
            retedges.setdefault(rk, {})[caller] = (
                lab,
                push((pinst, pfn, pblk, i + 1), s2),
                back,
            )

        while work:
            nk = heapq.heappop(work)[3]
            queued.discard(nk)
            inst = nk[0][0]
            s = dict(self.states[nk])
            out = []
            for lab, nxt, copies in self.run(nk, s):
                progress.update()
                if nxt is None and inst in self.roots:
                    exits.append(s)
                    out.append((lab, None, ()))
                elif nxt is None:
                    rets[nk] = (lab, s, copies)
                    for caller, sc in self.callers.get(inst, {}).items():
                        deliver(nk, caller, sc)
                else:
                    s2 = dict(s)
                    for dst, src in copies:
                        s2[dst] = self.val(s, src, inst)
                    if nxt[0] != inst:
                        reg = self.callers.setdefault(nxt[0], {})
                        if reg.get((nk, lab[2])) != s:
                            reg[(nk, lab[2])] = s
                            for rk in [r for r in rets if r[0][0] == nxt[0]]:
                                deliver(rk, (nk, lab[2]), s)
                    back = tuple(
                        (d, (inst, o[1]) if o[0] == "v" else None) for d, o in copies
                    )
                    out.append((lab, push(nxt, s2), back))
            self.graph[nk] = out
        for rk, edges in retedges.items():
            self.graph[rk] = list(edges.values())
        taint = {}
        for s in exits:
            for loc, (_, t, _) in s.items():
                if loc[0] == "m" and t:
                    taint[loc[1:]] = taint.get(loc[1:], EMPTY) | t
        return taint

    def forward(self):
        """Forward passes until the stored bytes, advanced cells and the memory taint
        calls leave settle."""
        with tqdm(
            desc="initiations: forward", unit="edge", leave=False, mininterval=2
        ) as progress:
            self.taint = {}
            while True:
                before = (set(self.stored), set(self.advancing), dict(self.taint))
                taint = self._round(progress)
                for c, t in self.taint.items():
                    taint[c] = taint.get(c, EMPTY) | t
                if (self.stored, self.advancing, taint) == before:
                    return
                self.taint = taint

    def contributions(self, nk):
        """``[(label, reach, target)]`` the successors of ``nk`` give its end."""
        out = []
        for lab, nxt, copies in self.graph[nk]:
            if nxt is not None:
                r = self.reach.get(nxt)
                out.append(
                    (lab, Reach() if r is None else r.through(copies, False), nxt)
                )
            elif self.insts[nk[0][0]][0] in self.entries:
                for h in self.heads:
                    r = self.reach.get(h) or Reach()
                    out.append((lab, r.through((), True), h))
        return out

    def record(self):
        """Each segment's effects and end state; the operands each value op takes its
        value from (``flows``)."""
        self.recs, self.flows = {}, {}
        for nk in self.graph:
            rec, s = [], dict(self.states[nk])
            self.run(nk, s, rec)
            self.recs[nk] = (rec, s)

    def backward(self):
        """Reach with least delay at each segment's entry, the play call's exit feeding its
        entry, in postorder."""
        self.record()
        preds = {}
        for nk, out in self.graph.items():
            for _, nxt, _ in out:
                for t in self.heads if nxt is None else [nxt]:
                    preds.setdefault(t, set()).add(nk)
        self.reach = {}

        def rank(k):
            return (-self.m.rpo[k[0][1]][k[0][2]], -k[0][3], k[0][0])

        work = [(rank(nk), id(nk), nk) for nk in self.graph]
        heapq.heapify(work)
        queued = set(self.graph)
        with tqdm(
            desc="initiations: backward", unit="seg", leave=False, mininterval=2
        ) as progress:
            while work:
                nk = heapq.heappop(work)[2]
                queued.discard(nk)
                progress.update()
                r = Reach.meet(c for _, c, _ in self.contributions(nk))
                for _, eff, _, _ in reversed(self.recs[nk][0]):
                    r.back(eff)
                if r != self.reach.get(nk):
                    self.reach[nk] = r
                    for p in preds.get(nk, ()):
                        if p not in queued:
                            queued.add(p)
                            heapq.heappush(work, (rank(p), id(p), p))

    def pitch_edges(self):
        """Two-way branch edges on which a value carrying a sequence read reaches a
        frequency register and not on another edge of its branch."""
        out = set()
        for nk, (_, s) in self.recs.items():
            contribs = self.contributions(nk)
            if len(contribs) < 2:
                continue
            reach = [{l for l in r.may if self.get(s, l)[1]} for _, r, _ in contribs]
            for (lab, _, _), r in zip(contribs, reach):
                if any(r - o for o in reach):
                    out.add(lab)
        return out

    def restarted(self, edges):
        """The advanced cells some store resets in a block control dependent on a branch
        edge in ``edges``: cursors of tables restarted where song data reaches a pitch.
        """
        blocks = set()
        for fn, blk, _, kind in edges:
            if kind in ("taken", "fall"):
                stop = self.m.ipdom[fn].get(blk)
                b = successors(self.m.funcs[fn][1][blk])[0 if kind == "taken" else 1]
                while b is not None and b != stop:
                    blocks.add((fn, b))
                    b = self.m.ipdom[fn].get(b)
        return {
            c for fn, b, c in self.resets if (fn, b) in blocks and c in self.advancing
        }

    def analyze(self):
        """``{(function, read): "note" | "other"}`` of the sequence reads, repeating the
        passes until no further cursor is found restarted where song data reaches a pitch
        (a table restarted with a note: reads through it are no sequence reads). A note
        read carries a value that may reach a frequency register."""
        while True:
            self.forward()
            self.backward()
            sub = self.subordinate | self.restarted(self.pitch_edges())
            if sub == self.subordinate:
                break
            self.subordinate = sub
        out = {}
        for rec, _ in self.recs.values():
            for _, _, _, src in rec:
                if src is not None:
                    out.setdefault((self.insts[src[0]][0], src[1]), "other")
        for nk, r in self.reach.items():
            for l in r.may:
                for e in self.get(self.states[nk], l)[1]:
                    out[self.insts[e[0]][0], e[1]] = "note"
        return out
