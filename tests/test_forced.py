"""Tests for tools/forced.py on a synthetic PSID whose play has branches a song takes one way:
into code, into code with a branch of its own, and into the song table.
"""

import json

import numpy as np
import pytest
from jennings.acme import assemble

import forced
import fuzzplayer
import liftcheck
import transliterate

PLAYER = """
init:
    rts
play:
    ldy pos
    lda song,y
bsong bmi special
back:
    sta $d400
    lda mode
bmode bne song
    iny
    tya
    and #3
    sta pos
    rts
special:
    lda deep
bdeep beq +
    inc extra
+   lda #0
    jmp back
song !byte $60, $18, $38, $60
pos !byte 0
mode !byte 0
deep !byte 0
extra !byte 0
"""


@pytest.fixture(scope="module", name="tune")
def fixture_tune():
    """(PSID, symbols) of the synthetic player."""
    return assemble(fuzzplayer.HEADER + PLAYER + "\n}\n")


@pytest.fixture(scope="module", name="base")
def fixture_base(tune):
    """(image, play, proven) of the synthetic PSID's post-init state."""
    play, (image,) = liftcheck.song_states(tune[0])
    return image, play, forced.proven(image, play, transliterate.VOLATILE)


def test_one_way_branches(tune, base):
    """The certificate has the two branches of play's own path one-way, each with the
    successor it takes; the table is song data and no instruction is."""
    _, syms = tune
    image, _, (edges, first, data) = base
    mem = np.frombuffer(image, np.uint8)
    one = forced.gaps(edges, mem)
    assert one == {syms["bsong"]: syms["bsong"] + 2, syms["bmode"]: syms["bmode"] + 2}
    assert forced.other_side(mem, syms["bsong"], one[syms["bsong"]]) == syms["special"]
    assert forced.other_side(mem, syms["bsong"], syms["special"]) == syms["bsong"] + 2
    assert data[syms["song"] : syms["song"] + 4].all() and not data[syms["play"]]
    assert first[syms["play"]] == 0 and syms["special"] not in first


def test_forced_run(tune, base):
    """A forced branch runs its other side to the return; one never reached gives None; one
    that runs the song table, or leaves the code given, is refused."""
    _, syms = tune
    image, play, (_, _, data) = base
    shot, code = forced.snapshots(image, play, [0])[0], ~data
    got = forced.run(shot, play, ((syms["bsong"], syms["special"]),), code)
    taken, inner = (
        syms["bsong"] << 16 | syms["special"],
        syms["bdeep"] << 16 | syms["bdeep"] + 2,
    )
    assert taken in got.tolist() and inner not in got.tolist()
    assert forced.run(shot, play, ((0xFFFF, 0),), code) is None
    assert forced.run(shot, play, ((syms["bmode"], syms["song"]),), code) is False
    assert forced.run(shot, play, ((syms["bsong"], syms["special"]),), ~code) is False
    deep = ((syms["bsong"], syms["special"]), (syms["bdeep"], syms["bdeep"] + 2))
    assert inner in forced.run(shot, play, deep, code)
    assert forced.run(shot, play, (deep[0], (syms["bmode"] + 99, 0)), code) is None


def test_report(tune):
    """Forcing leaves only the branch into the song table one-way, reported as a stray; the
    branch found in forced code is forced too, and no forced edge is one the song takes.
    """
    psid, syms = tune
    got = forced.report(psid)
    assert got["one_way"] == [2, 1] and got["left"] == [syms["bmode"]]
    assert got["strays"] == [[syms["bmode"], syms["song"]]]
    assert got["instructions"][1] > got["instructions"][0] and got["runs"] == 2
    edges = {tuple(e) for e in got["forced_edges"]}
    assert {
        (syms["bdeep"], syms["bdeep"] + 2),
        (syms["bdeep"], syms["bdeep"] + 5),
    } <= edges
    assert (syms["bsong"], syms["bsong"] + 2) not in edges


def test_cli(tune, tmp_path, capsys):
    """The command line prints each tune's counts and writes the same report as JSON."""
    path, out = tmp_path / "synthetic.sid", tmp_path / "f.json"
    path.write_bytes(tune[0])
    assert forced.main([str(path), "--json", str(out)]) == 0
    got = json.loads(out.read_text("utf-8"))[str(path)]
    assert got["one_way"] == [2, 1]
    text = capsys.readouterr().out
    assert "synthetic: instructions" in text and f"left ${tune[1]['bmode']:04X}" in text
