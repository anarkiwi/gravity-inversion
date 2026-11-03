"""Tests for the generic parts of tools/sidtrace.py."""

import hashlib

import pytest

import sidtrace


def _psid(version=2, extra=b""):
    head = bytearray(0x7C)
    head[:4] = b"PSID"
    head[5] = version
    head[0x7A : 0x7A + len(extra)] = extra
    return bytes(head)


def test_sid_ranges():
    """Second and third SID addresses come from a v3/v4 header."""
    assert sidtrace.sid_ranges(_psid()) == [range(0xD400, 0xD41D)]
    assert sidtrace.sid_ranges(_psid(4, b"\x42\x50"))[1:] == [
        range(0xD420, 0xD43D),
        range(0xD500, 0xD51D),
    ]


def test_parse_labels():
    """ACME label dumps parse to addresses."""
    text = "\tfoo\t= $1000\t; ?\n\tbar\t= $14a2\t; unused\n"
    assert sidtrace.parse_labels(text) == {"foo": 0x1000, "bar": 0x14A2}


def test_parse_songs():
    """Subtune lists and ranges."""
    assert sidtrace.parse_songs("0,3-5") == [0, 3, 4, 5]


def test_songlength(tmp_path):
    """HVSC lengths, with optional milliseconds and attributes, become frames."""
    data = b"tune"
    md5 = hashlib.md5(data).hexdigest()
    path = tmp_path / "Songlengths.md5"
    path.write_text(f"; x\n{md5}=1:02 0:00.5(G)\n", encoding="latin-1")
    assert sidtrace.songlength(path, data, 0) == 3100
    assert sidtrace.songlength(path, data, 1) == 25
    with pytest.raises(KeyError):
        sidtrace.songlength(path, b"other", 0)


def test_diff():
    """First differing write is located, including short frames."""
    ref = [[(1, 2)], [(3, 4), (5, 6)]]
    assert sidtrace.diff(ref, ref) is None
    assert sidtrace.diff(ref, [[(1, 2)], [(3, 4), (5, 7)]]) == (1, 1, (5, 6), (5, 7))
    assert sidtrace.diff(ref, [[(1, 2)], [(3, 4)]]) == (1, 1, (5, 6), None)


def _code_psid(code, speed=0):
    """PSID with `code` at $1000: init $1000, play $1000, one subtune."""
    head = bytearray(0x7C)
    head[:4] = b"PSID"
    head[5] = 2
    head[6:16] = bytes([0, 0x7C, 0x10, 0, 0x10, 0, 0x10, 0, 0, 1])
    head[0x11] = 1
    head[0x12:0x16] = speed.to_bytes(4, "big")
    return bytes(head) + bytes(code)


CIA_INIT = [0xA2, 0x98, 0x8E, 0x04, 0xDC, 0xA0, 0x09, 0x8C, 0x05, 0xDC, 0x60]


def test_play_rate():
    """VBI tunes play at 50 Hz, CIA tunes at the timer A period init sets."""
    assert sidtrace.play_rate(_code_psid(CIA_INIT)) == 50
    assert sidtrace.play_rate(_code_psid(CIA_INIT, 1)) == sidtrace.PAL_CLOCK / 0x999
    assert sidtrace.play_rate(_code_psid([0x60], 1)) == sidtrace.PAL_CLOCK / 0x4026


def test_songlength_cia(tmp_path):
    """HVSC lengths of CIA tunes become play calls at the tune's rate."""
    data = _code_psid(CIA_INIT, 1)
    path = tmp_path / "Songlengths.md5"
    path.write_text(f"{hashlib.md5(data).hexdigest()}=0:10\n", encoding="latin-1")
    assert sidtrace.songlength(path, data, 0) == round(10 * sidtrace.PAL_CLOCK / 0x999)


def test_raster_and_poke():
    """Init may wait for a raster line; poked RAM is read back."""
    wait = [0xA9, 0xFC, 0xCD, 0x12, 0xD0, 0xD0, 0xFB]  # lda #$fc: cmp $d012: bne
    copy = [0xAD, 0x1B, 0xD4, 0x8D, 0x00, 0xD4, 0x60]  # lda $d41b: sta $d400: rts
    data = _code_psid(wait + copy)
    assert sidtrace.trace_psid(data, 0, poke={0xD41B: 7}) == [[(0xD400, 7)]]
