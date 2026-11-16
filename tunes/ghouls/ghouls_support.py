"""Paths, the C build and patched variants used by the Ghouls'n'Ghosts tests."""

import os
import pathlib
import subprocess

from gen_ghouls import BLOCK_DST, BLOCK_LEN, BLOCK_SRC, generate
from sidtrace import psid_parts
from transliterate import build

ROOT = pathlib.Path(__file__).resolve().parent
BUILD = ROOT / "build"
FIXTURES = ROOT.parent.parent / "fixtures"
PSID = FIXTURES / "Ghouls_n_Ghosts.sid"
HVSC = FIXTURES
DRIVER = ROOT.parent.parent / "c64"

SONG_PTRS_SRC = 0x2A44  # block 0, copied to the song pointer tables at $730e
SONG_INDEX, SONG_BLOCK_B = 0x2A0F, 0x2A25


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


def gen_prg(psid, out_dir):
    """Compile the C transliterated from `psid`; return its path."""
    return build(psid, out_dir)[0]


def _image(psid):
    """(byte offset in `psid` of original address a, little-endian word at a)."""
    load, _, _, image = psid_parts(psid)
    head = len(psid) - len(image)

    def at(addr):
        return head + addr - load

    return at, lambda addr: psid[at(addr)] | psid[at(addr) + 1] << 8


def _block(psid, song):
    """Table offset of music `song`'s (0-6) second copy block."""
    at, _ = _image(psid)
    return 2 * psid[at(SONG_BLOCK_B + song)]


def pattern_block(psid, song):
    """(source, destination, length) of music `song`'s (0-6) second copy block."""
    _, word = _image(psid)
    return tuple(
        word(t + _block(psid, song)) for t in (BLOCK_SRC, BLOCK_DST, BLOCK_LEN)
    )


def with_block_src(psid, song, src):
    """`psid` with music `song`'s (0-6) second copy block read from `src`."""
    at, _ = _image(psid)
    data = bytearray(psid)
    data[at(BLOCK_SRC + _block(psid, song))] = src & 0xFF
    data[at(BLOCK_SRC + _block(psid, song)) + 1] = src >> 8
    return bytes(data)


def with_pattern(psid, song, pattern):
    """`psid` with every voice of music `song` (0-6) playing `pattern`, which replaces the
    start of the song's second copy block."""
    at, _ = _image(psid)
    src, dst, length = pattern_block(psid, song)
    assert len(pattern) <= length
    data = bytearray(psid)
    data[at(src) : at(src) + len(pattern)] = pattern
    col = data[at(SONG_INDEX + song)]
    for voice in range(3):
        data[at(SONG_PTRS_SRC + 14 * voice + col)] = dst & 0xFF
        data[at(SONG_PTRS_SRC + 14 * voice + 7 + col)] = dst >> 8
    return bytes(data)
