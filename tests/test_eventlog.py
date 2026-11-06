"""Tests for tools/eventlog.py and tools/initiations.py: register logs, synthetic C players,
fuzz players, Emomyst and Commando against their hand C's note reads."""

import json
import pathlib
import subprocess

import numpy as np
import pytest
from jennings.coverage.elf import load_elf

import fuzzplayer
import initiations
import irmodule
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


def test_frequency_registers():
    """The six frequency registers and their mirrors map to their voices; nothing else."""
    regs = eventlog.frequency_registers()
    assert {a: regs[a] for a in range(0xD400, 0xD415) if a in regs} == {
        0xD400: 0,
        0xD401: 0,
        0xD407: 1,
        0xD408: 1,
        0xD40E: 2,
        0xD40F: 2,
    }
    assert regs[0xD7EE] == 2 and 0xD404 not in regs and len(regs) == 6 * 32


def _onsets(frames, voice=0):
    out = eventlog.register_onsets(eventlog.flatten(frames), len(frames))
    got = {
        k: np.flatnonzero(v[:, voice]).tolist() for k, v in out.items() if k != "prep"
    }
    return got, out["prep"][:, voice]


def test_gate_edge():
    """Gate rises are onsets; waveform changes with the gate held are not."""
    got, _ = _onsets([[(4, 0x10)], [(4, 0x11)], [(4, 0x21)], [(4, 0x20)], [(4, 0x21)]])
    assert got == {"gate": [1, 4], "test": [], "hard_restart": []}


def test_hard_restart():
    """Gate off with an AD/SR change, then gate on: a hard restart prepared from the frame of
    the change. Rewriting the same AD/SR values every frame, as a shadow buffer does, is not
    a change."""
    frames = [
        [(5, 0x22), (6, 0x88), (4, 0x41)],
        [(5, 0x22), (6, 0x88), (4, 0x40)],
        [(5, 0x00), (6, 0x00), (4, 0x40)],
        [(5, 0x22), (6, 0x88), (4, 0x41)],
        [(5, 0x22), (6, 0x88), (4, 0x40)],
        [(5, 0x22), (6, 0x88), (4, 0x41)],
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
    """A pitch change with the gate held shows no register onset."""
    got, _ = _onsets([[(0, 0x10), (1, 0x10), (4, 0x41)], [(0, 0x20), (1, 0x12)]])
    assert got == {"gate": [0], "test": [], "hard_restart": []}


def test_same_values():
    """A write is a refresh when its register already holds the value."""
    flat = eventlog.flatten([[(0, 1), (1, 2)], [(0, 1), (1, 3), (0, 1)]])
    assert eventlog.same_values(flat).tolist() == [False, False, True, False, True]


def test_pair_values():
    """Frequency, pulse and cutoff pairs carry their value and change; others the byte."""
    flat = eventlog.flatten(
        [[(0, 0x34), (1, 0x12), (2, 0xFF), (3, 0x1F), (0x15, 7), (0x16, 1), (4, 9)]]
    )
    value, delta = eventlog.pair_values(flat)
    assert value.tolist() == [0x34, 0x1234, 0xFF, 0xFFF, 7, 15, 9]
    assert delta.tolist() == [0x34, 0x1200, 0xFF, 0xF00, 7, 8, 0]


def test_decode_shared():
    """Resonance/routing and mode/volume fields."""
    assert eventlog.decode_shared(0x17, 0xF5) == {"res": 15, "routing": 5, "ext": 0}
    assert eventlog.decode_shared(0x18, 0x9A)["voice3_off"] == 1
    assert not eventlog.decode_shared(0x15, 3)


def test_initiations_of():
    """A read initiates on a voice where its tag first reaches that voice's frequency
    registers, newer than any tag that reached them before; timed by its read."""
    writes = [[], [(0, 1), (4, 0x41)], [(1, 2), (7, 5)], [(0, 3), (14, 4)], [(8, 1)]]
    tags = [[], [1, 1], [1, 2], [3, 4], [2]]
    reads = np.array([[0, 0], [1, 0], [1, 1], [2, 0], [3, 1]])
    got = eventlog.initiations_of(writes, tags, reads)
    assert got == {(1, 0): (0, 0), (1, 1): (1, 1), (2, 0): (0, 1), (3, 2): (1, 0)}


def _synthetic():
    """A two-note voice 0 with a gate-held third initiation, a filter write, and the
    initiations."""
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
    writes = [[tuple(w[:2]) for w in f] for f in ref]
    initiated = {(f, 0): (0, 0) for f in notes}
    return ref, writes, initiated


def test_events_synthetic():
    """Events start at each initiation, the gate-held one included; the log rebuilds the
    write stream; frequency offsets are relative to the base pitch."""
    ref, writes, initiated = _synthetic()
    log = eventlog.Log(ref, writes, initiated)
    events, shared = log.events(), log.shared()
    v0 = [e for e in events if e["voice"] == 0]
    assert [(e["frame"], e["start"], e["lead"]) for e in v0] == [
        (0, 0, True),
        (1, 0, False),
        (6, -1, False),
        (9, 0, False),
    ]
    assert v0[1]["registers"] == ["gate"] and v0[2]["registers"] == [
        "gate",
        "hard_restart",
    ]
    assert v0[3]["registers"] == [] and v0[3]["delay"] == 0
    assert v0[1]["gate_frames"] is None and v0[3]["gate_frames"] == 1
    assert v0[1]["base"]["freq"] == 0x1000 and v0[1]["cycle"] == 0
    up = next(op for op in v0[1]["ops"] if op[0] == 2)
    assert up[4] == 0x1040 and up[5] == 0x40 and up[7] == 0
    assert up[6] == round(1200 * np.log2(0x1040 / 0x1000), 2)
    assert [op[7] for op in v0[2]["ops"] if op[2] in (5, 6)] == [0, 0, 0, 0]
    assert [op[7] for op in v0[3]["ops"] if op[2] in (0, 1)] == [1, 1]
    pw = [op for op in v0[1]["ops"] if op[2] == 3][0]
    assert pw[4] == 0x800 and pw[5] == 0x800
    assert [(s["reg"], s.get("cutoff")) for s in shared] == [(0x18, None), (0x16, 256)]
    assert shared[0]["volume"] == 15 and shared[0]["mode"] == 1
    flat = [(f, r, v) for f, ws in enumerate(ref) for r, v, *_ in ws]
    assert eventlog.rebuild(events, shared) == flat
    s = log.summary()
    assert s["violations"] == [] and s["initiations"] == 3 and s["register_onsets"] == 2
    assert eventlog.Log(ref, writes, {(5, 0): (0, 1)}).matches(0) == {5: (6, 6)}


def test_violations():
    """A gate rise with no initiation at its frame is a violation."""
    ref, writes, initiated = _synthetic()
    del initiated[6, 0]
    assert eventlog.Log(ref, writes, initiated).violations() == [[6, 0]]


PLAYER = """#include <stdint.h>
void io_write(uint16_t a, uint8_t v);
uint8_t io_read(uint16_t a);
void trap(uint16_t k);
static const uint8_t notes[3][8] = {%s};
static const uint8_t lens[3][8] = {%s};
static const uint8_t lo[8] = {0x10, 0x20, 0x30, 0x40, 0x50, 0x60, 0x70, 0x80};
static const uint8_t hi[8] = {1, 2, 3, 4, 5, 6, 7, 8};
uint8_t pos[3], dur[3], shadow[25];
static void put(uint8_t r, uint8_t v) { %s; }
static void copy(void) {
  for (uint8_t r = 0; r < 25; r++)
    io_write(0xD400 + r, shadow[r]);
}
void cgen_init(uint8_t a) {
  for (uint8_t v = 0; v < 3; v++)
    pos[v] = dur[v] = a;
}
void cgen_play(uint8_t a) {
  (void)a;
  %s
  for (uint8_t v = 0; v < 3; v++) {
    if (dur[v]) {
      dur[v]--;
      continue;
    }
    uint8_t n = notes[v][pos[v]];
    dur[v] = lens[v][pos[v]];
    pos[v] = (pos[v] + 1) & 7;
    if (n < 0x80) {
      put(7 * v, lo[n & 7]);
      put(7 * v + 1, hi[n & 7]);
      put(7 * v + 4, 0x41);
    } else {
      put(7 * v + 4, 0x40);
    }
  }
  %s
}
"""
SONG = [
    [0, 0x80, 3, 3, 5, 0x80, 1, 2],
    [2, 2, 2, 0x80, 7, 6, 0x80, 4],
    [0x80, 1, 3, 5, 0x80, 0x80, 6, 0],
]
LENS = [[1, 0, 2, 0, 1, 1, 0, 3], [0, 0, 1, 2, 0, 1, 0, 0], [2, 0, 1, 0, 0, 1, 3, 0]]


def _player(tmp_path, copy):
    """A C player of SONG with SID writes direct (`copy` None) or through a shadow buffer
    copied "after" the voices or "before" them."""

    def rows(table):
        return ", ".join("{" + ", ".join(map(str, r)) + "}" for r in table)

    put = "io_write(0xD400 + r, v)" if copy is None else "shadow[r] = v"
    text = PLAYER % (
        rows(SONG),
        rows(LENS),
        put,
        "copy();" if copy == "before" else "",
        "copy();" if copy == "after" else "",
    )
    path = tmp_path / "player.c"
    path.write_text(text, encoding="ascii")
    return path


def _note_reads(frames):
    """{(frame, voice)} of the rows of SONG reading a note."""
    out, pos, dur = set(), [0] * 3, [0] * 3
    for f in range(1, frames + 1):
        for v in range(3):
            if dur[v]:
                dur[v] -= 1
                continue
            if SONG[v][pos[v]] < 0x80:
                out.add((f, v))
            dur[v] = LENS[v][pos[v]]
            pos[v] = (pos[v] + 1) & 7
    return out


@pytest.mark.parametrize("copy", [None, "after", "before"])
def test_synthetic_player(copy, tmp_path):
    """A pointer advanced per row with a note byte into a frequency table: the note read is
    a note read, the duration byte another; initiations are the rows reading a note, through
    a shadow buffer too, whose copy before the voices delays the registers a call."""
    path = _player(tmp_path, copy)
    m = irmodule.Module(irmodule.lower(path, [path.parent], []))
    kinds = initiations.Analysis(
        m, eventlog.frequency_registers(), ["cgen_play"], ["cgen_init"]
    ).analyze()
    arrays = {
        (fn, op[1]): op[3][0][1][0]
        for fn, (_, blocks, _) in m.funcs.items()
        for ops in blocks.values()
        for op in ops
        if op[0] == "getelementptr" and op[3][0][0] == "p"
    }
    loads = {
        (fn, op[1]): arrays.get((fn, op[3][0][1]))
        for fn, (_, blocks, _) in m.funcs.items()
        for ops in blocks.values()
        for op in ops
        if op[0] == "load"
    }
    assert {loads[k]: kind for k, kind in kinds.items()} == {
        "notes": "note",
        "lens": "other",
    }
    player = eventlog.Player(path, tmp_path)
    writes, tags, reads = player.run(0, 40)
    got = eventlog.initiations_of(writes, tags, reads)
    delay = 1 if copy == "before" else 0
    assert set(got) == {(f, v) for f, v in _note_reads(40) if f + delay <= 40}
    assert {d for _, d in got.values()} == {delay}


def _fuzz(style, seed, tmp_path, seq=None):
    src = tmp_path / f"{style}{seed}.asm"
    src.write_text(fuzzplayer.player(style, seed, seq=seq), encoding="ascii")
    sid = tmp_path / f"{style}{seed}.sid"
    subprocess.run(["acme", "-o", sid, src], check=True, capture_output=True)
    return sid


FUZZ_FRAMES = 200


@pytest.mark.parametrize("seq", fuzzplayer.SEQ)
@pytest.mark.parametrize("style", ["loop", "jsr3", "fallthrough", "unrolled", "smc"])
@pytest.mark.parametrize("seed", [4, 13])
def test_fuzz(style, seed, seq, tmp_path):
    """Sequencer players: initiations are exactly the generated note reads (notes, legato
    notes with the gate held, repeated pitches) whose registers land within the run, direct
    or through a shadow buffer copied after the voices or at the start of the next call;
    the log rebuilds the writes; every register onset is at an initiation; no hard restart
    is found."""
    psid = _fuzz(style, seed, tmp_path, seq).read_bytes()
    cells = {fuzzplayer.STATE + fuzzplayer.NOTE * 0x20 + 7 * k: k for k in range(3)}
    truth = set()
    trace = sidtrace.trace_psid(
        psid,
        FUZZ_FRAMES,
        watch=list(cells),
        on_watch=lambda f, a, v: truth.add((f, cells[a])),
    )
    events, shared, summary = eventlog.run_tune(psid, FUZZ_FRAMES)
    delay = 1 if seq == "shadow_next" else 0
    truth = {(f, v) for f, v in truth if f + delay <= FUZZ_FRAMES}
    assert {(e["frame"], e["voice"]) for e in events if not e["lead"]} == truth
    flat = [(f, r, v) for f, ws in enumerate(trace) for r, v in ws]
    assert eventlog.rebuild(events, shared) == flat
    assert summary["violations"] == [] and summary["note_reads"]
    assert not any("hard_restart" in e["registers"] for e in events)
    delays = {e["delay"] for e in events if not e["lead"]}
    assert delays == {1 if seq == "shadow_next" else 0}


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
    got = {(e["frame"], e["voice"]) for e in events if not e["lead"]}
    flat = [
        (f, r, v)
        for f, ws in enumerate(sidtrace.trace_psid(psid, frames))
        for r, v in ws
    ]
    assert eventlog.rebuild(events, shared) == flat
    assert summary["violations"] == []
    return got


NOTE_ROWS = 0x60


@pytest.fixture(scope="module")
def emomyst():
    """``(initiations, rows reading a note, summary)`` of Emomyst over its HVSC length:
    the rows are where the hand C sets CUR_NOTE to a pitch."""
    psid = EMOMYST.read_bytes()
    frames = sidtrace.song_calls(psid, 0, ROOT / "fixtures")
    rows = _hand(
        EMOMYST_PRG,
        frames,
        lambda s: [s["ch"] + 0x2B + 7 * v for v in range(3)],
        lambda a, v, s: (a - s["ch"] - 0x2B) // 7 if 0 < v < NOTE_ROWS else None,
    )
    events, shared, summary = eventlog.run_tune(psid, frames)
    flat = [
        (f, r, v)
        for f, ws in enumerate(sidtrace.trace_psid(psid, frames))
        for r, v in ws
    ]
    assert eventlog.rebuild(events, shared) == flat
    return {(e["frame"], e["voice"]) for e in events if not e["lead"]}, rows, summary


@pytest.mark.slow
@_needs(EMOMYST, EMOMYST_PRG)
def test_emomyst_initiations_read_notes(
    emomyst,
):  # pylint: disable=redefined-outer-name
    """Every initiation is a row where the hand C reads a note, timed by that read."""
    got, rows, summary = emomyst
    assert got and got <= rows and summary["note_reads"]


@pytest.mark.slow
@_needs(EMOMYST, EMOMYST_PRG)
@pytest.mark.xfail(
    strict=True,
    reason="drum rows: the instrument's wavetable sets absolute pitches, so the note value "
    "never reaches the frequency registers (docs/eventlog.md)",
)
def test_emomyst_hand_note_rows(emomyst):  # pylint: disable=redefined-outer-name
    """Initiations are exactly the rows where the hand C reads a note."""
    got, rows, _ = emomyst
    assert got == rows


@pytest.mark.slow
@_needs(COMMANDO / "ref.sid", COMMANDO / "commando.prg")
def test_commando_hand_note_ons():
    """Rob Hubbard's player: initiations are exactly the hand C's newnote calls that read
    a note (notelen without the legato bit, which only releases the gate)."""
    psid = (COMMANDO / "ref.sid").read_bytes()
    notelen = 192 + 9

    def keep(addr, value, syms):
        return None if value & 0x40 else addr - syms["v"] - notelen

    truth = _hand(
        COMMANDO / "commando.prg",
        6000,
        lambda s: [s["v"] + notelen + x for x in range(3)],
        keep,
    )
    assert _check(psid, 6000) == truth


def test_main(tmp_path):
    """The CLI writes the event, shared and summary files."""
    sid = _fuzz("loop", 14, tmp_path, "direct")
    out = tmp_path / "log"
    assert eventlog.main([str(sid), str(out), "--frames", "50"]) == 0
    events = [json.loads(l) for l in (out / "events.jsonl").read_text().splitlines()]
    assert {e["voice"] for e in events} == {0, 1, 2}
    assert json.loads((out / "summary.json").read_text())["frames"] == 51
    assert (out / "shared.jsonl").exists()
