"""Value flow of a translated SID player: how song bytes and state cells become SID writes.

Nodes are cells (by original address, voice index dropped), song spans read through a pointer
and SID registers; solid edges carry values, dashed ones addresses (docs/flowgraph.md).
"""

import argparse
import json
import pathlib
import re
import subprocess
import sys
import tempfile
from collections import Counter

import networkx as nx

import eventlog
import irflow
import irmodule
import sidtrace

DRIVER = pathlib.Path(__file__).resolve().parent.parent / "c64"
SID = range(0xD400, 0xD420)
RESIDUAL = re.compile(r"(?:oob_|cgen_)?rd|wr|codechk|trap|oob_wr")
STYLE = {
    "song": ("box", "lightyellow"),
    "cell": ("ellipse", None),
    "sid": ("box", "lightblue"),
}


def _name(key, origins, labels):
    g, off = key
    if g is None:
        return "mem"
    if off is None:
        return f"{g}[ptr]"
    if g not in origins:
        return f"{g}+{off}"
    a = (origins[g] + off) & 0xFFFF
    return labels.get(a, f"${a:04X}")


def reach(flow, entries):
    """The functions ``entries`` call, transitively, with themselves."""
    callees = {}
    for fn, _, callee, _, _ in flow.calls:
        callees.setdefault(fn, set()).add(callee)
    seen, todo = set(), list(entries)
    while todo:
        fn = todo.pop()
        if fn not in seen:
            seen.add(fn)
            todo += callees.get(fn, ())
    return seen


def graph(
    c_path, labels=None, entries=None, split=False
):  # pylint: disable=too-many-locals
    """``(networkx.MultiDiGraph, summary)`` of the generated C at ``c_path`` (its .h beside):
    every store, read and ``io_write`` traced to the loads its value and address derive from;
    with ``entries`` only those of the functions they reach; ``split`` names a cell's def-use
    webs apart, all but the one the first entry's calls keep by ``cell@line``.
    """
    h_text = pathlib.Path(c_path).with_suffix(".h").read_text(encoding="utf-8")
    origins = eventlog.origins(h_text)
    inc = [pathlib.Path(c_path).parent, DRIVER]
    text = irmodule.lower(c_path, inc, ["CGEN_HOST"], passes=True)
    flow = irflow.Flow(irflow.sroa(text), re.compile(irmodule.HELPERS))
    fns = reach(flow, entries) if entries else None
    webs = irflow.Webs(flow, entries[0]) if entries and split else None

    def kept(fn):
        return fns is None or fn in fns

    stored = {k for k, _ in flow.stores}
    edges, residual, g, kinds = Counter(), Counter(), nx.MultiDiGraph(), {}

    def node(key, addr=frozenset(), ident=None):
        if key[1] is None:
            cells = sorted({read(d) for d in flow.loads(addr) if d in flow.reads})
            return f"{key[0]}[{','.join(cells)}]"
        name = _name(key, origins, labels or {})
        root = webs.web.get(ident) if webs else None
        if root is None or root == (key, irflow.KEPT):
            return name
        return f"{name}@{flow.where[root[1]][1][0]}" if root[0] == "st" else f"{name}@?"

    def read(d):
        name = node(*flow.reads[d][2:], ident=d)
        kinds.setdefault(name, "cell" if flow.reads[d][2] in stored else "song")
        return name

    def link(deps, dst, kind):
        for d in flow.loads(deps):
            if d in flow.reads:
                edges[read(d), dst, kind] += 1

    for ident, (fn, _, key, addr) in flow.reads.items():
        if not kept(fn):
            continue
        g.add_node(read(ident), kind="cell" if key in stored else "song")
        link(addr, read(ident), "address")
    for i, (key, val) in enumerate(flow.stores):
        if not kept(flow.where[i][0]):
            continue
        dst = node(key, ident=("st", i))
        g.add_node(dst, kind="cell")
        link(val, dst, "value")
        link(flow.where[i][2], dst, "address")
    for fn, _, callee, deps, base in flow.calls:
        if not kept(fn):
            continue
        if callee == "io_write" and deps:
            reg = f"SID+${base - SID.start:02X}" if base in SID else f"IO ${base:04X}"
            g.add_node(reg, kind="sid")
            link(deps[1], reg, "value")
            link(deps[0], reg, "address")
        elif RESIDUAL.fullmatch(callee or ""):
            residual[callee] += 1
    for (src, dst, kind), w in edges.items():
        g.add_edge(src, dst, kind=kind, weight=w)
    for n in g:
        g.nodes[n].setdefault("kind", kinds.get(n, "cell"))
    values = nx.DiGraph((u, v) for u, v, k in g.edges(data="kind") if k == "value")
    cycles = [sorted(c) for c in nx.strongly_connected_components(values) if len(c) > 1]
    return g, {
        "nodes": Counter(k for _, k in g.nodes(data="kind")),
        "edges": Counter(k for _, _, k in g.edges(data="kind")),
        "residual_calls": dict(residual),
        "value_cycles": sorted(cycles, key=len, reverse=True),
    }


def dot(g):
    """Graphviz DOT text of ``g``."""
    out = ["digraph flow {", "rankdir=LR;", "node [fontsize=10];"]
    for n, kind in g.nodes(data="kind"):
        shape, fill = STYLE[kind]
        style = f', style=filled, fillcolor="{fill}"' if fill else ""
        out.append(f'"{n}" [shape={shape}{style}];')
    for u, v, d in g.edges(data=True):
        line = "dashed" if d["kind"] == "address" else "solid"
        width = min(1 + d["weight"] / 4, 4)
        out.append(f'"{u}" -> "{v}" [style={line}, penwidth={width:.1f}];')
    return "\n".join(out + ["}"]) + "\n"


def main(argv=None):
    """CLI: write OUT.dot, OUT.svg and OUT.json for a PSID."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("psid")
    ap.add_argument("out", help="output path stem")
    ap.add_argument("--labels", help="ACME label file naming addresses")
    args = ap.parse_args(argv)
    import transliterate  # pylint: disable=import-outside-toplevel

    labels = {}
    if args.labels:
        text = pathlib.Path(args.labels).read_text(encoding="ascii")
        labels = {a: n for n, a in sidtrace.parse_labels(text).items() if a >= 0x100}
    with tempfile.TemporaryDirectory() as tmp:
        psid = pathlib.Path(args.psid).read_bytes()
        c, h, _ = transliterate.transliterate(psid, "gen.h")
        c_path = pathlib.Path(tmp) / "gen.c"
        c_path.write_text(c, encoding="utf-8")
        c_path.with_suffix(".h").write_text(h, encoding="utf-8")
        g, summary = graph(c_path, labels)
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".dot").write_text(dot(g), encoding="utf-8")
    svg = out.with_suffix(".svg")
    subprocess.run(["dot", "-Tsvg", "-o", svg, out.with_suffix(".dot")], check=True)
    out.with_suffix(".json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print({k: v for k, v in summary.items() if k != "value_cycles"})
    print("value cycles:", [len(c) for c in summary["value_cycles"]])
    return 0


if __name__ == "__main__":
    sys.exit(main())
