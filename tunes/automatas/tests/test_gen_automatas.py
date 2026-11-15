"""Tests for gen_automatas.py."""

import re

import pytest

import gen_automatas
import sidtrace


def _define(header, name):
    return re.search(rf"#define {name} (.*)", header)[1]


def _bytes(header, name):
    return bytes(int(b, 16) for b in _define(header, name).split(","))


def test_data_is_byte_identical(psid):
    """Each held array is the image's bytes at its addresses: the word table, the code
    bytes read past it, and TABLES_ORG to the image's end."""
    header = gen_automatas.generate(psid)
    load, _, _, image = sidtrace.psid_parts(psid)
    held = gen_automatas.held(psid)
    assert held["words"] == range(0x1554, 0x168C)
    assert held["tables"] == range(0x177F, load + len(image))
    assert int(_define(header, "WORDS_ORG"), 16) == 0x1554
    assert int(_define(header, "TABLES_ORG"), 16) == 0x177F
    addrs = [int(a, 16) for a in _define(header, "CODE_READ_ADDRS").split(",")]
    assert addrs == list(held["code_read"])
    for name, macro in (("words", "WORDS"), ("code_read", "CODE_READ")):
        assert _bytes(header, macro) == bytes(image[a - load] for a in held[name])
    assert _bytes(header, "TABLES") == image[0x177F - load :]


def test_voice_init(psid):
    """Voice state held in the player's code comes from the image."""
    header = gen_automatas.generate(psid)
    voices = re.findall(r"\{([^}]*)\}", _define(header, "VOICE_INIT"))
    fields = [dict(f.split("=") for f in v.split(",")) for v in voices]
    assert [v[".route_or"] for v in fields] == ["0x1", "0x2", "0x4"]
    assert [v[".route_and"] for v in fields] == ["0xfe", "0xfd", "0xfb"]
    assert [v[".detune"] for v in fields] == ["0x0", "0x1", "0x2"]
    assert _define(header, "CUT_SUB") == "0"


def test_cut_opcodes_checked(psid):
    """Cutoff opcodes that disagree are an error."""
    data = bytearray(psid)
    data[0x10BF - 0x0FD0 + 0x7E] = 0xE9
    with pytest.raises(AssertionError):
        gen_automatas.generate(bytes(data))
    data[0x10B8 - 0x0FD0 + 0x7E] = 0xE9
    assert _define(gen_automatas.generate(bytes(data)), "CUT_SUB") == "1"


def test_main(psid, tmp_path):
    """CLI writes the header."""
    sid, out = tmp_path / "t.sid", tmp_path / "t.h"
    sid.write_bytes(psid)
    gen_automatas.main([str(sid), str(out)])
    assert out.read_text(encoding="ascii") == gen_automatas.generate(psid)
