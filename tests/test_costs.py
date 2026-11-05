"""Generated C free of the constructs docs/costs.md lists as costing code size (tools/costs.py)."""

import functools
import subprocess

import pytest

from constructs import ROOT, load
from transliterate import transliterate
import fuzzplayer
import costs

TUNES = ("commando", "incoherent", "automatas", "ghouls", "emomyst")
DRIVER = ROOT / "c64"
INSTANCE = "folded copies' state addressed as base + stride * k"
DUMMY = "dummy reads of indices and pointers the analysis cannot bound"
MISSED = {
    ("automatas", "K1"): INSTANCE,
    ("automatas", "K2"): INSTANCE,
    ("automatas", "K5"): "the copies' offsets ($88 per voice) computed in 16 bits",
    ("ghouls", "K1"): INSTANCE,
    ("ghouls", "K2"): INSTANCE,
    ("ghouls", "K3"): "per-copy constants as select-sums",
    ("ghouls", "K5"): "region indices computed in 16 bits",
    ("ghouls", "K6"): DUMMY,
    ("emomyst", "K1"): "the relocator's LDA $1111,Y at $1163 reads through its written "
    "operand, called with Y = 0 and 1",
    (
        "emomyst",
        "K5",
    ): "the relocator's ($02),Y stores into the image, indexed by the 16-bit "
    "pointer",
}

SEEDS = range(3)
SIMPLE = ("loop", "jsr3", "fallthrough") + fuzzplayer.MOTIFS
FALSE_FOLD = "statements on fields $20 apart fold as a coincidental instance class"
DUMMY_FUZZ = "dummy reads of a voice index the analysis does not bound"
FUZZ_MISSED = (
    {("K2", s, 2): FALSE_FOLD for s in SIMPLE}
    | {
        ("K6", s, n): DUMMY_FUZZ
        for s, seeds in {
            "loop": (1, 2),
            "rti": (0, 1, 2),
            "brk": (0, 1, 2),
            "stackswitch": (1, 2),
            "inlineargs": (2,),
            "skip": (1, 2),
            "decimal": (2,),
            "rmw": (1, 2),
            "vflag": (1, 2),
            "indx": (0, 1, 2),
            "jam": (0, 1, 2),
        }.items()
        for n in seeds
    }
    | {("K2", "unrolled", n): INSTANCE for n in SEEDS}
    | {("K2", "smc", n): INSTANCE for n in SEEDS}
    | {("K3", "smc", n): "per-copy constants as select-sums" for n in SEEDS}
)


def _write(out, c, h):
    out.mkdir(exist_ok=True)
    (out / "gen.c").write_text(c, encoding="utf-8")
    (out / "gen.h").write_text(h, encoding="utf-8")
    return costs.totals(costs.ast(out / "gen.c", [out, DRIVER]))


@functools.cache
def _totals(tune, tmp):
    c, h, _ = transliterate((ROOT / load(tune)["psid"]).read_bytes(), "gen.h")
    return _write(tmp / tune, c, h)


@functools.cache
def _fuzz_totals(style, seed, tmp):
    out = tmp / f"{style}{seed}"
    out.mkdir(exist_ok=True)
    src = out / "p.asm"
    src.write_text(fuzzplayer.player(style, seed), encoding="ascii")
    subprocess.run(["acme", "-o", out / "p.sid", src], check=True, capture_output=True)
    c, h, _ = transliterate((out / "p.sid").read_bytes(), "gen.h")
    return _write(out, c, h)


@pytest.fixture(scope="module", name="tmp")
def fixture_tmp(tmp_path_factory):
    """A directory for the generated sources."""
    return tmp_path_factory.mktemp("costs")


def _cases():
    return [
        pytest.param(
            t,
            k,
            marks=(
                [pytest.mark.xfail(strict=True, reason=MISSED[t, k])]
                if (t, k) in MISSED
                else []
            ),
            id=f"{t}-{k}",
        )
        for t in TUNES
        for k in costs.KINDS
    ]


@pytest.mark.slow
@pytest.mark.parametrize("tune,kind", _cases())
def test_no_costly_construct(tune, kind, tmp):
    """The generated C has no construct of this kind."""
    assert _totals(tune, tmp)[kind] == 0


def _fuzz_cases():
    return [
        pytest.param(
            s,
            n,
            k,
            marks=(
                [pytest.mark.xfail(strict=True, reason=FUZZ_MISSED[k, s, n])]
                if (k, s, n) in FUZZ_MISSED
                else []
            ),
            id=f"{s}{n}-{k}",
        )
        for s in fuzzplayer.STYLES
        for n in SEEDS
        for k in costs.KINDS
    ]


@pytest.mark.slow
@pytest.mark.parametrize("style,seed,kind", _fuzz_cases())
def test_fuzz_no_costly_construct(style, seed, kind, tmp):
    """A fuzz player's generated C has no construct of this kind."""
    assert _fuzz_totals(style, seed, tmp)[kind] == 0
