"""Tests for the generic parts of tools/sidtrace.py."""

import hashlib


from vagrant_moses.memmap import PAL

import sidtrace


def test_parse_labels():
    """ACME label dumps parse to addresses."""
    text = "\tfoo\t= $1000\t; ?\n\tbar\t= $14a2\t; unused\n"
    assert sidtrace.parse_labels(text) == {"foo": 0x1000, "bar": 0x14A2}


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


def test_raster_and_poke():
    """Init may wait for a raster line; poked RAM is read back."""
    wait = [0xA9, 0xFC, 0xCD, 0x12, 0xD0, 0xD0, 0xFB]  # lda #$fc: cmp $d012: bne
    copy = [0xAD, 0x00, 0xC0, 0x8D, 0x00, 0xD4, 0x60]  # lda $c000: sta $d400: rts
    data = _code_psid(wait + copy)
    assert sidtrace.trace_psid(data, 0, poke={0xC000: 7}) == [[(0, 7)]]


def _hvsc(tmp_path, data, lengths):
    docs = tmp_path / "DOCUMENTS"
    docs.mkdir(exist_ok=True)
    md5 = hashlib.md5(data).hexdigest()
    (docs / "Songlengths.md5").write_text(f"; x\n{md5}={lengths}\n", encoding="latin-1")
    return tmp_path


def test_song_calls(tmp_path):
    """HVSC lengths become play calls at the tune's measured cadence (PAL frames here;
    tunes/automatas checks a CIA tune)."""
    vbi = _code_psid([0x60])
    assert sidtrace.song_calls(vbi, 0, _hvsc(tmp_path, vbi, "0:10")) == int(
        10 * PAL.clock_hz / PAL.cycles_per_frame
    )


def test_songs():
    """1-based HVSC subtune specs become 0-based subtunes."""
    data = bytearray(_code_psid([0x60]))
    data[0x0F] = 4
    assert sidtrace.songs("all", bytes(data)) == [0, 1, 2, 3]
    assert sidtrace.songs("1,3-4", bytes(data)) == [0, 2, 3]
