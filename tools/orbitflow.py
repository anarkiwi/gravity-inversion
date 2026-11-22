"""Value-flow graph of a PSID's play straight from the 6502, for tools/flowsegment.py.

Cells are the persistent locations of jennings' components over the calls covering play, song
nodes the constant tables its loads read, SID nodes the registers it writes; edges are explicit
data dependence, load/store site taint and branch site taint into what the branches gate.
"""

import argparse
import json
import math
import pathlib
import sys
from collections import Counter
from dataclasses import dataclass

import networkx as nx
import numpy as np
from jennings import isa
from jennings.coverage import components as C
from tqdm import tqdm

import flowgraph
import flowsegment
import sidtrace

LEARN = 2000
LIMIT = 1 << 20
SID = range(0xD400, 0xD419)
WRITE = 1 << 16
CHUNK = 256


@dataclass(frozen=True)
class Orbit:  # pylint: disable=too-many-instance-attributes
    """What :func:`observe` saw of a tune's play.

    Attributes:
        comps: jennings ``Components`` over the covering calls, widened until autonomous.
        flows: jennings explicit ``Flows`` over the same calls.
        written: the memory some call writes.
        calls: the covering calls (``cover_calls``), None if the feed set does not repeat.
        learned: the calls the taints were learned over.
        accesses: ``pc << 17 | write << 16 | address`` of each distinct data access.
        bases: ``{pc: bases}`` of each load (``run_to_rts_accesses(bases=True)``).
        rows: uint8 ``[calls, persistent]`` trajectory of the persistent locations.
        cycles: jennings ``Cycles`` of the closures on ``rows``.
        ram: the RAM play is first called on.
    """

    comps: object
    flows: object
    written: frozenset
    calls: object
    learned: int
    accesses: np.ndarray
    bases: dict
    rows: np.ndarray
    cycles: object
    ram: bytes


def accesses(state, play, calls):
    """``(keys, bases)``: the distinct data access keys of ``calls`` calls of ``play`` and
    ``{pc: bases}`` of its loads."""
    m = C.machine(state)
    seen, chunk = np.zeros(0, np.uint64), []
    for t in tqdm(range(calls), desc="accesses", unit="call", mininterval=1):
        _trace, log, based = m.run_to_rts_accesses(play, 0, C.MAX_CYCLES, bases=True)
        key = np.frombuffer(log, np.uint64) << np.uint64(16)
        chunk.append(np.unique(key | np.frombuffer(based, np.uint16)))
        if len(chunk) == CHUNK or t == calls - 1:
            seen = np.union1d(seen, np.concatenate(chunk))
            chunk = []
    keys = np.unique(seen >> np.uint64(16))
    bases = {}
    for k in seen[(seen & np.uint64(WRITE << 16)) == 0].tolist():
        bases.setdefault(k >> 33, set()).add(k & 0xFFFF)
    return keys, bases


def observe(data, song=None, limit=LIMIT):
    """:class:`Orbit` of 0-based ``song`` (default the start song) of PSID ``data`` booted on
    the reference machine (``sidtrace.boot``), over at most ``limit`` calls."""
    mpu, _mem, play = sidtrace.boot(data, song)
    comps = C.learn(mpu, play, LEARN)
    calls, comps = C.cover_calls(mpu, play, comps, limit)
    learned = comps.calls
    flows = C.explicit(mpu, play, learned)
    _cd, written = C.control(mpu, play, learned)
    keys, bases = accesses(mpu, play, learned)
    rows, cyc = trajectory(mpu, play, comps, max(4 * learned, LEARN), limit)
    return Orbit(
        comps,
        flows,
        frozenset(written),
        calls,
        learned,
        keys,
        bases,
        rows,
        cyc,
        bytes(mpu.memory),
    )


def trajectory(state, play, comps, start, limit):
    """``(rows, cycles)``: a trajectory of the persistent locations long enough for each
    closure's cycle to repeat twice, grown from ``start`` up to ``limit`` calls."""
    n = min(start, limit)
    while True:
        rows = C.trajectory(state, play, comps.persistent, n)
        cyc = C.cycles(state, play, comps, n, rows=rows)
        need = max(
            (mu + 2 * lam for mu, lam in filter(None, cyc.closures.values())), default=0
        )
        if n >= limit or (need <= n and None not in cyc.closures.values()):
            return rows, cyc
        n = min(limit, max(4 * n, need))


def _song_name(orbit, pc):
    """A table read at ``pc`` by its one base (``$hhhh[]``), else through a pointer: the
    zero-page operand of ``(zp),Y`` or ``(zp,X)`` at ``pc`` (``($zz)@$pppp``), a patched
    operand by its address (``($hhhh)[]``)."""
    bases = orbit.bases.get(pc, ())
    if len(bases) == 1:
        return f"${next(iter(bases)):04X}[]"
    if isa.mode(orbit.ram[pc]) in ("indy", "indx"):
        return f"(${orbit.ram[pc + 1]:02X})@${pc:04X}"
    return f"(${pc + 1:04X})[]"


def graph(orbit):  # pylint: disable=too-many-locals
    """``networkx.MultiDiGraph`` of ``orbit`` with flowgraph's node and edge kinds; each edge's
    ``witness`` holds the taints backing it: a jennings site key, or ``(map, location)`` of
    the explicit ``deps``, ``stored``, ``addressed`` or ``gated`` it is in.
    """
    persistent = set(orbit.comps.persistent)
    flows, sites = orbit.flows, orbit.flows.sites
    keys = orbit.accesses
    pcs = (keys >> np.uint64(17)).astype(int).tolist()
    addrs = (keys & np.uint64(0xFFFF)).astype(int).tolist()
    writes = ((keys & np.uint64(WRITE)) != 0).tolist()
    accs = list(zip(pcs, addrs, writes))
    song = {
        pc: _song_name(orbit, pc)
        for pc in sorted({p for p, a, w in accs if not w and a not in orbit.written})
    }

    def node(loc):
        if loc in persistent:
            return C.location(loc)
        if loc >= C.CONST and loc - C.CONST in song:
            return song[loc - C.CONST]
        return None

    witness, kinds = {}, {}

    def link(srcs, dst, kind, why):
        for s in srcs:
            u = node(s)
            if u is not None:
                witness.setdefault((u, dst, kind), set()).add(why)

    for loc in persistent:
        n = C.location(loc)
        kinds[n] = "cell"
        link(orbit.flows.deps.get(loc, ()), n, "value", ("deps", loc))
    for pc, a, w in accs:
        dst = C.location(a) if a in persistent else song.get(pc)
        if not w and dst is not None:
            kinds.setdefault(dst, "song")
            link(sites.get((pc, C.LOAD), ()), dst, "address", (pc, C.LOAD))
    for loc in sorted(set(flows.stored) | set(flows.addressed) | set(flows.gated)):
        if loc in SID:
            dst = f"SID+${loc - SID.start:02X}"
            kinds[dst] = "sid"
            link(flows.stored.get(loc, ()), dst, "value", ("stored", loc))
        elif loc in persistent:
            dst = C.location(loc)
        else:
            continue
        link(flows.addressed.get(loc, ()), dst, "address", ("addressed", loc))
        link(flows.gated.get(loc, ()), dst, "control", ("gated", loc))
    g = nx.MultiDiGraph()
    for n, k in kinds.items():
        g.add_node(n, kind=k)
    for (u, v, k), why in sorted(witness.items()):
        g.add_edge(u, v, key=k, kind=k, weight=len(why), witness=sorted(why, key=str))
    return g


def registers(orbit):
    """The node names of the persistent registers."""
    return [C.location(loc) for loc in orbit.comps.persistent if loc >= C.REG]


def projection(sub, mu, lam):
    """``(preperiod, period)`` of the rows of ``sub``, periodic with ``lam`` from ``mu``."""
    period = next(
        p
        for p in range(1, lam + 1)
        if lam % p == 0 and (sub[mu + p : mu + lam + p] == sub[mu : mu + lam]).all()
    )
    bad = np.flatnonzero((sub[period : mu + period] != sub[:mu]).any(axis=1))
    return (int(bad[-1]) + 1 if bad.size else 0), period


def cycle(orbit, locs):
    """``(preperiod, period)`` of the locations ``locs`` over ``orbit``'s trajectory, from the
    union of the smallest autonomous closure holding each; None if one does not repeat.
    """
    closures = orbit.comps.closures
    held = {
        min((len(c), i) for i, c in enumerate(closures) if loc in c)[1] for loc in locs
    }
    cycs = [orbit.cycles.closures.get(i) for i in held]
    if not cycs or None in cycs:
        return None
    mu, lam = max(c[0] for c in cycs), math.lcm(*(c[1] for c in cycs))
    if mu + 2 * lam > len(orbit.rows):
        return mu, lam
    col = {c: i for i, c in enumerate(orbit.comps.persistent)}
    sub = np.ascontiguousarray(orbit.rows[:, sorted(col[c] for c in locs)])
    return projection(sub, mu, lam)


def locations(orbit, names):
    """The persistent locations named ``names``."""
    by = {C.location(loc): loc for loc in orbit.comps.persistent}
    return [by[n] for n in names if n in by]


def voice(g, nodes):
    """The SID voice (0-2) whose registers alone ``nodes`` reach in ``g`` through cells only
    (a song table read by every voice's code ties no voice), else None."""
    cells = g.subgraph(n for n, k in g.nodes(data="kind") if k != "song")
    reach = set(nodes).union(*(nx.descendants(cells, n) for n in nodes if n in cells))
    regs = {
        int(v[5:], 16) // flowsegment.VOICE
        for v in reach
        if g.nodes[v]["kind"] == "sid"
        and int(v[5:], 16) < flowsegment.VOICE * flowsegment.VOICES
    }
    return regs.pop() if len(regs) == 1 else None


def events(col):
    """Per call transition, whether the column ``col`` changes."""
    return col[1:] != col[:-1]


def tracker(orbit, q, roles):  # pylint: disable=too-many-locals
    """``{machine: [tracker role]}``: ``order position`` and ``pattern row`` for table machine
    cells, one restarting the other at each of its changes; ``tick`` an autonomous counter
    gating machines; ``row timer`` a counter reloaded from song data gating a sequencer;
    ``program`` positions by the register groups their tables drive."""
    span = len(orbit.rows)
    if orbit.calls is not None:
        span = min(span, orbit.calls + 1)
    col = {c: i for i, c in enumerate(orbit.comps.persistent)}
    rows = orbit.rows[:span]
    seq = {
        loc: n
        for n, r in roles.items()
        if r[0] in ("sequencer", "program")
        for loc in locations(orbit, q.nodes[n]["members"])
    }
    ev = {loc: events(rows[:, col[loc]]) for loc in seq}
    out = {n: set() for n in roles}
    for o, eo in ev.items():
        for r, er in ev.items():
            if o == r or not eo.any() or (eo & ~er).any() or (er == eo).all():
                continue
            if len(set(rows[1:, col[r]][eo].tolist())) == 1:
                out[seq[o]].add("order position")
                out[seq[r]].add("pattern row")
    sequencers = {n for n, r in roles.items() if r[0] == "sequencer"}
    for n, r in roles.items():
        locs = locations(orbit, q.nodes[n]["members"])
        if r[0] == "program":
            out[n].add(f"program ({'/'.join(r[1])})")
        if r[0] not in ("timer", "flag") or not locs:
            continue
        held = set().union(*(orbit.comps.deps.get(loc, frozenset()) for loc in locs))
        from_song = any(q.nodes[u]["kind"] == "song" for u in q.predecessors(n))
        if held <= set(locs) and r[4]:
            out[n].add("tick")
        elif from_song and set(r[4]) & sequencers:
            out[n].add("row timer")
    return {n: sorted(v) for n, v in out.items() if v}


def segment(data, song=None, limit=LIMIT):
    """``(state machine graph, orbit)`` of PSID ``data``'s play: tools/flowsegment.py's
    machines, classes and quotient over :func:`graph`, the registers contracted; each node's
    ``voice`` by its members (:func:`voice`)."""
    orbit = observe(data, song, limit)
    g = flowsegment.contract(graph(orbit), registers(orbit))
    g = flowsegment.machines(g)
    q = flowsegment.quotient(g, flowsegment.classes(g))
    for n, members in q.nodes(data="members"):
        q.nodes[n]["voice"] = voice(g, members)
    return q, orbit


def report(q, orbit):
    """JSON summary of a state machine graph from :func:`segment`: per machine its role,
    register groups, tables, gates, cycle, feed membership, voice and tracker roles, and the
    coverage."""
    roles = flowsegment.roles(q)
    track = tracker(orbit, q, roles)
    feed = set(orbit.comps.feed)
    machines = {}
    for n, r in roles.items():
        locs = locations(orbit, q.nodes[n]["members"])
        machines[n] = {
            "role": r[0],
            "groups": r[1],
            "tables": r[2],
            "gated_groups": r[3],
            "gated_machines": r[4],
            "members": q.nodes[n]["members"],
            "cycle": cycle(orbit, locs),
            "feed": bool(feed & set(locs)),
            "voice": q.nodes[n]["voice"],
            "tracker": track.get(n, []),
        }
    return {
        **flowsegment.summary(q),
        "roles": dict(Counter(m["role"] for m in machines.values())),
        "machines": machines,
        "coverage": {
            "cover_calls": orbit.calls,
            "feed_repeated": orbit.calls is not None,
            "feed_cycle": orbit.cycles.feed,
            "learned_calls": orbit.learned,
            "persistent": len(orbit.comps.persistent),
            "feed": len(feed),
            "closures": len(orbit.comps.closures),
        },
    }


COLOUR = {
    "sequencer": "#f4a582",
    "program": "#fddbc7",
    "modulator": "#d1e5f0",
    "timer": "#b8e186",
    "flag": "#e0e0e0",
}
LINE = {"address": "dashed", "control": "dotted", "value": "solid"}


def dot(q, rep):
    """Graphviz DOT text of a state machine graph with :func:`report`'s ``rep``: machines
    coloured by role, labelled with their cycle, table count and tracker roles, clustered per
    voice; song tables contracted into the paths through them; control edges grey."""
    machines = rep["machines"]
    shown = flowsegment.contract(q, [n for n, k in q.nodes(data="kind") if k == "song"])

    def line(n):
        m = machines.get(n)
        if m is None:
            return f'"{n}" [shape=box, style=filled, fillcolor="lightblue"];'
        label = [n, m["role"] + ("" if m["feed"] else ", arithmetic")]
        if m["cycle"]:
            mu, lam = m["cycle"]
            label.append(f"period {lam}, preperiod {mu}")
        if m["tables"]:
            label.append(f"{len(m['tables'])} tables")
        text = "\\n".join(label + m["tracker"])
        style = "filled" if m["feed"] else "filled,dashed"
        return (
            f'"{n}" [label="{text}", style="{style}", fillcolor="{COLOUR[m["role"]]}"];'
        )

    by = {}
    for n in shown:
        by.setdefault(q.nodes[n]["voice"], []).append(n)
    out = ["digraph machines {", "rankdir=LR;", "node [fontsize=10];"]
    for v, ns in sorted(by.items(), key=lambda kv: (kv[0] is None, kv[0] or 0)):
        if v is not None:
            out.append(
                f'subgraph cluster_voice{v} {{ label="voice {v + 1}"; color=grey;'
            )
        out += [line(n) for n in ns]
        if v is not None:
            out.append("}")
    out.append('subgraph cluster_legend { label="legend"; fontsize=10;')
    for r, fill in COLOUR.items():
        out.append(f'"legend {r}" [label="{r}", style=filled, fillcolor="{fill}"];')
    out.append(
        '"legend arithmetic" [label="outside the feed set", style="filled,dashed"];'
    )
    out.append("}")
    for u, v, d in shown.edges(data=True):
        width = min(1 + d["weight"] / 4, 4)
        extra = ', color="grey60", constraint=false' if d["kind"] == "control" else ""
        out.append(
            f'"{u}" -> "{v}" [style={LINE[d["kind"]]}, penwidth={width:.1f}{extra}];'
        )
    return "\n".join(out + ["}"]) + "\n"


def main(argv=None):
    """CLI: write OUT.dot, OUT.png, OUT.svg and OUT.json of a PSID's play state machines."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("psid")
    ap.add_argument("out", help="output path stem")
    ap.add_argument("--song", type=int, help="1-based subtune (default: start song)")
    ap.add_argument("--limit", type=int, default=LIMIT, help="the most calls run")
    args = ap.parse_args(argv)
    song = None if args.song is None else args.song - 1
    q, orbit = segment(pathlib.Path(args.psid).read_bytes(), song, args.limit)
    rep = report(q, orbit)
    out = pathlib.Path(args.out)
    flowgraph.render(out, dot(q, rep), "png", "svg")
    out.with_suffix(".json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    print(json.dumps({k: rep[k] for k in ("nodes", "roles", "coverage")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
