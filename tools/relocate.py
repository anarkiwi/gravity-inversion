"""Relocate a PSID player's data after init by whole pages (docs/relocate.md).

Play's data groups into blocks; each on its code's pages moves by a multiple of 256 into free
memory with a free page after it; the high bytes of the addresses play forms into them are
rewritten at their sources (:class:`Sources`). The result is a PSID of the songs relocated
whose init stores one song's post-init state, or for several runs the original's and moves
what it leaves."""

import argparse
import dataclasses
import json
import pathlib
import sys
from collections import defaultdict

import numpy as np
from deity_informant.postinit import X, Run
from jennings.acme import read_symbol_list
from jennings.coverage.cfg import LEN
from jennings.devices.mpu6502 import NativeMPU
from jennings.opcodes import OPCODES
from pysidtracker.emu import wire_mpu
from pysidtracker.header import parse_sid_header
from pysidtracker.relocate import init_fixup, store_stub
from pysidtracker.reglog import first_divergence
from pysidtracker.survey import hvsc_capture
from tqdm import tqdm
from vagrant_moses import memmap as mm
from vagrant_moses.cgen import VOLATILE

from sidtrace import (
    against,
    IO,
    boot_ram,
    default_song,
    header_model,
    loop_frame,
    overrun_remap,
    psid_parts,
    song_calls,
    songs,
    trace_psid,
)
from footprint import mask
from playtables import (
    LOW,
    PAGE,
    groups,
    masks,
    overreach,
    ruled,
    static_reach,
    trace_init,
)
from pointers import entries, widen
from transliterate import c64_init

#: The first page a PSID may load at ($07E8 and up).
FLOOR = 0x800
#: The vectors a 6502 reads ($FFFA-$FFFF).
VECTORS = 0xFFFA
#: The CPU port's bank bits: RAM at $A000-$BFFF and $E000-$FFFF, I/O at $D000.
RAM_IO = 0x05
#: The CPU port a PSID's init runs under: BASIC, I/O and KERNAL.
INIT_PORT = 0x37
#: Modes whose access address has a high byte play forms: an operand's or a pointer's.
ADDRESSED = frozenset(("abs", "absx", "absy", "ind", "indx", "indy"))
COMPARE = {"CMP": "a", "CPX": "x", "CPY": "y"}
STEP = {"INX": "x", "DEX": "x", "INY": "y", "DEY": "y"}


@dataclasses.dataclass
class Post:  # pylint: disable=too-many-instance-attributes
    """A PSID `data` after init of `song`: memory `mem` (uint8 [65536]), init's volatile
    writes `io` in order and the addresses it `stored` to; its image spans `load`-`end`.
    """

    data: bytes
    song: int
    load: int
    end: int
    play: int
    mem: np.ndarray
    io: list
    stored: np.ndarray


def post_init(data, song=None):
    """The :class:`Post` of `data` after init of 0-based `song` (default the start song), run
    by tools/transliterate.py's `c64_init` on sidtrace's boot RAM and the header's SID model.
    """
    song = default_song(data) if song is None else song
    load, init, play, image = psid_parts(data)
    ram = boot_ram(data)
    io, stored = c64_init(ram, init, song, header_model(data))
    mem = np.frombuffer(bytes(ram), np.uint8).copy()
    end = load + len(image)
    return Post(
        data, song, load, end, play, mem, io, np.array(sorted(stored), np.int64)
    )


def _fit(lo, hi, pad, free, start):
    """The least multiple of 256 putting `lo`-`hi` at or after `start`, it and the `pad`
    bytes after it in `free` (bool [65536]), or None."""
    d = -(-(start - lo) // PAGE) * PAGE
    while hi + d + pad < free.size:
        if free[lo + d : hi + d + pad + 1].all():
            return d
        d += PAGE
    return None


def place(spans, why, start, own=()):
    """(Δ per (lo, hi, pad, need) of ascending `spans`, the first byte past the last pad from
    `start`, the bool [65536] mask of the blocks placed and their pads): first, in address
    order, the spans holding bytes of the reasons `own` before the others, 0 for each span that
    and the `need` bytes after it (the pad it needs where it is) lie on bytes no mask of `why`
    ({reason: bool [65536]}) nor a span kept before holds, those of the reasons `own` aside;
    then for the others the least multiple of 256 putting it and its pad past the previous pad
    from `start`, else from :data:`FLOOR`, on bytes no mask nor placed span holds. Raises
    naming, for the span that fits nowhere, the reasons holding the window it comes closest at.
    """
    free = ~np.logical_or.reduce(list(why.values()))
    mine = ~np.logical_or.reduce([m for k, m in why.items() if k not in own])
    held = np.zeros(free.size, bool)
    out, cursor = [None] * len(spans), {start: start, FLOOR: FLOOR}
    theirs = np.logical_or.reduce(
        [np.zeros(free.size, bool)] + [why[k] for k in own if k in why]
    )
    order = sorted(
        range(len(spans)), key=lambda i: not theirs[slice(*spans[i][:2])].any()
    )
    for i in order:
        lo, hi, _, need = spans[i]
        if (mine & ~held)[lo : hi + need + 1].all():
            out[i] = 0
            free[lo : hi + need + 1] = False
            held[lo : hi + need + 1] = True
    for i, (lo, hi, pad, _) in enumerate(spans):
        if out[i] is not None:
            continue
        d = next(
            (
                d
                for at in (start, FLOOR)
                if (d := _fit(lo, hi, pad, free, cursor[at])) is not None
            ),
            None,
        )
        if d is None:
            raise RuntimeError(blocked(lo, hi, pad, {**why, "blocks placed": held}))
        at = start if lo + d >= start else FLOOR
        cursor[at] = hi + d + pad + 1
        free[lo + d : cursor[at]] = False
        held[lo + d : cursor[at]] = True
        out[i] = d
    return out, cursor[start], held


def blocked(lo, hi, pad, why):
    """Why `lo`-`hi` and the `pad` bytes after it fit nowhere: the reasons of `why` holding
    the window a whole-page move from :data:`FLOOR` puts it on with the fewest bytes held.
    """
    n = hi - lo + pad + 1
    d = np.arange(-(-(FLOOR - lo) // PAGE) * PAGE, 0x10000 - hi - pad, PAGE)
    if not d.size:
        return f"${lo:04X}-${hi:04X}: {n} bytes exceed memory"
    win = (lo + d)[:, None] + np.arange(n)
    any_ = np.logical_or.reduce(list(why.values()))
    best = int(np.argmin(any_[win].sum(axis=1)))
    held = {k: int(m[win[best]].sum()) for k, m in why.items() if m[win[best]].any()}
    return (
        f"no free memory for ${lo:04X}-${hi:04X}: at ${lo + d[best]:04X} "
        + ", ".join(f"{k} {v}" for k, v in held.items())
    )


def _add(f, g, sign=1):
    """The form `f` plus `sign` times `g`; None the constant, X unknown."""
    if X in (f, g):
        return X
    c = defaultdict(int, f or ())
    for a, k in g or ():
        c[a] += sign * k
    return tuple(sorted((a, k) for a, k in c.items() if k)) or None


class Sources(Run):  # pylint: disable=too-many-instance-attributes
    """deity-informant's :class:`~deity_informant.postinit.Run` tagging each value with its
    form, ``((addr, coefficient), ...)``: the image bytes it adds up, an immediate being its
    operand's byte; carries come from low bytes, which relocation keeps. `events`: each
    access's (pc, form of its address's high byte, base: an absolute access's operand, an
    indirect one's pointer word); `compares`: {(pc, forms): {values}};
    `patches`: the bytes stores into `control` (a bool mask) take their values from;
    `pairs`: {zero-page pointer: {(pc, offset)}} of the indirect accesses through it. Under
    `rule` ({pc: int64 [65536]}, playtables' `masks`) an instruction's accesses it redirects
    read 0, a constant, or the address it names, and write nothing."""

    def __init__(self, image, control, rule=None):
        super().__init__(image, VOLATILE)
        self.events, self.compares, self.patches = set(), defaultdict(set), set()
        self.pairs = defaultdict(set)
        self.control, self._last, self._sign = control, None, 1
        self.rule, self._pc, self._quiet = rule or {}, None, False
        if self.rule:
            at = np.flatnonzero(
                np.logical_or.reduce([v != -1 for v in self.rule.values()])
            ).tolist()
            self.mem.subscribe_to_read(at, self._redirect)
            self.mem.subscribe_to_write(at, self._drop)

    def _masked(self, a):
        """The address (-2: none, a zero) the current instruction reads at `a` under the
        rule, or None."""
        z = self.rule.get(self._pc)
        if self._quiet or z is None or z[a] == -1:
            return None
        return int(z[a])

    def _redirect(self, a):
        src = self._masked(a)
        if src is None or src == -2:
            return None if src is None else 0
        self._quiet = True
        v = self.mem[src]
        self._quiet = False
        return v

    def _drop(self, a, _):
        if self._masked(a) is None:
            return None
        self._quiet = True
        v = self.mem[a]
        self._quiet = False
        return v

    def merge(self, other):
        """This run with the observations of `other` added."""
        self.events |= other.events
        self.patches |= other.patches
        for mine, theirs in (
            (self.compares, other.compares),
            (self.pairs, other.pairs),
        ):
            for k, v in theirs.items():
                mine[k] |= v
        return self

    def _tag(self, a, key):
        src = self._masked(a)
        if src == -2:
            self._last = None
        elif src is not None:
            self._last = self.tags.get(src, X if self._vol(src) else ((src, 1),))
        else:
            self._last = self.tags.get(a, X if self._vol(a) else ((a, 1),))
        return self._last

    def _adc(self, ta, tm):
        if self.mpu.p & NativeMPU.DECIMAL:
            return X, None
        return _add(ta, tm, self._sign), None

    def _pair(self, mode, ops):
        """The zero-page pointer an indirect access in `mode` reads."""
        return (self.mem[ops[0]] + (self.mpu.x if mode == "indx" else 0)) & 0xFF

    def _word(self, mode, ops):
        """The operand word of an absolute access in `mode`, the pointer word of an indirect
        one."""
        if mode in ("indx", "indy"):
            z = self._pair(mode, ops)
            at = (z, (z + 1) & 0xFF)
        else:
            at = ops
        self._quiet = True
        word = int(self.mem[at[0]]) | int(self.mem[at[1]]) << 8
        self._quiet = False
        return word

    def _high(self, mode, ops):
        """The form of the high byte of the address an instruction in `mode` accesses."""
        if mode in ("indx", "indy"):
            a = (self._pair(mode, ops) + 1) & 0xFF
        else:
            a = ops[1]
        return self.tags.get(a, ((a, 1),))

    def step(self):
        mpu, mem, reg = self.mpu, self.mem, self.reg
        pc = mpu.pc
        name, mode = OPCODES[mem[pc]]
        ops = [(pc + 1 + i) & 0xFFFF for i in range(LEN[mem[pc]] - 1)]
        if mode == "imm":
            self.tags.setdefault(ops[0], ((ops[0], 1),))
        high = self._high(mode, ops) if mode in ADDRESSED else None
        base = self._word(mode, ops) if mode in ADDRESSED else None
        if mode in ("indx", "indy"):
            self.pairs[self._pair(mode, ops)].add((pc, mpu.y if mode == "indy" else 0))
        before = dict(reg)
        value = {"a": mpu.a, "x": mpu.x, "y": mpu.y}
        self._sign = -1 if name == "SBC" else 1
        self._pc = pc
        held = dict(self.tags) if pc in self.rule else None
        super().step()
        if held is not None:
            for a, w in self.acc:
                if w and self._masked(a) is not None:
                    self.tags.pop(a, None)
                    if a in held:
                        self.tags[a] = held[a]
        if name in STEP:
            reg[STEP[name]] = before[STEP[name]]
        if name in ("INC", "DEC") and mode != "acc":
            self.tags[self.acc[-1][0]] = self._last
        if self.acc and self.acc[-1][1] and self.control[self.acc[-1][0]]:
            form = self.tags.get(self.acc[-1][0])
            self.patches.update(a for a, _ in (() if form in (None, X) else form))
        if high is not None and self.acc and name not in ("JSR", "JMP"):
            self.events.add((pc, high, base))
        if name in COMPARE:
            r = COMPARE[name]
            at, form = (
                (ops[0], self.tags[ops[0]])
                if mode == "imm"
                else (self.acc[-1][0], self._last)
            )
            self.compares[(pc, before[r], form)].add((value[r], int(mem[at])))


def provenance(posts, frames, control, rule=None):
    """The :class:`Sources` runs of each song's count of `frames` play calls from its post of
    `posts` under `rule`, merged (:attr:`Sources.control`)."""
    out = None
    for post, n in zip(posts, frames):
        run = Sources(post.mem.tobytes(), control, rule)
        for _ in tqdm(range(n), desc=f"provenance {post.song}", unit="frame"):
            run.call(post.play)
        out = run if out is None else out.merge(run)
    return out


def _shift(form, s):
    return sum(k * s.get(a, 0) for a, k in form or ()) & 0xFF


def solve(cons, s=None):
    """({addr: shift}, indices left unsatisfied) for constraints `cons` [(form, d)], the shifts
    `s` given: the form's bytes shifted add up to `d` (mod 256). A byte shifts when every
    constraint holding it asks it the same nonzero shift; the byte the most constraints hold
    first."""
    s = dict(s or {})
    res = [(d - _shift(form, s)) & 0xFF for form, d in cons]
    holds = defaultdict(list)
    for i, (form, _) in enumerate(cons):
        for a, k in form:
            holds[a].append((i, k))

    def asked(a, ik):
        if a in s or any(k not in (1, -1) for _, k in ik):
            return None
        want = {res[i] * k & 0xFF for i, k in ik}
        return want.pop() if len(want) == 1 and 0 not in want else None

    while cands := [
        (len(ik), -a, a, v)
        for a, ik in holds.items()
        if (v := asked(a, ik)) is not None
    ]:
        *_, a, v = max(cands)
        s[a] = v
        for i, k in holds[a]:
            res[i] = (res[i] - k * v) & 0xFF
    return s, [i for i, r in enumerate(res) if r]


def flags(r, m):
    """N, Z and C of comparing register `r` with operand `m`."""
    return (r - m) & 0x80, r == m, r >= m


def bound(compares, s):
    """A (form, 0) constraint, the register's form less the operand's, per compare of
    `compares` (:attr:`Sources.compares`) whose flags the shifts `s` change."""
    out = []
    for (_, rf, mf), seen in compares.items():
        gap = _add(rf, mf, -1)
        if gap in (None, X):
            continue
        dr, dm = _shift(rf, s), _shift(mf, s)
        if any(flags(r, m) != flags((r + dr) & 0xFF, (m + dm) & 0xFF) for r, m in seen):
            out.append((gap, 0))
    return out


@dataclasses.dataclass
class Plan:  # pylint: disable=too-many-instance-attributes
    """A relocation: `moved` [(lo, hi, Δ)], `kept` {(lo, hi): why}, `overrun` [(addr, Δ)] the
    bytes entry pointers reach copied (:func:`entries`), `shift` {addr: s} and its `kinds`,
    `unsolved` [(pc, base, form, d)] the addresses no shift explains, `free` the free memory
    mask, `cursor` the first byte past the last pad, `left` {pointer: why} the zero-page
    pointers whose post-init word stays, `sites` the overrun rule (:func:`overruns`) the
    original is judged under, `port` the CPU port init leaves banking RAM over the ROMs, or
    None, `held` the bool [65536] mask of the moved blocks and their pads, or None, `fill`
    per moved block (its pad's length, its last record's first byte and length): the pad
    repeats the block's last record, `rom` [(addr, value)] the ROM bytes play reads, stored to
    RAM for `port`."""

    moved: list
    kept: dict
    overrun: list
    shift: dict
    kinds: dict
    unsolved: list
    free: np.ndarray
    cursor: int
    left: dict
    sites: list = dataclasses.field(default_factory=list)
    port: int | None = None
    held: np.ndarray | None = None
    fill: list = dataclasses.field(default_factory=list)
    rom: list = dataclasses.field(default_factory=list)


def rom(posts):
    """The lowest address the CPU port may map ROM or I/O at after init of any of `posts`."""
    return min(
        [IO.start]
        + [r[0] for p in posts for r in mm.rom_ranges(int(p.mem[mm.CPU_PORT]))]
    )


def limits(posts, acc, starts, code):
    """({reason: bool [65536]} of the bytes no block may take: zero page, the stack and
    $0200-$07FF, I/O, the vectors, the pages of `code` (and, for several songs, of init's
    code), for several songs the bytes init touches, the ROMs init leaves mapped where play
    runs them, else the ROM bytes play reads; the CPU port that maps RAM over the ROMs
    instead, or None; [(addr, value)] of the ROM bytes play reads, which RAM there then
    holds)."""
    why = {"below $0800": mask([(0, FLOOR)]), "I/O": mask([(IO.start, IO.stop)])}
    why["vectors"] = mask([(VECTORS, 0x10000)])
    pages = code.reshape(-1, PAGE).any(axis=1)
    if len(posts) > 1:
        why["init's data"] = np.zeros(0x10000, bool)
        for q in posts:
            ic, touched = trace_init(q)
            pages |= ic.reshape(-1, PAGE).any(axis=1)
            why["init's data"] |= touched
    why["code pages"] = np.repeat(pages, PAGE)
    port = int(posts[0].mem[mm.CPU_PORT])
    visible = mask([r for p in posts for r in mm.rom_ranges(int(p.mem[mm.CPU_PORT]))])
    visible &= ~why["I/O"]
    if not visible.any():
        return why, None, []
    if (visible & starts).any():
        why["ROM play runs"] = visible
        return why, None, []
    read = np.zeros(0x10000, bool)
    read[acc[3][acc[1] == 0]] = True
    why["ROM bytes play reads"] = read & visible
    _, mem = wire_mpu(bytearray(posts[0].mem.tobytes()))
    held = [(a, int(mem[a])) for a in np.flatnonzero(read & visible).tolist()]
    return why, (port & ~0x07) | RAM_IO, held


def kind(mem, starts, a):
    """What the byte at `a` is to play: an operand of an instruction at `starts` (a bool
    mask), zero page or data."""
    for n in (1, 2):
        pc = (a - n) & 0xFFFF
        if starts[pc] and LEN[mem[pc]] > n:
            name, mode = OPCODES[mem[pc]]
            return f"{mode} operand of {name}"
    return "zero page" if a < PAGE else "data"


def _stores(acc, extent):
    """((u, v) table index arrays) of the stores of `acc` past their table `u`'s end
    (`extent`, :func:`tables`) into table `v`."""
    _, w, _, addr = acc
    starts, ends, cell, inside, _ = extent
    out = (cell >= 0) & ~inside & (w == 1)
    at = np.searchsorted(starts, addr[out], "right") - 1
    hit = (at >= 0) & (addr[out] <= ends[np.maximum(at, 0)])
    return cell[out][hit], at[hit]


def staying(spans, run, code):
    """{block index: why} of the `spans` that stay: holding `code` or the bytes play's
    control flow is patched from (:attr:`Sources.patches` of `run`)."""
    feeds = np.zeros(0x10000, bool)
    feeds[list(run.patches)] = True
    out = {
        i: "holds code" for i, (lo, hi) in enumerate(spans) if code[lo : hi + 1].any()
    }
    out.update(
        (i, "feeds control")
        for i, (lo, hi) in enumerate(spans)
        if feeds[lo : hi + 1].any()
    )
    return out


def rewrite(run, pages):
    """(shifts, unexplained [(pc or None, base, form, d)]) moving each access of `run`
    (:attr:`Sources.events`) by the `pages` ({(pc, base): Δ >> 8}) of its table, the compares
    on what moves keeping their flags."""
    ev = [(p, b, f) for p, f, b in run.events if f not in (None, X)]
    cons = [(f, pages.get((p, b), 0)) for p, b, f in ev]
    bad = [
        (p, b, f, pages[(p, b)])
        for p, f, b in run.events
        if f in (None, X) and pages.get((p, b))
    ]
    s, unsat = solve(cons)
    while not unsat and (more := [c for c in bound(run.compares, s) if c not in cons]):
        cons += more
        s, unsat = solve(cons, s)
    at = [(p, b) for p, b, _ in ev] + [(None, None)] * (len(cons) - len(ev))
    return s, bad + [(*at[i], *cons[i]) for i in unsat]


def blocking(run, unsolved, table):
    """The tables (`table` {(pc, base): table index}) of the accesses of `run` unsolved: those
    `unsolved` names and those whose address's high byte sums a byte of an unsolved form.
    """
    held = {a for *_, f, _ in unsolved if f not in (None, X) for a, _ in f}
    keys = {(p, b) for p, b, _, _ in unsolved if p is not None}
    keys |= {
        (p, b)
        for p, f, b in run.events
        if f not in (None, X) and held & {a for a, _ in f}
    }
    return {table[k] for k in keys if table.get(k, -1) >= 0}


def plan(posts, frames, reach=None):
    """The :class:`Plan` relocating the play data of the songs of `posts` (:class:`Post` s of
    one PSID), from each song's count of `frames` play calls under the overrun rule of
    play's tables (their ends reaching what `reach`, playtables' `static_reach`, bounds); the
    instructions of a block that stays read past their tables as they
    stand, until no other does."""
    exempt = set()
    while True:
        p, more = _plan(posts, frames, exempt, reach)
        if more <= exempt:
            return p
        exempt |= more


def _plan(posts, frames, exempt, reach):  # pylint: disable=too-many-locals
    """(:func:`plan` with the instructions `exempt` from the overrun rule, the instructions
    with sites in blocks that stay or reading past their tables only within their block).
    Tables join in blocks (:func:`groups`), each padded with the bytes past its end its
    accesses can reach (a page for one an entry pointer's copy goes in); a block stays in place
    off code pages when it and its pad can. A block an unsolved access reaches
    (:func:`blocking`), or a stayer's instruction reads past its table into, stays, until
    none does."""
    acc, starts, code, control, extent, sites = ruled(posts, frames, exempt, reach)
    pc, w, base, addr = acc
    _, _, cell, inside, _ = extent
    has = cell >= 0
    keys = list(zip(pc[has].tolist(), base[has].tolist()))
    run = provenance(posts, frames, control, masks(sites))
    blocks, blk = groups(
        run,
        dict(zip(keys, cell[has].tolist())),
        extent,
        overreach(extent, acc, posts[0].mem),
        _stores(acc, extent),
    )
    spans = [b[:2] for b in blocks]
    mine = np.where(has, blk[cell], -1)
    seen = np.zeros(len(spans), np.int64)
    np.maximum.at(
        seen, mine[has], addr[has] - np.array([hi for _, hi in spans])[mine[has]]
    )
    table = dict(zip(keys, mine[has].tolist()))
    tabs = defaultdict(set)
    for p, c in zip(pc[has].tolist(), mine[has].tolist()):
        tabs[p].add(c)
    kept = staying(spans, run, code)
    span = np.full(0x10000, -1, np.int64)
    for i, (lo, hi) in enumerate(spans):
        span[lo : hi + 1] = i
    past = has & ~inside & np.isin(pc, list(exempt)) & (span[addr] != mine)
    why, port, reads = limits(posts, acc, starts, code)
    hidden = why.get("ROM play runs", why.get("ROM bytes play reads"))
    if hidden is not None:
        kept.update(
            (i, "ROM play reads")
            for i, (lo, hi) in enumerate(spans)
            if hidden[lo : hi + 1].any()
        )
    real = np.zeros(0x10000, bool)
    real[addr[~has | inside | past]] = True
    wide = {}
    while True:
        while True:
            kept.update(
                (i, "read past a table that stays")
                for i in set(span[addr[past]]) - {-1}
            )
            move = [i for i in range(len(spans)) if i not in kept]
            pad = {i: max(blocks[i][2], wide.get(i, 0)) for i in move}
            need = {i: max(int(seen[i]), wide.get(i, 0)) for i in move}
            why["data that stays"] = real & ~np.isin(span, move)
            deltas, cursor, held = place(
                [(*spans[i], pad[i], need[i]) for i in move],
                why,
                posts[0].end,
                ("init's data",),
            )
            delta = dict(zip(move, deltas))
            pad.update((i, need[i]) for i, d in delta.items() if not d)
            s, bad = rewrite(
                run, {k: delta.get(c, 0) // PAGE for k, c in table.items()}
            )
            if not (stuck := blocking(run, bad, table) - set(kept)):
                break
            kept.update(dict.fromkeys(stuck, "address cannot move"))
        taken = {
            a + d: a
            for i, d in delta.items()
            for a in range(spans[i][0], spans[i][1] + 1)
        }
        rom_lo = rom(posts) if port is None else IO.start
        layout = (
            span,
            delta,
            {i: (spans[i][1] + d + 1, pad[i]) for i, d in delta.items()},
            ~mask([(0, LOW), (rom_lo, 0x10000), *((a, a + 1) for a in addr[w == 1])]),
            taken,
        )
        ptrs, copies, left = entries(posts, run, tabs, layout, s)
        more = widen(posts, run, tabs, left, layout[2])
        if all(wide.get(i, 0) >= n for i, n in more.items()):
            break
        for i, n in more.items():
            wide[i] = max(wide.get(i, 0), n)
    s.update(ptrs)
    out = has & ~inside
    inner = set(pc[out & (span[addr] == mine)].tolist()) - set(
        pc[out & (span[addr] != mine)].tolist()
    )
    stays = {
        site["pc"]
        for site in sites
        if tabs.get(site["pc"], set()) & set(kept) or site["pc"] in inner
    }
    return (
        Plan(
            [(*spans[i], delta[i]) for i in move],
            {spans[i]: why_ for i, why_ in sorted(kept.items())},
            sorted(copies),
            s,
            {a: kind(posts[0].mem, starts, a) for a in s},
            bad,
            ~np.logical_or.reduce(list(why.values())),
            cursor,
            left,
            [site for site in sites if site["pc"] not in stays],
            port,
            held,
            [(pad[i], *blocks[i][3]) for i in move],
            reads,
        ),
        stays,
    )


def pairs(p, still=False):
    """(src, dst) int64 arrays of the bytes :class:`Plan` `p` copies: its blocks' (those in
    place when `still`), their pads' (each a repeat of its block's last record), then its
    overrun bytes."""
    moved = [(lo, hi, d) for lo, hi, d in p.moved if d or still]
    pad = [
        np.arange(hi + 1, hi + n + 1) for (_, hi, _), (n, _, _) in zip(p.moved, p.fill)
    ]
    src = [np.arange(lo, hi + 1) for lo, hi, _ in moved]
    src += [f + (a - f) % k for a, (_, f, k) in zip(pad, p.fill)]
    src += [[a for a, _ in p.overrun]]
    dst = [np.arange(lo, hi + 1) + d for lo, hi, d in moved]
    dst += [a + d for a, (_, _, d) in zip(pad, p.moved)]
    dst += [[a + d for a, d in p.overrun]]
    return np.concatenate(src).astype(np.int64), np.concatenate(dst).astype(np.int64)


def runs(src, dst):
    """[(src, dst, n)]: the byte copies `src` to `dst` (ascending `src`) in runs of at most
    255 bytes, the most one of `init_fixup`'s copy loops moves."""
    out = []
    for a, b in zip(src.tolist(), dst.tolist()):
        if out and sum(out[-1][::2]) == a and out[-1][1] - out[-1][0] == b - a:
            if out[-1][2] < PAGE - 1:
                out[-1] = (*out[-1][:2], out[-1][2] + 1)
                continue
        out.append((a, b, 1))
    return out


def _shifted(mem, shift):
    """`mem` with each byte of `shift` ({addr: s}) moved by its s."""
    out = mem.copy()
    at = np.array(list(shift), np.int64)
    out[at] += np.array(list(shift.values()), np.uint8)
    return out


def _corrections(posts, p, image, span, base):
    """((addr, value) adds, (src, dst) copies) making what init of each song of `posts`
    leaves on `image` (a uint8 [65536] memory, `base`, the original's with `p`'s shifts, with
    `p` applied, loaded over `span` (lo, hi)) what it leaves on the original with `p` applied:
    per byte outside free memory, the stack and I/O, the one difference every song leaves;
    then each copy whose source or destination init changes from `base`."""
    init = psid_parts(posts[0].data)[1]
    data = psid(posts, span[0], image[span[0] : span[1]].tobytes(), init)
    want = np.stack([_shifted(q.mem, p.shift) for q in posts])
    got = np.stack([post_init(data, q.song).mem for q in tqdm(posts, desc="fix-up")])
    gap = want - got
    gap[:, mask([(PAGE, LOW), *VOLATILE]) | p.free] = 0
    at = np.flatnonzero(gap.any(axis=0))
    if (bad := at[(gap[:, at] != gap[:1, at]).any(axis=0)]).size:
        raise RuntimeError(f"songs' init leaves ${int(bad[0]):04X} apart by song")
    src, dst = pairs(p)
    redo = ((want[:, src] != base[src]) | (want[:, dst] != base[dst])).any(axis=0)
    return list(zip(at.tolist(), gap[0, at].tolist())), (src[redo], dst[redo])


def room(free, start, n):
    """The least address at or after `start`, else the least, with `n` bytes of `free` (bool
    [65536])."""
    run = np.flatnonzero(
        np.convolve(free.astype(np.int64), np.ones(n, np.int64), "valid") == n
    )
    if not run.size:
        raise RuntimeError(f"no free memory for {n} bytes of init")
    after = run[run >= start]
    return int(after[0] if after.size else run[0])


def _held(p):
    """The bool [65536] mask of the moved blocks of :class:`Plan` `p` and their pads."""
    return np.zeros(0x10000, bool) if p.held is None else p.held


def _one(post, p, copies, span, poison):
    """(memory, [(addr, value)] init's state outside `span` and free memory) of one song's
    `post` relocated by `p`: shifts applied, the old bytes of the blocks `poison`, then the
    (src, dst) `copies` made."""
    mem = _shifted(post.mem, p.shift)
    out = mem.copy()
    out[p.free] = 0
    out[mask([(a, b + 1) for a, b, _ in p.moved]) & ~_held(p)] = poison
    out[copies[1]] = mem[copies[0]]
    vol = mask([(PAGE, LOW), *VOLATILE])
    held = np.union1d(post.stored, np.array(list(p.shift), np.int64))
    state = [
        (int(a), int(out[a]))
        for a in held
        if not (vol[a] or p.free[a] or span[0] <= a < span[1])
    ]
    return out, state


def apply(posts, p, poison=0):
    """(load, image, init) of the songs of `posts` relocated by :class:`Plan` `p`, free
    bytes 0. Of one song: its post-init memory, shifts applied, the old bytes of the blocks
    `poison`, then the blocks and the entries' bytes copied by their Δ; init a stub storing
    init's state outside the image and free memory, init's volatile writes, then the CPU
    port of `p`. Of several: the original image so relocated, its old
    bytes kept; init the original's, then the `init_fixup` of its :func:`_corrections`. Init
    goes in the first free bytes (:func:`room`) past the last pad that no ROM :data:`INIT_PORT`
    maps hides; the image spans the
    original's, the blocks moved (of one song, in place too) and init."""
    post = posts[0]
    src, dst = pairs(p, True)
    top = [hi + d + 1 for _, hi, d in p.moved] if len(posts) == 1 else []
    span = (min([post.load, *dst.tolist()]), max([post.end, p.cursor, *top]))
    tail = p.rom + ([] if p.port is None else [(mm.CPU_PORT, p.port)])
    if len(posts) > 1:
        out = _shifted(np.frombuffer(boot_ram(post.data), np.uint8).copy(), p.shift)
        mem = out.copy()
        out[p.free] = 0
        out[dst] = mem[src]
        adds, (src, dst) = _corrections(posts, p, out, span, mem)
        init = init_fixup(psid_parts(post.data)[1], adds, runs(src, dst), tail)
    else:
        out, state = _one(post, p, (src, dst), span, poison)
        init = store_stub(state + post.io + tail)
    for a, v in p.rom:
        out[a] = v
    seen = mask(mm.rom_ranges(INIT_PORT))
    at = room(p.free & ~_held(p) & ~seen, p.cursor, len(init))
    lo, hi = min(span[0], at), max(span[1], at + len(init))
    out[at : at + len(init)] = np.frombuffer(init, np.uint8)
    return lo, out[lo:hi].tobytes(), at


def psid(posts, load, image, init):
    """The PSID with the header of `posts` loading `image` at `load`, init at `init`: of its
    songs, or play-only of one song's."""
    head = parse_sid_header(posts[0].data)
    if len(posts) == 1:
        speed = int(head.cia_speed(posts[0].song))
        head = head.replace(songs=1, start_song=1, speed=speed)
    head = head.replace(load_address=0, init_address=init)
    return head.to_bytes() + bytes([load & 0xFF, load >> 8]) + image


def protect(data, vacated, frames, song=None):
    """(SID writes, hits) of `frames` play calls of `song` of PSID `data`, each data access
    by play to an address of `vacated` a hit (pc, addr, frame)."""
    hits = []

    def watch(mpu, mem, log):
        def hit(a, *_):
            if len(log.calls) > 1:
                hits.append((mpu.pc, a, len(log.calls) - 1))

        mem.subscribe_to_read(vacated, hit)
        mem.subscribe_to_write(vacated, hit)

    return trace_psid(data, frames, song=song, observe=watch), hits


def check(posts, p, frames, captures=None):
    """Per song of `posts`, each its count of `frames`: (song, hits, first mismatch with the
    original under the overrun rule of `p` (:attr:`Plan.sites`), first mismatch with it as it
    stands, against its song's sidtrace capture of `captures` (a path per song, or None) when it
    has one and the original renders as it, else traced) of relocation `p`, its vacated bytes
    poisoned when init leaves them alone."""
    data = psid(posts, *apply(posts, p, poison=0xFF))
    old = mask([(lo, hi + 1) for lo, hi, _ in p.moved]) & ~_held(p)
    old[[a + d for lo, hi, d in p.moved for a in range(lo, hi + 1)]] = False
    vacated = np.flatnonzero(old).tolist()
    out = []
    for post, n, cap in zip(posts, frames, captures or [None] * len(posts)):
        got, hits = protect(data, vacated, n, None if len(posts) == 1 else post.song)

        def ref(s, post=post, n=n):
            return trace_psid(post.data, n, song=post.song, remap=overrun_remap(s))

        plain = cap and against(cap, data, 1 if len(posts) == 1 else post.song + 1)
        if cap is None or plain and against(cap, post.data, post.song + 1):
            plain = first_divergence(ref(()), got)
        out.append((post.song, hits, first_divergence(ref(p.sites), got), plain))
    return out


def report(p):
    """The JSON-able summary of :class:`Plan` `p`."""
    by = defaultdict(list)
    for a, k in sorted(p.kinds.items()):
        by[k].append(f"${a:04X}+{p.shift[a]}")
    return {
        "moved": [
            f"${lo:04X}-${hi:04X} {'+-'[d < 0]}${abs(d):04X}"
            for lo, hi, d in p.moved
            if d
        ],
        "in place": [f"${lo:04X}-${hi:04X}" for lo, hi, d in p.moved if not d],
        "kept": {f"${lo:04X}-${hi:04X}": why for (lo, hi), why in p.kept.items()},
        "overrun": [f"${a:04X} +${d:04X}" for a, d in p.overrun],
        "rewritten": dict(by),
        "unsolved": [[pc, b, f, d] for pc, b, f, d in p.unsolved],
        "left": {f"${z:02X}": why for z, why in p.left.items()},
        "overruns": {
            f"${s['pc']:04X}": [f"${lo:04X}-${hi:04X}" for lo, hi in s["ok"]]
            for s in p.sites
        },
        "port": None if p.port is None else f"${p.port:02X}",
    }


def length(data, song, args):
    """Play calls of 0-based `song` of PSID `data` by `args`: --frames, its HVSC length or the
    frame by which its track lists wrap."""
    if args.frames:
        return args.frames
    if args.hvsc:
        return song_calls(data, song, args.hvsc)
    addr = read_symbol_list(pathlib.Path(args.wrap[0]).read_text("ascii"))[args.wrap[1]]
    return loop_frame(data, addr, args.limit)


def main(argv=None):
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("psid")
    ap.add_argument("out", help="the play-only PSID")
    end = ap.add_mutually_exclusive_group(required=True)
    end.add_argument("--frames", type=int, help="play calls")
    end.add_argument("--hvsc", help="HVSC root (DOCUMENTS/Songlengths.md5): length")
    end.add_argument(
        "--wrap",
        nargs=2,
        metavar=("LABELS", "LABEL"),
        help="until every byte of LABEL[0..2] in an ACME label dump is reset",
    )
    ap.add_argument("--limit", type=int, default=50_000, help="--wrap's most frames")
    ap.add_argument(
        "--songs", help="1-based songs: all, or e.g. 1,3-5 (default: start)"
    )
    ap.add_argument("--times", type=int, default=1, help="this multiple of the length")
    ap.add_argument("--check", action="store_true", help="protected run and oracle")
    ap.add_argument(
        "--captures",
        help="root of sidtrace captures by HVSC path (with --hvsc): the original as it stands",
    )
    ap.add_argument("--report", help="write the relocation summary (JSON) here")
    args = ap.parse_args(argv)
    data = pathlib.Path(args.psid).read_bytes()
    posts = [
        post_init(data, s)
        for s in (songs(args.songs, data) if args.songs else [default_song(data)])
    ]
    frames = [args.times * length(data, post.song, args) for post in posts]
    p = plan(posts, frames, static_reach(data))
    summary = json.dumps(report(p), indent=1)
    print(summary)
    if args.report:
        pathlib.Path(args.report).write_text(summary, "utf-8")
    if p.unsolved:
        return 1
    pathlib.Path(args.out).write_bytes(psid(posts, *apply(posts, p)))
    if not args.check:
        return 0
    failed = 0
    caps = [
        args.captures
        and args.hvsc
        and hvsc_capture(data, q.song, args.hvsc, args.captures)
        for q in posts
    ]
    for (song, hits, rule, plain), n in zip(check(posts, p, frames, caps), frames):
        for pc, a, f in hits[:10]:
            print(f"song {song}: vacated ${a:04X} accessed at pc ${pc:04X}, frame {f}")
        print(f"song {song}: {len(hits)} vacated accesses in {n} frames")
        print(f"song {song}: oracle under the overrun rule: {rule or 'identical'}")
        name = "the capture" if caps[0] else "it stands"
        print(f"song {song}: oracle as {name}: {plain or 'identical'}")
        failed += bool(hits or rule)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
