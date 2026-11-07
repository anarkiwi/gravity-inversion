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
MISSED = {
    (
        "emomyst",
        "K5",
    ): "init's relocator stores each operand byte through its patch-site table: the 30 "
    "operands it patches lie scattered through the player ($1256-$1B4F), so the store's "
    "address set is one held span's, indexed by 16 bits",
}

SEEDS = range(3)
FUZZ_MISSED = {}


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


SAMPLE = """#include <stdint.h>
uint8_t rd(uint16_t a);
uint8_t R0[4];
static void f1(uint8_t v1) {
  uint8_t v2 = R0[v1];
  if ((v2 != 0) && (R0[v1] = v2 - 1, v2 == 1))
    R0[2] = rd(0x1000 + v1);
  R0[3] = v1 == 1;
}
void tune_play(void) {
  f1(0);
  f1(1);
}
"""


def test_sites_name_each_construct(tmp_path, capsys):
    """`--sites` lists each construct by line: an instance-dependent `rd()` is K1, an index
    compared as a value K3, and a comparison that is a comma expression's value inside a
    branch condition is no construct."""
    src = tmp_path / "s.c"
    src.write_text(SAMPLE, encoding="ascii")
    costs.main([str(src), "--sites"])
    out = capsys.readouterr().out.splitlines()
    assert out[:2] == ["7: K1 f1: rd(0x1000 + v1);", "8: K3 f1: v1 == 1;"]
    assert out[-1] == "total K1=1 K2=0 K3=1 K4=0 K5=0 K6=0"
