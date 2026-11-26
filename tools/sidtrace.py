"""Trace SID register writes of a PSID or C64 PRG on pysidtracker's C64 and compare them."""

import argparse
import json
import sys

import numpy as np
from jennings.acme import read_symbol_list
from jennings.coverage.components import MAX_CYCLES
from jennings.coverage.elf import load_elf
from jennings.devices.mpu6502 import Remap
from jennings.opcodes import OPCODES
from pysidtracker.emu import SidWriteLog, wire_mpu
from pysidtracker.errors import SidError
from pysidtracker.gridcmp import verdict_lead
from pysidtracker.header import parse_sid_header
from pysidtracker.image import SidImage
from pysidtracker.oracle import aligned_match, boot_flat
from pysidtracker.poweron import place_tune, power_on_ram
from pysidtracker.registers import sid_model_for_flags
from pysidtracker.reglog import first_divergence
from pysidtracker.survey import (
    file_oracle,
    oracle_frames,
    read_sidtrace,
    sidtrace_calls,
    song_frames,
    song_list,
)
from tqdm import tqdm

MODEL_FLAGS = {"6581": 0x10, "8580": 0x20}
#: The (zp,X) and (zp),Y opcodes: the overrun rule reads their pointer bytes as they are.
POINTER_OPCODES = [op for op, (_, mode) in OPCODES.items() if mode in ("indx", "indy")]
#: The I/O page: SID writes are logged there, and the overrun rule leaves it to the machine.
IO = range(0xD000, 0xE000)


def machine(load, image, poke=None, sid_model="6581"):
    """(mem, mpu): pysidtracker's C64 over its PSID power-on RAM holding `image` placed
    at `load` with the BASIC LOAD pointers, and `poke` {addr: byte}."""
    ram = power_on_ram()
    place_tune(ram, load, image)
    for addr, value in (poke or {}).items():
        ram[addr] = value
    mpu, mem = wire_mpu(ram, sid_model=sid_model)
    return mem, mpu


def overrun_sources(site):
    """int64 [65536]: per address, what a data access by the instruction of overrun `site`
    (deity-informant's `info["overruns"]`: `pc`, its accesses' address hull `addr`, the
    object's runs `ok`, inclusive, ascending) there reads: -1 the address as it stands (inside
    `ok`, outside `addr`, or I/O), -2 zero, else the address it reads instead. With `last`, a
    read outside `ok` reads the last valid entry: `last` an address, or per run of `ok` a
    (first, stride) pair, the run's last record, which a read past the nearest run below it
    repeats."""
    out = np.full(0x10000, -1, np.int64)
    out[site["addr"][0] : site["addr"][1] + 1] = -2
    for lo, hi in site["ok"]:
        out[lo : hi + 1] = -1
    out[IO.start : IO.stop] = -1
    last = site.get("last")
    at = np.flatnonzero(out == -2)
    if last is None or not at.size:
        return out
    if isinstance(last, int):
        out[at] = last
        return out
    first, step = np.array(last, np.int64).reshape(-1, 2).T
    ends = np.array([hi for _, hi in site["ok"]], np.int64)
    i = np.maximum(np.searchsorted(ends, at) - 1, 0)
    out[at] = first[i] + (at - first[i]) % step[i]
    return out


def overrun_remap(sites):
    """jennings' Remap of the translation's overrun rule: a data access by the instruction
    of a site to an address of its `addr` outside its `ok` reads what :func:`overrun_sources`
    gives (0, or with `last` the last valid entry) and writes nothing; an indirect
    instruction's pointer bytes read as they are. None without sites."""
    at = {s["pc"]: overrun_sources(s) for s in sites}
    at = {pc: v.astype(np.int32) for pc, v in at.items() if (v != -1).any()}
    return Remap(at, POINTER_OPCODES) if at else None


def sid_image(data, sid_model=None):
    """SidImage of a PSID, its header naming `sid_model` alone when given."""
    image = SidImage.from_sid(data)
    if sid_model:
        flags = image.header.flags & ~0x30 | MODEL_FLAGS[sid_model]
        image.header = image.header.replace(flags=flags)
    return image


def boot(data, song=None, *, poke=None, sid_model=None, setup=None, run_init=None):
    """(mpu, mem, play) after init of 0-based `song` (default the start song) on
    pysidtracker's PSID machine (`oracle.boot_flat`), on the header's SID model unless
    `sid_model`; `poke` {addr: byte} is set and `setup(mpu, ram)` called before init, which
    `run_init` (as boot_flat's `init_run`) may replace."""

    def before(mpu, ram):
        for addr, value in (poke or {}).items():
            ram[addr] = value
        if setup:
            setup(mpu, ram)

    song = default_song(data) if song is None else song
    image = sid_image(data, sid_model)
    _, mpu, mem, play = boot_flat(
        image, song, MAX_CYCLES, setup=before, init_run=run_init
    )
    return mpu, mem, play


def boot_ram(data, sid_model=None):
    """The RAM `boot` starts `data`'s init on."""
    ram = []
    boot(
        data,
        sid_model=sid_model,
        setup=lambda _mpu, live: ram.append(bytearray(live)),
        run_init=lambda *_: None,
    )
    return ram[0]


def header_model(data):
    """The SID model pysidtracker plays a PSID on, from its header."""
    return sid_model_for_flags(parse_sid_header(data).flags)


def default_song(data):
    """0-based start song of a PSID."""
    return parse_sid_header(data).start_song - 1


def patched(data, patches):
    """PSID `data` with the image bytes loaded at {address: byte} `patches` replaced."""
    head, out = parse_sid_header(data), bytearray(data)
    for addr, value in patches.items():
        out[head.data_start + addr - head.real_load_address] = value
    return bytes(out)


def psid_parts(data):
    """Return (load, init, play, image) of a PSID file."""
    s = SidImage.from_sid(data)
    return s.load, s.header.init_address or s.load, s.header.play_address, s.image


def trace_psid(  # pylint: disable=too-many-arguments
    data,
    frames,
    watch=None,
    on_watch=None,
    song=None,
    *,
    poke=None,
    sid_model=None,
    detail=False,
    observe=None,
    remap=None,
):
    """SID writes of init then `frames` play calls on `boot`'s machine: a list per frame,
    init first; init and play run under the jennings Remap `remap` (:func:`overrun_remap`).

    Writes to `watch` addresses are passed to on_watch(frame, addr, value); each write is a
    (register, value) pair, with `detail` a pysidtracker SidWriteLog record (register, address
    read last, pc, value, cycle from its call's start). `observe(mpu, mem,
    log)` is called once the machine is wired; len(log.calls) - 1 is the current call.
    """
    box = []

    def setup(mpu, _ram):
        log = SidWriteLog(mpu, detail=detail, cycles=detail)
        box.append(log)
        if watch:
            mpu.memory.subscribe_to_write(
                watch, lambda addr, value: on_watch(len(log.calls) - 1, addr, value)
            )
        if observe:
            observe(mpu, mpu.memory, log)
        log.start()

    def run_init(mpu, _mem, init, a, max_cycles):
        mpu.run_to_rts(init, a, max_cycles, remap)

    mpu, _, play = boot(
        data, song, poke=poke, sid_model=sid_model, setup=setup, run_init=run_init
    )
    for _ in tqdm(range(frames), desc="psid", unit="frame"):
        depth = mpu.sp
        box[0].start()
        mpu.run_to_rts(play, 0, MAX_CYCLES, remap)
        if mpu.sp < depth:
            raise SidError("play call did not return")
    return box[0].calls


def trace_prg(  # pylint: disable=too-many-arguments
    prg,
    frames,
    song=0,
    *,
    poke=None,
    sid_model="6581",
    watch=None,
    on_watch=None,
    observe=None,
    max_cycles=MAX_CYCLES,
):
    """SID writes of a C build: frame n holds the writes after its n-th frame_wait.

    `prg` links c64/driver.c, with symbols in `prg`.elf; the harness sets
    song_number on entry to main and returns from each frame_wait at once.
    `watch` is a function of the symbol table giving addresses whose writes are
    passed to on_watch(frame, addr, value). `observe(mpu, syms, log)` is called once the
    machine is wired, before the C runtime starts; len(log.calls) is 0 until init.
    `max_cycles` bounds each call."""
    _, syms = load_elf(f"{prg}.elf")
    with open(prg, "rb") as f:
        data = f.read()
    mem, mpu = machine(data[0] | data[1] << 8, data[2:], poke, sid_model)
    log = SidWriteLog(mpu, detail=False)
    if watch:
        mem.subscribe_to_write(
            watch(syms), lambda addr, value: on_watch(len(log.calls) - 1, addr, value)
        )
    if observe:
        observe(mpu, syms, log)
    mpu.pc = syms["_start"]
    assert mpu.run_until(max_cycles=MAX_CYCLES, stop_pcs=[syms["main"]]) == "pc"
    mem[syms["song_number"]] = song

    def to_wait():
        reason = mpu.run_until(max_cycles=max_cycles, stop_pcs=[syms["frame_wait"]])
        trap = syms.get("cgen_trap")
        k = "" if trap is None else f" (trap {mem[trap] | mem[trap + 1] << 8})"
        assert reason == "pc", f"call {len(log.calls)}: {reason} at ${mpu.pc:04x}{k}"

    log.start()
    to_wait()
    for _ in tqdm(range(frames), desc="prg", unit="frame"):
        log.start()
        lo, hi = mem[0x100 + (mpu.sp + 1) % 256], mem[0x100 + (mpu.sp + 2) % 256]
        mpu.sp = (mpu.sp + 2) & 0xFF
        mpu.pc = (lo | hi << 8) + 1
        to_wait()
    return log.calls


def song_calls(data, song, hvsc):
    """Play calls of 0-based subtune `song` over its HVSC length, at the tune's cadence;
    `hvsc` is a directory holding DOCUMENTS/Songlengths.md5."""
    return song_frames(data, song + 1, hvsc=hvsc)


def against(path, data, song):
    """None if the render of 1-based `song` of PSID `data` (pysidtracker's `file_oracle`)
    matches the sidtrace capture at `path` over its play calls within a silent lead-in, else
    (frame, register, capture, render) at their first difference."""
    rows = read_sidtrace(path)
    n, ref = oracle_frames(rows, nframes=sidtrace_calls(rows))
    got = np.asarray(file_oracle(data, song, n)[1])
    if aligned_match(ref.tolist(), got.tolist()):
        return None
    v = verdict_lead(ref, got, max_lead=0)
    f, r = v.first, v.first_reg
    return f, r, int(ref[f, r]), int(got[f, r])


def songs(spec, data):
    """0-based subtunes of an HVSC-style 1-based spec ('all', '1,3-5')."""
    return [s - 1 for s in song_list(spec, parse_sid_header(data).songs)]


def loop_frame(psid, posoff, limit):
    """First frame by which every voice has wrapped its track list (posoff:=0)."""
    wrapped = {}

    def on_watch(frame, addr, value):
        if frame > 1 and value == 0:
            wrapped.setdefault(addr, frame)

    trace_psid(psid, limit, list(range(posoff, posoff + 3)), on_watch)
    assert len(wrapped) == 3, "tune did not loop within limit"
    return max(wrapped.values())


def read_sites(path):
    """The overrun sites (deity-informant's `info["overruns"]`) of a translation's JSON at
    `path` (`make`'s `build/gen/overruns.json`); none without a path."""
    if not path:
        return ()
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def effects(data, frames, sites, song=None):
    """{pc: (first frame, accesses, addresses)} of the overrun `sites` that take effect when
    `data` plays with no overrun rule: an access by a site's instruction to an address
    :func:`overrun_sources` redirects, an indirect instruction's pointer bytes aside;
    `addresses` a [65536] bool mask of those it took. Init is frame 0, play call n frame n.
    """
    image = boot_ram(data)
    hit = {}
    for s in sites:
        t = overrun_sources(s) != -1
        if image[s["pc"]] in POINTER_OPCODES:
            zp = image[s["pc"] + 1]
            t[[zp, (zp + 1) & 0xFF]] = False
        if t.any():
            hit[s["pc"]] = t
    got = {}

    def note(frame, log):
        k = np.asarray(log, np.int64)
        pc, addr = k >> 17, k & 0xFFFF
        for p in np.intersect1d(np.unique(pc), np.fromiter(hit, np.int64)):
            at = addr[pc == p]
            at = at[hit[int(p)][at]]
            if at.size:
                first, count, mask = got.get(
                    int(p), (frame, 0, np.zeros(0x10000, bool))
                )
                mask[at] = True
                got[int(p)] = (first, count + int(at.size), mask)

    def run_init(mpu, _mem, init, a, max_cycles):
        note(0, mpu.run_to_rts_accesses(init, a, max_cycles)[1])

    mpu, _, play = boot(data, song, run_init=run_init)
    for frame in tqdm(range(1, frames + 1), desc="overrun effects", unit="frame"):
        note(frame, mpu.run_to_rts_accesses(play, 0, MAX_CYCLES)[1])
    return got


def read_song_data(path):
    """[65536] bool mask of a lift's song data (`info["song_data"]`: the bytes play reads,
    minus those it may write, minus its instructions', as inclusive runs), or None when the
    file holds none or there is no path."""
    if not path:
        return None
    with open(path, encoding="utf-8") as f:
        runs = json.load(f)
    if runs is None:
        return None
    out = np.zeros(0x10000, bool)
    for lo, hi in runs:
        out[lo : hi + 1] = True
    return out


def hidden(sites, found, song_data):
    """The sites of `found` (:func:`effects`) whose rule replaces a read of song data, each
    ``(site, first frame, accesses, song bytes)``: an extent ending inside the data block.
    A rule that only takes the place of code, state play writes or I/O is the tune never
    stopping. Without `song_data` every site of `found` is one."""
    by_pc = {s["pc"]: s for s in sites}
    out = []
    for pc, (first, count, mask) in sorted(found.items()):
        lost = mask if song_data is None else mask & song_data
        if lost.any():
            out.append((by_pc[pc], first, count, np.flatnonzero(lost)))
    return out


def reference(data, frames, sites=(), **kw):
    """:func:`trace_psid` of `data` under the overrun rule of `sites` (:func:`overrun_remap`):
    what a translation lifted under them is judged against."""
    return trace_psid(data, frames, remap=overrun_remap(sites), **kw)


def compare(psid, prg, frames, song, sites=()):
    """(frames, writes, first mismatch or None) of one subtune, the C build on the PSID's
    SID model; the reference under the overrun rule of `sites` (:func:`reference`).
    """
    ref = reference(psid, frames, sites, song=song)
    got = trace_prg(prg, frames, song, sid_model=header_model(psid))
    return frames, sum(map(len, ref)), first_divergence(ref, got)


def report_hidden(psid, frames, sites, song, song_data):
    """Print each site of `sites` whose rule replaces song data on the original's `song`
    (:func:`hidden`) as an `OVERRUN` line; their count."""
    found = hidden(sites, effects(psid, frames, sites, song), song_data)
    for site, first, count, lost in found:
        ok = ",".join(f"${lo:04X}-${hi:04X}" for lo, hi in site["ok"])
        print(
            f"song {song} OVERRUN ${site['pc']:04X} (object {ok}) replaces song data "
            f"${lost[0]:04X}-${lost[-1]:04X} on the original from frame {first}, "
            f"{count} accesses"
        )
    return len(found)


def main(argv=None):
    """Compare a C build's SID writes against a reference PSID, per subtune."""
    ap = argparse.ArgumentParser(description=main.__doc__)
    ap.add_argument("psid")
    ap.add_argument("prg", help="C build, with symbols in PRG.elf")
    ap.add_argument(
        "--songs", help="1-based subtunes: all, or e.g. 1,3-5 (default: start song)"
    )
    end = ap.add_mutually_exclusive_group(required=True)
    end.add_argument("--frames", type=int, help="compare this many frames")
    end.add_argument(
        "--hvsc", help="HVSC root (DOCUMENTS/Songlengths.md5): song lengths"
    )
    end.add_argument(
        "--wrap",
        nargs=2,
        metavar=("LABELS", "LABEL"),
        help="compare until every byte of LABEL[0..2] in an ACME label dump is reset",
    )
    ap.add_argument("--limit", type=int, default=50_000)
    ap.add_argument(
        "--times", type=int, default=1, help="compare this multiple of the length"
    )
    ap.add_argument(
        "--overruns",
        help="the translation's overrun sites (JSON): the reference reads past them as the "
        "translation does",
    )
    ap.add_argument(
        "--song-data",
        help="the lift's song data (JSON runs, `info['song_data']`): a site of --overruns whose "
        "rule replaces a read of it on the original fails",
    )
    args = ap.parse_args(argv)
    with open(args.psid, "rb") as f:
        psid = f.read()
    sites = read_sites(args.overruns)
    song_data = read_song_data(args.song_data)
    picked = songs(args.songs, psid) if args.songs else [default_song(psid)]
    failed = 0
    for song in picked:
        frames = args.frames
        if args.hvsc:
            frames = song_calls(psid, song, args.hvsc)
        elif args.wrap:
            with open(args.wrap[0], encoding="ascii") as f:
                addr = read_symbol_list(f.read())[args.wrap[1]]
            frames = loop_frame(psid, addr, args.limit)
        frames, writes, bad = compare(psid, args.prg, args.times * frames, song, sites)
        if bad:
            failed += 1
            print(
                f"song {song} MISMATCH frame {bad[0]} write {bad[1]}: ref {bad[2]} got {bad[3]}"
            )
        else:
            print(f"song {song} OK: {frames} frames, {writes} SID writes identical")
        if sites:
            failed += report_hidden(psid, frames, sites, song, song_data)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
