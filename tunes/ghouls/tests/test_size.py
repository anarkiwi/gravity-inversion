"""The C playroutine stays under twice the size of the original's listing."""

import os
import re

import disasm
import sidtrace
from ghouls_support import ROOT, SONGLENGTHS

SONGS = list(range(32))


def test_c_under_twice_listing(psid):
    """Compare src/ghouls.c to the jennings listing of the code all subtunes run."""
    frames = [sidtrace.songlength(SONGLENGTHS, psid, s) for s in SONGS]
    source = disasm.listing(psid, frames, SONGS, os.cpu_count()).source
    code = re.findall(r"^[0-9A-F]{4}  (?!.*\.byte).*\n", source, re.M)
    assert len((ROOT / "src" / "ghouls.c").read_bytes()) < 2 * len("".join(code))
