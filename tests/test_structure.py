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
    "incoherent": "gotos beyond the map: $089F/$08AA/$08DC → $08DE, $08A2/$08C5/$08DC → $08EE, "
    "$095A → $095D, $096A → $097D, $0A83 → $0A93, $0A88 → $0AB3, $0C3E → $0AC7, "
    "$0B02 → $0B09, $08D4/$08EB → $0B1B, $0871/$0C4C → $0B1E, $0AB6 → $0B21, "
    "$0B90/$0BAA → $0BC0, $0B71/$0BA0 → $0BF6",
    "emomyst": "gotos beyond the map: $11DB → $11F4, $125A → $1268, $130B → $1378, "
    "$1319/$13C6/$13ED → $1520, $1332 → $1337, $13CB → $13E0, $13E2 → $13E3, $14C1 → $14D1, "
    "$14CF → $14D7, $1727 → $1760, $1817 → $181A, $1829 → $182C, $1B25 → $1AFD; map gotos "
    "not printed: $1151 → $115B (init's, folded at lift time)",
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
    """The generated C has a goto exactly at each edge of the map's goto entries."""
    gotos = {tuple(e) for e in generated(name)[1]["provenance"]["gotos"]}
    mapped = {
        tuple(e)
        for c in constructs.load(name)["constructs"]
        if c["kind"] == "goto"
        for e in c["edges"]
    }
    assert gotos == mapped, (sorted(gotos - mapped), sorted(mapped - gotos))
