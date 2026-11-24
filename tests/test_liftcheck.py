"""Tests for tools/liftcheck.py on the sample PSID's lift."""

import json
import re
import shutil

import pytest
from deity_informant import orbit
from jennings.opcodes import OPCODES

import liftcheck
import sidtrace
import survey
import transliterate

FRAMES = 120
PROBE = """
int liftcheck_probe(int n) {
    if (n) goto done;
    n = 2;
done:
    return n;
}
"""


@pytest.fixture(scope="module", name="lifted")
def _lifted(sample_psid, tmp_path_factory):
    """(directory, unbounded count, measures) of the sample PSID's lift."""
    d = tmp_path_factory.mktemp("lift")
    n = liftcheck.lift(sample_psid, d)
    return d, n, liftcheck.measure(d, sample_psid, FRAMES)


def test_lift_measures(lifted):
    """The lift builds clean, replays exactly, and its gotos are the play units' own."""
    d, n, got = lifted
    assert got["werror"] == "clean"
    assert got["sidtrace"].startswith(f"OK {FRAMES} frames")
    if n is not None:
        assert n == len(json.loads((d / "unbounded.json").read_text("utf-8")))
    play = liftcheck.play_units(liftcheck.units(d))
    assert play and all(p.name.endswith(liftcheck.PLAY) for p in play)
    text = "".join(p.read_text("utf-8") for p in play)
    assert got["goto"] == len(re.findall(r"\bgoto\s+\w+\s*;", text))
    assert got["cognitive"] > 0


def test_hand_differences(lifted, sample_psid, tmp_path):
    """A rewrite with one more goto scores it; one that warns fails -Werror but still runs."""
    d, _, got = lifted
    hand = tmp_path / "hand"
    shutil.copytree(d, hand)
    (hand / "units").unlink()
    play = next(p for p in hand.glob("*_play.c"))
    play.write_text(play.read_text("utf-8") + PROBE, "utf-8")
    more = liftcheck.measure(hand, sample_psid, FRAMES)
    assert more["goto"] == got["goto"] + 1
    assert more["cognitive"] > got["cognitive"]
    assert more["werror"] == "clean"
    play.write_text(play.read_text("utf-8") + "int liftcheck_warn(int u) { int x; }\n")
    assert liftcheck.measure(hand, sample_psid, FRAMES)["werror"] == "FAILS"


def test_replay_mismatch(lifted, sample_psid):
    """Sites zeroing the reference's indexed reads make the replay differ."""
    d, _, _ = lifted
    load, _, play, data = sidtrace.psid_parts(sample_psid)
    pcs = survey.executed(sample_psid, FRAMES)
    image = bytes(load) + bytes(data)
    sites = [
        {"pc": p, "addr": [0x200, 0xCFFF], "ok": []}
        for p in sorted(pcs)
        if load <= p < load + len(data)
        and OPCODES[image[p]][1] in ("absx", "absy")
        and p != play
    ]
    assert sites
    got = liftcheck.replay(sample_psid, d / "gen.prg", FRAMES, sites)
    assert got.startswith("MISMATCH")


def test_table():
    """Every measure gets a row, the hand's value beside the lift's."""
    rows = liftcheck.table({"goto": 3, "werror": "clean"}, {"goto": 0}).splitlines()
    assert len(rows) == len(liftcheck.MEASURES) + 1
    gotos = next(r for r in rows if r.startswith("gotos"))
    assert gotos.split()[1:] == ["3", "0"]
    assert next(r for r in rows if r.startswith("unbounded")).split()[1:] == ["-", "-"]


def test_opacity(monkeypatch, tmp_path):
    """Opacity is the census' opaque names plus twice its raw-memory accesses, as
    virtuous-cycles.toml weighs them, its components beside it in the table."""
    assert liftcheck.COMBINE == {"opacity": {"opaque-name": 1, "raw-memory": 2}}
    counts = {"goto": 1, "opaque-name": 3, "raw-memory": 4}
    monkeypatch.setattr(liftcheck, "census", lambda *_: counts)
    monkeypatch.setattr(liftcheck.transliterate, "compile_prg", lambda *_, **__: None)
    monkeypatch.setattr(liftcheck, "replay", lambda *_: "OK")
    monkeypatch.setattr(
        liftcheck.fcomplex, "score", lambda _: {"total": {"cognitive": 0}}
    )
    got = liftcheck.measure(tmp_path, b"", 1)
    assert got["opacity"] == 11 and got["goto"] == 1
    row = next(r for r in liftcheck.table(got, {}).splitlines() if "opacity" in r)
    assert row.split()[2:] == [
        "11",
        "=",
        "1*3",
        "opaque-name",
        "+",
        "2*4",
        "raw-memory",
        "-",
    ]


def test_main(sample_psid, tmp_path, capsys):
    """The CLI lifts, measures and prints the table; its JSON holds the lift's measures."""
    psid, out = tmp_path / "t.sid", tmp_path / "t.json"
    psid.write_bytes(sample_psid)
    args = [str(psid), str(tmp_path / "lift"), "--frames", "40", "--json", str(out)]
    assert liftcheck.main(args) == 0
    assert "fcomplex cognitive" in capsys.readouterr().out
    got = json.loads(out.read_text("utf-8"))
    assert got["hand"] == {} and got["lift"]["sidtrace"].startswith("OK 40 frames")


def test_main_crash_leaves_no_json(sample_psid, tmp_path, monkeypatch):
    """A run that fails before measuring removes the previous run's JSON."""
    psid, out = tmp_path / "t.sid", tmp_path / "t.json"
    psid.write_bytes(sample_psid)
    out.write_text('{"lift": {"sidtrace": "OK stale"}}', "utf-8")

    def crash(*_):
        raise RuntimeError("lift failed")

    monkeypatch.setattr(liftcheck, "lift", crash)
    args = [str(psid), str(tmp_path / "lift"), "--frames", "40", "--json", str(out)]
    with pytest.raises(RuntimeError):
        liftcheck.main(args)
    assert not out.exists()


def test_violations():
    """An access outside its instruction's site hull and tables, or a successor the lift has
    no instruction at, is reported; one inside any of them, or with no bounds, is not.
    """
    sites = [{"pc": 0x1000, "addr": [0x2000, 0x20FF], "ok": [[0x2000, 0x2003]]}]
    tables = [[0x1003, 0x3000, 0x3007, 1, False], [0x1003, 0x3100, 0x3107, 1, False]]
    starts = [0x1000, 0x1003, 0x1006, 0x1008]
    inside = {(0x1000, False): [0x2000, 0x20FF], (0x1003, True): [0x3007, 0x3100]}
    inside[(0x1006, False)] = [0x10, 0xFFFF]
    succ = {0x1006: [0x1008], 0x1008: [0x1000, 0x1003]}
    assert liftcheck.violations(inside, succ, sites, tables, starts) == ([], [])
    outside = {
        (0x1000, False): [0x2100],
        (0x1003, True): [0x3008],
        (0x1006, False): [5],
    }
    got = liftcheck.violations(outside, {0x1006: [0x100A]}, sites, tables, starts)
    assert got == ([0x1000, 0x1003], [0x1006])


def test_check_orbit(lifted, sample_psid, tmp_path):
    """The lift's bounds hold over play's calls; narrowing a table or deleting an executed
    instruction start violates them."""
    d, _, _ = lifted
    bounds = json.loads((d / "bounds.json").read_text("utf-8"))
    if not bounds["tables"]:
        pytest.skip("the lifter reports no table bounds (info['tables'])")
    got = liftcheck.check_orbit(d, sample_psid, 50)
    assert got.startswith("OK ") and "open" in got
    play, states = liftcheck.song_states(sample_psid)
    _, _, acc, succ = orbit.walk(states[0], play, transliterate.VOLATILE, 50, [])
    tab = next(
        t for t in bounds["tables"] if (t[0], False) in acc or (t[0], True) in acc
    )
    bad = tmp_path / "bad"
    shutil.copytree(d, bad)
    narrowed = [[*t[:2], t[1] - 1, *t[3:]] if t == tab else t for t in bounds["tables"]]
    arm = next(p for p, ss in succ.items() if len(ss) > 1)
    starts = [s for s in bounds["starts"] if s != max(succ[arm])]
    (bad / "bounds.json").write_text(json.dumps({"tables": narrowed, "starts": starts}))
    got = liftcheck.check_orbit(bad, sample_psid, 50)
    assert (
        got.startswith("VIOLATED") and f"${tab[0]:04X}" in got and f"${arm:04X}" in got
    )
