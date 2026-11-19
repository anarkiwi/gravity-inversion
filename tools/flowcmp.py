"""Value-flow graphs, state machines and PRG sizes of tunes' C in two forms: reduced by
wayward-eagle's rewrites against left whole (`raw`), or as translation units against one file
(`split`).

Forms that keep behavior keep what derives from what, so the graphs and machines of the two
should match; each difference is an edge one has that the other lacks (docs/flowgraph.md).

    python tools/flowcmp.py OUT TUNE ...  # OUT/TUNE/{raw,reduced}/{*.c,graph.dot,machines.dot}
    python tools/flowcmp.py OUT TUNE --against split  # OUT/TUNE/{split,reduced}/...
    python tools/flowcmp.py OUT TUNE --edges  # every differing edge too
    python tools/flowcmp.py OUT TUNE --base OLD  # each form against OLD's, another lifter's
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
        c, h, info = transliterate.transliterate(psid, "gen.h", split=split)
    d.mkdir(parents=True, exist_ok=True)
    units = transliterate.write(d, c, h, "gen.h")
    g, _ = flowgraph.graph(units, {}, header=d / "gen.h")
    q = flowsegment.segment(units, header=d / "gen.h")
    (d / "graph.dot").write_text(flowgraph.dot(g), encoding="utf-8")
    (d / "machines.dot").write_text(flowgraph.dot(q), encoding="utf-8")
    found = {"graph": sorted(edges(g), key=str), "machines": sorted(edges(q), key=str)}
    found["vars"] = info["state"]["vars"]
    (d / "edges.json").write_text(json.dumps(found), encoding="utf-8")
    prg = transliterate.compile_prg(units)
    return edges(g), edges(q), prg.stat().st_size, found["vars"]


def provenance(node, at):
    """Graph node ``node`` with each of its names (a machine's state lists several, ``a, b
    (x2)``) by the original's address where it is a promoted cell's element (``name+offset``,
    ``at`` the translation's ``{lvalue: address}``), else as it is."""

    def one(word):
        name, _, off = word.rpartition("+")
        addr = at.get(f"{name}[{off}]", at.get(name) if off == "0" else None)
        return word if addr is None else f"${addr:04X}"

    parts = [p.split(" ") for p in str(node).split(", ")]
    return ", ".join(" ".join([one(p[0]), *p[1:]]) for p in parts)


def by_provenance(found, at):
    """Edges ``found`` with their nodes by :func:`provenance`."""
    return {(provenance(u, at), provenance(v, at), k) for u, v, k in found}


def against_base(base, tune, mode, got):
    """``{part: {"lost": n, "gained": n}}`` of form ``mode``'s edges ``got`` (graph, machines,
    the cells' addresses) against those a run under another lifter wrote in
    ``base``/TUNE/MODE/edges.json, nodes compared by :func:`provenance`."""
    old = json.loads((base / tune / mode / "edges.json").read_text(encoding="utf-8"))
    out = {}
    for part, new in zip(("graph", "machines"), got[:2]):
        was = by_provenance(old[part], old.get("vars", {}))
        now = by_provenance(new, got[2])
        out[part] = {"lost": len(was - now), "gained": len(now - was)}
    return out


def compare(tune, out, every=False, against="raw", base=None):
    """``{part: {against: n, "reduced": n, "lost": {...}, "gained": {...}}, "prg": {form:
    bytes}}`` of ``tune``'s graph and machines in form ``against`` and reduced, ``lost``/``gained``
    counting edges by ``(source, kind)``; with ``base``, ``"base": {form: ...}`` each form's
    against ``base``'s (:func:`against_base`)."""
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
    if base is not None:
        rows["base"] = {
            m: against_base(base, tune, m, (*v[:2], v[3])) for m, v in got.items()
        }
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
    ap.add_argument(
        "--base", type=pathlib.Path, help="an OUT of another lifter to compare against"
    )
    args = ap.parse_args(argv)
    differ = False
    for tune in tqdm(args.tunes, desc="flowcmp", unit="tune"):
        rows = compare(tune, args.out, args.edges, args.against, args.base)
        differ |= any(
            rows[p]["lost"] or rows[p]["gained"] for p in ("graph", "machines")
        )
        differ |= any(
            any(n.values()) for b in rows.get("base", {}).values() for n in b.values()
        )
        tqdm.write(json.dumps({tune: rows}))
    return int(differ)


if __name__ == "__main__":
    sys.exit(main())
