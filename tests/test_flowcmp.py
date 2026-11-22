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


def _commando(capsys, out, form, *args):
    """Commando's CLI row for ``out`` and ``args``, after checking it exits 0 and the graph and
    machines of ``form`` have the reduced form's edge counts."""
    assert flowcmp.main([str(out), "commando", *args]) == 0
    row = json.loads(capsys.readouterr().out.strip().splitlines()[-1])["commando"]
    for part in ("graph", "machines"):
        assert row[part][form] == row[part]["reduced"] > 0
    return row


@pytest.mark.slow
def test_commando_keeps_its_flow(tmp_path, capsys):
    """Commando's graph and machines have no edge one form has that the other lacks; the CLI
    says so and exits 0."""
    row = _commando(capsys, tmp_path, "raw", "--edges")
    for part in ("graph", "machines"):
        assert row[part]["edges"] == {"lost": [], "gained": []}
    assert (tmp_path / "commando" / "reduced" / "machines.dot").exists()


@pytest.mark.slow
def test_split_keeps_flow_and_size(tmp_path, capsys):
    """Commando as translation units has the one file's graph and machines, and its PRG."""
    row = _commando(capsys, tmp_path, "split", "--against", "split")
    assert row["prg"]["split"] == row["prg"]["reduced"]
    assert (tmp_path / "commando" / "split" / "gen_play.c").exists()
