"""Paths, the C build and patched variants used by the Ghouls'n'Ghosts tests."""

import os
import pathlib
import subprocess

from gen_ghouls import generate
from sidtrace import psid_parts

ROOT = pathlib.Path(__file__).resolve().parent
BUILD = ROOT / "build"
FIXTURES = ROOT.parent.parent / "fixtures"
PSID = FIXTURES / "Ghouls_n_Ghosts.sid"
SONGLENGTHS = FIXTURES / "Songlengths.md5"
DRIVER = ROOT.parent.parent / "c64"

SONG_PTRS_SRC = 0x2A44  # block 0, copied to the song pointer tables at $730e
SONG_INDEX = 0x2A0F
PATTERN = 0x8800  # free after the copied blocks


def build_prg(psid, out_dir):
    """Compile the C player against data generated from `psid`; return its path."""
    out_dir = pathlib.Path(out_dir)
    (out_dir / "ghouls_data.h").write_text(generate(psid), encoding="ascii")
    prg = out_dir / "ghouls.prg"
    subprocess.run(
        [os.environ.get("CC", "mos-c64-clang"), "-Os", f"-I{out_dir}", "-o", prg]
        + [f"-I{DRIVER}", str(ROOT / "src" / "ghouls.c"), str(DRIVER / "driver.c")],
        check=True,
    )
    return prg


def with_pattern(psid, song, pattern):
    """`psid` with every voice of music `song` (0-6) playing `pattern` at PATTERN."""
    load, _, _, image = psid_parts(psid)
    data = bytearray(psid)
    head = len(psid) - len(image)

    def at(addr):
        return head + addr - load

    col = data[at(SONG_INDEX + song)]
    for voice in range(3):
        data[at(SONG_PTRS_SRC + 14 * voice + col)] = PATTERN & 0xFF
        data[at(SONG_PTRS_SRC + 14 * voice + 7 + col)] = PATTERN >> 8
    return bytes(data) + bytes(PATTERN - load - len(image)) + bytes(pattern)
