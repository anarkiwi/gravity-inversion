"""Fuzz the transliteration with synthetic players from tools/fuzzplayer.py.

Each player dispatches one voice routine as a loop, by JSR, by fall-through, or as unrolled and
self-modifying copies. The generated C must match its SID writes exactly, and should recover the
routine, need no more than three functions, and no gotos or flags (every body is structured).
"""

import os
import pathlib
import re
import subprocess

import pytest

import fuzzplayer
import sidtrace
from transliterate import transliterate

DRIVER = pathlib.Path(__file__).resolve().parent.parent / "c64"
SEEDS = range(3)
FRAMES = 200
IDEAL_FUNCTIONS = 3
VOICE_REGS = range(0xD407, 0xD415)
MISSED = {
    "voice": {
        "fallthrough": "the fall-through voice is a copy specialised to X=14",
        "unrolled": "the unrolled copies are not recovered as one routine",
        "smc": "the self-modifying copies are not recovered as one routine",
    },
    "functions": {
        "jsr3": "joins of the voice routine are outlined as functions",
        "fallthrough": "joins outlined, per copy",
        "unrolled": "joins outlined, per copy",
        "smc": "joins outlined, per copy",
    },
}
FOLDED = {("functions", "smc", 2)}


def _cases(check):
    return [
        (
            pytest.param(
                s, seed, marks=pytest.mark.xfail(strict=True, reason=MISSED[check][s])
            )
            if s in MISSED.get(check, {}) and (check, s, seed) not in FOLDED
            else (s, seed)
        )
        for s in fuzzplayer.STYLES
        for seed in SEEDS
    ]


def _psid(style, seed, out):
    src = out / f"{style}{seed}.asm"
    src.write_text(fuzzplayer.player(style, seed), encoding="ascii")
    sid = out / f"{style}{seed}.sid"
    subprocess.run(["acme", "-o", sid, src], check=True, capture_output=True)
    return sid.read_bytes()


@pytest.fixture(scope="module", name="translated")
def fixture_translated(tmp_path_factory):
    """{(style, seed): (psid, c, h, info)}, translated once."""
    out = tmp_path_factory.mktemp("fuzz")
    done = {}
    for s in fuzzplayer.STYLES:
        for seed in SEEDS:
            psid = _psid(s, seed, out)
            done[s, seed] = (psid, *transliterate(psid, "fuzz.h"))
    return done


@pytest.mark.slow
@pytest.mark.parametrize("style,seed", _cases("exact"))
def test_exact(style, seed, translated, tmp_path):
    """The generated C, built for the C64, writes the SID as the original does."""
    psid, c, h, _ = translated[style, seed]
    (tmp_path / "fuzz.c").write_text(c, encoding="utf-8")
    (tmp_path / "fuzz.h").write_text(h, encoding="utf-8")
    prg = tmp_path / "fuzz.prg"
    subprocess.run(
        [os.environ.get("CC", "mos-c64-clang"), "-Os", f"-I{tmp_path}", f"-I{DRIVER}"]
        + ["-o", prg, tmp_path / "fuzz.c", DRIVER / "driver.c"],
        check=True,
    )
    _, writes, bad = sidtrace.compare(psid, str(prg), FRAMES, 0)
    assert bad is None and writes


@pytest.mark.parametrize("style,seed", _cases("voice"))
def test_voice_routine(style, seed, translated):
    """Voice 2 and 3 registers are written through the voice index, never as constants."""
    c = translated[style, seed][1]
    found = {int(a, 16) for a in re.findall(r"io_write\(0x([0-9A-F]{4})\b", c)}
    assert not found & set(VOICE_REGS)


@pytest.mark.parametrize("style,seed", _cases("functions"))
def test_functions(style, seed, translated):
    """No more functions than init, play and the voice routine."""
    assert translated[style, seed][3]["structure"]["functions"] <= IDEAL_FUNCTIONS


@pytest.mark.parametrize("style,seed", _cases("edges"))
def test_structured(style, seed, translated):
    """A structured body needs no gotos and no structuring flags."""
    c, info = translated[style, seed][1], translated[style, seed][3]
    assert c.count("goto ") + info["structure"].get("flags", 0) == 0


def test_cli(tmp_path):
    """The CLI writes ACME source the assembler accepts."""
    out = tmp_path / "p.asm"
    fuzzplayer.main(["smc", "1", str(out)])
    assert "s0f" in out.read_text(encoding="ascii")
    subprocess.run(["acme", "-o", tmp_path / "p.sid", out], check=True)
