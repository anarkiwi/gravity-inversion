"""Translate a PSID's playroutine to C with vagrant-moses' cgen (deity-informant).

Writes the player and C64 host (`tune_init`/`tune_play` for c64/driver.c) to OUT.c and
the song data to the header."""

import argparse
import pathlib
import sys

from vagrant_moses.c64 import poweron_ram
from vagrant_moses.cgen import translate_c64

from sidtrace import psid_parts


def transliterate(psid, header):
    """(c, h, info) of `psid` loaded over power-on RAM; the .c includes `header`."""
    load, init, play, image = psid_parts(psid)
    ram = bytearray(poweron_ram())
    ram[load : load + len(image)] = image
    return translate_c64(
        ram, init, play, loaded=(load, load + len(image)), header=header
    )


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
