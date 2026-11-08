"""State machines of a value-flow graph (tools/flowgraph.py), alike ones contracted.

Classes are signatures by dynamic programming over the graph's condensation: back from the SID
registers (a voice's register by its offset in the voice), then forward from the sources. Per-
voice copies, pointer table halves and relocated patch sites share a class (docs/flowgraph.md).
"""

import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys
from collections import Counter

import networkx as nx

import flowgraph

VOICE = 7
VOICES = 3
REGISTER = re.compile(r"rv\d+\+0(?:@\S+)?")
#: SID register groups by offset in a voice (``voice+k``) or address (``sid+XX``).
GROUPS = {
    "voice+0": "freq",
    "voice+1": "freq",
    "voice+2": "pulse",
    "voice+3": "pulse",
    "voice+4": "control",
    "voice+5": "adsr",
    "voice+6": "adsr",
    "sid+15": "cutoff",
    "sid+16": "cutoff",
    "sid+17": "resonance",
    "sid+18": "volume",
}


def role(g, n):
    """A node's kind, a SID register's by its offset in its voice."""
    return register(n) if g.nodes[n]["kind"] == "sid" else g.nodes[n]["kind"]


def register(n):
    """A SID register node's offset in its voice (``voice+k``) or address (``sid+XX``)."""
    if not n.startswith("SID+$"):
        return "sid"
    reg = int(n[5:], 16)
    return f"voice+{reg % VOICE}" if reg < VOICE * VOICES else f"sid+{reg:02X}"


def _h(*parts):
    return hashlib.blake2b(repr(parts).encode(), digest_size=8).hexdigest()


def _inner(g, members):
    """Signature of a component's own structure: its roles and internal edges by role."""
    inside = {
        (role(g, u), role(g, v), k)
        for u, v, k in g.edges(data="kind")
        if u in members and v in members
    }
    return _h(sorted(role(g, n) for n in members), sorted(inside))


def _through(a, b):
    """The kind of a path of an ``a`` then a ``b`` edge: control when either is, else an
    address when either is, else a value."""
    return next(k for k in ("control", "address", "value") if k in (a, b))


def contract(g, nodes):
    """``g`` without ``nodes``, each source of one feeding each of its targets by the kind of
    the path through it (``_through``); edges of distinct kinds stay apart."""
    h = _simple(g)
    for n in nodes:
        ins = [(u, k, w) for u, _, k, w in h.in_edges(n, keys=True, data="weight")]
        outs = [(v, k, w) for _, v, k, w in h.out_edges(n, keys=True, data="weight")]
        h.remove_node(n)
        for u, ku, wu in ins:
            for v, kv, wv in outs:
                if n not in (u, v):
                    _add(h, u, v, _through(ku, kv), min(wu, wv))
    return h


def _add(h, u, v, kind, weight):
    if h.has_edge(u, v, kind):
        h[u][v][kind]["weight"] += weight
    else:
        h.add_edge(u, v, key=kind, kind=kind, weight=weight)


def registers(g):
    """``g`` without the cells the lift passes registers between functions in."""
    return contract(g, [n for n in g if REGISTER.fullmatch(n)])


def flows(g):
    """``g`` with its value and address edges only."""
    h = nx.MultiDiGraph()
    h.add_nodes_from(g.nodes(data=True))
    h.add_edges_from(e for e in g.edges(data=True) if e[2]["kind"] != "control")
    return h


def machines(g):
    """``g`` with only the cells that are state machines: those in a value or address cycle,
    holding a value derived from their own."""
    v = flows(g)
    loops = {a for a, b in v.edges() if a == b}
    loops.update(
        n
        for c in nx.strongly_connected_components(nx.DiGraph(v))
        if len(c) > 1
        for n in c
    )
    return contract(
        g, [n for n, k in g.nodes(data="kind") if k == "cell" and n not in loops]
    )


def _simple(h):
    """``h`` with its parallel edges of one kind merged, keyed by kind."""
    s = nx.MultiDiGraph()
    s.add_nodes_from(h.nodes(data=True))
    for u, v, d in h.edges(data=True):
        _add(s, u, v, d["kind"], d["weight"])
    return s


def classes(g):
    """``{node: class}``: equal classes feed and are fed by equal classes the same way, by
    value and address over the condensation, then by control between those classes."""
    v = flows(g)
    dag = nx.condensation(nx.DiGraph(v))
    comp, members = dag.graph["mapping"], nx.get_node_attributes(dag, "members")
    kinds = {}
    for a, b, k in v.edges(data="kind"):
        if comp[a] != comp[b]:
            kinds.setdefault((comp[a], comp[b]), set()).add(k)
    order = list(nx.topological_sort(dag))
    back, fwd = {}, {}
    for c in reversed(order):
        out = {(k, back[d]) for d in dag.successors(c) for k in kinds[c, d]}
        back[c] = _h(_inner(v, members[c]), sorted(out))
    for c in order:
        into = {(k, fwd[p]) for p in dag.predecessors(c) for k in kinds[p, c]}
        fwd[c] = _h(back[c], sorted(into))
    ctl = {c: set() for c in order}
    for a, b, k in g.edges(data="kind"):
        if k == "control":
            ctl[comp[a]].add(("to", back[comp[b]] if comp[a] != comp[b] else "self"))
            ctl[comp[b]].add(("from", fwd[comp[a]] if comp[a] != comp[b] else "self"))
    return {n: _h(fwd[comp[n]], sorted(ctl[comp[n]])) for n in g}


def quotient(g, cls):
    """The graph of classes: one node per class, named by its members, one edge per kind."""
    groups = {}
    for n in sorted(g):
        groups.setdefault(cls[n], []).append(n)
    name = {}
    for c, ns in groups.items():
        head = ", ".join(ns[:3]) + (f" +{len(ns) - 3}" if len(ns) > 3 else "")
        name[c] = f"{head} (x{len(ns)})" if len(ns) > 1 else head
    q = nx.MultiDiGraph()
    for c, ns in groups.items():
        q.add_node(name[c], kind=g.nodes[ns[0]]["kind"], members=ns)
    edges = Counter()
    for u, v, d in g.edges(data=True):
        edges[name[cls[u]], name[cls[v]], d["kind"]] += d["weight"]
    for (u, v, k), w in edges.items():
        q.add_edge(u, v, kind=k, weight=w)
    return q


def summary(g):
    """Node counts by kind, edge count and value cycle sizes of ``g``."""
    values = nx.DiGraph((u, v) for u, v, k in g.edges(data="kind") if k == "value")
    cyc = [len(c) for c in nx.strongly_connected_components(values) if len(c) > 1]
    return {
        "nodes": dict(Counter(k for _, k in g.nodes(data="kind"))),
        "edges": g.number_of_edges(),
        "value_cycles": sorted(cyc, reverse=True),
    }


def roles(g):  # pylint: disable=too-many-locals
    """``{machine: (role, register groups, tables, gated groups, gated machines)}`` of a state
    machine graph, roles by value and address edges (docs/flowgraph.md, *Roles*), gates by
    control edges."""
    q = flows(g)

    def groups(nodes):
        return sorted(
            {
                GROUPS.get(register(m), "io")
                for v in nodes
                if q.nodes[v]["kind"] == "sid"
                for m in q.nodes[v].get("members", [v])
            }
        )

    cells = [n for n, k in q.nodes(data="kind") if k == "cell"]
    tables = {
        n: sorted(
            v
            for v in q.successors(n)
            if v != n
            and q.nodes[v]["kind"] == "song"
            and "address" in edge_kinds(q, n, v)
        )
        for n in cells
    }
    out = {}
    for n in cells:
        succ = {v for v in q.successors(n) if v != n}
        if tables[n]:
            sets = succ.union(*(q.successors(t) for t in tables[n]))
            seq = any(v != n and tables.get(v) for v in sets)
            reach = set().union(*(nx.descendants(q, t) | {t} for t in tables[n]))
            name = "sequencer" if seq else "program"
            regs = groups(nx.descendants(q, n) if seq else reach)
        else:
            regs = groups(succ)
            feeds = any(q.nodes[v]["kind"] == "cell" for v in succ)
            name = "modulator" if regs else "timer" if feeds else "flag"
        ctl = {
            w for _, w, k in g.out_edges(n, data="kind") if k == "control" and w != n
        }
        gated = sorted(w for w in ctl if q.nodes[w]["kind"] == "cell")
        out[n] = (name, regs, tables[n], groups(ctl), gated)
    return out


def edge_kinds(q, u, v):
    """The edge kinds from ``u`` to ``v``."""
    return {d["kind"] for d in q.get_edge_data(u, v, default={}).values()}


def segment(c_path, entry="cgen_play"):
    """The state machines of what ``entry`` runs in the generated C at ``c_path``, cells split
    by def-use web, register cells contracted, classes contracted."""
    g, _ = flowgraph.graph(pathlib.Path(c_path), entries=[entry], split=True)
    g = machines(registers(g))
    return quotient(g, classes(g))


def main(argv=None):
    """CLI: write OUT.dot, OUT.png and OUT.json of a generated C's state machines."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("c", help="generated C (its .h beside)")
    ap.add_argument("out", help="output path stem")
    ap.add_argument("--entry", default="cgen_play")
    args = ap.parse_args(argv)
    q = segment(args.c, args.entry)
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".dot").write_text(flowgraph.dot(q), encoding="utf-8")
    png = out.with_suffix(".png")
    subprocess.run(["dot", "-Tpng", "-o", png, out.with_suffix(".dot")], check=True)
    report = {**summary(q), "roles": roles(q)}
    out.with_suffix(".json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
