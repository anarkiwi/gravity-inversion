"""Generated C keeps the structure of the hand translations: voice routines, procedures, gotos.

Voice: registers written only by voice code go through the voice index, not per-voice constants.
Procedures: no more functions than the hand C (outlined tails fragment procedures).
Gotos: the hand C has a goto at each original edge with no structured C form (tune READMEs);
gotos plus structuring flags stay within those edges."""

import functools
import pathlib
import re

import pytest

from transliterate import transliterate

ROOT = pathlib.Path(__file__).resolve().parent.parent
TUNE = {
    "commando": "tunes/commando/build/ref.sid",
    "incoherent": "fixtures/Incoherent_Nightmare_tune_2.sid",
    "automatas": "fixtures/Automatas.sid",
    "ghouls": "fixtures/Ghouls_n_Ghosts.sid",
    "emomyst": "fixtures/Emomyst.sid",
}
VOICE_REGS = {
    "commando": (0xD407, 0xD40E),
    "incoherent": (0xD40E, 0xD413, 0xD414),
    "automatas": (0xD409, 0xD410),
    "ghouls": (0xD407, 0xD40E),
    "emomyst": (0xD413, 0xD414),
}
MISSED_VOICES = {
    "incoherent": "voice 3 falls through into $0996 (init into $0922): copy specialised "
    "to X=$0E",
    "automatas": "unrolled voice copies $1022/$1053/$1084, $112A/$11B2/$123A, $12BE+$31k",
    "ghouls": "unrolled voice copies $6234/$6421/$6610 and their command handlers",
    "emomyst": "JSR $124D with X=$0E/$07/$00: calls specialised per voice",
}
FRAGMENTED = {
    "incoherent": "joins outlined as functions, e.g. $0BED (hand read_row next:) and $0BF6 "
    "(hand voice_play regs:)",
    "automatas": "each voice copy's joins outlined separately",
    "ghouls": "each voice copy's and command handler's joins outlined separately",
    "emomyst": "joins outlined as functions where the hand C uses labels "
    "(wf_arp, vib_slide, filter_prg, pw_prg)",
}
OVER_GOTOS = {
    "incoherent": "structuring flags beyond the original's unstructured edges",
}


@functools.cache
def generated(name):
    """(c text, structure counts) of the tune's generated translation."""
    c, _, info = transliterate((ROOT / TUNE[name]).read_bytes(), "t.h")
    return c, info["structure"]


def hand(name):
    """The hand translation's C text."""
    return next((ROOT / "tunes" / name / "src").glob("*.c")).read_text(encoding="ascii")


def _cases(missed):
    return [
        (
            pytest.param(n, marks=[pytest.mark.xfail(strict=True, reason=missed[n])])
            if n in missed
            else n
        )
        for n in TUNE
    ]


@pytest.mark.slow
@pytest.mark.parametrize("name", _cases(MISSED_VOICES))
def test_voice_routine_recovered(name):
    """Voice registers are written through the voice index, as in the hand translation."""
    found = {
        int(a, 16)
        for a in re.findall(r"io_write\(0x([0-9A-F]{4})\b", generated(name)[0])
    }
    assert not found & set(VOICE_REGS[name]), sorted(
        map(hex, found & set(VOICE_REGS[name]))
    )


@pytest.mark.slow
@pytest.mark.parametrize("name", _cases(FRAGMENTED))
def test_procedures_not_fragmented(name):
    """The generated player has no more functions than the hand translation."""
    functions = len(re.findall(r"^(?:static )?[a-z].*\) \{", hand(name), re.M))
    assert generated(name)[1]["functions"] <= functions


@pytest.mark.slow
@pytest.mark.parametrize("name", _cases(OVER_GOTOS))
def test_unstructured_edges(name):
    """Gotos plus structuring flags stay within the hand C's gotos, one per unstructured edge."""
    c, counts = generated(name)
    assert c.count("goto ") + counts.get("flags", 0) <= hand(name).count("goto ")
