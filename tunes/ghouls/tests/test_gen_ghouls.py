"""Tests for gen_ghouls.py."""

import gen_ghouls
import sidtrace
from tunetest import array, define


def test_generate_is_byte_identical(psid):
    """Each held array is the image's bytes at its origin; copy destinations have only a
    length."""
    header = gen_ghouls.generate(psid)
    load, _, _, image = sidtrace.psid_parts(psid)
    for name, addrs in gen_ghouls.held(psid).items():
        assert int(define(header, f"{name.upper()}_ORG"), 16) == addrs[0]
        if name in gen_ghouls.COPIES:
            assert int(define(header, f"{name.upper()}_LEN"), 16) == len(addrs)
        else:
            body = array(header, f"{name.upper()}_DATA")
            assert body == bytes(image[a - load] for a in addrs)


def test_held(psid):
    """The block tables and sources, then the copy destinations' union per region."""
    held = gen_ghouls.held(psid)
    assert held["blocks"] == range(0x29E7, 0x60B2)
    assert held["music"] == range(0x730E, 0x86E9)
    assert held["patterns"] == range(0x914F, 0x9E07)
