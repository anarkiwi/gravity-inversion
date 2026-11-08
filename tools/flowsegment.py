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


def contract(g, nodes):
    """``g`` without ``nodes``, each source of one feeding each of its targets: an address
    when either edge is one, else a value."""
    h = nx.MultiDiGraph(g)
    for n in nodes:
        ins = [(u, d) for u, _, d in h.in_edges(n, data=True) if u != n]
        outs = [(v, d) for _, v, d in h.out_edges(n, data=True) if v != n]
        h.remove_node(n)
        for u, du in ins:
            for v, dv in outs:
                kind = "address" if "address" in (du["kind"], dv["kind"]) else "value"
                h.add_edge(u, v, kind=kind, weight=min(du["weight"], dv["weight"]))
    return _simple(h)


def registers(g):
    """``g`` without the cells the lift passes registers between functions in."""
    return contract(g, [n for n in g if REGISTER.fullmatch(n)])


def machines(g):
    """``g`` with only the cells that are state machines: those in a cycle, holding a value
    derived from their own."""
    loops = {u for u, v in g.edges() if u == v}
    loops.update(
        n
        for c in nx.strongly_connected_components(nx.DiGraph(g))
        if len(c) > 1
        for n in c
    )
    return contract(
        g, [n for n, k in g.nodes(data="kind") if k == "cell" and n not in loops]
    )


def _merge(h):
    out = Counter()
    for u, v, d in h.edges(data=True):
        out[u, v, d["kind"]] += d["weight"]
    return out


def _simple(h):
    s = nx.MultiDiGraph()
    s.add_nodes_from(h.nodes(data=True))
    for (u, v, k), w in _merge(h).items():
        s.add_edge(u, v, kind=k, weight=w)
    return s


def classes(g):
    """``{node: class}``: equal classes feed and are fed by equal classes the same way."""
    dag = nx.condensation(nx.DiGraph(g))
    comp, members = dag.graph["mapping"], nx.get_node_attributes(dag, "members")
    kinds = {}
    for u, v, k in g.edges(data="kind"):
        if comp[u] != comp[v]:
            kinds.setdefault((comp[u], comp[v]), set()).add(k)
    order = list(nx.topological_sort(dag))
    back, fwd = {}, {}
    for c in reversed(order):
        out = {(k, back[d]) for d in dag.successors(c) for k in kinds[c, d]}
        back[c] = _h(_inner(g, members[c]), sorted(out))
    for c in order:
        into = {(k, fwd[p]) for p in dag.predecessors(c) for k in kinds[p, c]}
        fwd[c] = _h(back[c], sorted(into))
    return {n: fwd[comp[n]] for n in g}


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


def roles(q):
    """``{machine: (role, register groups, tables)}`` of a state machine graph. A machine
    indexing song tables is a ``sequencer`` when it or its tables' rows set another such
    machine (a score row restarting a program), else a ``program``; one indexing none is a
    ``modulator`` when it feeds registers, a ``timer`` when it feeds only machines, else a
    ``flag`` (control only). A program's groups are those its tables feed, a sequencer's
    those of all it feeds."""

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
        out[n] = (name, regs, tables[n])
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
