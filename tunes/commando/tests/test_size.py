"""The C playroutine stays under twice the size of the original source."""

import re

from support import ASM, ROOT


def test_c_under_twice_asm():
    """Compare src/commando.c to the asm playroutine, comments included."""
    asm = ASM.read_text(encoding="ascii")
    player = re.search(r"^TRACK .*?(?=^freqsh = )", asm, re.S | re.M)[0]
    assert len((ROOT / "src" / "commando.c").read_bytes()) < 2 * len(player)
