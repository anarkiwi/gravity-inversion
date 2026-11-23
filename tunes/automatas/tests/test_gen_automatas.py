"""Tests for gen_automatas.py."""

import re

import pytest

import gen_automatas
import sidtrace
from tunetest import array, define


def test_data_is_byte_identical(psid):
    """Each held array is the image's bytes at its addresses: the word table, the code
    bytes read past it, and TABLES_ORG to the image's end."""
    header = gen_automatas.generate(psid)
    load, _, _, image = sidtrace.psid_parts(psid)
    held = gen_automatas.held(psid)
    assert held["words"] == range(0x1554, 0x168C)
    assert held["tables"] == range(0x177F, load + len(image))
    assert int(define(header, "WORDS_ORG"), 16) == 0x1554
    assert int(define(header, "TABLES_ORG"), 16) == 0x177F
    addrs = [int(a, 16) for a in define(header, "CODE_READ_ADDRS").split(",")]
    assert addrs == list(held["code_read"])
    for name, macro in (("words", "WORDS"), ("code_read", "CODE_READ")):
        assert array(header, macro) == bytes(image[a - load] for a in held[name])
    assert array(header, "TABLES") == image[0x177F - load :]


def test_voice_init(psid):
    """Voice state held in the player's code comes from the image."""
    header = gen_automatas.generate(psid)
    voices = re.findall(r"\{([^}]*)\}", define(header, "VOICE_INIT"))
    fields = [dict(f.split("=") for f in v.split(",")) for v in voices]
    assert [v[".route_or"] for v in fields] == ["0x1", "0x2", "0x4"]
    assert [v[".route_and"] for v in fields] == ["0xfe", "0xfd", "0xfb"]
    assert [v[".detune"] for v in fields] == ["0x0", "0x1", "0x2"]
    assert define(header, "CUT_SUB") == "0"


def test_cut_opcodes_checked(psid):
    """Cutoff opcodes that disagree are an error."""
    data = sidtrace.patched(psid, {0x10BF: 0xE9})
    with pytest.raises(AssertionError):
        gen_automatas.generate(data)
    data = sidtrace.patched(data, {0x10B8: 0xE9})
    assert define(gen_automatas.generate(data), "CUT_SUB") == "1"
