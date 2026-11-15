"""Per-byte check of a hand translation's data against the original's data footprint.

Each tune's generator maps the hand C's arrays to original addresses (`held(psid)`) and names
the other addresses it keeps as variables or in code (`named(psid)`); jennings coverage of the
original's runs gives the bytes it uses as data (docs/footprint.md).
"""

import argparse
import concurrent.futures
import importlib
import os
import sys

import numpy as np
from jennings.coverage import READ, WRITE
from jennings.coverage.cfg import LEN

from constructs import ROOT, load, memory
from copies import instructions
from disasm import coverage, listing
from sidtrace import parse_labels, psid_parts, songs

IO, STACK, ZP = (0xD000, 0xE000), (0x100, 0x200), (0, 0x100)
CLEAN = {"extra": [], "missing": [], "overlap": []}


def mask(spans):
    """64 KiB boolean array of the half-open address spans."""
    out = np.zeros(0x10000, bool)
    for lo, hi in spans:
        out[lo:hi] = True
    return out


def runs(m):
    """Half-open (lo, hi) runs of True in a boolean array."""
    d = np.diff(np.r_[0, m.astype(np.int8), 0])
    return list(zip(np.flatnonzero(d == 1).tolist(), np.flatnonzero(d == -1).tolist()))


def instruction_bytes(data, cov):
    """Mask of the image's instruction bytes: jennings' listing from `cov`."""
    load_addr, _, _, image = psid_parts(data)
    mem = memory(data)
    code = np.zeros(0x10000, bool)
    for a, *_ in instructions(listing(data, 0, cov=cov).source):
        code[a : a + int(LEN[mem[a]])] = True
    return code & mask([(load_addr, load_addr + len(image))])


class Original:  # pylint: disable=too-few-public-methods
    """Coverage flags, instruction and image masks of an original over its runs."""

    def __init__(self, tune_runs, jobs=4):
        """`tune_runs`: (PSID bytes, play calls per song, songs), each over `jobs` processes
        at once; init runs under coverage."""
        self.flags = np.zeros(0x10000, np.uint8)
        self.code = np.zeros(0x10000, bool)
        self.image = np.zeros(0x10000, bool)

        def one(run):
            data, frames, picked = run
            cov = coverage(data, frames, picked, jobs, init=True)
            return data, cov, instruction_bytes(data, cov)

        with concurrent.futures.ThreadPoolExecutor(len(tune_runs)) as ex:
            for data, cov, code in ex.map(one, tune_runs):
                self.flags |= cov.flags
                self.code |= code
                load_addr, _, _, image = psid_parts(data)
                self.image[load_addr : load_addr + len(image)] = True
        self.used = (self.flags & (READ | WRITE)) != 0
        self.used &= ~mask([IO, STACK])

    def footprint(self):
        """Mask of the original's data: non-instruction image bytes and bytes used as data."""
        return (self.image & ~self.code) | self.used


def address_mask(arrays):
    """Mask of {name: original addresses}."""
    out = np.zeros(0x10000, bool)
    for addrs in arrays.values():
        out[list(addrs)] = True
    return out


def check(orig, held, named):
    """Runs of held bytes outside the footprint (extra); of bytes read as data, outside zero
    page and the instruction bytes the original writes (its operand state), neither held
    nor named (missing); of held bytes also named (overlap)."""
    h, n = address_mask(held), address_mask(named)
    operands = orig.code & ((orig.flags & WRITE) != 0)
    read = orig.used & ((orig.flags & READ) != 0)
    return {
        "extra": runs(h & ~orig.footprint()),
        "missing": runs(read & ~mask([ZP]) & ~operands & ~h & ~n),
        "overlap": runs(h & n),
    }


def generator(tune):
    """The module of tunes/<tune>/gen_*.py."""
    path = next((ROOT / "tunes" / tune).glob("gen_*.py"))
    sys.path.insert(0, str(path.parent))
    return importlib.import_module(path.stem)


def maps(tune, psid):
    """A tune's (held, named) {name: original addresses} for the PSID file `psid`, with its
    ACME labels where the build assembles it."""
    labels = psid.with_suffix(".labels")
    extra = (
        [parse_labels(labels.read_text(encoding="ascii"))] if labels.exists() else []
    )
    gen = generator(tune)
    return gen.held(psid.read_bytes(), *extra), gen.named(psid.read_bytes(), *extra)


def default_runs(tune):
    """(PSID path, runs) of a tune: every song for its construct map's frames."""
    path = ROOT / load(tune)["psid"]
    data = path.read_bytes()
    n = len(songs("all", data))
    return path, [(data, [load(tune)["frames"]] * n, list(range(n)))]


def report(tune, jobs=4):
    """check() of a tune's hand C over default_runs(), and its held byte count."""
    path, tune_runs = default_runs(tune)
    held, named = maps(tune, path)
    out = check(Original(tune_runs, jobs), held, named)
    out["held"] = int(address_mask(held).sum())
    return out


def fmt(spans):
    """Runs as `$lo-$hi` text."""
    return " ".join(f"${lo:04X}-${hi - 1:04X}" for lo, hi in spans) or "none"


def main(argv=None):
    """CLI entry point: report each tune's held bytes against its original's footprint."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tunes", nargs="+")
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    args = ap.parse_args(argv)
    results = {t: report(t, args.jobs) for t in args.tunes}
    for t, r in results.items():
        print(f"{t}: held {r['held']}; " + "; ".join(f"{k} {fmt(r[k])}" for k in CLEAN))
    return int(any(r[k] for r in results.values() for k in CLEAN))


if __name__ == "__main__":
    raise SystemExit(main())
