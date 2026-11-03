"""Tests for gen_incoherent.py."""

import re

import gen_incoherent
import sidtrace


def _define(header, name):
    return re.search(rf"#define {name} (.*)", header)[1]


def test_data_is_byte_identical(psid):
    """DATA is the original image from DATA_ORG to its end."""
    header = gen_incoherent.generate(psid)
    load, _, _, image = sidtrace.psid_parts(psid)
    data = bytes(int(b, 16) for b in _define(header, "DATA").split(","))
    assert int(_define(header, "DATA_ORG"), 16) == 0x0CCF
    assert data == image[0x0CCF - load :]


def test_state_init(psid):
    """State the player keeps in its image comes from the image, per voice."""
    header = gen_incoherent.generate(psid)
    voices = re.findall(r"\{([^{}]*)\}", _define(header, "VOICE_INIT"))
    fields = [dict(f.split("=") for f in v.split(",")) for v in voices]
    assert [v[".order"] for v in fields] == ["0x0", "0x1", "0x2"]
    assert [v[".gate"] for v in fields] == ["0xff"] * 3
    assert fields[0][".freq"] == "0x343"
    assert _define(header, "FILTER_INIT").startswith(
        "{.ptr=0x9f,.time=0x0,.cutoff=0x40,"
    )
    assert _define(header, "INIT_FUNKTEMPO") == "{0x08,0x09}"
    assert [_define(header, k) for k in ("INIT_SONG", "INIT_TEMPO", "HR_LIMIT")] == [
        "0xff",
        "0x05",
        "0x2b",
    ]


def test_main(psid, tmp_path):
    """CLI writes the header."""
    sid, out = tmp_path / "t.sid", tmp_path / "t.h"
    sid.write_bytes(psid)
    gen_incoherent.main([str(sid), str(out)])
    assert out.read_text(encoding="ascii") == gen_incoherent.generate(psid)
