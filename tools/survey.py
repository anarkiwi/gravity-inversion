"""Rank PSID tunes by the playroutine code they execute (unique PCs) under jennings."""

import argparse
import concurrent.futures
import csv
import sys

import numpy as np
from tqdm import tqdm

from sidtrace import MAX_CYCLES, default_song, machine, psid_parts, song_calls


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


def _job(path, frames):
    try:
        with open(path, "rb") as f:
            data = f.read()
        if data[:4] != b"PSID" or psid_parts(data)[2] == 0:
            return path, None
        if isinstance(frames, str):
            frames = song_calls(data, default_song(data), frames)
        return path, coverage(data, frames)
    except (AssertionError, KeyError, RuntimeError, ValueError, IndexError):
        return path, None


def main(argv=None):
    """Rank tunes listed one path per line on stdin; write CSV to stdout."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--frames", type=int, default=3000)
    ap.add_argument("--jobs", type=int, default=32)
    ap.add_argument("--hvsc", help="HVSC root: play each tune's Songlengths.md5 length")
    args = ap.parse_args(argv)
    paths = [line.strip() for line in sys.stdin if line.strip()]
    frames = args.hvsc or args.frames
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
