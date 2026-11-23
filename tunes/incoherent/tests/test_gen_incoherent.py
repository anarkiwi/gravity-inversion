"""Tests for gen_incoherent.py."""

import re

import gen_incoherent
import sidtrace
from tunetest import array, define


def test_data_is_byte_identical(psid):
    """DATA is the original image from DATA_ORG to its end."""
    header = gen_incoherent.generate(psid)
    load, _, _, image = sidtrace.psid_parts(psid)
    data = array(header, "DATA")
    assert int(define(header, "DATA_ORG"), 16) == 0x0CCF
    assert data == image[0x0CCF - load :]


def test_state_init(psid):
    """State the player keeps in its image comes from the image, per voice."""
    header = gen_incoherent.generate(psid)
    voices = re.findall(r"\{([^{}]*)\}", define(header, "VOICE_INIT"))
    fields = [dict(f.split("=") for f in v.split(",")) for v in voices]
    assert [v[".order"] for v in fields] == ["0x0", "0x1", "0x2"]
    assert [v[".gate"] for v in fields] == ["0xff"] * 3
    assert fields[0][".freq"] == "0x343"
    assert define(header, "FILTER_INIT").startswith(
        "{.ptr=0x9f,.time=0x0,.cutoff=0x40,"
    )
    assert define(header, "INIT_FUNKTEMPO") == "{0x08,0x09}"
    assert [define(header, k) for k in ("INIT_SONG", "INIT_TEMPO", "HR_LIMIT")] == [
        "0xff",
        "0x05",
        "0x2b",
    ]
