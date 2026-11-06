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
WR = "rd()/wr() dispatch and code store checks where indices are unbounded"
CODE = "C0 keeps the instruction bytes the player never reads as data"
BITMAP = "the C0_DATA bitmap"
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
    ("generated", "commando", "code"): RD,
    ("generated", "incoherent", "code"): RD,
    ("generated", "automatas", "code"): WR,
    (
        "generated",
        "emomyst",
        "code",
    ): "rd() for song-data reads through zero-page pointers "
    "loaded from split tables a song byte indexes, passing 16-bit addresses, and cells init "
    "writes after its SID clear",
    ("generated", "commando", "data"): f"{CODE}, and R1 spans bytes past the image "
    "nothing writes",
    ("generated", "ghouls", "data"): f"{CODE}, and the music init's block copy ($29C6) "
    "writes each song's data to $730E and $914F-$9614: RAM regions spanning every song's copy",
    ("generated", "automatas", "data"): f"{CODE}, with {BITMAP}",
    (
        "generated",
        "emomyst",
        "data",
    ): "C0, C3 and C5 hold 442 instruction bytes that voice "
    "state accesses ($1028,X ...) reach with the voice index unbounded, and the rd() pointer "
    "reads keep every other byte of the image as data",
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
