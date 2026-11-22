"""Trace SID register writes of a PSID or C64 PRG on pysidtracker's C64 and compare them."""

import argparse
import sys

from jennings.acme import read_symbol_list
from jennings.coverage.components import MAX_CYCLES
from jennings.coverage.elf import load_elf
from pysidtracker.emu import SidWriteLog, wire_mpu
from pysidtracker.header import parse_sid_header
from pysidtracker.image import SidImage
from pysidtracker.oracle import boot_flat
from pysidtracker.poweron import place_tune, power_on_ram
from pysidtracker.registers import sid_model_for_flags
from pysidtracker.reglog import first_divergence
from pysidtracker.survey import song_frames, song_list
from tqdm import tqdm

MODEL_FLAGS = {"6581": 0x10, "8580": 0x20}


def machine(load, image, poke=None, sid_model="6581"):
    """(mem, mpu): pysidtracker's C64 over its PSID power-on RAM holding `image` placed
    at `load` with the BASIC LOAD pointers, and `poke` {addr: byte}."""
    ram = power_on_ram()
    place_tune(ram, load, image)
    for addr, value in (poke or {}).items():
        ram[addr] = value
    mpu, mem = wire_mpu(ram, sid_model=sid_model)
    return mem, mpu


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
):
    """SID writes of init then `frames` play calls on `boot`'s machine: a list per frame,
    init first.

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

    _, mem, play = boot(data, song, poke=poke, sid_model=sid_model, setup=setup)
    for _ in tqdm(range(frames), desc="psid", unit="frame"):
        box[0].call(mem, play, MAX_CYCLES)
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


def compare(psid, prg, frames, song):
    """(frames, writes, first mismatch or None) of one subtune, the C build on the PSID's
    SID model."""
    ref = trace_psid(psid, frames, song=song)
    got = trace_prg(prg, frames, song, sid_model=header_model(psid))
    return frames, sum(map(len, ref)), first_divergence(ref, got)


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
    args = ap.parse_args(argv)
    with open(args.psid, "rb") as f:
        psid = f.read()
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
        frames, writes, bad = compare(psid, args.prg, args.times * frames, song)
        if bad:
            failed += 1
            print(
                f"song {song} MISMATCH frame {bad[0]} write {bad[1]}: ref {bad[2]} got {bad[3]}"
            )
        else:
            print(f"song {song} OK: {frames} frames, {writes} SID writes identical")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
