"""Tests for tools/flowcmp.py: a tune's value-flow graph and state machines are the same with
and without wayward-eagle's rewrites."""

import json

import networkx as nx
import pytest
from deity_informant.cgen import printer

import flowcmp


def test_rewrites_swap_and_restore_the_reducer():
    """Inside ``rewrites(False)`` the printer leaves each function whole, the one definition it
    was; outside it the reducer is back, also after an error."""
    keep = printer.reduce_function
    with flowcmp.rewrites(False):
        assert printer.reduce_function is flowcmp.unreduced
    assert printer.reduce_function is keep
    with pytest.raises(RuntimeError), flowcmp.rewrites(False):
        raise RuntimeError
    assert printer.reduce_function is keep
    fd = object()
    assert flowcmp.unreduced(fd, frozenset(), None) == [fd]


def test_edges_by_name_and_kind():
    """A multigraph's edges by node name and kind, parallel edges of other kinds apart."""
    g = nx.MultiDiGraph()
    g.add_edge("a", "b", kind="value")
    g.add_edge("a", "b", kind="control")
    assert flowcmp.edges(g) == {("a", "b", "value"), ("a", "b", "control")}


@pytest.mark.slow
def test_commando_keeps_its_flow(tmp_path, capsys):
    """Commando's graph and machines have no edge one form has that the other lacks; the CLI
    says so and exits 0."""
    assert flowcmp.main([str(tmp_path), "commando", "--edges"]) == 0
    row = json.loads(capsys.readouterr().out.strip().splitlines()[-1])["commando"]
    for part in ("graph", "machines"):
        assert row[part]["raw"] == row[part]["reduced"] > 0
        assert row[part]["edges"] == {"lost": [], "gained": []}
    assert (tmp_path / "commando" / "reduced" / "machines.dot").exists()


@pytest.mark.slow
def test_split_keeps_flow_and_size(tmp_path, capsys):
    """Commando as translation units has the one file's graph and machines, and its PRG."""
    assert flowcmp.main([str(tmp_path), "commando", "--against", "split"]) == 0
    row = json.loads(capsys.readouterr().out.strip().splitlines()[-1])["commando"]
    for part in ("graph", "machines"):
        assert row[part]["split"] == row[part]["reduced"] > 0
    assert row["prg"]["split"] == row["prg"]["reduced"]
    assert (tmp_path / "commando" / "split" / "gen_play.c").exists()


def test_against_base_counts_edges_lost_and_gained(tmp_path):
    """A form's edges against those another run wrote: each part's lost and gained, a
    promoted cell's element by its address whatever each run names it."""
    d = tmp_path / "t" / "reduced"
    d.mkdir(parents=True)
    old = {
        "graph": [
            ["a", "b", "value"],
            ["S_1+0", "c", "value"],
            ["V0_f00+1", "c", "value"],
        ],
        "machines": [["s", "t", None]],
        "vars": {"S_1": 0x1001, "V0_f00[1]": 0x1010},
    }
    (d / "edges.json").write_text(json.dumps(old), encoding="utf-8")
    graph = {("a", "b", "value"), ("g_ctrl+0", "c", "value"), ("g_ad+1", "c", "value")}
    got = (
        graph | {("c", "d", "value")},
        {("s", "t", None)},
        {"g_ctrl": 0x1001, "g_ad[1]": 0x1010},
    )
    assert flowcmp.against_base(tmp_path, "t", "reduced", got) == {
        "graph": {"lost": 0, "gained": 1},
        "machines": {"lost": 0, "gained": 0},
    }
    assert flowcmp.provenance("g_ad+2", got[2]) == "g_ad+2"
    assert flowcmp.provenance("$12, g_ctrl+0 (x2)", got[2]) == "$12, $1001 (x2)"
