"""Bytes of each tune's generated PRG as a JSON object, the `prg` metric of
virtuous-cycles.toml (docs/readability.md).

Every tune whose Makefile sets `GEN := 1` is measured by its `build/gen.prg` (common.mk);
one not built is an error, so a missing build cannot drop its key from the ratchet.
"""

import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
GEN = re.compile(r"^GEN\s*:?=\s*1\s*$", re.M)


def generated(tunes):
    """The tune directories under `tunes` whose Makefile builds a generated translation."""
    return sorted(
        m.parent
        for m in pathlib.Path(tunes).glob("*/Makefile")
        if GEN.search(m.read_text(encoding="utf-8"))
    )


def sizes(tunes):
    """{tune: bytes of build/gen.prg} of every generated tune under `tunes`."""
    out = {}
    for d in generated(tunes):
        prg = d / "build" / "gen.prg"
        if not prg.is_file():
            raise FileNotFoundError(f"{prg}: not built (make -C {d})")
        out[d.name] = prg.stat().st_size
    return out


def main(argv=None):
    """CLI entry point: print {tune: PRG bytes} as JSON."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tunes", type=pathlib.Path, default=ROOT / "tunes")
    args = ap.parse_args(argv)
    try:
        got = sizes(args.tunes)
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        return 1
    print(json.dumps(got, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
