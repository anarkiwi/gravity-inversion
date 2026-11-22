"""Tests for tools/orbitflow.py: the orbit source of tools/flowsegment.py on a synthetic PSID
whose play steps an order list and pattern rows on a tick, a wave program and a flag."""

import json

import networkx as nx
import numpy as np
import pytest
from jennings.acme import assemble

import flowsegment
import fuzzplayer
import orbitflow

PLAYER = """
init:
    rts
play:
    dec tick
    bpl wave
    lda #2
    sta tick
    ldx ord
    lda order,x
    clc
    adc row
    tay
    lda notes,y
    sta $d401
    lda inst,y
    sta wpos
    inc row
    lda row
    cmp #4
    bne wave
    lda #0
    sta row
    inx
    cpx #3
    bne +
    ldx #0
+   stx ord
wave:
    ldx wpos
    lda wavetab,x
    sta $d404
    inx
    cpx #8
    bne +
    ldx #0
+   stx wpos
    lda flag
    eor #1
    sta flag
    beq +
    lda #$09
    sta $d405
+   rts
tick !byte 0
ord !byte 0
row !byte 0
wpos !byte 0
flag !byte 0
order !byte 0, 4, 0
notes !byte $10, $12, $14, $16, $20, $22, $24, $26
inst !byte 0, 4, 0, 4, 4, 0, 4, 0
wavetab !byte $41, $41, $40, $40, $11, $11, $10, $10
"""


VOICES = """
init:
    rts
play:
    ldx #14
-   lda pos,x
    sta $d400,x
    inc pos,x
    dex
    dex
    dex
    dex
    dex
    dex
    dex
    bpl -
    rts
pos !byte 0, 0, 0, 0, 0, 0, 0, 5, 0, 0, 0, 0, 0, 0, 9
"""


@pytest.fixture(scope="module", name="tune")
def fixture_tune():
    """The synthetic PSID, its symbols, its state machine graph, orbit and report."""
    psid, syms = assemble(fuzzplayer.HEADER + PLAYER + "\n}\n")
    q, orbit = orbitflow.segment(psid, limit=4096)
    return psid, syms, q, orbit, orbitflow.report(q, orbit)


def _named(syms, rep, sym):
    name = f"${syms[sym]:04X}"
    return next(
        m for n, m in rep["machines"].items() if name in m["members"] or n == name
    )


def test_machines_roles_and_periods(tune):
    """The tick, flag, order, row and wave program machines, roles and periods."""
    _psid, syms, _q, _orbit, rep = tune
    tick, flag = _named(syms, rep, "tick"), _named(syms, rep, "flag")
    row, ord_ = _named(syms, rep, "row"), _named(syms, rep, "ord")
    wpos = _named(syms, rep, "wpos")
    assert (tick["role"], tick["cycle"], tick["tracker"]) == ("flag", (0, 3), ["tick"])
    assert (flag["role"], flag["cycle"], flag["tracker"]) == ("flag", (0, 2), [])
    assert flag["gated_groups"] == ["adsr"] and not flag["gated_machines"]
    assert row["role"] == "sequencer" and row["cycle"][1] == 12
    assert "pattern row" in row["tracker"]
    assert ord_["cycle"][1] == 36 and "order position" in ord_["tracker"]
    assert wpos["role"] == "program" and wpos["groups"] == ["control"]
    assert all(m["feed"] for m in (tick, flag, row, ord_, wpos))
    assert rep["coverage"]["feed_repeated"]
    assert rep["coverage"]["cover_calls"] <= rep["coverage"]["learned_calls"]


def test_every_edge_is_backed_by_a_taint(tune):
    """Each edge names the taints behind it; value, address and control stay apart."""
    _psid, syms, _q, orbit, _rep = tune
    g = orbitflow.graph(orbit)
    assert g.number_of_edges()
    for u, v, d in g.edges(data=True):
        assert d["witness"] and d["weight"] == len(d["witness"])
        for why in d["witness"]:
            if isinstance(why[0], str):
                srcs = getattr(orbit.flows, why[0])[why[1]]
            else:
                srcs = orbit.flows.sites[why]
            assert u in {orbitflow.C.location(s) for s in srcs} or any(
                s >= orbitflow.C.CONST for s in srcs
            ), (u, v, why)
    notes = f"${syms['notes']:04X}[]"
    assert g.nodes[notes]["kind"] == "song"
    assert flowsegment.edge_kinds(g, notes, "SID+$01") == {"value"}
    assert flowsegment.edge_kinds(g, f"${syms['row']:04X}", notes) == {"address"}
    tick = f"${syms['tick']:04X}"
    assert flowsegment.edge_kinds(g, tick, "SID+$01") == {"control"}
    assert "control" not in flowsegment.edge_kinds(g, tick, "SID+$04")


def test_projection_refines_a_closure_cycle():
    """A projection's period divides its closure's, its preperiod is at most its."""
    sub = np.array([[9], [1], [2], [1], [2], [1], [2]], np.uint8)
    assert orbitflow.projection(sub, 1, 2) == (1, 2)
    flat = np.array([[1], [2], [1], [2], [1], [2], [1], [2], [1]], np.uint8)
    assert orbitflow.projection(flat, 0, 4) == (0, 2)


def test_voice_of_a_machine_is_the_voice_its_registers_share():
    """A machine is in the voice its registers share, song tables crossing voices."""
    g = nx.MultiDiGraph()
    g.add_nodes_from("abc", kind="cell")
    g.add_node("t", kind="song")
    g.add_nodes_from(["SID+$08", "SID+$0E", "SID+$18"], kind="sid")
    g.add_edge("a", "SID+$08", kind="value")
    g.add_edge("b", "SID+$08", kind="control")
    g.add_edge("b", "SID+$18", kind="value")
    g.add_edge("c", "t", kind="address")
    g.add_edge("t", "SID+$0E", kind="value")
    g.add_edge("t", "SID+$08", kind="value")
    assert orbitflow.voice(g, ["a", "b"]) == 1
    assert orbitflow.voice(g, ["c"]) is None
    assert orbitflow.voice(g, ["a", "c"]) == 1


def test_cli_writes_the_diagram(tmp_path, tune):
    """The CLI writes DOT, PNG, SVG and the JSON report."""
    psid = tmp_path / "t.sid"
    psid.write_bytes(tune[0])
    out = tmp_path / "m" / "t.1"
    argv = [str(psid), str(out), "--song", "1", "--limit", "4096"]
    assert orbitflow.main(argv) == 0
    rep = json.loads((tmp_path / "m" / "t.1.json").read_text(encoding="utf-8"))
    assert rep["roles"] == tune[4]["roles"]
    for suffix in (".png", ".svg"):
        assert (tmp_path / "m" / f"t.1{suffix}").stat().st_size
    text = (tmp_path / "m" / "t.1.dot").read_text(encoding="utf-8")
    assert "cluster_legend" in text and "period 3," in text


def test_one_store_serving_three_voices_keeps_them_apart():
    """A per-voice cell feeds its own voice's register only, and lies in that voice."""
    psid, syms = assemble(fuzzplayer.HEADER + VOICES + "\n}\n")
    g = orbitflow.graph(orbitflow.observe(psid, limit=4096))
    pos = [f"${syms['pos'] + 7 * v:04X}" for v in range(3)]
    for v, cell in enumerate(pos):
        assert flowsegment.edge_kinds(g, cell, f"SID+${7 * v:02X}") == {"value"}
        assert not g.has_edge(cell, f"SID+${7 * ((v + 1) % 3):02X}")
        assert orbitflow.voice(g, [cell]) == v
