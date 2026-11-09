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
        "hand",
        "ghouls",
        "data",
    ): "ram[] is RAM $2980-$9FFF as one array: the image's 2665 instruction bytes, the 3750 "
    "bytes $86E9-$958E between the songs' copies and the 766 bytes $9D02-$9FFF past them, "
    "which nothing writes",
    (
        "hand",
        "automatas",
        "data",
    ): "data[] is the image $1554-$2FAF with play's 243 instruction bytes between its tables, "
    "and voice[] holds 75 bytes of state the original keeps in its instructions' operands, "
    "which the bound counts as code",
    (
        "generated",
        "commando",
        "data",
    ): "span C0 holds init's code $1368-$139D, which the post-init lift does not see as "
    "instructions, so the song pointers' data run starts where play's code ends, and the "
    "instrument rows $1B57-$1BEF past the image, which the pulse-width store at 8 times a song "
    "byte below $80 may write and the song never does",
    (
        "generated",
        "ghouls",
        "data",
    ): "span C3 $6CB7-$9E06, the song pointers' one array from the image's song data through "
    "the songs' copies past it, holds $86E9-$958E between the copies and $9D02-$9E06 past them, "
    "which nothing writes, and init's per-song state is replayed by copy tables (CP_*, LIT) "
    "where the original's init computes it",
    (
        "generated",
        "automatas",
        "data",
    ): "span C0 $14D4-$1713 holds init's code $14D4-$1553 (the frequency reads at $13F4 index "
    "by Y = 2A after a BPL that leaves Y >= $80, which the lift's interval does not narrow) "
    "and play's code $168C-$1713 (reads at $1426/$143F indexed by a note byte), with the "
    "promoted cells and init's SID writes, which the original keeps in its code",
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
