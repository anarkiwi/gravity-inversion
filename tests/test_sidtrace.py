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
