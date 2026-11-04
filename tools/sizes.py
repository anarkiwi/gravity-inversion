"""Size bounds of a translation, relative to the original 6502 player (docs/sizes.md).

Code: the PRG's .text, less the runtime an empty player costs, at most 2.5 times the original's
instruction bytes. Data: the PRG's data sections at most the original's data footprint plus the
code bytes it reads as data. The PRG loads below $D000."""

import argparse
import pathlib

import numpy as np
from elftools.elf.elffile import ELFFile
from jennings.coverage import READ, WRITE
from jennings.coverage.cfg import LEN

from constructs import memory, run
from sidtrace import psid_parts

CODE_FACTOR = 2.5
IO, STACK = (0xD000, 0xE000), (0x100, 0x200)
TOP = 0xD000
DATA_SECTIONS = (".rodata", ".data", ".bss", ".zp", ".zp.data", ".zp.bss", ".noinit")


def sections(elf):
    """({section: bytes} of an llvm-mos ELF, highest address any section reaches)."""
    with open(elf, "rb") as f:
        e = ELFFile(f)
        alloc = [s for s in e.iter_sections() if s["sh_flags"] & 2 and s["sh_size"]]
        top = max(
            (s["sh_addr"] + s["sh_size"] for s in alloc if s["sh_addr"] < 0x10000),
            default=0,
        )
        return {s.name: s["sh_size"] for s in alloc}, top


def usage(elf, runtime):
    """(code, data, top) bytes of a PRG's ELF, code less the `runtime` .text of an empty player."""
    size, top = sections(elf)
    return (
        size.get(".text", 0) - runtime,
        sum(size.get(n, 0) for n in DATA_SECTIONS),
        top,
    )


def bounds(tune, jobs=4):
    """(code bound, data bound) of a tune from its original's listing and run coverage."""
    _, data, cov, starts = run(tune, jobs)
    load, _, _, image = psid_parts(data)
    mem = memory(data)
    code = np.zeros(0x10000, bool)
    for a in starts:
        if load <= a < load + len(image):
            code[a : a + int(LEN[mem[a]])] = True
    inside = np.zeros(0x10000, bool)
    inside[load : load + len(image)] = True
    skip = np.zeros(0x10000, bool)
    for lo, hi in (IO, STACK):
        skip[lo:hi] = True
    written = (cov.flags & WRITE) != 0
    read = (cov.flags & READ) != 0
    footprint = int((inside & ~code).sum()) + int((written & ~inside & ~skip).sum())
    return int(CODE_FACTOR * code.sum()), footprint + int((read & code).sum())


def main(argv=None):
    """CLI entry point: print a PRG's sizes against its tune's bounds."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tune")
    ap.add_argument("elf", type=pathlib.Path)
    ap.add_argument(
        "--runtime", type=int, required=True, help=".text of an empty player"
    )
    args = ap.parse_args(argv)
    code, data, top = usage(args.elf, args.runtime)
    cb, db = bounds(args.tune)
    print(f"{args.tune}: code {code}/{cb}, data {data}/{db}, top ${top:04X}/${TOP:04X}")
    return 0 if code <= cb and data <= db and top <= TOP else 1


if __name__ == "__main__":
    raise SystemExit(main())
