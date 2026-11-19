"""Tests for tools/namestats.py: a translation's names summarized from its info."""

import json

import pytest

import namestats

INFO = {
    "names": {
        "atoms": {
            "g_ctrl": {"kind": "array", "label": "ctrl"},
            "g_ptr": {"kind": "pair", "label": None},
            "M_12": {"kind": "scalar", "label": None},
        },
        "accesses": {"atom": 5, "straddling": 1},
        "straddling": {0: [[1, 6]], 1: []},
        "variables": {"labelled": 5, "role": 2, "numbered": 3, "carry": 4},
    }
}


def test_summary_counts_atoms_accesses_and_names():
    """Atoms by kind, the labelled ones, straddling sets and the share of variables left
    numbered among the others than carries."""
    assert namestats.summary(INFO) == {
        "atoms": {"array": 1, "pair": 1, "scalar": 1},
        "labelled": 1,
        "accesses": {"atom": 5, "straddling": 1},
        "straddling": 1,
        "variables": {"labelled": 5, "role": 2, "numbered": 3, "carry": 4},
        "numbered": 0.3,
    }
    none = {"names": {**INFO["names"], "variables": {}}}
    assert namestats.summary(none)["numbered"] == 0.0


@pytest.mark.slow
def test_commando_names(capsys):
    """Commando's voice state is atoms, most of them labelled, few variables left numbered."""
    assert namestats.main(["commando"]) == 0
    row = json.loads(capsys.readouterr().out.strip().splitlines()[-1])["commando"]
    assert (
        row["atoms"]["array"] >= 10 and row["labelled"] >= 10 and row["numbered"] < 0.5
    )
