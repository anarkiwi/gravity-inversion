"""Tests for tools/irflow.py, tools/flowgraph.py, tools/flowsegment.py and tools/condition.py:
value flow and def-use webs of a small C player, the segmentation's contractions and classes,
and the hand conditioning of generated C."""

import json

import networkx as nx
import pytest

import condition
import irflow
import flowgraph
import flowsegment

PLAYER = """#include <stdint.h>
void io_write(uint16_t a, uint8_t v);
static const uint8_t tab[4] = {1, 2, 3, 4};
uint8_t t, pos;
static void voice(uint8_t k) {
  t = tab[(pos + k) & 3];
  io_write(0xD400 + 7 * k, t);
}
void cgen_init(uint8_t song) { pos = song; }
void cgen_play(void) {
  t = tab[pos & 3];
  io_write(0xD418, t);
  voice(1);
  pos = pos + 1;
}
"""


@pytest.fixture(name="player")
def fixture_player(tmp_path):
    """The small player's .c with an empty .h beside it."""
    c = tmp_path / "gen.c"
    c.write_text(PLAYER, encoding="ascii")
    c.with_suffix(".h").write_text("", encoding="ascii")
    return c


def test_webs(player):
    """A scratch cell is a web per store, but the store reaching play's end is the value
    kept between calls, the cell's own name; the position is fed from itself; init is
    outside play's graph."""
    g, s = flowgraph.graph(player, entries=["cgen_play"], split=True)
    lines = PLAYER.split("\n")
    t = sorted(n for n in g if n.startswith("t+0"))
    assert t[0] == "t+0" and len(t) == 2
    assert lines[int(t[1].split("@")[1]) - 1].strip() == "t = tab[pos & 3];"
    assert g.has_edge("pos+0", "pos+0") and "pos+0" in g
    assert {n for n, k in g.nodes(data="kind") if k == "sid"} == {"SID+$00", "SID+$18"}
    assert not s["residual_calls"]
    whole, _ = flowgraph.graph(player)
    assert "t+0" in whole and not any("@" in n for n in whole)


def test_segment(player):
    """Play's state machines: the position, the table it indexes, the registers written."""
    q = flowsegment.segment(player)
    assert sorted(n for n, k in q.nodes(data="kind") if k == "cell") == ["pos+0"]
    assert flowsegment.summary(q)["value_cycles"] == []
    assert flowsegment.roles(q) == {"pos+0": ("program", ["freq", "volume"], ["tab+0"])}
    assert irflow.bases(
        "@K4 = internal constant [2 x ptr] [ptr @C1, ptr getelementptr (i8, ptr @C1, i64 8)]\n"
        "@K5 = internal constant [2 x ptr] [ptr @C1, ptr @C2]\n"
    ) == {"K4": "C1"}
    dot = flowgraph.dot(q)
    assert '"pos+0" -> "tab+0" [style=dashed' in dot


def _g(edges, kinds):
    g = nx.MultiDiGraph()
    for n, k in kinds.items():
        g.add_node(n, kind=k)
    for u, v, k in edges:
        g.add_edge(u, v, kind=k, weight=1)
    return g


KINDS = {
    "song": "song",
    "a": "cell",
    "b": "cell",
    "SID+$00": "sid",
    "SID+$07": "sid",
    "SID+$01": "sid",
}


def test_classes_merge_voice_copies():
    """Copies feeding the same register of different voices share a class; a copy feeding
    another register does not."""
    g = _g([("song", "a", "value"), ("song", "b", "value")], KINDS)
    g.add_edges_from([("a", "SID+$00"), ("b", "SID+$07")], kind="value", weight=1)
    cls = flowsegment.classes(g)
    assert cls["a"] == cls["b"] and cls["SID+$00"] == cls["SID+$07"]
    g.remove_edge("b", "SID+$07")
    g.add_edge("b", "SID+$01", kind="value", weight=1)
    cls = flowsegment.classes(g)
    assert cls["a"] != cls["b"]
    xy = _g([], {"x": "cell", "y": "cell"})
    q = flowsegment.quotient(xy, flowsegment.classes(xy))
    assert sorted(q) == ["x, y (x2)"] and q.nodes["x, y (x2)"]["members"] == ["x", "y"]


def test_contractions():
    """Register cells and cells outside any cycle pass their sources on; an address through
    either edge stays an address; machines and song tables stay."""
    kinds = {
        "song": "song",
        "rv1+0@9": "cell",
        "m": "cell",
        "t": "cell",
        "SID+$00": "sid",
    }
    g = _g(
        [
            ("m", "m", "value"),
            ("m", "rv1+0@9", "value"),
            ("rv1+0@9", "song", "address"),
            ("song", "t", "value"),
            ("t", "SID+$00", "value"),
        ],
        kinds,
    )
    r = flowsegment.registers(g)
    assert "rv1+0@9" not in r and r.has_edge("m", "song")
    assert r.get_edge_data("m", "song")[0]["kind"] == "address"
    m = flowsegment.machines(r)
    assert set(m) == {"song", "m", "SID+$00"} and m.has_edge("song", "SID+$00")
    regs = _g([], {"SID+$15": "sid", "SID+$0E": "sid", "IO D418": "sid"})
    assert [flowsegment.role(regs, n) for n in regs] == ["sid+15", "voice+0", "sid"]


HEADER = """#define C0_ORG 0x1000
#define C0_LEN 256
#define C1_ORG 0x2000
#define C1_LEN 16
#define R5_ORG 0x1F01
#define R5_LEN 2
"""


def test_base_calls_direct():
    """``wr`` into code becomes a store into the span holding its address, ``codechk`` 0;
    the address of an expression takes span origins and every variable 0."""
    org = condition.origins(HEADER)
    assert condition.base("(uint16_t)(C1_ORG + 3 + v7)", org) == 0x2003
    assert condition.base("C0_ORG | (v2 << 8)", org) == 0x1000
    with pytest.raises(ValueError):
        condition.base("f(v1)", org)
    c = "static void wr(uint16_t a, uint8_t v);\nvoid cgen_init(void) {\n"
    c += "  wr(C1_ORG + 1 + v3, f(v2, v4));\n  if (codechk(v1 + (v2)) != 0) {}\n}\n"
    out, n, k = condition.direct(c, HEADER)
    assert (n, k) == (1, 1)
    assert "C1[(uint16_t)(C1_ORG + 1 + v3) - C1_ORG] = f(v2, v4)" in out
    assert "if (0 != 0)" in out and out.startswith("static void wr(")
    assert list(condition.calls("g(a, (b, c))", "g")) == [(0, 12, ["a", "(b, c)"])]


def test_sidwizard():
    """Relocated operand pairs read through one pointer per header word, set where the
    relocator ends."""
    load = 0x1000
    image = bytearray(0x1000)
    for i, (site, index, off) in enumerate(
        [
            (0x1F00, 0, 0),
            (0x1F00, 0, 0),
            (0x1F00, 0, 0),
            (0x1F00, 0, 0),
            (0x1F00, 6, 0xFE),
        ]
    ):
        image[condition.SW_SITE + 2 * i - load] = site & 0xFF
        image[condition.SW_SITE + 2 * i + 1 - load] = site >> 8
        image[condition.SW_INDEX + 2 * i - load] = index
        image[condition.SW_OFFSET + 2 * i - load] = off
    c = "#include <stdint.h>\nvoid cgen_init(void) {\n  x = R5[0] | ((uint16_t)R5[1] << 8);\n"
    c += "  if ((uint16_t)(a - R63_ORG) < R63_LEN) {}\n}\n"
    out, pointers, reads = condition.sidwizard(c, HEADER, image, load)
    assert (pointers, reads) == (5, 1)
    assert "x = (uint16_t)(cgen_ptr[P_PTN_LO] + (-2));" in out
    assert (
        "cgen_ptr[P_PTN_LO] = (uint16_t)((((uint16_t)R5[1] << 8) | R5[0]) - (-2));"
        in out
    )
    assert out.index("cgen_ptr[P_PTN_LO] = ") < out.index("R63_ORG")
    with pytest.raises(ValueError):
        condition.sidwizard("#include <stdint.h>\n", HEADER, image, load)


def test_tune_path():
    """A fixture name resolves under fixtures/, anything else under HVSC."""
    assert condition.tune_path("Emomyst.sid").parent.name == "fixtures"
    assert condition.tune_path("DEMOS/x.sid") == condition.HVSC / "DEMOS/x.sid"


def test_main(tmp_path, capsys):
    """The SID-Wizard fixture lifted, conditioned and traced exactly; its graphs written by
    each tool's CLI."""
    assert condition.main(["sidwizard", "--out", str(tmp_path), "--frames", "50"]) == 0
    row = json.loads((tmp_path / "sidwizard" / "row.json").read_text(encoding="utf-8"))
    assert row["trace"] == "exact 50" and row["pointers"] == len(condition.SW_WORDS) + 3
    assert (tmp_path / "sidwizard" / "machines.png").stat().st_size
    with pytest.raises(SystemExit):
        condition.main(["nosuch"])
    c = tmp_path / "sidwizard" / "gen.c"
    assert flowsegment.main([str(c), str(tmp_path / "seg")]) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["nodes"]["sid"]
    psid = condition.tune_path("Emomyst.sid")
    assert flowgraph.main([str(psid), str(tmp_path / "g" / "flow")]) == 0
    assert (tmp_path / "g" / "flow.svg").stat().st_size


def test_roles():
    """A sequencer indexes a table and restarts another machine; a program indexes a table
    feeding registers; a modulator feeds registers itself; a timer feeds only machines; a
    flag feeds neither."""
    kinds = {"seq": "cell", "prog": "cell", "mod": "cell", "tim": "cell", "flg": "cell"}
    kinds.update({"order": "song", "wave": "song", "SID+$04": "sid", "SID+$09": "sid"})
    g = _g(
        [
            ("seq", "order", "address"),
            ("order", "prog", "value"),
            ("seq", "prog", "value"),
            ("prog", "wave", "address"),
            ("wave", "SID+$04", "value"),
            ("mod", "SID+$09", "value"),
            ("tim", "mod", "value"),
        ],
        kinds,
    )
    r = flowsegment.roles(g)
    assert r["seq"] == ("sequencer", ["control"], ["order"])
    assert r["prog"] == ("program", ["control"], ["wave"])
    assert r["mod"] == ("modulator", ["pulse"], [])
    assert r["tim"][0] == "timer" and r["flg"][0] == "flag"
