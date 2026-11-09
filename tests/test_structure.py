"""Generated C keeps the structure of the hand translations: voice routines, procedures, gotos.

Voice: registers written only by voice code go through the voice index, not per-voice constants.
Procedures: no more functions than the hand C (outlined tails fragment procedures).
Gotos: exactly the edges of the construct map's `goto` entries, the original's edges with no
structured form (docs/constructs.md, Rule)."""

import functools
import pathlib
import re

import pytest

import constructs
from transliterate import transliterate

ROOT = pathlib.Path(__file__).resolve().parent.parent
TUNES = ("commando", "incoherent", "automatas", "ghouls", "emomyst")
VOICE_REGS = {
    "commando": (0xD407, 0xD40E),
    "incoherent": (0xD40E, 0xD413, 0xD414),
    "automatas": (0xD409, 0xD410),
    "ghouls": (0xD407, 0xD40E),
    "emomyst": (0xD407, 0xD40E),
}
MISSED_VOICES = {}
FRAGMENTED = {}
OTHER_GOTOS = {
    "emomyst": "$1B25 → $1AFD, a jump back into $1AFD from command code the song never runs, "
    "which the run's listing decodes as data, so the map cannot name the edge",
}


@functools.cache
def generated(name):
    """(c text, info) of the tune's generated translation."""
    c, _, info = transliterate(
        (ROOT / constructs.load(name)["psid"]).read_bytes(), "t.h"
    )
    return c, info


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
        for n in TUNES
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
    assert generated(name)[1]["structure"]["functions"] <= functions


@pytest.mark.slow
@pytest.mark.parametrize("name", _cases(OTHER_GOTOS))
def test_unstructured_edges(name):
    """The generated C has a goto exactly at each edge of the map's goto entries from the code
    it translates (init's run on the host)."""
    info = generated(name)[1]
    gotos, starts = {tuple(e) for e in info["provenance"]["gotos"]}, set(info["starts"])
    mapped = {
        tuple(e)
        for c in constructs.load(name)["constructs"]
        if c["kind"] == "goto"
        for e in c["edges"]
        if e[0] in starts
    }
    assert gotos == mapped, (sorted(gotos - mapped), sorted(mapped - gotos))
