"""Disassemble a PSID with jennings: a coverage-driven Listing of init and its play calls."""

import argparse
import concurrent.futures
import multiprocessing
import os
import tempfile

import numpy as np
from jennings.coverage import FALLTHRU, RETURN, TAKEN, CoverageMPU, call
from jennings.coverage._core import CoverageMap
from jennings.coverage.cfg import successors
from jennings.coverage.listing import Listing
from tqdm import tqdm

from pysidtracker.emu import run_to_rts

from sidtrace import MAX_CYCLES, default_song, machine, psid_parts, song_calls, songs

RASTER = 0xD012


def _image(data):
    load, init, play, image = psid_parts(data)
    mem = [0] * 0x10000
    mem[load : load + len(image)] = image
    return mem, (load, load + len(image) - 1), [init, play]


def _init_cover(mpu, init, song, steps=MAX_CYCLES):
    """Run init under coverage; a raster counter at $D012 advances every 64 steps for polls."""
    sp = mpu.sp
    mpu._push_word((RETURN - 1) & 0xFFFF)  # pylint: disable=protected-access
    mpu.a, mpu.pc = song, init
    for n in range(steps):
        mpu.memory[RASTER] = n >> 6 & 0xFF
        mpu.step()
        if mpu.pc == RETURN and mpu.sp == sp:
            return
    raise RuntimeError(f"init ${init:04X} did not return")


def _cover(
    data, song, frames, path, init_cov=False
):  # pylint: disable=too-many-arguments
    """Save to `path` the coverage of `frames` play calls after init `song`.

    Init runs on sidtrace's C64 machine; the play calls under coverage on its RAM. With
    `init_cov`, init also runs under coverage, from the loaded image over power-on RAM.
    """
    load, init, play, image = psid_parts(data)
    mem, native = machine(load, image)
    mpu = CoverageMPU()
    if init_cov:
        mpu.memory = list(bytes(mem.ram))
        _init_cover(mpu, init, song)
        cov = mpu.cov
    run_to_rts(native, mem, init, song, MAX_CYCLES)
    mpu = CoverageMPU()
    if init_cov:
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
    """jennings Listing of the payload, from `cov` or coverage(data, frames, song, jobs)."""
    cov = coverage(data, frames, song, jobs) if cov is None else cov
    mem, span, entries = _image(data)
    return Listing.disassemble(mem, entries, cov=cov, ranges=[span])


def one_way(data, cov):
    """{(branch PC, target never taken)} of the branches `cov` saw go one way only."""
    mem = np.array(_image(data)[0], np.uint8)
    bt = cov.flags & (TAKEN | FALLTHRU)
    return {
        (int(pc), t)
        for pc in np.flatnonzero((bt == TAKEN) | (bt == FALLTHRU))
        for flag, t in successors(mem, int(pc))[0]
        if not bt[pc] & flag
    }


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
