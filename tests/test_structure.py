"""Generated C keeps the original's per-voice routine: no voice-specific constant SID writes.

Registers listed per tune are written only by its voice code (the hand translation writes
them through a voice index); a constant write to one of them is a voice copy or a call
specialised per voice. tunes/*/README.md describe the original's voice structure."""

import pathlib
import re

import pytest

from transliterate import transliterate

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIX = ROOT / "fixtures"

TUNES = {
    "commando": (ROOT / "tunes/commando/build/ref.sid", (0xD407, 0xD40E), None),
    "incoherent": (
        FIX / "Incoherent_Nightmare_tune_2.sid",
        (0xD40E, 0xD413, 0xD414),
        "voice 3 falls through into $0996 (and init into $0922): copy specialised to X=$0E",
    ),
    "automatas": (
        FIX / "Automatas.sid",
        (0xD409, 0xD410),
        "unrolled voice copies $1022/$1053/$1084, $112A/$11B2/$123A, $12BE+$31k",
    ),
    "ghouls": (
        FIX / "Ghouls_n_Ghosts.sid",
        (0xD407, 0xD40E),
        "unrolled voice copies $6234/$6421/$6610 and their command handlers",
    ),
    "emomyst": (
        FIX / "Emomyst.sid",
        (0xD413, 0xD414),
        "JSR $124D with X=$0E/$07/$00: calls specialised per voice",
    ),
}


def _case(name):
    _, _, missed = TUNES[name]
    marks = [pytest.mark.xfail(strict=True, reason=missed)] if missed else []
    return pytest.param(name, marks=marks)


@pytest.mark.slow
@pytest.mark.parametrize("name", [_case(n) for n in TUNES])
def test_voice_routine_recovered(name):
    """Voice registers are written through the voice index, as in the hand translation."""
    psid, regs, _ = TUNES[name]
    c, _, _ = transliterate(psid.read_bytes(), "t.h")
    found = {int(a, 16) for a in re.findall(r"io_write\(0x([0-9A-F]{4})\b", c)}
    assert not found & set(regs), sorted(hex(a) for a in found & set(regs))
