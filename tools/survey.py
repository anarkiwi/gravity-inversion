"""Rank PSID tunes by the playroutine code they execute (unique PCs) under jennings."""

import argparse
import concurrent.futures
import csv
import hashlib
import sys

import numpy as np
from jennings.coverage.cfg import successors
from jennings.isa import is_branch
from tqdm import tqdm

from sidtrace import MAX_CYCLES, default_song, machine, psid_parts, songlengths


def executed(data, frames, song=None):
    """Set of PCs executed by init + `frames` play calls."""
    return set(_run(data, frames, song)[0].tolist())


def coverage(data, frames, song=None):
    """Return (unique PCs, total cycles, play calls) of init + `frames` play calls."""
    pcs, cycles = _run(data, frames, song)
    return len(pcs), cycles, frames


def _traces(data, frames, song):
    """(PC trace per call of init + `frames` play calls, play cycles)."""
    load, init, play, image = psid_parts(data)
    song = default_song(data) if song is None else song
    _, mpu = machine(load, image[: 0x10000 - load])
    pcs = [np.asarray(mpu.run_to_rts_traced(init, song, MAX_CYCLES).pc)]
    start = mpu.processorCycles
    for _ in range(frames):
        pcs.append(np.asarray(mpu.run_to_rts_traced(play, 0, MAX_CYCLES).pc))
    return pcs, mpu.processorCycles - start


def _run(data, frames, song):
    pcs, cycles = _traces(data, frames, song)
    return np.unique(np.concatenate(pcs)), cycles


def transitions(data, frames, song=None):
    """Set of executed (PC, next PC) pairs, encoded PC << 16 | next, of init +
    `frames` play calls."""
    pcs = _traces(data, frames, song)[0]
    return set(
        np.unique(
            np.concatenate([p[:-1].astype(np.int64) << 16 | p[1:] for p in pcs])
        ).tolist()
    )


def branch_gaps(data, seen):
    """Outcomes {(branch PC, target)} of the branches executed in `seen`
    transitions (see transitions()) that were never taken."""
    load, _, _, image = psid_parts(data)
    mem = np.zeros(0x10000, np.uint8)
    code = np.frombuffer(image, np.uint8)[: 0x10000 - load]
    mem[load : load + len(code)] = code
    gaps = set()
    for pc in {e >> 16 for e in seen}:
        if is_branch(int(mem[pc])):
            gaps.update(
                (pc, t) for _, t in successors(mem, pc)[0] if pc << 16 | t not in seen
            )
    return gaps


def _job(path, frames):
    try:
        with open(path, "rb") as f:
            data = f.read()
        if data[:4] != b"PSID" or psid_parts(data)[2] == 0:
            return path, None
        if isinstance(frames, dict):
            frames = frames.get(hashlib.md5(data).hexdigest(), [3000])[
                default_song(data)
            ]
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
