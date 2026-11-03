"""The C playroutine stays under twice the size of the original's listing."""

import pathlib

import disasm

SOURCE = pathlib.Path(__file__).resolve().parent.parent / "src" / "emomyst.c"


def test_c_under_twice_listing(psid, frames):
    """src/emomyst.c against the jennings listing's instruction lines."""
    listing = disasm.listing(psid, frames).source
    code = "\n".join(line for line in listing.splitlines() if ".byte" not in line)
    assert code.count("\n") > 900
    assert len(SOURCE.read_bytes()) < 2 * len(code)
