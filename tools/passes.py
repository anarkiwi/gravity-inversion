"""Procedure graphs of a tune and the facts the pass-count proof rests on (docs/passes.md).

Procedures are the program entries and JSR targets; a procedure's graph is what its entry reaches
without entering another procedure, RTS and RTI to a return site being exits. Retreating edges of a
depth-first search are loops; one whose target does not dominate its source is a multi-entry loop.
"""

import argparse

import networkx as nx
from jennings.coverage.cfg import successors

from constructs import memory, run
from sidtrace import psid_parts

EXIT = -1
JSR, RTS, RTI = 0x20, 0x60, 0x40


def procedures(mem, cov, starts, entries):
    """{entry: graph} of every procedure, EXIT the shared exit node."""
    starts = set(starts)
    calls = {int(mem[a + 1]) | int(mem[a + 2]) << 8 for a in starts if mem[a] == JSR}
    returns = {a + 3 for a in starts if mem[a] == JSR}
    heads = set(entries) | (calls & starts)
    succ = {}
    for a in starts:
        static = [b for _, b in successors(mem, a)[0]]
        dynamic = [b for x, b in cov.edges if x == a]
        if mem[a] in (RTS, RTI):
            dynamic = [b for b in dynamic if b not in returns]
        succ[a] = sorted({b for b in static + dynamic if b in starts})
    graphs = {}
    for e in sorted(heads):
        g, todo = nx.DiGraph(), [e]
        g.add_node(e)
        while todo:
            a = todo.pop()
            nxt = succ.get(a, [])
            if not nxt:
                g.add_edge(a, EXIT)
            for b in nxt:
                if b in heads and b != e:
                    g.add_edge(a, EXIT)
                    continue
                if b not in g:
                    todo.append(b)
                g.add_edge(a, b)
        graphs[e] = g
    return graphs


def retreating(g, root):
    """Edges to a node still on the depth-first search stack."""
    out, state, stack = set(), {root: 1}, [(root, iter(sorted(g.successors(root))))]
    while stack:
        n, it = stack[-1]
        for m in it:
            if m not in state:
                state[m] = 1
                stack.append((m, iter(sorted(g.successors(m)))))
                break
            if state[m] == 1:
                out.add((n, m))
        else:
            state[n] = 2
            stack.pop()
    return out


def loops(g, root):
    """(natural back edges, multi-entry retreating edges) of a procedure graph."""
    dom = nx.immediate_dominators(g, root)

    def dominates(b, a):
        while a != b and dom.get(a, a) != a:
            a = dom[a]
        return a == b

    back = retreating(g, root)
    natural = {(a, b) for a, b in back if dominates(b, a)}
    return natural, back - natural


def analyse(tune, jobs=4):
    """{entry: (natural back edges, multi-entry retreating edges)} of a tune's procedures."""
    _, data, cov, starts = run(tune, jobs)
    _, init, play, _ = psid_parts(data)
    graphs = procedures(memory(data), cov, starts, [init, play])
    return {e: loops(g, e) for e, g in graphs.items()}


def main(argv=None):
    """CLI entry point: print each tune's loops by kind."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tunes", nargs="+")
    args = ap.parse_args(argv)
    for t in args.tunes:
        res = analyse(t)
        nat = sum(len(n) for n, _ in res.values())
        multi = sorted(f"${a:04X}->${b:04X}" for _, x in res.values() for a, b in x)
        print(f"{t}: {len(res)} procedures, {nat} natural loops, multi-entry {multi}")


if __name__ == "__main__":
    main()
