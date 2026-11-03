"""The C playroutine stays under twice the size of the original's listing."""

import re

import disasm
import sidtrace
from automatas_support import ROOT


def test_c_under_twice_listing(psid):
    """Compare src/automatas.c to the jennings listing's instruction lines."""
    load, _, _, image = sidtrace.psid_parts(psid)
    text = disasm.listing(psid, 2000).source
    code = [
        line
        for line in text.splitlines(keepends=True)
        if (m := re.match(r"([0-9A-F]{4})  [0-9A-F]{2}", line))
        and load <= int(m[1], 16) < load + len(image)
    ]
    assert len(code) > 600
    assert len((ROOT / "src" / "automatas.c").read_bytes()) < 2 * len("".join(code))
