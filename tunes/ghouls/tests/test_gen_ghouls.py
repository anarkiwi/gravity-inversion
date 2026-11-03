"""Tests for gen_ghouls.py."""

import re

import gen_ghouls
import sidtrace


def _image(header):
    text = re.search(r"#define IMAGE (.*)", header)[1]
    return bytes(int(b, 16) for b in text.split(","))


def test_generate_is_byte_identical(psid):
    """The generated image is the PSID payload at its load address."""
    header = gen_ghouls.generate(psid)
    load, _, _, image = sidtrace.psid_parts(psid)
    assert _image(header) == image
    assert f"#define IMAGE_ORG 0x{load:04x}\n" in header


def test_main(tmp_path, psid):
    """CLI writes the header for a PSID file."""
    src, out = tmp_path / "t.sid", tmp_path / "t.h"
    src.write_bytes(psid)
    gen_ghouls.main([str(src), str(out)])
    assert out.read_text(encoding="ascii") == gen_ghouls.generate(psid)
