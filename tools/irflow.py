"""Value flow of a generated C's LLVM IR: what each load, store and call derives from, and
the def-use webs of its cells (docs/flowgraph.md)."""

import re

import llvmlite.binding as llvm

import irmodule


def sroa(text):
    """The module of ``text`` with its locals promoted to SSA values (SROA): each C array
    read stays one ``load`` with its source location."""
    mod = llvm.parse_assembly(text)
    pb = llvm.create_pass_builder(
        irmodule.target(), llvm.create_pipeline_tuning_options()
    )
    fpm = llvm.create_new_function_pass_manager()
    fpm.add_sroa_pass()
    for f in mod.functions:
        if not f.is_declaration:
            fpm.run(f, pb)
    return mod


def _locations(printed):
    """``{function: [(line, column)]}`` of each instruction in the printed module's order."""
    locs = {int(a): (int(b), int(c)) for a, b, c in irmodule.LOC.findall(printed)}
    out, fn = {}, None
    for line in printed.split("\n"):
        d = irmodule.DEFINE.match(line)
        if d:
            fn = d.group(1)
            out[fn] = []
        elif line.startswith("}"):
            fn = None
        elif fn and line.startswith("  ") and not line.lstrip().startswith(";"):
            m = irmodule.DBG.search(line)
            out[fn].append(locs.get(int(m.group(1)), (0, 0)) if m else (0, 0))
    return out


#: A constant array of pointers: its name and its elements.
POINTERS = re.compile(r"^@([\w.]+) = [\w ]*constant \[\d+ x ptr\] \[(.*)\]", re.M)


def bases(printed):
    """``{array: global}`` of the constant pointer arrays whose elements all point into one
    global (a per-voice pointer table)."""
    out = {}
    for name, elems in POINTERS.findall(printed):
        into = set(re.findall(r"ptr (?:getelementptr \([^,]+, ptr )?@([\w.]+)", elems))
        if len(into) == 1:
            out[name] = into.pop()
    return out


class Flow:  # pylint: disable=too-many-instance-attributes
    """Value sources of one module: what each load's address, each stored value, each call
    argument and each return derives from, as loads, arguments and returns."""

    def __init__(self, mod, helpers):
        self.helpers, self.loc = helpers, _locations(str(mod))
        self.bases = bases(str(mod))
        self.reads, self.stores, self.args, self.rets, self.cfg = {}, [], {}, {}, {}
        self.where, self.calls = {}, []
        for fn in mod.functions:
            if not fn.is_declaration and not helpers.fullmatch(fn.name):
                self._function(fn)

    def _function(self, fn):  # pylint: disable=too-many-locals,too-many-statements
        defs = {str(i).strip(): i for b in fn.blocks for i in b.instructions}
        params = {str(a).strip(): k for k, a in enumerate(fn.arguments)}
        memo, where, blocks = {}, iter(self.loc[fn.name]), {}

        def node(op):
            s = str(op).strip()
            if s in params:
                return frozenset([("arg", fn.name, params[s])])
            ins = defs.get(s)
            if ins is None:
                return frozenset()
            if s not in memo:
                memo[s] = frozenset()
                memo[s] = sources(ins, s)
            return memo[s]

        def sources(ins, s):
            ops = list(ins.operands)
            if ins.opcode == "load" or self._helper(ins):
                return frozenset([("ld", fn.name, s)])
            if ins.opcode == "call":
                return frozenset([("ret", ops[-1].name)])
            ops = ops[1:] if ins.opcode == "select" else ops
            return frozenset().union(*(node(o) for o in ops))

        def cell(op):
            """``(global, constant offset)`` of a pointer, its index terms dropped."""
            s = str(op).lstrip()
            if s.startswith("@"):
                return op.name, 0
            ins = defs.get(s.strip())
            if ins is None:
                p = irmodule.pointer(s[4:] if s.startswith("ptr ") else s)
                return (p[0], p[1] or 0) if p else (None, 0)
            ops = list(ins.operands)
            if ins.opcode == "load" and cell(ops[0])[0] in self.bases:
                return self.bases[cell(ops[0])[0]], 0
            if ins.opcode != "getelementptr":
                return None, 0
            g, c = cell(ops[0])
            return g, c + sum(constant(o) for o in ops[1:])

        def constant(op):
            ins = defs.get(str(op).strip())
            if ins is None:
                m = re.fullmatch(r"i\d+ (-?\d+)", str(op).strip())
                return int(m.group(1)) if m else 0
            if ins.opcode in ("add", "zext", "sext", "trunc"):
                return sum(constant(o) for o in ins.operands)
            return 0

        for b in fn.blocks:
            acc = []
            for i in b.instructions:
                loc, s, ops = next(where), str(i).strip(), list(i.operands)
                if i.opcode == "load":
                    key = cell(ops[0])
                    self.reads[("ld", fn.name, s)] = (fn.name, loc, key, node(ops[0]))
                    acc.append(("load", key, ("ld", fn.name, s)))
                elif self._helper(i):
                    deps = frozenset().union(*(node(o) for o in ops[:-1]))
                    span = ops[-1].name[2:] if ops[-1].name.startswith("rdC") else None
                    self.reads[("ld", fn.name, s)] = (fn.name, loc, (span, None), deps)
                elif i.opcode == "call":
                    deps = [node(o) for o in ops[:-1]]
                    for k, d in enumerate(deps):
                        self.args.setdefault((ops[-1].name, k), set()).update(d)
                    base = constant(ops[0]) & 0xFFFF if len(ops) > 1 else 0
                    self.calls.append((fn.name, loc, ops[-1].name, deps, base))
                    acc.append(("call", ops[-1].name, None))
                elif i.opcode == "store":
                    key = cell(ops[1])
                    self.stores.append((key, node(ops[0])))
                    self.where[len(self.stores) - 1] = (fn.name, loc, node(ops[1]))
                    acc.append(("store", key, len(self.stores) - 1))
                elif i.opcode == "ret" and ops:
                    self.rets.setdefault(fn.name, set()).update(node(ops[0]))
            blocks[b.name] = (
                acc,
                re.findall(r"label %([\w.]+)", str(list(b.instructions)[-1])),
            )
        self.cfg[fn.name] = (next(iter(fn.blocks)).name, blocks)

    def _helper(self, ins):
        ops = list(ins.operands)
        return (
            ins.opcode == "call"
            and ops
            and bool(self.helpers.fullmatch(ops[-1].name or ""))
        )

    def loads(self, deps, stop=frozenset()):
        """The loads and the ``stop`` arguments ``deps`` derive from, through arguments
        (every call's actual) and returns."""
        out, seen, todo = set(), set(), list(deps)
        while todo:
            n = todo.pop()
            if n in seen:
                continue
            seen.add(n)
            if n[0] == "ld" or n in stop:
                out.add(n)
            elif n[0] == "arg":
                todo += self.args.get((n[1], n[2]), ())
            else:
                todo += self.rets.get(n[1], ())
        return out

    def first(self, fn, key, memo):
        """``load`` when some path from ``fn``'s entry loads cell ``key`` before storing it,
        else ``store`` when one stores it, else None (calls followed)."""
        if fn not in self.cfg:
            return None
        if (fn, key) not in memo:
            memo[fn, key] = None
            memo[fn, key] = self._first(fn, key, memo)
        return memo[fn, key]

    def _first(self, fn, key, memo):
        entry, blocks = self.cfg[fn]
        seen, todo, stored = set(), [entry], False
        while todo:
            b = todo.pop()
            if b in seen:
                continue
            seen.add(b)
            acc, succ = blocks[b]
            hit = None
            for kind, k, _ in acc:
                hit = self.first(k, key, memo) if kind == "call" else k == key and kind
                if hit:
                    break
            if hit == "load":
                return "load"
            stored |= hit == "store"
            todo += [] if hit else succ
        return "store" if stored else None


ENTRY, KEPT = "entry", "kept"


def _join(a, b):
    return {k: a.get(k, {ENTRY}) | b.get(k, {ENTRY}) for k in a.keys() | b.keys()}


def _apply(state, summary):
    out = dict(state)
    for k, defs in summary.items():
        out[k] = set().union(
            *(state.get(k, {ENTRY}) if d == ENTRY else {d} for d in defs)
        )
    return out


class Webs:  # pylint: disable=too-few-public-methods
    """Def-use webs of the cells: which stores' values each load may read, by reaching
    definitions over each function's blocks, a call applying its callee's summary (the stores
    reaching its exit per cell, computed once per function). ``entry``'s live-in values are
    those of earlier calls (``KEPT``)."""

    def __init__(self, flow, entry):
        self.flow, self.summary, self.at, self.sites = flow, {}, {}, {}
        for fn in flow.cfg:
            self._run(fn)
        self.web = self._unite(entry)

    def _run(self, fn):
        if fn in self.summary:
            return self.summary[fn]
        self.summary[fn] = {}
        entry, blocks = self.flow.cfg[fn]
        ins, exits, todo = {entry: {}}, {}, [entry]
        while todo:
            b = todo.pop()
            state = dict(ins[b])
            acc, succ = blocks[b]
            for pos, (kind, k, ident) in enumerate(acc):
                if kind == "load":
                    self.at[ident] = (fn, state.get(k, {ENTRY}))
                elif kind == "store":
                    state[k] = {ident}
                elif k in self.flow.cfg:
                    self.sites.setdefault(k, {})[fn, b, pos] = dict(state)
                    state = _apply(state, self._run(k))
            if not succ:
                exits = _join(exits, state) if exits else state
            for s in succ:
                new = _join(ins[s], state) if s in ins else state
                if ins.get(s) != new:
                    ins[s] = new
                    todo.append(s)
        self.summary[fn] = {k: v for k, v in exits.items() if v != {ENTRY}}
        return self.summary[fn]

    def _unite(self, entry):
        parent = {}

        def find(x):
            while parent.setdefault(x, x) != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        memo = {}

        def live(fn, k):
            """Stores (or ``KEPT``) that may reach ``fn``'s entry for cell ``k``."""
            if fn == entry:
                return {KEPT}
            if (fn, k) not in memo:
                memo[fn, k] = set()
                out = set()
                for (caller, _, _), state in self.sites.get(fn, {}).items():
                    for d in state.get(k, {ENTRY}):
                        out |= live(caller, k) if d == ENTRY else {d}
                memo[fn, k] = out
            return memo[fn, k]

        for ident, (fn, defs) in self.at.items():
            k = self.flow.reads[ident][2]
            for d in defs:
                for src in live(fn, k) if d == ENTRY else {d}:
                    parent[find(ident)] = find(
                        (k, KEPT) if src == KEPT else ("st", src)
                    )
        for k, defs in self.summary.get(entry, {}).items():
            for d in defs - {ENTRY}:
                parent[find(("st", d))] = find((k, KEPT))
        for i in range(len(self.flow.stores)):
            find(("st", i))
        groups = {}
        for x in parent:
            groups.setdefault(find(x), []).append(x)
        rep = {}
        for members in groups.values():
            kept = [m for m in members if m[1] == KEPT]
            stores = sorted(m for m in members if m[0] == "st")
            r = kept[0] if kept else stores[0] if stores else members[0]
            rep.update(dict.fromkeys(members, r))
        return rep
