"""Cognitive complexity of generated C (docs/fcomplex.md): clang-tidy's per-function score,
each increment with its construct and nesting penalty.
"""

import argparse
import json
import pathlib
import re
import subprocess
import sys
from collections import Counter, defaultdict

from tqdm import tqdm

from constructs import ROOT

CHECK = "readability-function-cognitive-complexity"
CONFIG = (
    f"{{CheckOptions: {{{CHECK}.Threshold: 0, {CHECK}.DescribeBasicIncrements: true}}}}"
)
WARN = re.compile(
    r"^(.*?):(\d+):\d+: warning: function '(\w+)' has cognitive complexity of (\d+)"
)
NOTE = re.compile(
    r"^(.*?):(\d+):(\d+): note: \+(\d+)(?:, including nesting penalty of (\d+))?"
)
KINDS = ("if", "else", "for", "while", "do", "switch", "goto", "&&", "||", "?", "catch")

#: The llvm-mos SDK's C64 headers, copied out of its image (docs/fcomplex.md).
SDK = ROOT / "out" / "mos-sdk"


def flags(includes):
    """The clang flags parsing a tune's C for the C64 with `includes`."""
    dirs = [*includes, SDK / "c64", SDK / "commodore", SDK / "common"]
    return ["-D__C64__", "-w", *(f"-I{d}" for d in dirs)]


def kind(lines, n, col):
    """The construct an increment at line `n`, column `col` counts."""
    text = lines[n - 1][col - 1 :]
    return next(
        (k for k in KINDS if text.startswith(k)),
        text.split()[0] if text.split() else "?",
    )


def increments(path, includes):
    """{function: [(line, kind, points, nesting penalty)]} by clang-tidy's cognitive complexity
    of `path`, each function with any increment."""
    path = pathlib.Path(path)
    args = ["clang-tidy", str(path), f"--checks=-*,{CHECK}", f"--config={CONFIG}", "--"]
    args += flags(includes)
    out = subprocess.run(args, capture_output=True, text=True, check=False).stdout
    lines, got, fn = (
        path.read_text(encoding="utf-8").splitlines(),
        defaultdict(list),
        None,
    )
    for row in out.splitlines():
        if m := WARN.match(row):
            fn = m.group(3) if pathlib.Path(m.group(1)) == path.resolve() else None
        elif (
            (m := NOTE.match(row)) and fn and pathlib.Path(m.group(1)) == path.resolve()
        ):
            n, col = int(m.group(2)), int(m.group(3))
            got[fn].append(
                (n, kind(lines, n, col), int(m.group(4)), int(m.group(5) or 0))
            )
    return got


def summarize(incs):
    """{measure: value} of increments `incs`."""
    by = Counter()
    for _, k, pts, _ in incs:
        by[k] += pts
    return {
        "cognitive": sum(p for _, _, p, _ in incs),
        "nesting": sum(n for *_, n in incs),
        "gotos": sum(1 for _, k, _, _ in incs if k == "goto"),
        "by construct": dict(by.most_common()),
    }


def score(paths, includes=()):
    """{"total": summary, "functions": {function: summary}} of the C at `paths` (a file or a
    translation's units), each file's own directory and the driver's on the include path.
    """
    paths = [paths] if isinstance(paths, (str, pathlib.PurePath)) else paths
    inc = {}
    for path in map(pathlib.Path, paths):
        inc.update(increments(path, [path.parent, ROOT / "c64", *includes]))
    fns = {f: summarize(v) for f, v in inc.items()}
    total = summarize([i for v in inc.values() for i in v])
    return {"total": {**total, "scored functions": len(fns)}, "functions": fns}


def main(argv=None):
    """CLI: one JSON line per C file, its total and its `--top` most complex functions; with
    `--units`, one line of all the files as one translation's units."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("c", nargs="+", type=pathlib.Path)
    ap.add_argument("--top", type=int, default=5, help="functions listed per file")
    ap.add_argument("--units", action="store_true", help="score the files as one")
    args = ap.parse_args(argv)
    for c in tqdm([args.c] if args.units else args.c, desc="fcomplex", unit="file"):
        got = score(c)
        top = sorted(got["functions"].items(), key=lambda x: -x[1]["cognitive"])[
            : args.top
        ]
        tqdm.write(
            json.dumps(
                {
                    " ".join(map(str, c)) if args.units else str(c): got["total"],
                    "top": {f: s["cognitive"] for f, s in top},
                }
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
