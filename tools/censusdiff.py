"""Per-function census differences between two directories of readability C (tools/readability.py
outputs): each function whose cognitive complexity, locals, lines or statements differ.

    python tools/censusdiff.py OLD_DIR NEW_DIR [TARGET ...]
"""

import argparse
import pathlib

from tqdm import tqdm
from wayward_eagle.census import census

from constructs import ROOT

FIELDS = ("complexity", "locals", "lines", "statements")


def report(path):
    """``{field: {function: value}}`` of C file ``path``."""
    r = census(str(path), include=(str(path.parent), str(ROOT / "c64")))
    return {k: dict(getattr(r, k)) for k in FIELDS}


def changes(old, new):
    """``[(function, field, old, new)]`` of the functions whose fields differ."""
    out = []
    for k in FIELDS:
        for name in sorted(set(old[k]) | set(new[k])):
            a, b = old[k].get(name, 0), new[k].get(name, 0)
            if a != b:
                out.append((name, k, a, b))
    return out


def main(argv=None):
    """CLI: print each target's differing functions."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("old", type=pathlib.Path)
    ap.add_argument("new", type=pathlib.Path)
    ap.add_argument("targets", nargs="*")
    args = ap.parse_args(argv)
    names = args.targets or sorted(p.stem for p in args.new.glob("*.c"))
    for name in tqdm(names, desc="census", unit="file"):
        old, new = (report(d / f"{name}.c") for d in (args.old, args.new))
        for row in changes(old, new):
            tqdm.write(f"{name} " + " ".join(map(str, row)))


if __name__ == "__main__":
    main()
