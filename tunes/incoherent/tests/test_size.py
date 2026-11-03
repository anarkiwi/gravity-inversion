"""The C playroutine stays under twice the size of the original's listing."""

import re

import disasm
import sidtrace
from incoherent_support import FIX, ROOT


def test_c_under_twice_listing(psid):
    """Compare src/incoherent.c to the jennings listing's instruction lines."""
    source = disasm.listing(psid, sidtrace.song_calls(psid, 0, FIX)).source
    code = re.findall(r"^[0-9A-F]{4}  (?!.*\.byte).*\n", source, re.M)
    assert len(code) > 450
    assert len((ROOT / "src" / "incoherent.c").read_bytes()) < 2 * len("".join(code))
