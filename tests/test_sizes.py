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
MISSED = {
    (
        "generated",
        "emomyst",
        "code",
    ): "init's relocator is lifted as code and play reads through the operands it patches, "
    "pointer variables the analysis does not bound, through rd(); a write through one goes "
    "through wr() and codechk()",
    (
        "generated",
        "emomyst",
        "data",
    ): "the reads through the relocated operands may reach any byte of the player, so the "
    "whole image is held as one span with a write mask of its data bytes",
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
    (
        "generated",
        "commando",
        "data",
    ): "the instrument table's rows past the image ($1B57-$1BF0) are written by the "
    "pulse-width store at 8 times a song byte below $80 (rows 0-31, the image holding 13), "
    "so they stay data",
    (
        "generated",
        "ghouls",
        "data",
    ): "the music init's block copy ($29C6) writes each song's data to $730E and past the "
    "image to $914F-$9F14, RAM spanning every song's copy; the voices' song pointers read "
    "one span from the image's song data through that copy, holding the bytes between the "
    "image and it too",
    (
        "generated",
        "automatas",
        "data",
    ): "held spans keep the instruction bytes between the operand cells the band copies "
    "keep their state in, and those between the tables interleaved with code",
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
