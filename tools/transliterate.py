"""Translate a PSID's playroutine to C with vagrant-moses' cgen (deity-informant).

Writes the player and C64 host (`tune_init`/`tune_play` for c64/driver.c) to OUT.c and
the song data to the header."""

import argparse
import os
import pathlib
import subprocess
import sys

from vagrant_moses.c64 import poweron_ram
from vagrant_moses.cgen import translate_c64

from sidtrace import psid_parts

DRIVER = pathlib.Path(__file__).resolve().parent.parent / "c64"


def transliterate(psid, header):
    """(c, h, info) of `psid` loaded over power-on RAM; the .c includes `header`."""
    load, init, play, image = psid_parts(psid)
    ram = bytearray(poweron_ram())
    ram[load : load + len(image)] = image
    return translate_c64(
        ram, init, play, loaded=(load, load + len(image)), header=header
    )


def build(psid, out_dir, name="gen", flags=()):
    """(PRG path, c, h) of the C generated from `psid` as out_dir/name.c and .h, built for
    the C64 with c64/driver.c by $CC (default mos-c64-clang); symbols in PRG.elf."""
    out = pathlib.Path(out_dir)
    c, h, _ = transliterate(psid, f"{name}.h")
    (out / f"{name}.c").write_text(c, encoding="utf-8")
    (out / f"{name}.h").write_text(h, encoding="utf-8")
    prg = out / f"{name}.prg"
    subprocess.run(
        [
            os.environ.get("CC", "mos-c64-clang"),
            "-Os",
            *flags,
            f"-I{out}",
            f"-I{DRIVER}",
        ]
        + ["-o", prg, out / f"{name}.c", DRIVER / "driver.c"],
        check=True,
    )
    return prg, c, h


def main(argv=None):
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("psid")
    ap.add_argument("out", help="output .c")
    ap.add_argument("--header", required=True, help="output .h")
    args = ap.parse_args(argv)
    header = pathlib.Path(args.header)
    c, h, info = transliterate(pathlib.Path(args.psid).read_bytes(), header.name)
    pathlib.Path(args.out).write_text(c, encoding="utf-8")
    header.write_text(h, encoding="utf-8")
    times = " ".join(f"{k} {v:.2f}s" for k, v in info["times"].items())
    print(f"{args.out}: {len(c)} B, {len(info['traps'])} traps; {times}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
