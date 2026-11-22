"""Disassemble a PSID with jennings: a coverage-driven Listing of init and its play calls."""

import argparse
import concurrent.futures
import multiprocessing
import os
import tempfile

import numpy as np
from jennings import coverage as jcov
from jennings.coverage import CoverageMPU, call
from jennings.coverage._core import CoverageMap
from jennings.coverage.cfg import instruction_bytes
from jennings.coverage.listing import Listing
from pysidtracker.image import SidImage
from tqdm import tqdm

from sidtrace import (
    MAX_CYCLES,
    boot,
    boot_ram,
    default_song,
    psid_parts,
    song_calls,
    songs,
)

RASTER_STEPS = 64


def _image(data):
    """(zeroed C64 memory holding the payload, inclusive payload span, [init, play])."""
    s = SidImage.from_sid(data)
    entries = [s.header.init_address or s.load, s.header.play_address]
    return np.frombuffer(s.mem, np.uint8), (s.load, s.end - 1), entries


def _cover(
    data, song, frames, path, init_cov=False
):  # pylint: disable=too-many-arguments
    """Save to `path` the coverage of `frames` play calls after init `song`.

    Init runs on sidtrace's PSID machine; the play calls under coverage on its RAM. With
    `init_cov`, init also runs under coverage from that machine's boot RAM, $D012 counting
    instructions by RASTER_STEPS."""
    mpu = CoverageMPU()
    if init_cov:
        mpu.memory = list(boot_ram(data))
        call(mpu, psid_parts(data)[1], song, max_steps=MAX_CYCLES, raster=RASTER_STEPS)
    cov = mpu.cov
    _, mem, play = boot(data, song)
    mpu = CoverageMPU()
    mpu.cov = cov
    mpu.memory = list(bytes(mem.ram))
    for _ in range(frames):
        call(mpu, play)
    mpu.cov.save(path)
    return path


def coverage(
    data, frames, song=None, jobs=1, init=False
):  # pylint: disable=R0913,R0917
    """jennings CoverageMap of `frames` play calls (and init, with `init`).

    `song` may be a list of subtunes, each run from a fresh image in one of
    `jobs` processes, with coverage merged; `frames` may then be a list of
    play calls per subtune."""
    picked = [default_song(data)] if song is None else song
    picked = picked if isinstance(picked, list) else [picked]
    frames = frames if isinstance(frames, list) else [frames] * len(picked)
    cov = CoverageMap()
    with tempfile.TemporaryDirectory() as tmp, tqdm(
        total=sum(frames), desc="cover", unit="frame"
    ) as progress, concurrent.futures.ProcessPoolExecutor(
        jobs, multiprocessing.get_context("forkserver")
    ) as ex:
        futs = {
            ex.submit(_cover, data, s, n, os.path.join(tmp, f"{s}.npz"), init): n
            for s, n in zip(picked, frames)
        }
        for fut in concurrent.futures.as_completed(futs):
            cov |= CoverageMap.load(fut.result())
            progress.update(futs[fut])
    return cov


def listing(data, frames, song=None, jobs=1, cov=None):
    """jennings Listing of the payload, from `cov` or coverage(data, frames, song, jobs), with
    the targets of computed transfers through constant tables decoded."""
    cov = coverage(data, frames, song, jobs) if cov is None else cov
    mem, span, entries = _image(data)
    return Listing.disassemble(mem, entries, cov=cov, ranges=[span], resolve=True)


def code_bytes(data, cov):
    """Address-space mask of the bytes of the instructions of `data`'s listing over `cov`."""
    lst = listing(data, 0, cov=cov)
    starts = np.zeros(0x10000, bool)
    starts[lst.lines[:, 1]] = True
    return instruction_bytes(_image(data)[0], starts)


def one_way(data, cov):
    """{(branch PC, target never taken)} of the branches `cov` saw go one way only."""
    return jcov.one_way(cov, _image(data)[0])


def main(argv=None):
    """CLI entry point: write the listing and its .jcov coverage sidecar."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("psid")
    ap.add_argument("out")
    ap.add_argument("--frames", type=int, default=3000)
    ap.add_argument(
        "--song", help="1-based subtunes: all, or e.g. 1,3-5 (default: start)"
    )
    ap.add_argument(
        "--hvsc", help="HVSC root (DOCUMENTS/Songlengths.md5): song lengths"
    )
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    args = ap.parse_args(argv)
    with open(args.psid, "rb") as f:
        data = f.read()
    picked = songs(args.song, data) if args.song else [default_song(data)]
    frames = args.frames
    if args.hvsc:
        frames = [song_calls(data, s, args.hvsc) for s in picked]
    listing(data, frames, picked, args.jobs).write(args.out)


if __name__ == "__main__":
    main()
