"""Paths, song variants and the C builds used by the Commando tests."""

import os
import pathlib
import subprocess

from gen_data import generate
from sidtrace import parse_labels, psid_parts
from transliterate import build

ROOT = pathlib.Path(__file__).resolve().parent
BUILD = ROOT / "build"
ASM = ROOT.parent.parent / "fixtures" / "Hubbard_Rob_Commando.asm"
DRIVER = ROOT.parent.parent / "c64"
STOP_FRAMES = 2500
INS = 8
VARIANTS = {
    "stop": [("song1t1", 4, 0xFE)],
    "skydive_vibrato_pwl": [
        ("ins_pwl", 2 * INS + 5, 1),
        ("ins_pwl", 2 * INS + 7, 0x0A),
        ("ins_pwl", 3 * INS + 7, 0x07),
        ("p02", 0, 0x86),
        ("p02", 2, 0x5F),
    ],
}


def patched(psid, labels, patches):
    """`psid` with (label, offset, value) byte patches."""
    load, _, _, image = psid_parts(psid)
    data = bytearray(psid)
    for label, offset, value in patches:
        data[labels[label] - load + len(psid) - len(image) + offset] = value
    return bytes(data)


def assemble(asm, out_dir):
    """(PSID, labels) of ACME source text `asm`."""
    out_dir = pathlib.Path(out_dir)
    src, sid, labels = (out_dir / f"t.{e}" for e in ("asm", "sid", "labels"))
    src.write_text(asm, encoding="ascii")
    subprocess.run(
        ["acme", "-l", labels, "-o", sid, src], check=True, capture_output=True
    )
    return sid.read_bytes(), parse_labels(labels.read_text(encoding="ascii"))


def _cc(prg, include, source):
    subprocess.run(
        [os.environ.get("CC", "mos-c64-clang"), "-Os", f"-I{include}", "-o", prg]
        + [f"-I{DRIVER}", str(source), str(DRIVER / "driver.c")],
        check=True,
    )
    return prg


def build_prg(psid, labels, out_dir):
    """Compile the C player against data generated from `psid`; return its path."""
    out_dir = pathlib.Path(out_dir)
    (out_dir / "commando_data.h").write_text(generate(psid, labels), encoding="ascii")
    return _cc(out_dir / "commando.prg", out_dir, ROOT / "src" / "commando.c")


def gen_prg(psid, out_dir):
    """(PRG path, .c, .h) of the C generated from `psid`."""
    return build(psid, out_dir, "commando")
