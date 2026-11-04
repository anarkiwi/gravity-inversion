"""tools/passes.py: the facts docs/passes.md's proof rests on, measured on the five tunes."""

import functools

import networkx as nx
import pytest

import constructs
import passes

TUNES = ("commando", "incoherent", "automatas", "ghouls", "emomyst")


@functools.cache
def loops(tune):
    """{entry: (natural back edges, multi-entry retreating edges)}."""
    return passes.analyse(tune)


@pytest.mark.slow
@pytest.mark.parametrize("tune", TUNES)
def test_multi_entry_loops_are_mapped(tune):
    """The only loops whose header does not dominate them are the map's multi-entry loops."""
    found = {e for _, multi in loops(tune).values() for e in multi}
    mapped = {
        tuple(e)
        for c in constructs.load(tune)["constructs"]
        if c["kind"] == "multi_entry_loop"
        for e in [[c["back"], c["head"]]] + c.get("copies", [])
    }
    assert found == mapped


@pytest.mark.slow
def test_post_dominators_need_loops():
    """Emomyst's init loop exit $1151 -> $115B: the raw graph's post-dominators make $115B the
    branch's join, but it leaves the loop at a target other than the loop's follow $1158.
    """
    _, data, cov, starts = constructs.run("emomyst", 4)
    _, init, play, _ = passes.psid_parts(data)
    g = passes.procedures(constructs.memory(data), cov, starts, [init, play])[init]
    ipdom = nx.immediate_dominators(g.reverse(), passes.EXIT)
    assert ipdom[0x1151] == 0x115B
    natural, _ = passes.loops(g, init)
    ((latch, head),) = [e for e in natural if e[1] == 0x1147]
    body = {head} | nx.ancestors(g.subgraph(set(g) - {head}), latch) | {latch}
    assert 0x1151 in body and 0x115B not in body and 0x1158 not in body


def test_main(capsys):
    """The CLI prints each tune's loops."""
    passes.main(["commando"])
    assert "commando: 2 procedures" in capsys.readouterr().out
