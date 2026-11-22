"""Tests for gen_emomyst.py: the generated header holds the original bytes."""

import pytest

from sidtrace import psid_parts
from tunetest import array, define


def test_generate_is_byte_identical(gen, psid):
    """TABLES and MUSIC are the loaded image at their original addresses."""
    header = gen.generate(psid)
    load, _, _, image = psid_parts(psid)
    org = int(define(header, "MUSIC_ORG"), 16)
    assert org == 0x1C00 and image[org - load : org - load + 4] == b"SWP1"
    assert array(header, "MUSIC") == image[org - load :]
    tables = array(header, "TABLES")
    assert len(tables) == gen.TABLES_END - gen.TABLES_ORG
    assert tables == image[gen.TABLES_ORG - load : gen.TABLES_END - load]
    assert f"#define TABLES_ORG 0x{gen.TABLES_ORG:04x}" in header
    addrs = define(header, "CODE_READ_ADDRS").split(",")
    assert [int(a, 16) for a in addrs] == list(gen.CODE_READ)
    assert array(header, "CODE_READ") == bytes(image[a - load] for a in gen.CODE_READ)


def test_music_org_checks_init(gen, psid):
    """The music base is read from init's LDX/LDY; other code is rejected."""
    load, init, _, image = psid_parts(psid)
    assert gen.music_org(load, init, image) == 0x1C00
    with pytest.raises(AssertionError):
        gen.music_org(load, init + 1, image)
    with pytest.raises(AssertionError):
        gen.music_org(load, init + 3, image)
