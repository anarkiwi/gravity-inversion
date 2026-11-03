"""Disassemble a PSID with jennings: a coverage-driven Listing of init and its play calls."""

import argparse

from jennings.coverage import CoverageMPU, call
from jennings.coverage.listing import Listing
from tqdm import tqdm

from sidtrace import psid_parts


def listing(data, frames, song=None):
    """jennings Listing of the payload after init and `frames` play calls."""
    load, init, play, image = psid_parts(data)
    mem = [0] * 0x10000
    mem[load : load + len(image)] = image
    mpu = CoverageMPU()
    mpu.memory = mem
    call(mpu, init, a=data[0x11] - 1 if song is None else song)
    for _ in tqdm(range(frames), desc="cover", unit="frame"):
        call(mpu, play)
    mem[load : load + len(image)] = image
    return Listing.disassemble(
        mem, [init, play], cov=mpu.cov, ranges=[(load, load + len(image) - 1)]
    )


def main(argv=None):
    """CLI entry point: write the listing and its .jcov coverage sidecar."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("psid")
    ap.add_argument("out")
    ap.add_argument("--frames", type=int, default=3000)
    ap.add_argument("--song", type=int)
    args = ap.parse_args(argv)
    with open(args.psid, "rb") as f:
        data = f.read()
    listing(data, args.frames, args.song).write(args.out)


if __name__ == "__main__":
    main()
