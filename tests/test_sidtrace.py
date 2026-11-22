"""Tests for the generic parts of tools/sidtrace.py."""

import hashlib

from pysidtracker.header import write_psid
from vagrant_moses.memmap import PAL

import sidtrace

# voice 3 at full rate, every control bit then sawtooth: its osc3 read differs by SID model
OSC3 = bytes.fromhex("a9ff8d0ed48d0fd48d12d4a9208d12d4eaeaad1bd48d00d460")


def _code_psid(code, load=0x1000, **kw):
    """PSID with `code` at `load`: init and play at its start, one subtune."""
    return write_psid(load=load, init=load, play=load, image=bytes(code), **kw)


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
    data = _code_psid([0x60], songs=4)
    assert sidtrace.songs("all", data) == [0, 1, 2, 3]
    assert sidtrace.songs("1,3-4", data) == [0, 2, 3]


def test_detail():
    """Detailed writes carry the storing instruction's cycle from the call's start and pc."""
    code = [0xA9, 0x01, 0x8D, 0x00, 0xD4, 0xEA, 0x8D, 0x01, 0xD4, 0x60]
    data = _code_psid(code)
    assert sidtrace.trace_psid(data, 1, detail=True)[1] == [
        (0, 1, 2, 0x1002),
        (1, 1, 8, 0x1006),
    ]


def test_boot_ram():
    """Init starts on pysidtracker's PSID RAM: the driver's clear, the BASIC LOAD
    pointers, the PAL flag, $D418 and init's bank; play runs in play's bank."""
    data = _code_psid([0x60], load=0xC000)
    ram = sidtrace.boot_ram(data)
    assert ram[0x2D:0x2F] == ram[0xAE:0xB0] == bytes([0x01, 0xC0])
    assert ram[0xAC:0xAE] == bytes([0x00, 0xC0])
    assert (ram[1], ram[0x2A6], ram[0xD418], ram[0x200]) == (0x36, 1, 0x0F, 0)
    mpu, _, play = sidtrace.boot(data)
    assert (mpu.memory[1], play) == (0x36, 0xC000)


def test_sid_model():
    """The header names the SID model both sides of a comparison play on; a model given
    overrides it."""
    data = _code_psid(OSC3, flags=0x20)
    assert sidtrace.header_model(data) == "8580"
    assert sidtrace.header_model(_code_psid(OSC3)) == "6581"
    ref = sidtrace.trace_psid(data, 0)
    assert ref == sidtrace.trace_psid(data, 0, sid_model="8580")
    assert ref != sidtrace.trace_psid(data, 0, sid_model="6581")


def test_patched():
    """Patches address the image by load address, whatever the header's size."""
    for embed in (False, True):
        data = sidtrace.patched(_code_psid([1, 2, 3], embed_load=embed), {0x1001: 9})
        assert sidtrace.psid_parts(data)[3] == bytes([1, 9, 3])
