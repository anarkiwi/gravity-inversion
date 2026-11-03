"""Rank PSID tunes by the playroutine code they execute (unique PCs) under jennings."""

import argparse
import concurrent.futures
import csv
import hashlib
import sys

import numpy as np
from jennings.devices.mpu6502 import MPU
from jennings.memory import NativeMemory
from tqdm import tqdm

from sidtrace import MAX_CYCLES, psid_parts, songlengths


def executed(data, frames, song=None):
    """Set of PCs executed by init + `frames` play calls."""
    return set(_run(data, frames, song)[0].tolist())


def coverage(data, frames, song=None):
    """Return (unique PCs, total cycles, play calls) of init + `frames` play calls."""
    pcs, cycles = _run(data, frames, song)
    return len(pcs), cycles, frames


def _run(data, frames, song):
    load, init, play, image = psid_parts(data)
    song = data[0x11] - 1 if song is None else song
    mem = NativeMemory(bytes(0x10000))
    mem.write(load, bytes(image[: 0x10000 - load]))
    mpu = MPU(memory=mem)
    pcs = [np.asarray(mpu.run_to_rts_traced(init, song, MAX_CYCLES).pc)]
    start = mpu.processorCycles
    for _ in range(frames):
        pcs.append(np.asarray(mpu.run_to_rts_traced(play, 0, MAX_CYCLES).pc))
    return np.unique(np.concatenate(pcs)), mpu.processorCycles - start


def _job(path, frames):
    try:
        with open(path, "rb") as f:
            data = f.read()
        if data[:4] != b"PSID" or psid_parts(data)[2] == 0:
            return path, None
        if isinstance(frames, dict):
            frames = frames.get(hashlib.md5(data).hexdigest(), [3000])[data[0x11] - 1]
        return path, coverage(data, frames)
    except (AssertionError, RuntimeError, ValueError, IndexError):
        return path, None


def main(argv=None):
    """Rank tunes listed one path per line on stdin; write CSV to stdout."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--frames", type=int, default=3000)
    ap.add_argument("--jobs", type=int, default=32)
    ap.add_argument(
        "--songlengths", help="HVSC Songlengths.md5: play each tune's length"
    )
    args = ap.parse_args(argv)
    paths = [line.strip() for line in sys.stdin if line.strip()]
    frames = songlengths(args.songlengths) if args.songlengths else args.frames
    rows = []
    with concurrent.futures.ThreadPoolExecutor(args.jobs) as ex:
        futs = [ex.submit(_job, p, frames) for p in paths]
        for fut in tqdm(concurrent.futures.as_completed(futs), total=len(futs)):
            path, res = fut.result()
            if res:
                rows.append((path, *res))
    rows.sort(key=lambda r: -r[1])
    out = csv.writer(sys.stdout)
    out.writerow(["path", "pcs", "cycles", "frames"])
    out.writerows(rows)


if __name__ == "__main__":
    main()
