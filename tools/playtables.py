"""Play's tables after init and the zero rule (docs/relocate.md): its data accesses by
instruction and base on sidtrace's C64, the tables its indexed bases start, the instructions
reading past them and the rule under which those reads read the last entry."""

import functools
from collections import defaultdict

import numpy as np

from deity_informant import cgen
from deity_informant.postinit import X
from jennings.coverage.cfg import control_operands, instruction_bytes
from jennings.devices.mpu6502 import Remap
from jennings.opcodes import OPCODES
from pysidtracker.emu import wire_mpu
from pysidtracker.header import parse_sid_header
from tqdm import tqdm
from vagrant_moses.cgen import ENTRIES, SENSITIVE, VOLATILE

from footprint import mask
from sidtrace import IO, MAX_CYCLES, boot_ram, header_model, overrun_sources, psid_parts
from transliterate import c64_init

#: A 6502 page.
PAGE = 0x100
#: Zero page and the stack: never play's data.
LOW = 0x200
#: Addressing mode by opcode.
MODES = [OPCODES[op][1] for op in range(256)]


def trace_play(post, frames, rule=None):
    """((pc, write, base, addr) int64 arrays of play's distinct data accesses outside zero
    page, the stack and I/O, `base` their jennings access base (an absolute indexed access's
    operand, an indirect indexed one's pointer, else the address); the bool mask of the
    instructions it ran) over `frames` calls from `post` on sidtrace's C64, an instruction's
    accesses that `rule` ({pc: int64 [65536]}, :func:`masks`) redirects reading 0 or the
    address it names and writing nothing."""
    mpu, _ = wire_mpu(bytearray(post.mem.tobytes()))
    remap = Remap({pc: z.astype(np.int32) for pc, z in rule.items()}) if rule else None
    ran, seen = np.zeros(0x10000, bool), []
    for _ in tqdm(range(frames), desc="play", unit="frame"):
        trace, log, bases = mpu.run_to_rts_accesses(
            post.play, 0, MAX_CYCLES, remap, True
        )
        ran[np.asarray(trace.pc, np.int64)] = True
        k, b = np.asarray(log, np.int64), np.asarray(bases, np.int64)
        seen.append(
            np.unique(k >> 17 << 33 | (k >> 16 & 1) << 32 | b << 16 | k & 0xFFFF)
        )
    acc = np.unique(np.concatenate(seen))
    acc = acc[~mask([(0, LOW), (IO.start, IO.stop)])[acc & 0xFFFF]]
    return (acc >> 33, acc >> 32 & 1, acc >> 16 & 0xFFFF, acc & 0xFFFF), ran


def trace_init(post):
    """(bool [65536] mask of the bytes init's instructions span, of the addresses it reads or
    writes outside zero page, the stack and I/O) of `post`'s song."""
    ram = boot_ram(post.data)
    mpu, _ = wire_mpu(bytearray(ram), sid_model=header_model(post.data))
    trace, log = mpu.run_to_rts_accesses(
        psid_parts(post.data)[1], post.song, MAX_CYCLES
    )
    starts, touched = np.zeros(0x10000, bool), np.zeros(0x10000, bool)
    starts[np.asarray(trace.pc, np.int64)] = True
    touched[np.asarray(log, np.int64) & 0xFFFF] = True
    touched &= ~mask([(0, LOW), (IO.start, IO.stop)])
    return instruction_bytes(ram, starts), touched


def observe(posts, frames, rule=None):
    """((pc, write, base, addr) of :func:`trace_play` under `rule` over every song of
    `posts`, each its count of `frames`, the bool mask of the instructions run, of their
    bytes, of the operand bytes of their branches, jumps and calls)."""
    acc, starts = [], np.zeros(0x10000, bool)
    code, control = starts.copy(), starts.copy()
    for post, n in zip(posts, frames):
        (pc, w, b, addr), ran = trace_play(post, n, rule)
        acc.append(pc << 33 | w << 32 | b << 16 | addr)
        starts |= ran
        code |= instruction_bytes(post.mem, ran)
        control |= control_operands(post.mem, ran)
    acc = np.unique(np.concatenate(acc))
    return (
        (acc >> 33, acc >> 32 & 1, acc >> 16 & 0xFFFF, acc & 0xFFFF),
        starts,
        code,
        control,
    )


def _starts(pc, base, addr):
    """(ascending table starts, each table's stride) of the accesses at `addr` from `base` by
    instructions at `pc`: each base but one indexing the table before it, being below every
    address that table's accesses reached (`LDA tbl+4,Y` beside `LDA tbl,Y`) or less than its
    stride past the table's start (a field of the same records: `LDA ins_ctrl,X` beside `LDA
    ins_pwl,X`, X a multiple of 8), the stride the greatest common divisor of the distances
    between the addresses each instruction from one of its bases reached, over the instructions
    whose addresses are not consecutive (a loop over every byte shows no records)."""
    bs, inv = np.unique(base, return_inverse=True)
    low = np.full(len(bs), 0x10000, np.int64)
    np.minimum.at(low, inv, addr)
    ins, at = np.unique(inv << 16 | pc, return_inverse=True)
    first = np.full(len(ins), 0x10000, np.int64)
    np.minimum.at(first, at, addr)
    gap = np.zeros(len(ins), np.int64)
    np.gcd.at(gap, at, addr - first[at])
    step = np.zeros(len(bs), np.int64)
    np.gcd.at(step, ins >> 16, np.where(gap > 1, gap, 0))
    out, strides, floor = [], [], -1
    for b, lo, g in zip(bs.tolist(), low.tolist(), step.tolist()):
        if out and (b < floor or b - out[-1] < strides[-1]):
            floor, strides[-1] = min(floor, lo), int(np.gcd(strides[-1], g))
            continue
        out.append(b)
        strides.append(g)
        floor = lo
    return np.array(out, np.int64), np.array(strides, np.int64)


def _stops(starts, code):
    """Per table of ascending `starts` the next start or `code` byte."""
    nxt = np.flatnonzero(code)
    return np.minimum(
        np.r_[starts[1:], 0x10000], np.r_[nxt, 0x10000][np.searchsorted(nxt, starts)]
    )


def _indexed(acc, code, mode, reach=None):
    """(starts, ends, strides, the accesses based in one) of the tables of :func:`tables` the
    indexed accesses of `acc` (modes `mode`) start, each table's end the last address its
    accesses reached, or the last before its stop their instructions' static `reach` ({pc:
    last address}) holds."""
    pc, _, base, addr = acc
    data = ~code[base]
    ind = np.isin(mode, ("indx", "indy"))
    idx = data & (ind | np.isin(mode, ("absx", "absy")))
    least = np.full(0x10000, 0x10000, np.int64)
    np.minimum.at(least, pc[ind], base[ind])
    first = idx & (~ind | (base == least[pc]))
    starts, strides = _starts(pc[first], base[first], addr[first])
    stop = _stops(starts, code)
    at = np.searchsorted(starts, base, "right") - 1
    ok = idx & (at >= 0)
    ok[ok] &= base[ok] < stop[at[ok]]
    held = ok.copy()
    held[ok] = addr[ok] < stop[at[ok]]
    ends = starts.copy()
    np.maximum.at(ends, at[held], addr[held])
    if reach:
        far = np.array([reach.get(p, -1) for p in pc[ok].tolist()], np.int64)
        np.maximum.at(ends, at[ok], np.minimum(far, stop[at[ok]] - 1))
    return starts, ends, strides, ok


def tables(acc, code, mem, reach=None):
    """(starts, ends, table per access or -1, in its table per access, last records) of play's
    tables: from each start (:func:`_starts` of the absolute indexed accesses' bases and each
    indirect instruction's least pointer) to the next start or code byte, ending at the last
    address an indexed access based in it reached there, or its instruction's static `reach`
    (:func:`static_reach`) does; an unindexed access belongs to the table holding its address,
    else is a table of its own byte. An access past its table's end overruns it. An indexed
    access from a base on a code byte (`LDA tbl-$40,Y`) is, like an unindexed one, in the table
    holding its address. A table's last record ((first byte, length) per table) is its last
    whole stride when its stride is a record's (more than one byte, within the table), else its
    last byte."""
    pc, _, base, addr = acc
    mode = np.array(MODES, object)[mem[pc]]
    starts, ends, strides, ok = _indexed(acc, code, mode, reach)
    lone = ~code[addr] & (code[base] | ~np.isin(mode, ("indx", "indy", "absx", "absy")))
    base = np.where(lone, addr, base)
    at = np.searchsorted(starts, addr, "right") - 1
    covered = lone & (at >= 0)
    covered[covered] = addr[covered] <= ends[at[covered]]
    own = np.unique(addr[lone & ~covered])
    order = np.argsort(np.r_[starts, own], kind="stable")
    starts, ends = np.r_[starts, own][order], np.r_[ends, own][order]
    step = np.r_[strides, np.ones(len(own), np.int64)][order]
    cell = np.where(ok | lone, np.searchsorted(starts, base, "right") - 1, -1)
    inside = cell >= 0
    inside[inside] = addr[inside] <= ends[cell[inside]]
    return starts, ends, cell, inside, _records(starts, ends, step)


def _records(starts, ends, step):
    """(first byte, length) per table of its last record: its last whole `step` when that is
    more than one byte and within the table, else its last byte."""
    n = ends - starts + 1
    step = np.where((step > 1) & (step <= n), step, 1)
    return np.stack([starts + (n // step - 1) * step, step], axis=1)


def overruns(acc, extent, exempt=()):
    """[{pc, addr, ok, last}] per instruction (but `exempt`) with an access past its table's
    end (`extent`, :func:`tables`): its accesses' hull, its tables and their last records, as
    sidtrace's `overrun_remap` takes them."""
    pc, _, _, addr = acc
    starts, ends, cell, inside, last = extent
    out = []
    for p in np.unique(pc[(cell >= 0) & ~inside]).tolist():
        if p in exempt:
            continue
        mine = pc == p
        cells = np.unique(cell[mine & (cell >= 0)])
        out.append(
            {
                "pc": p,
                "addr": [int(addr[mine].min()), int(addr[mine].max())],
                "ok": [[int(starts[c]), int(ends[c])] for c in cells],
                "last": last[cells].tolist(),
            }
        )
    return out


def masks(sites):
    """{pc: int64 [65536]} of what each address a site of `sites` (:func:`overruns`) reads
    (sidtrace's `overrun_sources`)."""
    return {site["pc"]: overrun_sources(site) for site in sites}


def overreach(extent, acc, mem):
    """Per table of `extent` (:func:`tables`) the bytes past its end the accesses `acc` based
    in it can reach: an absolute indexed one's operand plus 255 (any index), an indirect
    one's address as reached (`mem` the opcodes)."""
    pc, _, base, addr = acc
    _, ends, cell, *_ = extent
    has = cell >= 0
    far = np.where(
        np.isin(np.array(MODES, object)[mem[pc]], ("absx", "absy")),
        np.minimum(base + 0xFF, 0xFFFF),
        addr,
    )
    out = np.zeros(len(ends), np.int64)
    np.maximum.at(out, cell[has], np.maximum(far, addr)[has] - ends[cell[has]])
    return out


def components(u, v, n):
    """The least node of the component of each of `n` nodes joined by edges `u`-`v`."""
    lab = np.arange(n)
    while True:
        m = np.minimum(lab[u], lab[v])
        new = lab.copy()
        np.minimum.at(new, u, m)
        np.minimum.at(new, v, m)
        new = new[new]
        if (new == lab).all():
            return lab
        lab = new


def groups(run, table, extent, reach, joined=((), ())):
    """([(lo, hi, pad, last record)] ascending, block index per table) of the blocks the tables of
    `extent` (:func:`tables`, `reach` their :func:`overreach`) move as: tables whose accesses'
    address high bytes sum a common byte (`run.events`, relocate's `Sources`, `table` {(pc,
    base): table index}) move by one Δ, so join, as do the table pairs `joined` ((u, v)
    index arrays: a store past a table's end into another, which must land there); overlapping
    hulls join; a block's pad is the
    bytes past its end its tables' pads reach, its last record its last table's
    (:func:`tables`)."""
    starts, ends, *_ = extent
    n = len(starts)
    use = np.array(
        sorted(
            {
                (a, table[(p, b)])
                for p, f, b in run.events
                if f not in (None, X) and table.get((p, b), -1) >= 0
                for a, _ in f
            }
        ),
        np.int64,
    ).reshape(-1, 2)
    same = use[1:, 0] == use[:-1, 0]
    lab = components(
        np.r_[use[:-1, 1][same], np.asarray(joined[0], np.int64)],
        np.r_[use[1:, 1][same], np.asarray(joined[1], np.int64)],
        n,
    )
    lo = np.full(n, 0x10000, np.int64)
    hi = np.full(n, -1, np.int64)
    np.minimum.at(lo, lab, starts)
    np.maximum.at(hi, lab, ends)
    far = np.full(n, -1, np.int64)
    np.maximum.at(far, lab, ends + reach)
    root = np.unique(lab)
    order = root[np.argsort(lo[root], kind="stable")]
    out, blk = [], np.zeros(n, np.int64)
    for r in order.tolist():
        if out and lo[r] <= out[-1][1]:
            out[-1] = [out[-1][0], max(out[-1][1], hi[r]), max(out[-1][2], far[r])]
        else:
            out.append([int(lo[r]), int(hi[r]), int(far[r])])
        blk[lab == r] = len(out) - 1
    tail = {
        int(blk[t]): tuple(extent[4][t].tolist()) for t in np.argsort(ends).tolist()
    }
    return [(a, b, max(f - b, 0), tail[i]) for i, (a, b, f) in enumerate(out)], blk


def static_reach(data):
    """{pc: last address} of each access of PSID `data` deity-informant's lift bounds from
    its code and read-only data (`Program.addr_sets`)."""
    load, init, play, image = psid_parts(data)
    prog, _ = cgen.lift(
        boot_ram(data),
        list(zip((init, play), ENTRIES)),
        VOLATILE,
        sensitive=SENSITIVE,
        loaded=(load, load + len(image)),
        init=0,
        songs=range(parse_sid_header(data).songs),
        machine=functools.partial(c64_init, sid_model=header_model(data)),
        overrun="zero",
    )
    out = defaultdict(lambda: -1)
    for (pc, _), runs in (prog.addr_sets or {}).items():
        out[pc] = max(out[pc], max(hi for _, hi in runs) - 1)
    return dict(out)


def ruled(posts, frames, exempt, reach=None):
    """(accesses, instructions run, their bytes, control cells, :func:`tables` (with the
    static `reach`), overrun sites) of play under the overrun rule of its own tables
    (`exempt` left out): traced again under each rule until the sites it finds are those it
    ran under."""
    sites, seen = [], []
    while True:
        acc, starts, code, control = observe(posts, frames, masks(sites))
        extent = tables(acc, code, posts[0].mem, reach)
        found = overruns(acc, extent, exempt)
        if found == sites or found in seen:
            return acc, starts, code, control, extent, sites
        seen.append(sites)
        sites = found
