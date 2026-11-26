"""Tests for the generic parts of tools/sidtrace.py."""

import hashlib
import pathlib

import numpy as np
import pytest
from pysidtracker.header import write_psid
from pysidtracker.survey import hvsc_capture
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
    """Detailed writes carry the storing instruction's pc and cycle from the call's start."""
    code = [0xA9, 0x01, 0x8D, 0x00, 0xD4, 0xEA, 0x8D, 0x01, 0xD4, 0x60]
    data = _code_psid(code)
    assert sidtrace.trace_psid(data, 1, detail=True)[1] == [
        (0, None, 0x1002, 1, 2),
        (1, None, 0x1006, 1, 8),
    ]
    assert sidtrace.trace_psid(data, 1)[1] == [(0, 1), (1, 1)]


def _overrun_code():
    """An indexed load, an indirect load and an indexed store leaving $1000-$10FF, then a
    plain read of the stored byte, each value written to the SID."""
    return [
        *(0xA2, 0x10),  # $1000 ldx #$10
        *(0xBD, 0xF8, 0x10),  # $1002 lda $10f8,x ($1108)
        *(0x8D, 0x00, 0xD4),  # $1005 sta $d400
        *(0xA0, 0x05),  # $1008 ldy #5
        *(0xB1, 0xFB),  # $100a lda ($fb),y ($2005)
        *(0x8D, 0x01, 0xD4),  # $100c sta $d401
        *(0xA9, 0x33),  # $100f lda #$33
        *(0x9D, 0xF8, 0x10),  # $1011 sta $10f8,x ($1108)
        *(0xAD, 0x08, 0x11),  # $1014 lda $1108
        *(0x8D, 0x02, 0xD4),  # $1017 sta $d402
        0x60,
    ]


def test_overrun_remap():
    """Under the overrun rule a site's accesses outside its object read 0 and write nothing;
    its pointer bytes, other instructions and accesses inside the object are the machine's.
    """
    data = _code_psid(_overrun_code())
    poke = {0x1108: 0x77, 0x2005: 0x66, 0xFB: 0x00, 0xFC: 0x20}
    obj = [[0x1000, 0x10FF]]
    sites = [
        {"pc": 0x1002, "ok": obj, "addr": [0x10F8, 0x11F7]},
        {"pc": 0x100A, "ok": obj, "addr": [0x0000, 0xFFFF]},
        {"pc": 0x1011, "ok": obj, "addr": [0x10F8, 0x11F7]},
    ]

    def run(s):
        return sidtrace.trace_psid(data, 0, poke=poke, remap=sidtrace.overrun_remap(s))

    assert run(()) == [[(0, 0x77), (1, 0x66), (2, 0x33)]]
    assert run(sites) == [[(0, 0), (1, 0), (2, 0x77)]]
    inside = [{**s, "ok": [[0x0000, 0xFFFF]]} for s in sites]
    assert run(inside) == run(())


def test_overrun_remap_last():
    """A site with `last` reads the byte there instead of 0 outside its object."""
    data = _code_psid(_overrun_code())
    poke = {0x1108: 0x77, 0x10FF: 0x55, 0x2005: 0x66, 0xFB: 0x00, 0xFC: 0x20}
    obj = [[0x1000, 0x10FF]]
    sites = [
        {"pc": 0x1002, "ok": obj, "addr": [0x10F8, 0x11F7], "last": 0x10FF},
        {"pc": 0x100A, "ok": obj, "addr": [0x0000, 0xFFFF]},
    ]
    got = sidtrace.trace_psid(data, 0, poke=poke, remap=sidtrace.overrun_remap(sites))
    assert got == [[(0, 0x55), (1, 0), (2, 0x33)]]


def test_overrun_remap_indexed_pointer():
    """A (zp,X) site reads its pointer bytes at operand + X as they are, then the access
    outside its object its `last` byte."""
    code = [0xA2, 0x02, 0xA1, 0xFB, 0x8D, 0x00, 0xD4, 0x60]  # ldx #2: lda ($fb,x): sta
    poke = {0xFD: 0x00, 0xFE: 0x30, 0x3000: 0x42, 0x3001: 0x24, 0x2000: 0x99}
    site = {"pc": 0x1002, "addr": [0x0000, 0xCFFF], "ok": [[0x3000, 0x3000]]}
    data = _code_psid(code)
    for ok, want in (([[0x3000, 0x3000]], 0x42), ([[0x3001, 0x3001]], 0x99)):
        remap = sidtrace.overrun_remap([{**site, "ok": ok, "last": 0x2000}])
        assert sidtrace.trace_psid(data, 0, poke=poke, remap=remap) == [[(0, want)]]


def test_effects(tmp_path):
    """A site takes effect where the original accesses outside its object: from init (frame
    0), once per call, at the addresses it took; it hides song data only where those are
    song data, and every effect counts where the lift gives none."""
    data = _code_psid(_overrun_code())
    obj = [[0x1000, 0x10FF]]
    sites = [
        {"pc": 0x1002, "ok": obj, "addr": [0x10F8, 0x11F7]},
        {"pc": 0x1011, "ok": [[0x1100, 0x11FF]], "addr": [0x10F8, 0x11F7]},
    ]
    found = sidtrace.effects(data, 2, sites)
    assert list(found) == [0x1002] and found[0x1002][:2] == (0, 3)
    assert list(np.flatnonzero(found[0x1002][2])) == [0x1108]
    song = tmp_path / "song.json"
    song.write_text("[[4352, 4607]]", encoding="utf-8")
    lost = sidtrace.hidden(sites, found, sidtrace.read_song_data(str(song)))
    assert [(s["pc"], list(a)) for s, _, _, a in lost] == [(0x1002, [0x1108])]
    song.write_text("[[8192, 8447]]", encoding="utf-8")
    assert not sidtrace.hidden(sites, found, sidtrace.read_song_data(str(song)))
    song.write_text("null", encoding="utf-8")
    assert sidtrace.read_song_data(str(song)) is None
    assert len(sidtrace.hidden(sites, found, None)) == 1
    assert sidtrace.read_song_data(None) is None


def test_overrun_sources():
    """Outside its runs a site reads 0, or its `last` address, or the last record of the
    nearest run below, repeated; its runs and I/O read as they stand."""
    site = {
        "pc": 1,
        "addr": [0x2000, 0x2020],
        "ok": [[0x2000, 0x2005], [0x2010, 0x2013]],
    }
    zero = sidtrace.overrun_sources(site)
    assert zero[0x2006] == -2 and zero[0x2003] == zero[0x2021] == -1
    clamp = sidtrace.overrun_sources({**site, "last": [[0x2004, 2], [0x2013, 1]]})
    got = [int(clamp[a]) for a in (0x2006, 0x2007, 0x200F, 0x2014, 0x2020)]
    assert got == [0x2004, 0x2005, 0x2005, 0x2013, 0x2013]
    assert (
        sidtrace.overrun_sources({**site, "last": 0x2002})[0x2006:0x2010] == 0x2002
    ).all()
    io = sidtrace.overrun_sources({"pc": 1, "addr": [0xCFFF, 0xD001], "ok": []})
    assert io[0xCFFF] == -2 and io[0xD000] == -1


CAPTURES = pathlib.Path("/scratch/datasets/hvsc/C64Music")


@pytest.mark.skipif(not CAPTURES.exists(), reason="no HVSC captures")
def test_against():
    """The original renders as its capture."""
    fixtures = pathlib.Path(__file__).resolve().parents[1] / "fixtures"
    data = (fixtures / "Emomyst.sid").read_bytes()
    path = hvsc_capture(data, 0, fixtures, CAPTURES)
    assert path is not None and sidtrace.against(path, data, 1) is None


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
