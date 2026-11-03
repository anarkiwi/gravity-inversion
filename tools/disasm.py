"""Disassemble a PSID with jennings: a coverage-driven Listing of init and its play calls."""

import argparse

from jennings.coverage import CoverageMPU, call
from jennings.coverage.listing import Listing
from tqdm import tqdm

from sidtrace import MAX_CYCLES, psid_parts, ram_mpu


def listing(data, frames, song=None):
    """jennings Listing of the payload after init and `frames` play calls.

    Init runs on the native core (with its raster model); the play calls under coverage.
    """
    load, init, play, image = psid_parts(data)
    ram, native = ram_mpu(load, image)
    native.run_to_rts(init, data[0x11] - 1 if song is None else song, MAX_CYCLES)
    mem = list(bytes(ram))
    mpu = CoverageMPU()
    mpu.memory = mem
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
