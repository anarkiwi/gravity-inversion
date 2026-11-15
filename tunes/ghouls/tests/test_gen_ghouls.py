"""Tests for gen_ghouls.py."""

import re

import gen_ghouls
import sidtrace


def _define(header, name):
    return re.search(rf"#define {name} (.*)", header)[1]


def test_generate_is_byte_identical(psid):
    """Each held array is the image's bytes at its origin; copy destinations have only a
    length."""
    header = gen_ghouls.generate(psid)
    load, _, _, image = sidtrace.psid_parts(psid)
    for name, addrs in gen_ghouls.held(psid).items():
        assert int(_define(header, f"{name.upper()}_ORG"), 16) == addrs[0]
        if name in gen_ghouls.COPIES:
            assert int(_define(header, f"{name.upper()}_LEN"), 16) == len(addrs)
        else:
            body = _define(header, f"{name.upper()}_DATA").split(",")
            assert bytes(int(b, 16) for b in body) == bytes(
                image[a - load] for a in addrs
            )


def test_held(psid):
    """The block tables and sources, then the copy destinations' union per region."""
    held = gen_ghouls.held(psid)
    assert held["blocks"] == range(0x29E7, 0x60B2)
    assert held["music"] == range(0x730E, 0x86E9)
    assert held["patterns"] == range(0x914F, 0x9E07)


def test_main(tmp_path, psid):
    """CLI writes the header for a PSID file."""
    src, out = tmp_path / "t.sid", tmp_path / "t.h"
    src.write_bytes(psid)
    gen_ghouls.main([str(src), str(out)])
    assert out.read_text(encoding="ascii") == gen_ghouls.generate(psid)
