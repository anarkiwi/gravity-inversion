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
EXTRA_FUNCTIONS = {"brk": 1, "inlineargs": 1}
VOICE_REGS = range(0xD407, 0xD415)
MISSED = {
    (
        "functions",
        "jsr3",
        2,
    ): "the voice routine's increments at $1054/$106E/$1080, alike "
    "up to operands, fold into an indexed function",
    (
        "functions",
        "fallthrough",
        2,
    ): "the voice routine's increments at $1050/$106A/$107C, "
    "alike up to operands, fold into an indexed function",
}


def _cases(check):
    return [
        (
            pytest.param(
                s,
                seed,
                marks=pytest.mark.xfail(strict=True, reason=MISSED[check, s, seed]),
            )
            if (check, s, seed) in MISSED
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


RELOC = 0x2000


@pytest.fixture(scope="module", name="relocated")
def fixture_relocated(tmp_path_factory):
    """{(style, seed): c} of each player assembled at RELOC, translated once."""
    out = tmp_path_factory.mktemp("reloc")
    done = {}
    for s in fuzzplayer.STYLES:
        for seed in SEEDS:
            src = out / f"{s}{seed}.asm"
            text = fuzzplayer.player(s, seed)
            src.write_text(
                text.replace("ADDR = $1000", f"ADDR = ${RELOC:04x}"), "ascii"
            )
            sid = out / f"{s}{seed}.sid"
            subprocess.run(["acme", "-o", sid, src], check=True, capture_output=True)
            done[s, seed] = transliterate(sid.read_bytes(), "fuzz.h")[0]
    return done


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
    ideal = IDEAL_FUNCTIONS + EXTRA_FUNCTIONS.get(style, 0)
    assert translated[style, seed][3]["structure"]["functions"] <= ideal


@pytest.mark.parametrize("style,seed", _cases("edges"))
def test_structured(style, seed, translated):
    """A structured body needs no gotos and no structuring flags."""
    c, info = translated[style, seed][1], translated[style, seed][3]
    assert c.count("goto ") + info["structure"].get("flags", 0) == 0


@pytest.mark.parametrize("style,seed", _cases("reloc"))
def test_relocated(style, seed, translated, relocated):
    """The player assembled at another origin generates byte-identical C."""
    assert relocated[style, seed] == translated[style, seed][1]


def test_cli(tmp_path):
    """The CLI writes ACME source the assembler accepts."""
    out = tmp_path / "p.asm"
    fuzzplayer.main(["smc", "1", str(out)])
    assert "s0f" in out.read_text(encoding="ascii")
    subprocess.run(["acme", "-o", tmp_path / "p.sid", out], check=True)
