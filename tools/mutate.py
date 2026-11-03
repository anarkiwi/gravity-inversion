"""Mutation check: apply each source edit in turn, run a command, list survivors.

Edits are a JSON list of [old, new] string pairs; each `old` must occur exactly
once in the source. A mutant is killed when the command exits nonzero. The
source is restored afterwards."""

import argparse
import json
import pathlib
import subprocess
import sys

from tqdm import tqdm


def mutants(text, edits):
    """Yield the source text with each (old, new) edit applied alone."""
    for old, new in edits:
        assert text.count(old) == 1, f"edit not unique: {old!r}"
        yield text.replace(old, new)


def run(source, edits, command):
    """Indices of edits whose mutant the command does not kill."""
    path = pathlib.Path(source)
    text = path.read_text(encoding="utf-8")
    survivors = []
    try:
        for i, mutant in enumerate(
            tqdm(list(mutants(text, edits)), desc="mutants", unit="mutant")
        ):
            path.write_text(mutant, encoding="utf-8")
            if (
                subprocess.run(command, check=False, capture_output=True).returncode
                == 0
            ):
                survivors.append(i)
    finally:
        path.write_text(text, encoding="utf-8")
    return survivors


def main(argv=None):
    """CLI entry point: exit 1 if any mutant survives."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source")
    ap.add_argument("edits", help="JSON list of [old, new] pairs")
    ap.add_argument("command", nargs=argparse.REMAINDER)
    args = ap.parse_args(argv)
    with open(args.edits, encoding="utf-8") as f:
        edits = json.load(f)
    survivors = run(args.source, edits, args.command)
    for i in survivors:
        print(f"SURVIVED {i}: {edits[i][0]!r} -> {edits[i][1]!r}")
    print(f"{len(edits) - len(survivors)}/{len(edits)} mutants killed")
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
