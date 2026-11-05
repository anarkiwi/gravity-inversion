"""Tests for tools/eventlog.py: synthetic register logs and blocks, Emomyst and Commando
against their hand C's note-on points, and fuzz players."""

import json
import pathlib
import subprocess

import numpy as np
import pytest
from jennings.coverage.elf import load_elf

import fuzzplayer
import sidtrace

import eventlog

ROOT = pathlib.Path(__file__).resolve().parent.parent
EMOMYST = ROOT / "fixtures" / "Emomyst.sid"
EMOMYST_PRG = ROOT / "tunes" / "emomyst" / "build" / "emomyst.prg"
COMMANDO = ROOT / "tunes" / "commando" / "build"


def _needs(*paths):
    return pytest.mark.skipif(
        not all(p.exists() for p in paths), reason="needs `make` builds"
    )


@pytest.mark.parametrize("cents", [-49.0, -20.0, 0.0, 13.0, 49.0])
def test_note_round_trip(cents):
    """Register values of equal-tempered notes name back to the note; the cents error is
    bounded by half a register step."""
    notes = np.arange(12, 108)
    freq = eventlog.freq_of(notes, cents)
    reg = np.round(freq).astype(int)
    keep = (reg > 0) & (reg <= 0xFFFF)
    near, got = eventlog.note_of(reg[keep])
    want = np.round(notes[keep] + cents / 100).astype(int)
    clear = abs(cents) + 1200 * np.log2(reg[keep] / (reg[keep] - 0.5)) < 50
    assert (near == want)[clear].all() and clear.any()
    step = 1200 * np.log2(reg[keep] / (reg[keep] - 0.5))
    err = got - (cents - 100 * (want - notes[keep]))
    assert (np.abs(err) <= step)[clear].all()


def test_note_names():
    """Middle C, A4 and silence."""
    assert eventlog.name_of(60) == "C-4"
    assert eventlog.name_of(69) == "A-4"
    assert eventlog.name_of(-1) == "---"
    assert eventlog.note_of([0])[0][0] == -1
    assert abs(eventlog.freq_of(69) * eventlog.PAL.clock_hz / (1 << 24) - 440) < 1e-9


def _onsets(frames, voice=0):
    flat = eventlog.flatten(frames)
    return {
        k: np.flatnonzero(v[:, voice]).tolist()
        for k, v in eventlog.register_onsets(flat, len(frames)).items()
        if k != "prep"
    }, eventlog.register_onsets(flat, len(frames))["prep"][:, voice]


def test_gate_edge():
    """Gate rises are onsets; waveform changes with the gate held are not."""
    got, _ = _onsets([[(4, 0x10)], [(4, 0x11)], [(4, 0x21)], [(4, 0x20)], [(4, 0x21)]])
    assert got == {"gate": [1, 4], "test": [], "hard_restart": []}


def test_hard_restart():
    """Gate off with an AD/SR write, then gate on: a hard restart prepared from the
    frame of the AD/SR write."""
    frames = [
        [(4, 0x41)],
        [(4, 0x40)],
        [(5, 0x00), (6, 0x00)],
        [(5, 0x22), (6, 0x88), (4, 0x41)],
        [(4, 0x40)],
        [(4, 0x41)],
    ]
    got, prep = _onsets(frames)
    assert got == {"gate": [0, 3, 5], "test": [], "hard_restart": [3]}
    assert prep[3] == 2 and prep[5] == 5


def test_test_bit_retrigger():
    """The test bit set and cleared with the gate held is a retrigger; the test bit at a
    gate rise is part of that onset."""
    frames = [[(11, 0x41)], [(11, 0x49)], [(11, 0x41)], [(11, 0x08)], [(11, 0x09)]]
    frames += [[(11, 0x41)]]
    got, _ = _onsets(frames, voice=1)
    assert got == {"gate": [0, 4], "test": [2], "hard_restart": []}


def test_legato_is_not_a_register_onset():
    """A pitch change with the gate held shows no register onset: initiations of it come
    from the code (test_extra_initiation)."""
    got, _ = _onsets([[(0, 0x10), (1, 0x10), (4, 0x41)], [(0, 0x20), (1, 0x12)]])
    assert got == {"gate": [0], "test": [], "hard_restart": []}


def test_initiation_picks_the_onset_block():
    """The block matching onsets and one gate-held initiation wins over blocks that run
    every frame, run only for the voice with onsets, or run against them."""
    frames = 40
    y = np.zeros((frames, 3), bool)
    y[::5, 1] = True
    x = np.zeros((frames, 3, 4), bool)
    x[:, :, 0] = y
    x[7, 1, 0] = True
    x[:, :, 1] = True
    x[:, 1, 2] = True
    x[:, :, 3] = ~y
    chosen, union = eventlog.initiation(x, y)
    assert chosen == [0]
    assert (union == x[:, :, 0]).all()


def test_initiation_unions_paths():
    """Onsets reached by two paths take both blocks; no blocks, none."""
    frames = 60
    y = np.zeros((frames, 3), bool)
    y[::4, 0] = y[2::8, 2] = True
    x = np.zeros((frames, 3, 2), bool)
    x[::4, 0, 0] = x[2::8, 2, 1] = True
    assert eventlog.initiation(x, y)[0] == [0, 1]
    assert not eventlog.initiation(x[:, :, :0], y)[0]


def test_pair_values():
    """Frequency, pulse and cutoff pairs carry their value and change; others the byte."""
    flat = eventlog.flatten(
        [[(0, 0x34), (1, 0x12), (2, 0xFF), (3, 0x1F), (0x15, 7), (0x16, 1), (4, 9)]]
    )
    value, delta = eventlog.pair_values(flat)
    assert value.tolist() == [0x34, 0x1234, 0xFF, 0xFFF, 7, 15, 9]
    assert delta.tolist() == [0x34, 0x1200, 0xFF, 0xF00, 7, 8, 0]


def _synthetic():
    """A two-note voice 0 with a gate-held third initiation, a filter write, and the
    blocks of an onset path."""
    ref = [[(0x18, 0x1F, 10, 0)]]
    notes = {1: 0x1000, 6: 0x1800, 9: 0x1800}
    for f in range(1, 12):
        w = []
        if f in notes:
            w += [(0, notes[f] & 0xFF), (1, notes[f] >> 8)]
        if f in (1, 6):
            w += [(5, 0x11), (6, 0xF0), (4, 0x41)]
        if f == 5:
            w += [(4, 0x40), (5, 0), (6, 0)]
        if f == 3:
            w += [(0, 0x40), (2, 0x00), (3, 0x08)]
        if f == 4:
            w += [(0x16, 0x20)]
        if f == 10:
            w += [(4, 0x40)]
        ref.append([(*x, 20 * i, 0) for i, x in enumerate(w)])
    blocks = [np.array([1 << 2 | v for v in range(3)], np.int64) for _ in ref]
    for f in notes:
        blocks[f] = np.append(blocks[f], 2 << 2)
    writes = [[tuple(w[:2]) for w in f] for f in ref]
    return ref, writes, blocks


def test_events_synthetic():
    """Events start at each initiation, the gate-held one included; the log rebuilds the
    write stream; frequency offsets are relative to the base pitch."""
    ref, writes, blocks = _synthetic()
    log = eventlog.Log(ref, writes, blocks)
    events, shared = log.events(), log.shared()
    v0 = [e for e in events if e["voice"] == 0]
    assert [(e["frame"], e["start"], e["lead"]) for e in v0] == [
        (0, 0, True),
        (1, 0, False),
        (6, -1, False),
        (9, 0, False),
    ]
    assert v0[1]["registers"] == ["gate"] and v0[1]["initiated"]
    assert v0[2]["registers"] == ["gate", "hard_restart"]
    assert v0[3]["registers"] == [] and v0[3]["initiated"]
    assert v0[1]["gate_frames"] is None and v0[3]["gate_frames"] == 1
    assert v0[1]["base"]["freq"] == 0x1000 and v0[1]["cycle"] == 0
    up = next(op for op in v0[1]["ops"] if op[0] == 2)
    assert up[4] == 0x1040 and up[5] == 0x40
    assert up[6] == round(1200 * np.log2(0x1040 / 0x1000), 2)
    pw = [op for op in v0[1]["ops"] if op[2] == 3][0]
    assert pw[4] == 0x800 and pw[5] == 0x800
    assert [(s["reg"], s.get("cutoff")) for s in shared] == [(0x18, None), (0x16, 256)]
    assert shared[0]["volume"] == 15 and shared[0]["mode"] == 1
    flat = [(f, r, v) for f, ws in enumerate(ref) for r, v, *_ in ws]
    assert eventlog.rebuild(events, shared) == flat
    s = log.summary()
    assert s["unseen"] == [[9, 0]] and s["recall"] == 1.0


def test_decode_shared():
    """Resonance/routing and mode/volume fields."""
    assert eventlog.decode_shared(0x17, 0xF5) == {"res": 15, "routing": 5, "ext": 0}
    assert eventlog.decode_shared(0x18, 0x9A)["voice3_off"] == 1
    assert not eventlog.decode_shared(0x15, 3)


def _hand(prg, frames, watch, keep):
    """{(frame, voice)} of the hand build's writes that `keep(addr, value, syms)` maps to
    a voice, for addresses `watch(syms)`."""
    _, syms = load_elf(f"{prg}.elf")
    hits = set()

    def on(frame, addr, value):
        v = keep(addr, value, syms)
        if v is not None:
            hits.add((frame, v))

    sidtrace.trace_prg(prg, frames, 0, watch=watch, on_watch=on)
    return hits


def _check(psid, frames):
    events, shared, summary = eventlog.run_tune(psid, frames)
    got = {(e["frame"], e["voice"]) for e in events if e["initiated"]}
    flat = [
        (f, r, v)
        for f, ws in enumerate(sidtrace.trace_psid(psid, frames))
        for r, v in ws
    ]
    assert eventlog.rebuild(events, shared) == flat
    return got, summary


@pytest.mark.slow
@_needs(EMOMYST, EMOMYST_PRG)
def test_emomyst_hand_note_ons():
    """Over the HVSC length, initiations are exactly the hand C's gate-on points (the
    GATE field set to $FF at a note start or a gate-on note fx); the hand C's other pitch
    sets are legato ties, which show no register onset."""
    psid = EMOMYST.read_bytes()
    frames = sidtrace.song_calls(psid, 0, ROOT / "fixtures")

    def voice(field):
        return lambda a, v, s: (a - s["ch"] - field) // 7

    gate = _hand(
        EMOMYST_PRG,
        frames,
        lambda s: [s["ch"] + 5 + 7 * v for v in range(3)],
        lambda a, v, s: (a - s["ch"] - 5) // 7 if v == 0xFF else None,
    )
    pitch = _hand(
        EMOMYST_PRG,
        frames,
        lambda s: [s["ch"] + 0x2C + 7 * v for v in range(3)],
        voice(0x2C),
    )
    got, summary = _check(psid, frames)
    assert got == gate
    trace = sidtrace.trace_psid(psid, frames)
    onsets = eventlog.register_onsets(eventlog.flatten(trace), len(trace))
    seen = onsets["gate"] | onsets["test"]
    ties = pitch - got
    assert ties and not any(seen[f, v] for f, v in ties)
    assert summary["precision"] == summary["recall"] == 1.0


@pytest.mark.slow
@_needs(COMMANDO / "ref.sid", COMMANDO / "commando.prg")
def test_commando_hand_note_ons():
    """Rob Hubbard's player: initiations are exactly the hand C's newnote calls that read
    a note (notelen without the legato bit, which only releases the gate)."""
    psid = (COMMANDO / "ref.sid").read_bytes()
    frames = 6000
    notelen = 192 + 9

    def keep(addr, value, syms):
        return None if value & 0x40 else addr - syms["v"] - notelen

    truth = _hand(
        COMMANDO / "commando.prg",
        frames,
        lambda s: [s["v"] + notelen + x for x in range(3)],
        keep,
    )
    got, summary = _check(psid, frames)
    assert got == truth
    assert summary["unseen"]


def _fuzz(style, seed, tmp_path):
    src = tmp_path / f"{style}{seed}.asm"
    src.write_text(fuzzplayer.player(style, seed), encoding="ascii")
    sid = tmp_path / f"{style}{seed}.sid"
    subprocess.run(["acme", "-o", sid, src], check=True, capture_output=True)
    return sid


FUZZ_FRAMES = 200


@pytest.mark.parametrize("style", ["loop", "jsr3", "fallthrough", "unrolled", "smc"])
@pytest.mark.parametrize("seed", [4, 13, 14])
def test_fuzz(style, seed, tmp_path):
    """Synthetic players: the log rebuilds the writes and holds an event at every register
    onset; with the voice routine called per voice, a code initiation is a register onset
    whenever the routine's data branch moves with its gate."""
    psid = _fuzz(style, seed, tmp_path).read_bytes()
    events, shared, summary = eventlog.run_tune(psid, FUZZ_FRAMES)
    trace = sidtrace.trace_psid(psid, FUZZ_FRAMES)
    flat = [(f, r, v) for f, ws in enumerate(trace) for r, v in ws]
    assert eventlog.rebuild(events, shared) == flat
    onsets = eventlog.register_onsets(eventlog.flatten(trace), len(trace))
    labels = {tuple(x[::-1]) for x in np.argwhere((onsets["gate"] | onsets["test"]).T)}
    assert labels <= {(e["frame"], e["voice"]) for e in events if not e["lead"]}
    if style in ("loop", "jsr3", "fallthrough"):
        assert summary["precision"] == 1.0 or not summary["initiations"]


def test_main(tmp_path):
    """The CLI writes the event, shared and summary files."""
    sid = _fuzz("loop", 14, tmp_path)
    out = tmp_path / "log"
    assert eventlog.main([str(sid), str(out), "--frames", "50"]) == 0
    events = [json.loads(l) for l in (out / "events.jsonl").read_text().splitlines()]
    assert {e["voice"] for e in events} == {0, 1, 2}
    assert json.loads((out / "summary.json").read_text())["frames"] == 51
    assert (out / "shared.jsonl").exists()
