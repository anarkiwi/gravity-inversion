"""Trace SID register writes of a PSID or C64 PRG on pysidtracker's C64 and compare them."""

import argparse
import re
import sys

from jennings.coverage.elf import load_elf
from pysidtracker.emu import SidWriteLog, run_to_rts, wire_mpu
from pysidtracker.header import parse_sid_header
from pysidtracker.survey import song_frames, song_list
from tqdm import tqdm
from vagrant_moses.c64 import poweron_ram

MAX_CYCLES = 1_000_000


def parse_labels(text):
    """Parse an ACME label dump into {name: address}."""
    return {
        m[1]: int(m[2], 16)
        for m in re.finditer(r"^\s*(\S+)\s*=\s*\$([0-9a-fA-F]+)", text, re.M)
    }


def machine(load, image, poke=None, sid_model="6581"):
    """(mem, mpu): pysidtracker's C64 (vagrant-moses flat_mpu) over power-on RAM
    holding `image` at `load` and `poke` {addr: byte}."""
    ram = bytearray(poweron_ram())
    ram[load : load + len(image)] = image
    for addr, value in (poke or {}).items():
        ram[addr] = value
    mpu, mem = wire_mpu(ram, sid_model=sid_model)
    return mem, mpu


def default_song(data):
    """0-based start song of a PSID."""
    return parse_sid_header(data).start_song - 1


def psid_parts(data):
    """Return (load, init, play, image) of a PSID file."""
    head = parse_sid_header(data)
    assert head.is_psid, "not a PSID file"
    return (
        head.real_load_address,
        head.init_address or head.real_load_address,
        head.play_address,
        data[head.data_start :],
    )


class _CycleLog(SidWriteLog):
    """SidWriteLog that also keeps each write's cycle from the start of its call."""

    def __init__(self, mpu):
        self.starts = []
        super().__init__(mpu)

    def _write(self, addr, value):
        super()._write(addr, value)
        if self.calls:
            call = self.calls[-1]
            call[-1] = (*call[-1], self.mpu.processorCycles - self.starts[-1])

    def start(self):
        self.starts.append(self.mpu.processorCycles)
        return super().start()


def _log(mpu, detail=False):
    """pysidtracker's per-call SID write log, kept as (register, value), with `detail`
    as (register, value, cycle of the storing instruction from the call's start, its pc).
    """
    log = _CycleLog(mpu)
    keep = (lambda w: (w[0], w[3], w[4], w[2])) if detail else (lambda w: (w[0], w[3]))
    return log, lambda: [[keep(w) for w in call] for call in log.calls]


def trace_psid(  # pylint: disable=too-many-arguments
    data,
    frames,
    watch=None,
    on_watch=None,
    song=None,
    *,
    poke=None,
    sid_model="6581",
    detail=False,
):
    """SID writes of init then `frames` play calls: a list per frame, init first.

    Writes to `watch` addresses are passed to on_watch(frame, addr, value); with
    `detail` each write also carries its cycle from the start of its call and its pc."""
    load, init, play, image = psid_parts(data)
    mem, mpu = machine(load, image, poke, sid_model)
    log, writes = _log(mpu, detail)
    if watch:
        mem.subscribe_to_write(
            watch, lambda addr, value: on_watch(len(log.calls) - 1, addr, value)
        )
    log.start()
    run_to_rts(mpu, mem, init, default_song(data) if song is None else song, MAX_CYCLES)
    for _ in tqdm(range(frames), desc="psid", unit="frame"):
        log.call(mem, play, MAX_CYCLES)
    return writes()


def trace_prg(  # pylint: disable=too-many-arguments
    prg, frames, song=0, *, poke=None, sid_model="6581", watch=None, on_watch=None
):
    """SID writes of a C build: frame n holds the writes after its n-th frame_wait.

    `prg` links c64/driver.c, with symbols in `prg`.elf; the harness sets
    song_number on entry to main and returns from each frame_wait at once.
    `watch` is a function of the symbol table giving addresses whose writes are
    passed to on_watch(frame, addr, value)."""
    _, syms = load_elf(f"{prg}.elf")
    with open(prg, "rb") as f:
        data = f.read()
    mem, mpu = machine(data[0] | data[1] << 8, data[2:], poke, sid_model)
    log, writes = _log(mpu)
    if watch:
        mem.subscribe_to_write(
            watch(syms), lambda addr, value: on_watch(len(log.calls) - 1, addr, value)
        )
    mpu.pc = syms["_start"]
    assert mpu.run_until(max_cycles=MAX_CYCLES, stop_pcs=[syms["main"]]) == "pc"
    mem[syms["song_number"]] = song

    def to_wait():
        reason = mpu.run_until(max_cycles=MAX_CYCLES, stop_pcs=[syms["frame_wait"]])
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
    return writes()


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


def _at(writes, i):
    return writes[i] if i < len(writes) else None


def diff(ref, got):
    """Return (frame, index, ref write, got write) of the first mismatch, or None."""
    for frame, (r, g) in enumerate(zip(ref, got)):
        if r != g:
            i = next(
                (i for i, (a, b) in enumerate(zip(r, g)) if a != b), min(len(r), len(g))
            )
            return frame, i, _at(r, i), _at(g, i)
    return None


def compare(psid, prg, frames, song):
    """(frames, writes, first mismatch or None) of one subtune."""
    ref = trace_psid(psid, frames, song=song)
    got = trace_prg(prg, frames, song)
    return frames, sum(map(len, ref)), diff(ref, got)


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
                addr = parse_labels(f.read())[args.wrap[1]]
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
