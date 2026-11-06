"""The hand and generated C translations against the original, per SID write and play call."""

import functools

import pytest

from automatas_support import BUILD, FIX, SID, build_prg, gen_prg
from vagrant_moses.memmap import PAL

import sidtrace
import transliterate

ARRANGER_POS = range(0x10EB, 0x10EC)
VARIANT_CALLS = 30000
PRGS = {"hand": BUILD / "automatas.prg", "generated": BUILD / "gen.prg"}
BUILDERS = {"hand": build_prg, "generated": gen_prg}


def _wraps(psid, calls):
    """Play calls at which the arranger position moves back."""
    pos = []
    sidtrace.trace_psid(psid, calls, ARRANGER_POS, lambda f, _, v: pos.append((f, v)))
    return [f for (_, a), (f, b) in zip(pos, pos[1:]) if b <= a]


def test_cia_rate(psid):
    """The HVSC length (5:23) spans play calls at the CIA 1 timer A period init sets."""
    assert sidtrace.song_calls(psid, 0, FIX) == int(323 * PAL.clock_hz / 0x999)


def test_song_identical(capsys):
    """Every SID write matches the original over the HVSC song length."""
    args = [str(SID), str(BUILD / "automatas.prg")]
    assert sidtrace.main(args + ["--hvsc", str(FIX)]) == 0
    assert "OK: 129521 frames" in capsys.readouterr().out


@pytest.mark.parametrize("kind", PRGS)
def test_loop_identical(kind, psid):
    """Every SID write matches through the arranger loop, played twice."""
    calls = _wraps(psid, 300_000)[1] + 1000
    ref = sidtrace.trace_psid(psid, calls)
    assert sidtrace.diff(ref, sidtrace.trace_prg(PRGS[kind], calls)) is None


@functools.cache
def _base_c(psid):
    return transliterate.transliterate(psid, "gen.h")[0]


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


@pytest.mark.parametrize("kind", BUILDERS)
@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_variant_identical(name, kind, psid, tmp_path):
    """Patched songs and an 8580 exercise paths the tune does not use."""
    patches, machine = VARIANTS[name]
    variant = _patched(psid, patches)
    prg = BUILDERS[kind](variant, tmp_path)
    ref = sidtrace.trace_psid(variant, VARIANT_CALLS, **machine)
    assert sidtrace.diff(ref, sidtrace.trace_psid(psid, VARIANT_CALLS)) is not None
    got = sidtrace.trace_prg(prg, VARIANT_CALLS, **machine)
    assert sidtrace.diff(ref, got) is None


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_variant_c_identical(name, psid):
    """A patched song generates the original's .c: song data is only in the .h."""
    variant = _patched(psid, VARIANTS[name][0])
    assert transliterate.transliterate(variant, "gen.h")[0] == _base_c(psid)


def test_mismatch_reported(psid, capsys):
    """A differing song is reported as a mismatch."""
    variant = _patched(psid, VARIANTS["semitone_route"][0])
    path = BUILD / "variant.sid"
    path.write_bytes(variant)
    args = [str(path), str(BUILD / "automatas.prg"), "--frames", "20000"]
    assert sidtrace.main(args) == 1
    assert "MISMATCH" in capsys.readouterr().out
