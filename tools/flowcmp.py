"""Value-flow graphs, state machines and PRG sizes of tunes' C in two forms: reduced by
wayward-eagle's rewrites against left whole (`raw`), or as translation units against one file
(`split`).

Forms that keep behavior keep what derives from what, so the graphs and machines of the two
should match; each difference is an edge one has that the other lacks (docs/flowgraph.md).

    python tools/flowcmp.py OUT TUNE ...  # OUT/TUNE/{raw,reduced}/{*.c,graph.dot,machines.dot}
    python tools/flowcmp.py OUT TUNE --against split  # OUT/TUNE/{split,reduced}/...
    python tools/flowcmp.py OUT TUNE --edges  # every differing edge too
"""

import argparse
import contextlib
import json
import pathlib
import sys
from collections import Counter

from deity_informant.cgen import printer
from tqdm import tqdm

import constructs
import flowgraph
import flowsegment
import transliterate


def unreduced(fd, *_, **__):
    """``reduce_function``'s result for a function left as it is."""
    return [fd]


@contextlib.contextmanager
def rewrites(on):
    """The printer reducing functions (``on``) or leaving them as they are."""
    keep = printer.reduce_function
    printer.reduce_function = keep if on else unreduced
    try:
        yield
    finally:
        printer.reduce_function = keep


#: Each form: reduced, as translation units.
FORMS = {"raw": (False, False), "reduced": (True, False), "split": (True, True)}


def edges(g):
    """``{(source, target, kind)}`` of graph ``g``, nodes by name."""
    return {(str(u), str(v), d.get("kind")) for u, v, d in g.edges(data=True)}


def form(psid, mode, d):
    """``(graph edges, machine edges, PRG bytes)`` of ``psid``'s C in form ``mode`` under
    ``d``."""
    on, split = FORMS[mode]
    with rewrites(on):
        c, h, _ = transliterate.transliterate(psid, "gen.h", split=split)
    d.mkdir(parents=True, exist_ok=True)
    units = transliterate.write(d, c, h, "gen.h")
    g, _ = flowgraph.graph(units, {}, header=d / "gen.h")
    q = flowsegment.segment(units, header=d / "gen.h")
    (d / "graph.dot").write_text(flowgraph.dot(g), encoding="utf-8")
    (d / "machines.dot").write_text(flowgraph.dot(q), encoding="utf-8")
    prg = transliterate.compile_prg(units)
    return edges(g), edges(q), prg.stat().st_size


def compare(tune, out, every=False, against="raw"):
    """``{part: {against: n, "reduced": n, "lost": {...}, "gained": {...}}, "prg": {form:
    bytes}}`` of ``tune``'s graph and machines in form ``against`` and reduced, ``lost``/``gained``
    counting edges by ``(source, kind)``."""
    psid = (constructs.ROOT / constructs.load(tune)["psid"]).read_bytes()
    got = {m: form(psid, m, out / tune / m) for m in (against, "reduced")}
    rows = {}
    for i, part in enumerate(("graph", "machines")):
        a, b = got[against][i], got["reduced"][i]
        rows[part] = {
            against: len(a),
            "reduced": len(b),
            "lost": dict(Counter(f"{u} {k}" for u, _, k in a - b)),
            "gained": dict(Counter(f"{u} {k}" for u, _, k in b - a)),
        }
        if every:
            rows[part]["edges"] = {"lost": sorted(a - b), "gained": sorted(b - a)}
    rows["prg"] = {m: v[2] for m, v in got.items()}
    return rows


def main(argv=None):
    """CLI: compare each tune, print one JSON line per tune; exit 1 when any differs."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out", type=pathlib.Path)
    ap.add_argument("tunes", nargs="+")
    ap.add_argument("--edges", action="store_true", help="list every differing edge")
    ap.add_argument(
        "--against", choices=("raw", "split"), default="raw", help="the other form"
    )
    args = ap.parse_args(argv)
    differ = False
    for tune in tqdm(args.tunes, desc="flowcmp", unit="tune"):
        rows = compare(tune, args.out, args.edges, args.against)
        differ |= any(
            rows[p]["lost"] or rows[p]["gained"] for p in ("graph", "machines")
        )
        tqdm.write(json.dumps({tune: rows}))
    return int(differ)


if __name__ == "__main__":
    sys.exit(main())
