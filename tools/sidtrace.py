"""Trace SID register writes of a PSID or C64 PRG under jennings and compare them."""

import argparse
import hashlib
import re
import sys

from jennings.devices.mpu6502 import MPU
from jennings.memory import NativeMemory
from jennings.coverage.elf import load_elf
from tqdm import tqdm

SID = range(0xD400, 0xD41D)
MAX_CYCLES = 1_000_000
KERNAL_JUMPS = range(0xFF81, 0xFFF6, 3)
RTS = 0x60
PAL_CLOCK = 985248
CIA_TIMER = range(0xDC04, 0xDC06)
CIA_DEFAULT = 0x4025


def parse_labels(text):
    """Parse an ACME label dump into {name: address}."""
    return {
        m[1]: int(m[2], 16)
        for m in re.finditer(r"^\s*(\S+)\s*=\s*\$([0-9a-fA-F]+)", text, re.M)
    }


def sid_ranges(data):
    """SID register ranges a PSID writes: $d400 plus any v3/v4 extra SIDs."""
    extra = [data[o] for o in (0x7A, 0x7B) if len(data) > 0x7B and data[5] >= 3]
    return [
        range(a, a + 0x1D) for a in [0xD400] + [0xD000 | b << 4 for b in extra if b]
    ]


def ram_mpu(load, image, poke=None):
    """MPU over RAM holding `image` at `load`, `poke` {addr: byte} and a PAL raster."""
    mem = NativeMemory(bytes(0x10000))
    mem.write(load, bytes(image))
    for addr, value in (poke or {}).items():
        mem.write(addr, bytes([value]))
    mem.raster_model(False)
    mem.io = True
    return mem, MPU(memory=mem)


def psid_parts(data):
    """Return (load, init, play, image) of a PSID file."""
    assert data[:4] == b"PSID", "not a PSID file"
    word = lambda o: data[o] << 8 | data[o + 1]  # pylint: disable=C3001
    off = word(6)
    load = word(8) or data[off] | data[off + 1] << 8
    image = data[off + (0 if word(8) else 2) :]
    return load, word(0x0A), word(0x0C), image


def trace_psid(data, frames, watch=None, on_watch=None, song=None, *, poke=None):
    """SID writes of init then `frames` play calls: a list per frame, init first.

    Writes to `watch` addresses are passed to on_watch(frame, addr, value)."""
    load, init, play, image = psid_parts(data)
    mem, mpu = ram_mpu(load, image, poke)
    sids = sid_ranges(data)
    out = [[]]
    frame = [0]

    def on_write(addr, value):
        if any(addr in r for r in sids):
            out[-1].append((addr, value))
        else:
            on_watch(frame[0], addr, value)

    mem.observe_data_access(None, on_write, writes=sids + ([watch] if watch else []))
    mpu.run_to_rts(init, data[0x11] - 1 if song is None else song, MAX_CYCLES)
    for frame[0] in tqdm(range(1, frames + 1), desc="psid", unit="frame"):
        out.append([])
        mpu.run_to_rts(play, 0, MAX_CYCLES)
    return out


def trace_prg(prg, frames, sids=(SID,), song=0, *, poke=None):
    """SID writes of a C build: frame n holds the writes after its n-th frame_wait.

    `prg` links c64/driver.c, with symbols in `prg`.elf; the harness sets
    song_number on entry to main and returns from each frame_wait at once."""
    _, syms = load_elf(f"{prg}.elf")
    with open(prg, "rb") as f:
        data = f.read()
    mem, mpu = ram_mpu(data[0] | data[1] << 8, data[2:], poke)
    for addr in KERNAL_JUMPS:
        mem.write(addr, bytes([RTS]))
    out = [[] for _ in range(frames + 1)]
    frame = [0]

    def on_write(addr, value):
        out[frame[0]].append((addr, value))

    mem.observe_data_access(None, on_write, writes=list(sids))
    mpu.pc = syms["_start"]
    assert mpu.run_until(max_cycles=MAX_CYCLES, stop_pcs=[syms["main"]]) == "pc"
    mem[syms["song_number"]] = song

    def to_wait():
        reason = mpu.run_until(max_cycles=MAX_CYCLES, stop_pcs=[syms["frame_wait"]])
        assert reason == "pc", f"frame {frame[0]}: {reason} at ${mpu.pc:04x}"

    to_wait()
    for frame[0] in tqdm(range(1, frames + 1), desc="prg", unit="frame"):
        lo, hi = mem[0x100 + (mpu.sp + 1) % 256], mem[0x100 + (mpu.sp + 2) % 256]
        mpu.sp = (mpu.sp + 2) & 0xFF
        mpu.pc = (lo | hi << 8) + 1
        to_wait()
    return out


def play_rate(data, song=None):
    """Play calls per second of a subtune: 50, or with its PSID speed bit set the PAL
    clock over the CIA 1 timer A period (default $4025 unless init sets it)."""
    song = data[0x11] - 1 if song is None else song
    if not int.from_bytes(data[0x12:0x16], "big") >> min(song, 31) & 1:
        return 50
    latch = [CIA_DEFAULT & 0xFF, CIA_DEFAULT >> 8]

    def on_watch(_, addr, value):
        latch[addr - CIA_TIMER.start] = value

    trace_psid(data, 0, CIA_TIMER, on_watch, song)
    return PAL_CLOCK / ((latch[0] | latch[1] << 8) + 1)


def songlengths(path, rate=50):
    """{md5: [play calls per subtune at `rate` per second]} from an HVSC Songlengths.md5."""
    out = {}
    with open(path, encoding="latin-1") as f:
        for line in f:
            if "=" in line and not line.startswith(";"):
                md5, times = line.strip().split("=", 1)
                out[md5] = [
                    round((int(m) * 60 + float(sec)) * rate)
                    for m, sec in (t.split("(")[0].split(":") for t in times.split())
                ]
    return out


def songlength(path, data, song):
    """Play calls of subtune `song` (0-based) from an HVSC Songlengths.md5."""
    rate = play_rate(data, song)
    return songlengths(path, rate)[hashlib.md5(data).hexdigest()][song]


def loop_frame(psid, posoff, limit):
    """First frame by which every voice has wrapped its track list (posoff:=0)."""
    wrapped = {}

    def on_watch(frame, addr, value):
        if frame > 1 and value == 0:
            wrapped.setdefault(addr, frame)

    trace_psid(psid, limit, range(posoff, posoff + 3), on_watch)
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
    got = trace_prg(prg, frames, sid_ranges(psid), song)
    return frames, sum(map(len, ref)), diff(ref, got)


def main(argv=None):
    """Compare a C build's SID writes against a reference PSID, per subtune."""
    ap = argparse.ArgumentParser(description=main.__doc__)
    ap.add_argument("psid")
    ap.add_argument("prg", help="C build, with symbols in PRG.elf")
    ap.add_argument(
        "--songs", help="0-based subtunes, e.g. 0,3-5 (default: start song)"
    )
    end = ap.add_mutually_exclusive_group(required=True)
    end.add_argument("--frames", type=int, help="compare this many frames")
    end.add_argument(
        "--songlengths", help="HVSC Songlengths.md5: each subtune's length"
    )
    end.add_argument(
        "--wrap",
        nargs=2,
        metavar=("LABELS", "LABEL"),
        help="compare until every byte of LABEL[0..2] in an ACME label dump is reset",
    )
    ap.add_argument("--limit", type=int, default=50_000)
    args = ap.parse_args(argv)
    with open(args.psid, "rb") as f:
        psid = f.read()
    songs = parse_songs(args.songs) if args.songs else [psid[0x11] - 1]
    failed = 0
    for song in songs:
        frames = args.frames
        if args.songlengths:
            frames = songlength(args.songlengths, psid, song)
        elif args.wrap:
            with open(args.wrap[0], encoding="ascii") as f:
                addr = parse_labels(f.read())[args.wrap[1]]
            frames = loop_frame(psid, addr, args.limit)
        frames, writes, bad = compare(psid, args.prg, frames, song)
        if bad:
            failed += 1
            print(
                f"song {song} MISMATCH frame {bad[0]} write {bad[1]}: ref {bad[2]} got {bad[3]}"
            )
        else:
            print(f"song {song} OK: {frames} frames, {writes} SID writes identical")
    return 1 if failed else 0


def parse_songs(text):
    """'0,3-5' -> [0, 3, 4, 5]."""
    out = []
    for part in text.split(","):
        lo, _, hi = part.partition("-")
        out.extend(range(int(lo), int(hi or lo) + 1))
    return out


if __name__ == "__main__":
    sys.exit(main())
