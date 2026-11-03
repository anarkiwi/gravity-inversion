"""Trace SID register writes of a PSID or C64 PRG under jennings and compare them."""

import argparse
import sys

from jennings.devices.mpu6502 import MPU
from jennings.memory import NativeMemory
from tqdm import tqdm

from gen_data import parse_labels

SID = range(0xD400, 0xD41D)
PAL_FRAME = 63 * 312
PLAY_LINE = 0xFF
MAX_CYCLES = 1_000_000
KERNAL_JUMPS = range(0xFF81, 0xFFF6, 3)
RTS = 0x60


def _mpu(load, image):
    mem = NativeMemory(bytes(0x10000))
    mem.write(load, bytes(image))
    return mem, MPU(memory=mem)


def psid_parts(data):
    """Return (load, init, play, image) of a PSID file."""
    assert data[:4] == b"PSID", "not a PSID file"
    word = lambda o: data[o] << 8 | data[o + 1]  # pylint: disable=C3001
    off = word(6)
    load = word(8) or data[off] | data[off + 1] << 8
    image = data[off + (0 if word(8) else 2) :]
    return load, word(0x0A), word(0x0C), image


def trace_psid(data, frames, watch=None, on_watch=None):
    """SID writes of init then `frames` play calls: a list per frame, init first.

    Writes to `watch` addresses are passed to on_watch(frame, addr, value)."""
    load, init, play, image = psid_parts(data)
    mem, mpu = _mpu(load, image)
    out = [[]]
    frame = [0]

    def on_write(addr, value):
        if addr in SID:
            out[-1].append((addr, value))
        else:
            on_watch(frame[0], addr, value)

    mem.observe_data_access(None, on_write, writes=[SID] + ([watch] if watch else []))
    mpu.run_to_rts(init, 0, MAX_CYCLES)
    for frame[0] in tqdm(range(1, frames + 1), desc="psid", unit="frame"):
        out.append([])
        mpu.run_to_rts(play, 0, MAX_CYCLES)
    return out


def prg_entry(prg):
    """Return (load, sys address, image) of a PRG with a BASIC SYS stub."""
    load = prg[0] | prg[1] << 8
    line = prg[6:]
    sys_at = line.index(0x9E) + 1
    digits = bytes(line[sys_at : line.index(0, sys_at)]).strip()
    return load, int(digits), prg[2:]


def trace_prg(prg, frames):
    """SID writes of a PRG playing once per PAL frame at raster line PLAY_LINE.

    Writes before the first play are frame 0, matching trace_psid."""
    load, entry, image = prg_entry(prg)
    mem, mpu = _mpu(load, image)
    for addr in KERNAL_JUMPS:  # no ROMs: KERNAL calls return at once
        mem.write(addr, bytes([RTS]))
    mem.io = True
    mem.raster_model(False)
    out = [[] for _ in range(frames + 1)]
    start = PLAY_LINE * 63

    def on_write(addr, value):
        frame = (mpu.processorCycles - start) // PAL_FRAME + 1
        if frame <= frames:
            out[max(frame, 0)].append((addr, value))

    mem.observe_data_access(None, on_write, writes=SID)
    mpu.pc = entry
    for _ in tqdm(range(frames), desc="prg", unit="frame"):
        mpu.run_until(max_cycles=PAL_FRAME)
    return out


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


def main(argv=None):
    """Compare a PRG's SID writes against a reference PSID up to the tune's loop."""
    ap = argparse.ArgumentParser(description=main.__doc__)
    ap.add_argument("psid")
    ap.add_argument("prg")
    ap.add_argument("labels", help="ACME label dump of the reference (for posoff)")
    ap.add_argument("--limit", type=int, default=50_000)
    ap.add_argument(
        "--frames", type=int, help="compare this many frames, not to the loop"
    )
    args = ap.parse_args(argv)
    with open(args.psid, "rb") as f:
        psid = f.read()
    with open(args.prg, "rb") as f:
        prg = f.read()
    with open(args.labels, encoding="ascii") as f:
        posoff = parse_labels(f.read())["posoff"]
    frames = args.frames or loop_frame(psid, posoff, args.limit)
    ref, got = trace_psid(psid, frames), trace_prg(prg, frames)
    writes = sum(map(len, ref))
    bad = diff(ref, got)
    if bad:
        print(f"MISMATCH frame {bad[0]} write {bad[1]}: ref {bad[2]} got {bad[3]}")
        return 1
    print(f"OK: {frames} frames, {writes} SID writes identical")
    return 0


if __name__ == "__main__":
    sys.exit(main())
