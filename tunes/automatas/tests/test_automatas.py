"""The C translation against the original, per SID write and play call."""

import pytest

from automatas_support import BUILD, FIX, SID, build_prg
from vagrant_moses.memmap import PAL

import sidtrace

ARRANGER_POS = range(0x10EB, 0x10EC)
VARIANT_CALLS = 30000


def _wraps(psid, calls):
    """Play calls at which the arranger position moves back."""
    pos = []
    sidtrace.trace_psid(psid, calls, ARRANGER_POS, lambda f, _, v: pos.append((f, v)))
    return [f for (_, a), (f, b) in zip(pos, pos[1:]) if b <= a]


def test_cia_rate(psid):
    """Play calls run at the CIA 1 timer A period init sets."""
    assert sidtrace.play_rate(psid) == PAL.clock_hz / 0x999


def test_song_identical(capsys):
    """Every SID write matches the original over the HVSC song length."""
    args = [str(SID), str(BUILD / "automatas.prg")]
    assert sidtrace.main(args + ["--songlengths", str(FIX / "Songlengths.md5")]) == 0
    assert "OK: 129522 frames" in capsys.readouterr().out


def test_loop_identical(psid):
    """Every SID write matches through the arranger loop, played twice."""
    calls = _wraps(psid, 300_000)[1] + 1000
    ref = sidtrace.trace_psid(psid, calls)
    assert (
        sidtrace.diff(ref, sidtrace.trace_prg(BUILD / "automatas.prg", calls)) is None
    )


def _patched(psid, patches):
    data = bytearray(psid)
    for addr, value in patches.items():
        data[addr - 0x0FD0 + 0x7E] = value
    return bytes(data)


VARIANTS = {
    "semitone_route": ({0x2CC9: 0x05, 0x2CCC: 0x70}, {}),
    "semitone_high": ({0x2CC9: 0x7F, 0x2D84: 0x6A}, {}),
    "detune_carry": ({0x1081: 0xF0}, {}),
    "sid8580": ({}, {"sid_model": "8580"}),
}


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_variant_identical(name, psid, tmp_path):
    """Patched songs and an 8580 exercise paths the tune does not use."""
    patches, machine = VARIANTS[name]
    variant = _patched(psid, patches)
    prg = build_prg(variant, tmp_path)
    ref = sidtrace.trace_psid(variant, VARIANT_CALLS, **machine)
    assert sidtrace.diff(ref, sidtrace.trace_psid(psid, VARIANT_CALLS)) is not None
    got = sidtrace.trace_prg(prg, VARIANT_CALLS, **machine)
    assert sidtrace.diff(ref, got) is None


def test_mismatch_reported(psid, capsys):
    """A differing song is reported as a mismatch."""
    variant = _patched(psid, VARIANTS["semitone_route"][0])
    path = BUILD / "variant.sid"
    path.write_bytes(variant)
    args = [str(path), str(BUILD / "automatas.prg"), "--frames", "20000"]
    assert sidtrace.main(args) == 1
    assert "MISMATCH" in capsys.readouterr().out
