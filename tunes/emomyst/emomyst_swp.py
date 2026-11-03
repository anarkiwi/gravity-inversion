"""SWP music data of the Emomyst PSID: layout, patching, and the C build."""

import os
import pathlib
import subprocess
from typing import NamedTuple

from sidtrace import psid_parts

ROOT = pathlib.Path(__file__).resolve().parent
DRIVER = ROOT.parent.parent / "c64"
PSID_DATA = 0x7E  # payload offset of the PSID, after the in-file load address
PACKED = range(0x70, 0x78)
END = 0xFF


class Row(NamedTuple):
    """A pattern row: address of each byte present (note, ins, fx, value)."""

    pattern: int
    addrs: tuple


class Music:
    """The SWP data in a PSID image, addressed by original address."""

    def __init__(self, psid, gen):
        load, init, _, image = psid_parts(psid)
        self.psid = bytearray(psid)
        self.load = load
        self.base = gen.music_org(load, init, image)

    def _at(self, addr):
        assert self.load <= addr < self.load + len(self.psid) - PSID_DATA, hex(addr)
        return PSID_DATA + addr - self.load

    def __getitem__(self, addr):
        return self.psid[self._at(addr)]

    def __setitem__(self, addr, value):
        self.psid[self._at(addr)] = value

    def word(self, addr):
        """Little-endian word at addr."""
        return self[addr] | self[addr + 1] << 8

    def table(self, header):
        """Absolute address of the table at SWP header offset `header`."""
        return self.base + self.word(self.base + header)

    def pointers(self, lo, hi):
        """Absolute addresses held by the split pointer tables at header lo/hi."""
        lo, hi = self.table(lo), self.table(hi)
        return [self.base + (self[lo + i] | self[hi + i] << 8) for i in range(hi - lo)]

    def orderlist(self, voice):
        """Absolute address of a voice's orderlist."""
        return self.base + self.word(self.table(4) + 2 * voice)

    def rows(self):
        """Every row of patterns 1 and up, in pattern order."""
        for p, addr in enumerate(self.pointers(6, 8)[1:], 1):
            while self[addr] != END:
                n = 1
                if self[addr] & 0x80:
                    n = 2
                    if self[addr + 1] & 0x80:
                        n = 3 if self[addr + 2] & 0xE0 else 4
                yield Row(p, tuple(range(addr, addr + n)))
                addr += n

    def bytes(self):
        """The patched PSID."""
        return bytes(self.psid)


def build_prg(psid, gen, out_dir):
    """Compile the C player against data generated from `psid`; return its path."""
    out_dir = pathlib.Path(out_dir)
    (out_dir / "emomyst_data.h").write_text(gen.generate(psid), encoding="ascii")
    prg = out_dir / "emomyst.prg"
    subprocess.run(
        [os.environ.get("CC", "mos-c64-clang"), "-Os", f"-I{out_dir}", "-o", prg]
        + [f"-I{DRIVER}", str(ROOT / "src" / "emomyst.c"), str(DRIVER / "driver.c")],
        check=True,
    )
    return prg
