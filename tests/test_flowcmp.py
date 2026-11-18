"""Tests for tools/flowcmp.py: a tune's value-flow graph and state machines are the same with
and without wayward-eagle's rewrites."""

import json

import networkx as nx
import pytest
from deity_informant.cgen import printer

import flowcmp


def test_rewrites_swap_and_restore_the_reducer():
    """Inside ``rewrites(False)`` the printer leaves each function whole, one piece returning
    what it did; outside it the reducer is back, also after an error."""
    keep = printer.reduced
    with flowcmp.rewrites(False):
        assert printer.reduced is flowcmp.unreduced
    assert printer.reduced is keep
    with pytest.raises(RuntimeError), flowcmp.rewrites(False):
        raise RuntimeError
    assert printer.reduced is keep
    fn = flowcmp.wayward_eagle.Function(["a"], set(), False)
    pieces, sections, temps = flowcmp.unreduced(["x"], "f", fn)
    assert (pieces[0].name, pieces[0].ret, sections, temps) == ("f", "status", 1, set())


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
