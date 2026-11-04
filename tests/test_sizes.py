"""Hand and generated PRGs within the size bounds relative to the original 6502 (tools/sizes.py)."""

import functools
import os
import pathlib
import subprocess

import pytest

from constructs import ROOT, load
from transliterate import build
import sizes

TUNES = ("commando", "incoherent", "automatas", "ghouls", "emomyst")
DRIVER = ROOT / "c64"
CFLAGS = {"automatas": ["-DFRAME_CIA"]}
RD = "rd() dispatch for pointer reads"
DUMMY = "the 6502's dummy reads (page crossings, indexed I/O stores)"
WR = "rd()/wr() dispatch and code store checks where indices are unbounded"
STRUCTS = "register files the split-off functions return as structs"
CODE = "CODE keeps the instruction bytes the player never reads as data"
STACK = "the stack page is a data region"
BITMAP = "the CODE_DATA bitmap"
MISSED = {
    (
        "hand",
        "ghouls",
        "data",
    ): "ram[] reserves the image up to $A000, more than the original writes",
    (
        "hand",
        "automatas",
        "data",
    ): "voice state is copied out of the code image as well as kept in DATA",
    ("generated", "commando", "code"): f"{RD} and {DUMMY}",
    ("generated", "incoherent", "code"): f"{RD}, {DUMMY} and {STRUCTS}",
    ("generated", "automatas", "code"): f"{WR} and {DUMMY}",
    ("generated", "ghouls", "code"): f"{WR} and {DUMMY}",
    ("generated", "emomyst", "code"): f"{WR}, {DUMMY} and {STRUCTS}",
    ("generated", "commando", "data"): f"{CODE}, and R1 spans bytes past the image "
    "nothing writes",
    ("generated", "incoherent", "data"): f"{CODE} and {STACK}",
    ("generated", "automatas", "data"): f"{CODE}, with {BITMAP}, and {STACK}",
    ("generated", "emomyst", "data"): f"{CODE}, with {BITMAP}, and {STACK}",
}


def _cc(out, source, include, flags=()):
    cc = os.environ.get("CC", "mos-c64-clang")
    subprocess.run(
        [cc, "-Os", *flags, f"-I{include}", f"-I{DRIVER}", "-o", out, source]
        + [DRIVER / "driver.c"],
        check=True,
    )
    return pathlib.Path(f"{out}.elf")


@pytest.fixture(scope="module", name="runtime")
def fixture_runtime(tmp_path_factory):
    """.text of a player that does nothing."""
    out = tmp_path_factory.mktemp("empty")
    src = out / "empty.c"
    src.write_text(
        '#include "driver.h"\nvoid tune_init(uint8_t s) { (void)s; }\n'
        "void tune_play(void) {}\n",
        encoding="ascii",
    )
    return sizes.sections(_cc(out / "empty.prg", src, out))[0][".text"]


@functools.cache
def _bounds(tune):
    return sizes.bounds(tune)


def _elf(kind, tune, tmp):
    if kind == "hand":
        return ROOT / "tunes" / tune / "build" / f"{tune}.prg.elf"
    psid = (ROOT / load(tune)["psid"]).read_bytes()
    return pathlib.Path(f"{build(psid, tmp, flags=CFLAGS.get(tune, ()))[0]}.elf")


def _cases():
    return [
        pytest.param(
            k,
            t,
            m,
            marks=(
                [pytest.mark.xfail(strict=True, reason=MISSED[k, t, m])]
                if (k, t, m) in MISSED
                else []
            ),
            id=f"{k}-{t}-{m}",
        )
        for k in ("hand", "generated")
        for t in TUNES
        for m in ("code", "data", "top")
    ]


@pytest.mark.slow
@pytest.mark.parametrize("kind,tune,measure", _cases())
def test_size(kind, tune, measure, runtime, tmp_path):
    """Code within 2.5x the original's instructions, data within its footprint, below $D000."""
    code, data, top = sizes.usage(_elf(kind, tune, tmp_path), runtime)
    code_bound, data_bound = _bounds(tune)
    got, bound = {
        "code": (code, code_bound),
        "data": (data, data_bound),
        "top": (top, sizes.TOP),
    }[measure]
    assert got <= bound, (got, bound)


def test_main(capsys, runtime):
    """The CLI reports a built PRG against its bounds."""
    elf = ROOT / "tunes" / "commando" / "build" / "commando.prg.elf"
    assert sizes.main(["commando", str(elf), "--runtime", str(runtime)]) == 0
    assert capsys.readouterr().out.startswith("commando: code ")
