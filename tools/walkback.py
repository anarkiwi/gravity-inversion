"""Song-data patches that run the code of play a song never runs, each read off its guard.

walkback.py PSID... [--json PATH] [--images DIR] [--jobs N]: each one-way branch and computed
transfer is walked back through data flow to the song byte deciding it, and play run on the
song patched with the values the instructions say change it (docs/forced.md).
"""

import argparse
import bisect
import json
import os
import pathlib
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np
from deity_informant import lift
from deity_informant.lifter import bus_only
from jennings.coverage import RETURN
from jennings.coverage import components as C
from jennings.coverage.cfg import JMP, JSR, instruction_bytes
from jennings.dataflow import REGISTERS, flow
from jennings.opcodes import OPCODES
from tqdm import tqdm

import forced
import liftcheck
import sidtrace
import transliterate

#: The calls a patched run runs past the call its guard ran in.
WINDOW = 128
#: The runs of a guard in its call that are walked back.
INSTANCES = 3
#: The locations one walk visits.
NODES = 400
#: The values of a song byte run per guard run, the nearest the byte's own first.
CHOICES = 16
#: The song bytes one root is walked back to.
SOURCES = 6
#: The bases one guard is walked in.
TRIES = 3
#: The instructions one traced call may run.
STEPS = 1_000_000
#: The census access kinds that load (jennings `components.ACCESSES`).
LOADS = [i for i, k in enumerate(C.ACCESSES) if "load" in k or k == "read"]
#: The bits of P each flag is.
FLAGS = {"C": 0, "Z": 1, "I": 2, "D": 3, "B": 4, "V": 6, "N": 7}
#: The register varnode offsets by name.
OFFSET = {name: off for off, name in REGISTERS.items()}
#: The thread count each numeric library gets in a pool worker.
THREADS = dict.fromkeys(
    ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"), "2"
)
RTS, JMP_IND = 0x60, 0x6C
#: The opcodes that push and pull a register: PHA, PHP; PLA, PLP.
PUSH, PULL = (0x48, 0x08), (0x68, 0x28)
#: The addressing modes of a table at a constant base, indexed by a register.
INDEXED = ("absx", "absy", "zpx", "zpy")
CONSTANT = "value is a constant the player stores"
NO_DATA = "no song data feeds it"
TWO_BYTES = "needs two bytes at once"
NO_VALUE = "no value of the byte changes it"
REJECTED = "patched run rejected"
UNFLIPPED = "patched runs leave it one-way"


@dataclass(frozen=True)
class Stop:
    """Where a walk stopped: the location `loc` read at `pc` in call `call`, and why."""

    call: int
    pc: int
    loc: object
    reason: str
    detail: str = ""

    def json(self):
        """The JSON-ready form."""
        loc = self.loc if isinstance(self.loc, str) else f"${self.loc:04X}"
        return {
            "call": self.call,
            "pc": self.pc,
            "loc": loc,
            "reason": self.reason,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class Source:
    """Song byte `addr`, `old` its value, a root's value is computed from through `hops`
    ``(call, record, output, input)``, the root's own first."""

    addr: int
    old: int
    hops: tuple


@dataclass
class Trace:
    """One traced call: `records` and `regs` as :func:`records` gives them, `mem` RAM at the
    call's entry."""

    records: list
    regs: list
    mem: np.ndarray
    sets: dict = field(default_factory=dict)

    def outputs(self, i):
        """{location: (value inputs, address inputs)} record `i` sets."""
        return outputs(self.records[i])

    def writer(self, i, loc):
        """The last record before `i` that sets `loc`, None where none does."""
        if not self.sets:
            for j in range(len(self.records)):
                for at in self.outputs(j):
                    self.sets.setdefault(at, []).append(j)
        at = self.sets.get(loc, ())
        k = bisect.bisect_left(at, i)
        return at[k - 1] if k else None


def stack(sp, k):
    """The stack byte `k` above `sp`."""
    return 0x100 | ((sp + k) & 0xFF)


def _at(pc, code):
    """64 KiB holding instruction bytes `code` at `pc`."""
    mem = bytearray(0x10000)
    for i, b in enumerate(code):
        mem[(pc + i) & 0xFFFF] = b
    return mem


@lru_cache(maxsize=None)
def _shape(pc, code, nloads, nstores, addressed):
    """`flow` of instruction bytes `code` at `pc` with placeholder accesses and operands."""
    return flow(
        _at(pc, code),
        pc,
        [("L", i) for i in range(nloads)],
        [("S", i) for i in range(nstores)],
        {1: ("O", 1), 2: ("O", 2)},
        addressed=addressed,
    )


def outputs(rec):
    """{location: (value inputs, address inputs)} the trace record `rec` sets: the bytes and
    registers its value is computed from, and those only its loads' addresses are; a JSR's
    pushed return address constant."""
    return _outputs(rec[:3] + rec[4:])


@lru_cache(maxsize=1 << 16)
def _outputs(rec):
    """:func:`outputs` of a record without its loaded values."""
    pc, code, loads, stores, sp, smc = rec
    names = {("L", i): a for i, a in enumerate(loads)}
    names.update((("S", i), a) for i, a in enumerate(stores))
    names.update((("O", k), (pc + k) & 0xFFFF) for k in smc)

    def named(deps):
        return frozenset(
            names.get(d, d) for d in deps if isinstance(d, str) or d in names
        )

    full, value = (_shape(pc, code, len(loads), len(stores), a) for a in (True, False))
    out = {
        names.get(k, k): (named(value.get(k, ())), named(v) - named(value.get(k, ())))
        for k, v in full.items()
    }
    if code[0] == JSR:
        out.update(
            {stack(sp, 0): (frozenset(),) * 2, stack(sp, -1): (frozenset(),) * 2}
        )
    return out


@lru_cache(maxsize=None)
def _lifted(pc, code):
    """deity-informant's lift of instruction bytes `code` at `pc`, its bus-only op indices."""
    rec = lift(_at(pc, code), pc)
    return rec, frozenset(bus_only(rec["ops"]))


class Unsupported(Exception):
    """An instruction's P-Code the evaluator does not take."""


_BINARY = {
    "INT_ADD": np.add,
    "INT_SUB": np.subtract,
    "INT_AND": np.bitwise_and,
    "INT_OR": np.bitwise_or,
    "INT_XOR": np.bitwise_xor,
    "INT_LEFT": np.left_shift,
    "INT_RIGHT": np.right_shift,
    "INT_EQUAL": np.equal,
    "INT_NOTEQUAL": np.not_equal,
    "INT_LESS": np.less,
    "INT_LESSEQUAL": np.less_equal,
}


def extent(mask, at):
    """``[lo, hi)``: the run of set bytes of `mask` (bool [65536]) holding `at`, empty where
    `at` is not set."""
    if not mask[at]:
        return at, at
    below = np.flatnonzero(~mask[:at])
    above = np.flatnonzero(~mask[at:])
    return (int(below[-1]) + 1 if below.size else 0), (
        at + int(above[0]) if above.size else 0x10000
    )


def evaluate(trace, i, var, values, span):  # pylint: disable=too-many-locals
    """(get, valid): record `i` of `trace` run on each of `values` for location `var`, its
    other inputs as observed; ``get(loc)`` the values it leaves at `loc`, `valid` where every
    table it indexes stays in the :func:`extent` of `span` its seen entry is in. Raises
    :class:`Unsupported`."""
    pc, code, loads, vals, _, _, smc = trace.records[i]
    rec, dummy = _lifted(pc, code)
    n = len(values)
    regs = dict(zip(range(4), trace.regs[i][:4]))
    regs.update((OFFSET[f], trace.regs[i][4] >> b & 1) for f, b in FLAGS.items())
    regs = {k: np.full(n, v, np.int64) for k, v in regs.items()}
    if isinstance(var, str):
        regs[OFFSET[var]] = values
    mem = trace.mem.astype(np.int64)
    mem[list(loads)] = vals
    temps, stored, valid = {}, [], np.ones(n, bool)
    operand = {(pc + k) & 0xFFFF: k for k in smc}

    def const(v, at):
        offs, how = rec["prov"]["ops"].get(at, ((), None))
        k = operand.get(var)
        if k is None or k not in offs:
            return np.full(n, v, np.int64)
        if how == "id":
            return values
        if how == "word":
            return values << (8 * (k - 1)) | code[3 - k] << (8 * (2 - k))
        raise Unsupported(f"operand form {how}")

    def get(vn, at):
        space, off, _ = vn
        if space == "c":
            return const(off, at)
        return (temps if space == "u" else regs)[off]

    def load(addr):
        nonlocal valid
        if np.unique(addr).size > 1:
            seen = [a for a in loads if (addr == a).any()]
            lo, hi = extent(span, seen[0]) if seen else (0, 0)
            valid = valid & (addr >= lo) & (addr < hi)
        got = mem[addr]
        for s_addr, s_val in stored:
            got = np.where(addr == s_addr, s_val, got)
        return np.where(addr == var, values, got) if isinstance(var, int) else got

    for k, (mn, dst, ins) in enumerate(rec["ops"]):
        if k in dummy:
            continue
        if mn == "STORE":
            stored.append((get(ins[0], (k, 0)) & 0xFFFF, get(ins[1], (k, 1))))
            continue
        args = [get(vn, (k, j)) for j, vn in enumerate(ins)]
        if mn == "LOAD":
            got = load(args[0] & 0xFFFF)
        elif mn in ("COPY", "INT_ZEXT"):
            got = args[0]
        elif mn == "INT_CARRY":
            got = (args[0] + args[1]) >> (8 * ins[0][2]) & 1
        elif mn in _BINARY:
            got = _BINARY[mn](args[0], args[1]).astype(np.int64)
        else:
            raise Unsupported(mn)
        (temps if dst[0] == "u" else regs)[dst[1]] = got & ((1 << (8 * dst[2])) - 1)

    def value(loc):
        if isinstance(loc, str):
            return regs[OFFSET[loc]]
        got = mem[np.full(n, loc)]
        for s_addr, s_val in stored:
            got = np.where(s_addr == loc, s_val, got)
        return got

    return value, valid


def records(state, play, stored):
    """(records, registers) of one call of `play` from native machine `state`: per
    instruction ``(pc, code, loads, values, stores, sp, offsets of operand bytes among
    `stored`)`` as jennings' TraceMPU keeps them, and ``(A, X, Y, SP, P)`` before it."""
    _, acc = C.machine(state).run_to_rts_accesses(play, 0, sidtrace.MAX_CYCLES)
    acc = np.asarray(acc, np.int64).tolist()
    m = C.machine(state)
    sp, mem = m.sp, np.frombuffer(memoryview(m.memory), np.uint8)
    mem[0x100 + sp] = mem[0x100 + ((sp - 1) & 0xFF)] = (RETURN - 1) & 0xFF
    m.sp, m.a, m.pc = (sp - 2) & 0xFF, 0, play
    out, regs, k = [], [], 0
    for _ in range(STEPS):
        if m.pc == RETURN and m.sp == sp:
            return out, regs
        pc, at = m.pc, m.sp
        code = (
            mem[np.arange(pc, pc + 3) & 0xFFFF].tobytes()
            if pc > 0xFFFD
            else mem[pc : pc + 3].tobytes()
        )
        loads, stores = [], []
        while k < len(acc) and acc[k] >> 17 == pc:
            (stores if acc[k] >> 16 & 1 else loads).append(acc[k] & 0xFFFF)
            k += 1
        if code[0] in PUSH:
            stores.append(stack(at, 0))
        elif code[0] in PULL:
            loads.append(stack(at, 1))
        smc = tuple(j for j in (1, 2) if stored[(pc + j) & 0xFFFF])
        vals = tuple(mem[loads].tolist())
        out.append((pc, code, tuple(loads), vals, tuple(stores), at, smc))
        regs.append((m.a, m.x, m.y, at, m.p))
        m.step()
    raise RuntimeError(
        f"play at ${play:04X} does not return within {STEPS} instructions"
    )


class Base:  # pylint: disable=too-many-instance-attributes
    """A post-init image of play, `stored` the bytes play stores: its calls on demand,
    traced, and the addresses each stores."""

    def __init__(self, image, play, stored):
        self.image, self.play, self.stored = bytes(image), play, stored
        self.machine, self.calls = C.machine(self.image), 0
        self.entries, self.traces, self.stores = {}, {}, {}

    def entry(self, call):
        """A native machine at the entry of call `call`."""
        if call not in self.entries:
            below = [c for c in self.entries if c < call]
            at = max(below) if below else 0
            m = C.machine(self.entries[at] if below else self.image)
            if call > at:
                m.trajectory(self.play, 0, sidtrace.MAX_CYCLES, call - at, [self.play])
            self.entries[call] = m
        return self.entries[call]

    def trace(self, call):
        """The :class:`Trace` of call `call`."""
        if call not in self.traces:
            m = self.entry(call)
            ram = np.frombuffer(bytes(m.memory), np.uint8)
            self.traces[call] = Trace(*records(m, self.play, self.stored), ram)
        return self.traces[call]

    def last_store(self, addr, call):
        """The last call before `call` that stores `addr`, None where none does."""
        while self.calls < call:
            _, acc = self.machine.run_to_rts_accesses(self.play, 0, sidtrace.MAX_CYCLES)
            acc = np.asarray(acc, np.int64)
            for a in np.unique(acc[acc >> 16 & 1 == 1] & 0xFFFF).tolist():
                self.stores.setdefault(a, []).append(self.calls)
            self.calls += 1
        at = self.stores.get(addr, ())
        k = bisect.bisect_left(at, call)
        return at[k - 1] if k else None


@dataclass(frozen=True)
class Facts:
    """What the bases' runs show, as one walk takes it: `song` data, the `code` bytes run,
    the bytes a `table` may hold (the image but code and stored bytes), the bytes `stored`,
    and the successors `known` the guard walked has gone to."""

    song: np.ndarray
    code: np.ndarray
    table: np.ndarray
    stored: np.ndarray
    known: frozenset = frozenset()


def writer(base, at, loc, facts):
    """Where the value of `loc` read by record `at` ``(call, record)`` was set: ``(call,
    record)``, or a :class:`Source` or :class:`Stop` where it is an input of the call.
    """
    call, i = at
    trace = base.trace(call)
    j = trace.writer(i, loc)
    if j is not None:
        return call, j
    pc = trace.records[i][0]
    if not isinstance(loc, int):
        return Stop(call, pc, loc, NO_DATA, "register at play's entry")
    if facts.song[loc]:
        return Source(loc, int(trace.mem[loc]), ())
    if 0x100 <= loc < 0x200:
        return Stop(call, pc, loc, NO_DATA, "stack at play's entry")
    before = base.last_store(loc, call)
    if before is None:
        what = (
            "player code" if facts.code[loc] else "init's value, never stored by play"
        )
        return Stop(call, pc, loc, NO_DATA, what)
    return before, base.trace(before).writer(len(base.trace(before).records), loc)


def _order(deps):
    """The inputs of `deps` in walking order: memory, then A, X, Y, then flags."""
    rank = {"A": 1, "X": 2, "Y": 3}
    keep = [d for d in deps if d not in ("SP", "I", "D", "B")]
    return sorted(
        keep, key=lambda d: (0, d) if isinstance(d, int) else (rank.get(d, 4), 0)
    )


def sources(base, at, loc, facts, select=False):
    """The :class:`Source` s the value of `loc` read by record `at` ``(call, record)`` is
    computed from, depth first, loaded values before their addresses (with `select`, an
    indexed table's index first); last the :class:`Stop` of the first path."""
    todo, seen, first = [(*at, loc, ())], set(), None
    while todo and len(seen) < NODES:
        c, k, here, hops = todo.pop()
        if (c, k, here) in seen:
            continue
        seen.add((c, k, here))
        got = writer(base, (c, k), here, facts)
        if isinstance(got, Source):
            yield Source(got.addr, got.old, hops)
            continue
        if isinstance(got, Stop):
            first = first or got
            continue
        c2, j = got
        rec = base.trace(c2).records[j]
        value, address = base.trace(c2).outputs(j)[here]
        if select and OPCODES[rec[1][0]][1] in INDEXED:
            value, address = address, value
        deps = _order(value) + _order(address)
        if not deps:
            first = first or Stop(c2, rec[0], here, CONSTANT, f"set by ${rec[0]:04X}")
            continue
        todo.extend((c2, j, d, hops + ((c2, j, here, d),)) for d in reversed(deps))
    pc = base.trace(at[0]).records[at[1]][0]
    yield first or Stop(at[0], pc, loc, NO_DATA, "walk budget spent")


def walk(base, at, loc, facts):
    """The first of :func:`sources` of `loc`, loaded values first."""
    return next(sources(base, at, loc, facts))


def walks(base, at, loc, facts):
    """(the first :data:`SOURCES` of :func:`sources` of `loc`, a table's index first where
    `loc` is a byte, and their stop or None)."""
    got = []
    for w in sources(base, at, loc, facts, isinstance(loc, int)):
        if isinstance(w, Stop):
            return got, w
        got.append(w)
        if len(got) == SOURCES:
            break
    return got, None


def computed(base, src, table):
    """(values, valid) of the root walked to `src` (a :class:`Source`) over every value of
    its song byte, through its hops; `table` as :func:`evaluate` takes it."""
    values, valid = np.arange(256, dtype=np.int64), np.ones(256, bool)
    for c, j, out, inp in reversed(src.hops):
        get, ok = evaluate(base.trace(c), j, inp, values, table)
        values, valid = get(out), valid & ok
    return values, valid


def _word(v):
    """The words of byte arrays ``v[0]`` (low) and ``v[1]`` (high)."""
    return v[0] | v[1] << 8


def _return(v):
    """The address an RTS goes to from pulled bytes `v`."""
    return (_word(v) + 1) & 0xFFFF


def roots(trace, i, stored):
    """(locations, seen, successor) of the guard at record `i` of `trace`: the locations
    deciding where it goes, ``seen(next)`` their values where it went to ``next``, and
    ``successor(values)``; `stored` the bytes play stores."""
    pc, code, _, _, _, sp, _ = trace.records[i]
    if code[0] == RTS:
        return (
            [stack(sp, 1), stack(sp, 2)],
            lambda n: [n - 1 & 0xFF, n - 1 >> 8],
            _return,
        )
    if code[0] in (JMP_IND, JMP, JSR):
        locs = [pc + 1, pc + 2]
        if code[0] == JMP_IND:
            ptr = code[1] | code[2] << 8
            locs = [ptr, (ptr & 0xFF00) | ((ptr + 1) & 0xFF)]
        return locs, lambda n: [n & 0xFF, n >> 8], _word
    ctrl = _lifted(pc, code)[0]["ctrl"]
    name, cond = REGISTERS[ctrl[1][1]], ctrl[2]
    seen = [trace.regs[i][4] >> FLAGS[name] & 1, code[1]]
    locs = [name] + ([(pc + 1) & 0xFFFF] if stored[(pc + 1) & 0xFFFF] else [])

    def succ(v):
        off = v[1] if len(v) > 1 else seen[1]
        target = (pc + 2 + np.where(off > 127, off - 256, off)) & 0xFFFF
        return np.where(v[0] == cond, target, (pc + 2) & 0xFFFF)

    return locs, lambda _: seen, succ


def path(base, at, src, facts):
    """int [256]: per value of song byte `src` (a :class:`Source` of a root of record `at`
    ``(call, record)``), the branches run between its load and that record, their flag
    computed from it, that it sends the other way."""
    call, i = at
    load = src.hops[-1][:2] if src.hops else at
    off, tainted = np.zeros(256, np.int64), {src.addr}
    for c in sorted({h[0] for h in src.hops} | {call}):
        trace = base.trace(c)
        tainted = {t for t in tainted if isinstance(t, int)}
        for r in range(
            load[1] if c == load[0] else 0, i if c == call else len(trace.records)
        ):
            code = trace.records[r][1]
            if code[0] in forced.BRANCH:
                flag = REGISTERS[_lifted(trace.records[r][0], code)[0]["ctrl"][1][1]]
                if flag in tainted:
                    off += _against(base, (c, r), flag, src.addr, facts)
                continue
            for out, (value, address) in trace.outputs(r).items():
                if tainted & (value | address):
                    tainted.add(out)
                else:
                    tainted.discard(out)
    return off


def _against(base, at, flag, addr, facts):
    """int [256]: 1 where the value of song byte `addr` sends the branch at `at` ``(call,
    record)``, `flag` its flag, the other way."""
    w = walk(base, at, flag, facts)
    if not isinstance(w, Source) or w.addr != addr:
        return 0
    try:
        got, ok = computed(base, w, facts.table)
    except Unsupported:
        return 0
    went = base.trace(at[0]).regs[at[1]][4] >> FLAGS[flag] & 1
    return (~ok | (got != went)).astype(np.int64)


def candidates(base, at, facts):
    """(changes, stop): the :func:`_choose` song-byte changes that send the guard run by
    record `at` ``(call, record)`` to a successor not among `facts.known`; else the
    :class:`Stop` of the walk."""
    call, i = at
    trace = base.trace(call)
    pc = trace.records[i][0]
    locs, seen, succ = roots(trace, i, facts.stored)
    found = [walks(base, at, loc, facts) for loc in locs]
    if not any(f[0] for f in found):
        return [], found[0][1]
    fixed = seen(trace.records[i + 1][0] if i + 1 < len(trace.records) else RETURN)
    stop = None
    for addr in dict.fromkeys(w.addr for f in found for w in f[0]):
        each = [next((w for w in f[0] if w.addr == addr), None) for f in found]
        if any(w is None and f[0] for w, f in zip(each, found)):
            stop = stop or Stop(
                call, pc, locs[0], TWO_BYTES, f"${addr:04X} and another"
            )
            continue
        try:
            picks = _choose(base, at, zip(each, fixed), succ, facts)
        except Unsupported as e:
            stop = stop or Stop(call, pc, locs[0], NO_DATA, f"P-Code {e}")
            continue
        if picks:
            return picks, None
        why = found[0][1]
        detail = f"${addr:04X}" + (f"; {why.reason} at ${why.pc:04X}" if why else "")
        stop = stop or Stop(call, pc, addr, NO_VALUE, detail)
    return [], stop


def _choose(base, at, each, succ, facts):
    """``(address, new, branches turned)`` of the song byte the roots' ``(Source or None,
    value seen)`` `each` share, sending guard `at` through `succ` to a successor not among
    `facts.known`, fewest branches before it sent the other way first."""
    valid, vals, src = np.ones(256, bool), [], None
    for w, v in each:
        got, ok = (
            (np.full(256, v, np.int64), True)
            if w is None
            else computed(base, w, facts.table)
        )
        src = src or w
        vals.append(got)
        valid &= ok
    targets = succ(vals)
    off = path(base, at, src, facts)
    new = valid & ~np.isin(targets, list(facts.known))
    picks = {}
    for v in sorted(
        np.flatnonzero(new).tolist(), key=lambda v: (off[v], abs(v - src.old))
    ):
        picks.setdefault(int(targets[v]), v)
    return [(src.addr, v, int(off[v])) for v in list(picks.values())[:CHOICES]]


def guards(mem, edges, ran, stored):
    """{pc: kind} of the guards among instructions `ran` (bool [65536]) under edge keys
    `edges`: one-way conditional branches, branches and jumps whose operand play stores,
    ``JMP (ind)`` and returns to an address no ``JSR`` the edges run pushed."""
    out = {pc: "one-way" for pc in forced.gaps(edges, mem) if ran[pc]}
    pcs = np.flatnonzero(ran)
    op = mem[pcs]
    ctl = pcs[np.isin(op, forced.BRANCH)]
    out.update((int(pc), "operand") for pc in ctl if stored[(pc + 1) & 0xFFFF])
    jumps = pcs[np.isin(op, (JMP, JSR))]
    out.update(
        (int(pc), "operand")
        for pc in jumps
        if stored[(pc + 1) & 0xFFFF] or stored[(pc + 2) & 0xFFFF]
    )
    out.update((int(pc), "indirect") for pc in pcs[op == JMP_IND])
    src, dst = edges >> 16, edges & 0xFFFF
    back = (dst - 3) & 0xFFFF
    pushed = ran[src] & (mem[src] == RTS) & ~((mem[back] == JSR) & np.isin(back, src))
    out.update((int(pc), "return") for pc in np.unique(src[pushed]))
    return out


def observe(image, play, calls, masks):
    """(edge keys, {pc: first call}, stored) of `calls` calls of play from `image`; or the
    rule a run breaks: a call does not return, runs song data or leaves the image (`masks`
    ``(song, image)``), or loads I/O."""
    song, span = masks
    try:
        ran = C.Calls(image, play, max_cycles=sidtrace.MAX_CYCLES).run(calls)
    except RuntimeError:
        return "a call does not return"
    edges, at = ran.seen("edges", calls)
    edges = edges.astype(np.int64)
    pcs = np.unique(np.concatenate([edges >> 16, edges & 0xFFFF]))
    if not span[pcs].all():
        return f"runs ${pcs[~span[pcs]][0]:04X}, outside the image"
    starts = np.zeros(0x10000, bool)
    starts[pcs] = True
    mem = np.frombuffer(image, np.uint8)
    data = np.flatnonzero(instruction_bytes(mem, starts) & song)
    if data.size:
        return f"runs song data at ${data[0]:04X}"
    acc = ran.seen("accesses", calls)[0].astype(np.int64)
    addr, load = acc & 0xFFFF, np.isin(acc >> 16 & 7, LOADS)
    if ((addr[load] >= C.IO.start) & (addr[load] < C.IO.stop)).any():
        return "loads I/O"
    first, where = np.unique(edges >> 16, return_index=True)
    return (
        np.unique(edges),
        dict(zip(first.tolist(), at[where].tolist())),
        np.unique(addr[~load]),
    )


#: The bases a process keeps traced, the most recently walked last.
_BASES = {}
#: The bases kept.
KEPT = 4


def attempt(task):
    """``(pc, changes, stop)``: the :func:`candidates` of guard `pc` from its first runs in
    call `call` of a base, else where its walk stopped; a pool worker's job."""
    image, play, pc, call, facts = task
    key = (hash(image), hash(facts.stored.tobytes()))
    base = _BASES.pop(key, None) or Base(image, play, facts.stored)
    _BASES[key] = base
    while len(_BASES) > KEPT:
        _BASES.pop(next(iter(_BASES)))
    trace = base.trace(call)
    stop = None
    for i in [i for i, r in enumerate(trace.records) if r[0] == pc][:INSTANCES]:
        picks, why = candidates(base, (call, i), facts)
        if picks:
            return pc, picks, None
        stop = stop or why
    return pc, [], stop or Stop(call, pc, "PC", NO_DATA, "not run in its call")


def _worker():
    """Set the numeric libraries' thread counts in a pool worker."""
    os.environ.update(THREADS)


class Search:  # pylint: disable=too-many-instance-attributes
    """The bases of a tune (its song states, then each kept patched state) and what their
    unforced runs show, grown to the fixed point."""

    def __init__(self, play, states, proofs, span):
        self.play, self.span = play, span
        self.mem = np.frombuffer(states[0], np.uint8)
        self.song = np.zeros(0x10000, bool)
        self.stored = np.zeros(0x10000, bool)
        for _, _, data in proofs:
            self.song |= data
        self.edges = np.unique(np.concatenate([e for e, _, _ in proofs]))
        self.bases = [(bytes(s), {}, p[1]) for s, p in zip(states, proofs)]
        self.patches, self.tried, self.stops, self.rejected = [], {}, {}, {}
        self.values = {}
        self.seconds = {"walk": 0.0, "runs": 0.0}

    def code(self):
        """bool [65536]: the bytes of the instructions the bases run."""
        ran = np.zeros(0x10000, bool)
        ran[np.unique(self.edges >> 16)] = True
        return instruction_bytes(self.mem, ran)

    def tasks(self, todo):
        """``[(base, kind, task)]``: the walks of the open guards the bases `todo` run."""
        one, code, out = forced.gaps(self.edges, self.mem), self.code(), []
        for b in todo:
            image, _, first = self.bases[b]
            ran = np.zeros(0x10000, bool)
            ran[list(first)] = True
            for pc, kind in sorted(
                guards(self.mem, self.edges, ran, self.stored).items()
            ):
                if (kind == "one-way" and pc not in one) or self.tried.get(
                    pc, 0
                ) >= TRIES:
                    continue
                self.tried[pc] = self.tried.get(pc, 0) + 1
                known = frozenset(
                    (self.edges[self.edges >> 16 == pc] & 0xFFFF).tolist()
                )
                table = self.span & ~code & ~self.stored
                facts = Facts(self.song, code, table, self.stored, known)
                out.append((b, kind, (image, self.play, pc, first[pc], facts)))
        return out

    def run(self, pool=None):
        """Walk and patch to the fixed point; `pool` an executor or None."""
        todo = list(range(len(self.bases)))
        while todo:
            tasks = self.tasks(todo)
            start, work = time.time(), [t for _, _, t in tasks]
            got = list(
                tqdm(
                    pool.map(attempt, work) if pool else map(attempt, work),
                    total=len(work),
                    desc="walk",
                    unit="guard",
                    leave=False,
                )
            )
            self.seconds["walk"] += time.time() - start
            start, todo = time.time(), []
            for (b, kind, task), (pc, picks, stop) in zip(tasks, got):
                pushed = not (kind == "return" and stop and stop.reason == CONSTANT)
                if stop is not None and (pushed or self.mem[stop.pc] != JSR):
                    self.stops.setdefault(pc, (kind, stop))
                for addr, new, off in picks:
                    self.values.setdefault(pc, []).append(
                        f"${addr:04X}=${new:02X}/{off}"
                    )
                    kept = self.patch(b, pc, (addr, new), task[3] + WINDOW)
                    if kept is not None:
                        todo.append(kept)
            self.seconds["runs"] += time.time() - start

    def patch(self, b, pc, change, calls):
        """The index of the base kept from base `b` with `change` ``(address, new)`` made to
        its song and run `calls` calls for guard `pc`; None where the run is rejected or adds
        no edge."""
        image, changes, _ = self.bases[b]
        addr, new = change
        out = bytearray(image)
        old = changes.get(addr, (image[addr], None))[0]
        out[addr] = new
        got = observe(bytes(out), self.play, calls, (self.song, self.span))
        if isinstance(got, str):
            self.rejected.setdefault(pc, got)
            return None
        edges, first, stored = got
        fresh = np.setdiff1d(edges, self.edges)
        if not fresh.size:
            return None
        self.edges = np.union1d(self.edges, fresh)
        self.stored[stored] = True
        changes = {**changes, addr: (old, new)}
        self.bases.append((bytes(out), changes, first))
        self.patches.append(
            {
                "guard": pc,
                "changes": [[a, o, n] for a, (o, n) in sorted(changes.items())],
                "calls": calls,
                "edges": int(fresh.size),
            }
        )
        return len(self.bases) - 1

    def unresolved(self):
        """[{guard, kind, reason, ...}] of the guards still open: one-way branches, and
        computed transfers no kept patch was made for, each with where its walk stopped.
        """
        one = forced.gaps(self.edges, self.mem)
        patched = {p["guard"] for p in self.patches}
        out = {}
        for pc, (kind, stop) in sorted(self.stops.items()):
            if (pc not in one) if kind == "one-way" else (pc in patched):
                continue
            out[pc] = {"guard": pc, "kind": kind, **stop.json()}
            if pc in self.rejected and stop.reason == NO_VALUE:
                out[pc].update(reason=REJECTED, detail=self.rejected[pc])
        for pc in sorted(set(one) - set(out)):
            out[pc] = {
                "guard": pc,
                "kind": "one-way",
                "reason": REJECTED if pc in self.rejected else UNFLIPPED,
                "detail": self.rejected.get(pc, " ".join(self.values.get(pc, ()))),
            }
        return [out[pc] for pc in sorted(out)]


def report(psid, jobs=1, images=None):  # pylint: disable=too-many-locals
    """The JSON-ready report of walking play of `psid` back from its guards to its song data,
    the kept patched images saved to `images` (a .npz path) where given."""
    start = time.time()
    play, states = liftcheck.song_states(psid)
    load, _, _, body = sidtrace.psid_parts(psid)
    stored = np.zeros(0x10000, bool)
    proofs = [forced.proven(s, play, transliterate.VOLATILE, stored) for s in states]
    seconds = {"certificate": time.time() - start}
    start, span = time.time(), np.zeros(0x10000, bool)
    span[load : load + len(body)] = True
    code = span.copy()
    for _, _, data in proofs:
        code &= ~data
    base = np.unique(np.concatenate([e for e, _, _ in proofs]))
    extra, done = np.zeros(0, np.int64), set()
    for image, (edges, first, _) in zip(states, proofs):
        got = forced.force(image, play, code, (edges, first, done))[0]
        extra = np.union1d(extra, got)
    seconds["forcing"] = time.time() - start
    search = Search(play, states, proofs, span)
    search.stored |= stored
    if jobs > 1:
        with ProcessPoolExecutor(jobs, initializer=_worker) as pool:
            search.run(pool)
    else:
        search.run()
    seconds.update(search.seconds)
    if images is not None:
        images.parent.mkdir(parents=True, exist_ok=True)
        kept = [np.frombuffer(b[0], np.uint8) for b in search.bases[len(states) :]]
        np.savez_compressed(
            images, images=np.asarray(kept, np.uint8).reshape(-1, 0x10000)
        )
    left = search.unresolved()
    reasons = {}
    for u in left:
        reasons[u["reason"]] = reasons.get(u["reason"], 0) + 1
    every = np.union1d(search.edges, extra)
    return {
        "play": play,
        "states": len(states),
        "instructions": {
            "certificates": len(forced.instructions(base)),
            "forced": len(forced.instructions(np.union1d(base, extra))),
            "patched": len(forced.instructions(search.edges)),
            "all": len(forced.instructions(every)),
        },
        "executed": forced.instructions(every).tolist(),
        "patches": search.patches,
        "patched_edges": [
            [int(k >> 16), int(k & 0xFFFF)] for k in np.setdiff1d(search.edges, base)
        ],
        "unresolved": left,
        "reasons": reasons,
        "seconds": {k: round(v, 2) for k, v in seconds.items()},
    }


def main(argv=None):
    """Command line entry."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n", maxsplit=1)[0])
    ap.add_argument("psid", type=pathlib.Path, nargs="+")
    ap.add_argument("--json", type=pathlib.Path)
    ap.add_argument("--images", type=pathlib.Path, help="directory for patched images")
    ap.add_argument("--jobs", type=int, default=len(os.sched_getaffinity(0)))
    args = ap.parse_args(argv)
    out = {}
    for p in args.psid:
        npz = None if args.images is None else args.images / f"{p.stem}.npz"
        out[str(p)] = report(p.read_bytes(), max(1, args.jobs), npz)
    if args.json:
        args.json.write_text(json.dumps(out, indent=1), "utf-8")
    for name, r in out.items():
        n, s = r["instructions"], r["seconds"]
        why = ", ".join(f"{v} {k}" for k, v in sorted(r["reasons"].items()))
        print(
            f"{pathlib.Path(name).stem}: instructions {n['certificates']} certified, "
            f"{n['forced']} forced, {n['patched']} patched, {n['all']} all; "
            f"{len(r['patches'])} patches; {len(r['unresolved'])} unresolved"
            f"{' (' + why + ')' if why else ''}; seconds "
            + " ".join(f"{k} {v}" for k, v in s.items())
        )
        for u in r["unresolved"]:
            hop = f" at ${u['pc']:04X} {u['loc']} call {u['call']}" if "pc" in u else ""
            print(f"  ${u['guard']:04X} {u['kind']}{hop}: {u['reason']} {u['detail']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
