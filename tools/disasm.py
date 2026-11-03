"""Disassemble a PSID with jennings: a coverage-driven Listing of init and its play calls."""

import argparse
import concurrent.futures
import multiprocessing
import os
import tempfile

from jennings.coverage import CoverageMPU, call
from jennings.coverage._core import CoverageMap
from jennings.coverage.listing import Listing
from tqdm import tqdm

from pysidtracker.emu import run_to_rts

from sidtrace import (
    MAX_CYCLES,
    default_song,
    machine,
    parse_songs,
    psid_parts,
    songlength,
)


def _image(data):
    load, init, play, image = psid_parts(data)
    mem = [0] * 0x10000
    mem[load : load + len(image)] = image
    return mem, (load, load + len(image) - 1), [init, play]


def _cover(data, song, frames, path):
    """Save to `path` the coverage of `frames` play calls after init `song`.

    Init runs on sidtrace's C64 machine; the play calls under coverage on its RAM."""
    load, init, play, image = psid_parts(data)
    mem, native = machine(load, image)
    run_to_rts(native, mem, init, song, MAX_CYCLES)
    mpu = CoverageMPU()
    mpu.memory = list(bytes(mem.ram))
    for _ in range(frames):
        call(mpu, play)
    mpu.cov.save(path)
    return path


def listing(data, frames, song=None, jobs=1):
    """jennings Listing of the payload after init and `frames` play calls.

    `song` may be a list of subtunes, each run from a fresh image in one of
    `jobs` processes, with coverage merged; `frames` may then be a list of
    play calls per subtune."""
    songs = [default_song(data)] if song is None else song
    songs = songs if isinstance(songs, list) else [songs]
    frames = frames if isinstance(frames, list) else [frames] * len(songs)
    cov = CoverageMap()
    with tempfile.TemporaryDirectory() as tmp, tqdm(
        total=sum(frames), desc="cover", unit="frame"
    ) as progress, concurrent.futures.ProcessPoolExecutor(
        jobs, multiprocessing.get_context("forkserver")
    ) as ex:
        futs = {
            ex.submit(_cover, data, s, n, os.path.join(tmp, f"{s}.npz")): n
            for s, n in zip(songs, frames)
        }
        for fut in concurrent.futures.as_completed(futs):
            cov |= CoverageMap.load(fut.result())
            progress.update(futs[fut])
    mem, span, entries = _image(data)
    return Listing.disassemble(mem, entries, cov=cov, ranges=[span])


def main(argv=None):
    """CLI entry point: write the listing and its .jcov coverage sidecar."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("psid")
    ap.add_argument("out")
    ap.add_argument("--frames", type=int, default=3000)
    ap.add_argument("--song", help="0-based subtunes, e.g. 0,3-5 (default: start song)")
    ap.add_argument("--songlengths", help="HVSC Songlengths.md5: frames per subtune")
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    args = ap.parse_args(argv)
    with open(args.psid, "rb") as f:
        data = f.read()
    songs = parse_songs(args.song) if args.song else [default_song(data)]
    frames = args.frames
    if args.songlengths:
        frames = [songlength(args.songlengths, data, s) for s in songs]
    listing(data, frames, songs, args.jobs).write(args.out)


if __name__ == "__main__":
    main()
