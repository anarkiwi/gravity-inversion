"""Shared CLI of the tune data generators: PSID in, C header out."""

import argparse


def cli(generate, doc, argv=None):
    """Write generate(PSID bytes) to the output path named on the command line."""
    ap = argparse.ArgumentParser(description=doc)
    ap.add_argument("psid")
    ap.add_argument("out")
    args = ap.parse_args(argv)
    with open(args.psid, "rb") as f:
        psid = f.read()
    with open(args.out, "w", encoding="ascii") as f:
        f.write(generate(psid))
