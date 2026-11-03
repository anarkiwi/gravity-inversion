"""Tests for gen_emomyst.py: the generated header holds the original bytes."""

import re

import pytest

from sidtrace import psid_parts


def _array(header, name):
    text = re.search(rf"#define {name} (.*)", header)[1]
    return bytes(int(b, 16) for b in text.split(","))


def test_generate_is_byte_identical(gen, psid):
    """TABLES and MUSIC are the loaded image at their original addresses."""
    header = gen.generate(psid)
    load, _, _, image = psid_parts(psid)
    org = int(re.search(r"#define MUSIC_ORG (\w+)", header)[1], 16)
    assert org == 0x1C00 and image[org - load : org - load + 4] == b"SWP1"
    assert _array(header, "MUSIC") == image[org - load :]
    tables = _array(header, "TABLES")
    assert len(tables) == gen.TABLES_END - gen.TABLES_ORG
    assert tables == image[gen.TABLES_ORG - load : gen.TABLES_END - load]
    assert f"#define TABLES_ORG 0x{gen.TABLES_ORG:04x}" in header


def test_music_org_checks_init(gen, psid):
    """The music base is read from init's LDX/LDY; other code is rejected."""
    load, init, _, image = psid_parts(psid)
    assert gen.music_org(load, init, image) == 0x1C00
    with pytest.raises(AssertionError):
        gen.music_org(load, init + 1, image)
    with pytest.raises(AssertionError):
        gen.music_org(load, init + 3, image)


def test_main(gen, psid, tmp_path):
    """CLI writes the header."""
    sid, out = tmp_path / "t.sid", tmp_path / "t.h"
    sid.write_bytes(psid)
    gen.main([str(sid), str(out)])
    assert out.read_text(encoding="ascii") == gen.generate(psid)
