"""Paths and the C build used by the Commando tests."""

import os
import pathlib
import subprocess

from gen_data import generate

ROOT = pathlib.Path(__file__).resolve().parent
BUILD = ROOT / "build"
ASM = ROOT.parent.parent / "fixtures" / "Hubbard_Rob_Commando.asm"


def build_prg(psid, labels, out_dir):
    """Compile the C player against data generated from `psid`; return the PRG."""
    out_dir = pathlib.Path(out_dir)
    (out_dir / "commando_data.h").write_text(generate(psid, labels), encoding="ascii")
    prg = out_dir / "commando.prg"
    subprocess.run(
        [os.environ.get("CC", "mos-c64-clang"), "-Os", f"-I{out_dir}", "-o", prg]
        + [str(ROOT / "src" / f) for f in ("main.c", "commando.c")],
        check=True,
    )
    return prg.read_bytes()
