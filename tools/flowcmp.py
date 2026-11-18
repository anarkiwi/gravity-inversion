"""Value-flow graphs and state machines of tunes' C with and without wayward-eagle's rewrites.

Rewrites that keep behavior keep what derives from what, so the graphs and machines of the two
C texts should match; each difference is an edge one has that the other lacks (docs/flowgraph.md).

    python tools/flowcmp.py OUT TUNE ...  # OUT/TUNE/{raw,reduced}/{gen.c,graph.dot,machines.dot}
    python tools/flowcmp.py OUT TUNE --edges  # every differing edge too
"""

import argparse
import contextlib
import json
import pathlib
import sys
from collections import Counter

import wayward_eagle
from deity_informant.cgen import printer
from tqdm import tqdm

import constructs
import flowgraph
import flowsegment
import transliterate


def unreduced(body, name, fn):
    """``reduced``'s result for a function left as it is."""
    ret = None if fn.void else wayward_eagle.STATUS
    return [wayward_eagle.Piece(name, fn.params, body, ret)], 1, set()


@contextlib.contextmanager
def rewrites(on):
    """The printer reducing functions (``on``) or leaving them as they are."""
    keep = printer.reduced
    printer.reduced = keep if on else unreduced
    try:
        yield
    finally:
        printer.reduced = keep


def edges(g):
    """``{(source, target, kind)}`` of graph ``g``, nodes by name."""
    return {(str(u), str(v), d.get("kind")) for u, v, d in g.edges(data=True)}


def compare(tune, out, every=False):
    """``{part: {"raw": n, "reduced": n, "lost": {...}, "gained": {...}}}`` of ``tune``'s graph
    and machines, ``lost``/``gained`` counting edges by ``(source, kind)``."""
    psid = (constructs.ROOT / constructs.load(tune)["psid"]).read_bytes()
    got = {}
    for mode in ("raw", "reduced"):
        with rewrites(mode == "reduced"):
            c, h, _ = transliterate.transliterate(psid, "gen.h")
        d = out / tune / mode
        d.mkdir(parents=True, exist_ok=True)
        (d / "gen.c").write_text(c, encoding="utf-8")
        (d / "gen.h").write_text(h, encoding="utf-8")
        g, _ = flowgraph.graph(d / "gen.c", {})
        q = flowsegment.segment(d / "gen.c")
        (d / "graph.dot").write_text(flowgraph.dot(g), encoding="utf-8")
        (d / "machines.dot").write_text(flowgraph.dot(q), encoding="utf-8")
        got[mode] = {"graph": edges(g), "machines": edges(q)}
    rows = {}
    for part in ("graph", "machines"):
        a, b = got["raw"][part], got["reduced"][part]
        rows[part] = {
            "raw": len(a),
            "reduced": len(b),
            "lost": dict(Counter(f"{u} {k}" for u, _, k in a - b)),
            "gained": dict(Counter(f"{u} {k}" for u, _, k in b - a)),
        }
        if every:
            rows[part]["edges"] = {"lost": sorted(a - b), "gained": sorted(b - a)}
    return rows


def main(argv=None):
    """CLI: compare each tune, print one JSON line per tune; exit 1 when any differs."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out", type=pathlib.Path)
    ap.add_argument("tunes", nargs="+")
    ap.add_argument("--edges", action="store_true", help="list every differing edge")
    args = ap.parse_args(argv)
    differ = False
    for tune in tqdm(args.tunes, desc="flowcmp", unit="tune"):
        rows = compare(tune, args.out, args.edges)
        differ |= any(r["lost"] or r["gained"] for r in rows.values())
        tqdm.write(json.dumps({tune: rows}))
    return int(differ)


if __name__ == "__main__":
    sys.exit(main())
