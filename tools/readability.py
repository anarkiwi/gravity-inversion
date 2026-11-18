"""Generate the C the readability census ratchets (docs/readability.md): each tune's and fuzz
player's translation written to out/readability/<name>.c and .h."""

import argparse
import concurrent.futures
import multiprocessing
import os
import pathlib

from tqdm import tqdm

from constructs import ROOT
from residue import FUZZ, TUNES, target
from transliterate import transliterate

OUT = ROOT / "out" / "readability"


def generate(name, out=OUT):
    """Write target ``name``'s translation to ``out``/<name>.c and .h; the .c path."""
    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)
    c, h, _ = transliterate(target(name)[0], f"{name}.h")
    (out / f"{name}.h").write_text(h, encoding="utf-8")
    path = out / f"{name}.c"
    path.write_text(c, encoding="utf-8")
    return path


def main(argv=None):
    """Generate every target (default: each tune and fuzz player)."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("targets", nargs="*", help="tune or fuzz-<style>-<seed>")
    ap.add_argument("--out", type=pathlib.Path, default=OUT)
    ap.add_argument("-j", "--jobs", type=int, default=len(os.sched_getaffinity(0)))
    args = ap.parse_args(argv)
    names = args.targets or list(TUNES + FUZZ)
    ctx = multiprocessing.get_context("forkserver")
    with concurrent.futures.ProcessPoolExecutor(
        min(args.jobs, len(names)), ctx
    ) as pool:
        jobs = [pool.submit(generate, n, args.out) for n in names]
        done = concurrent.futures.as_completed(jobs)
        for job in tqdm(done, total=len(jobs), desc="readability C", unit="lift"):
            job.result()


if __name__ == "__main__":
    main()
