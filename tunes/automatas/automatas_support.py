"""Paths and the C build used by the Automatas tests."""

import os
import pathlib
import subprocess

from gen_automatas import generate
from transliterate import build

ROOT = pathlib.Path(__file__).resolve().parent
BUILD = ROOT / "build"
FIX = ROOT.parent.parent / "fixtures"
SID = FIX / "Automatas.sid"
DRIVER = ROOT.parent.parent / "c64"
CFLAGS = ["-DFRAME_CIA"]


def build_prg(psid, out_dir):
    """Compile the C player against data generated from `psid`; return its path."""
    out_dir = pathlib.Path(out_dir)
    (out_dir / "automatas_data.h").write_text(generate(psid), encoding="ascii")
    prg = out_dir / "automatas.prg"
    subprocess.run(
        [os.environ.get("CC", "mos-c64-clang"), "-Os", *CFLAGS, f"-I{out_dir}"]
        + ["-o", prg, f"-I{DRIVER}", str(ROOT / "src" / "automatas.c")]
        + [str(DRIVER / "driver.c")],
        check=True,
    )
    return prg


def gen_prg(psid, out_dir):
    """Compile the C transliterated from `psid`; return its path."""
    return build(psid, out_dir, flags=CFLAGS)[0]
