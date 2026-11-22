"""The hand and generated C translations against the original, per SID write and play call."""

import functools

import pytest
from pysidtracker.reglog import first_divergence

from automatas_support import BUILD, FIX, SID, build_prg, gen_prg
from vagrant_moses.memmap import PAL

import footprint
import gen_automatas
import sidtrace
import transliterate

ARRANGER_POS = range(0x10EB, 0x10EC)
VARIANT_CALLS = 30000
PAT_LO, PAT_HI = 0x1A00, 0x1A80
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
    got = sidtrace.trace_prg(PRGS[kind], calls, sid_model=MODEL)
    assert first_divergence(ref, got) is None


@functools.cache
def _base_c(psid):
    return transliterate.transliterate(psid, "gen.h")[0]


MODEL = sidtrace.header_model(SID.read_bytes())
VARIANTS = {
    "semitone_route": ({0x2CC9: 0x05, 0x2CCC: 0x70}, {}),
    "semitone_high": ({0x2CC9: 0x7F, 0x2D84: 0x6A}, {}),
    "detune_carry": ({0x1081: 0xF0}, {}),
    "sid6581": ({}, {"sid_model": "6581"}),
}


def _variant_cases():
    """(name, kind): the generated C on another chip is test_generated_model_probe's."""
    return [
        (n, k)
        for n in sorted(VARIANTS)
        for k in BUILDERS
        if not (k == "generated" and VARIANTS[n][1])
    ]


@pytest.mark.parametrize("name,kind", _variant_cases())
def test_variant_identical(name, kind, psid, tmp_path):
    """Patched songs and an 8580 exercise paths the tune does not use."""
    patches, machine = VARIANTS[name]
    machine = {"sid_model": MODEL} | machine
    variant = sidtrace.patched(psid, patches)
    prg = BUILDERS[kind](variant, tmp_path)
    ref = sidtrace.trace_psid(variant, VARIANT_CALLS, **machine)
    assert first_divergence(ref, sidtrace.trace_psid(psid, VARIANT_CALLS)) is not None
    got = sidtrace.trace_prg(prg, VARIANT_CALLS, **machine)
    assert first_divergence(ref, got) is None


MACHINES = {m: {"sid_model": m} for m in ("6581", "8580")}


@pytest.mark.parametrize("model", sorted(MACHINES))
def test_generated_model_probe(model, psid):
    """Init's SID model probe runs in the generated C: on each chip it plays as the
    original does on that chip, and the chips' plays differ."""
    ref = sidtrace.trace_psid(psid, VARIANT_CALLS, **MACHINES[model])
    for other in set(MACHINES) - {model}:
        alt = sidtrace.trace_psid(psid, VARIANT_CALLS, **MACHINES[other])
        assert first_divergence(ref, alt) is not None
    got = sidtrace.trace_prg(PRGS["generated"], VARIANT_CALLS, **MACHINES[model])
    assert first_divergence(ref, got) is None


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_variant_c_identical(name, psid):
    """A patched song generates the original's .c: song data is only in the .h."""
    variant = sidtrace.patched(psid, VARIANTS[name][0])
    assert transliterate.transliterate(variant, "gen.h")[0] == _base_c(psid)


@pytest.mark.parametrize("where", ["code", "end"])
def test_outside_data_traps(where, psid, tmp_path):
    """A read outside the data the hand C holds traps: every pattern pointer at a code byte
    no read takes, or at the image's end."""
    load, _, _, image = sidtrace.psid_parts(psid)
    addr = {"code": max(gen_automatas.CODE_READ[:3]) + 1, "end": load + len(image)}[
        where
    ]
    patches = {PAT_LO + i: addr & 0xFF for i in range(0x80)}
    variant = sidtrace.patched(
        psid, patches | {PAT_HI + i: addr >> 8 for i in range(0x80)}
    )
    with pytest.raises(AssertionError, match=r"jam at \$[0-9a-f]+ \(trap 1\)"):
        sidtrace.trace_prg(build_prg(variant, tmp_path), VARIANT_CALLS)


def test_data_footprint(psid):
    """The hand C holds only the original's data, and every byte the original reads as data
    but its operand state and the voice state it names, over the song and the patched
    variants."""
    runs = [(psid, [sidtrace.song_calls(psid, 0, FIX)], [0])]
    runs += [
        (sidtrace.patched(psid, p), [VARIANT_CALLS], [0])
        for p, _ in VARIANTS.values()
        if p
    ]
    orig = footprint.Original(runs, 1)
    got = footprint.check(orig, gen_automatas.held(psid), gen_automatas.named(psid))
    assert got == footprint.CLEAN


def test_mismatch_reported(psid, capsys):
    """A differing song is reported as a mismatch."""
    variant = sidtrace.patched(psid, VARIANTS["semitone_route"][0])
    path = BUILD / "variant.sid"
    path.write_bytes(variant)
    args = [str(path), str(BUILD / "automatas.prg"), "--frames", "20000"]
    assert sidtrace.main(args) == 1
    assert "MISMATCH" in capsys.readouterr().out
