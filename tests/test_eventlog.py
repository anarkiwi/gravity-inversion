"""Tests for tools/eventlog.py and tools/irmodule.py: read sites of synthetic C, fuzz players
against their originals, and the five tunes against their hand C's reads of the same data.
"""

import json
import pathlib
import re
import subprocess

import numpy as np
import pytest

import fuzzplayer
import irmodule
import sidtrace

import eventlog

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIX = ROOT / "fixtures"
COMMANDO = ROOT / "tunes" / "commando" / "build"
PSIDS = {
    "commando": COMMANDO / "ref.sid",
    "emomyst": FIX / "Emomyst.sid",
    "ghouls": FIX / "Ghouls_n_Ghosts.sid",
    "incoherent": FIX / "Incoherent_Nightmare_tune_2.sid",
    "automatas": FIX / "Automatas.sid",
}
#: The hand C's data arrays, ``{symbol: (origin macro or ref.labels name, data macro)}``.
HAND = {
    "commando": {
        "song": ("SONG_ORG", "SONG_DATA"),
        "v": ("freqsl", "FREQ_TABLE"),
        "sid": ("sid", "SID_OFFSETS"),
    },
    "emomyst": {"tab": ("TABLES_ORG", "TABLES"), "music": ("MUSIC_ORG", "MUSIC")},
    "ghouls": {"ram": ("IMAGE_ORG", "IMAGE")},
    "incoherent": {"data": ("DATA_ORG", "DATA")},
    "automatas": {"data": ("DATA_ORG", "DATA")},
}
#: The original's dispatch tables ``[lo, hi)``, branch offsets and jump vectors it stores
#: into its own code: the hand C dispatches with a switch instead and reads none of them.
DISPATCH = {
    "emomyst": [(0x19F1, 0x1A00), (0x1A6E, 0x1AAE)],
    "ghouls": [(0x6CB7, 0x6D35)],
    "incoherent": [(0x0C4F, 0x0C64)],
}
#: Song bytes the original reads into a cell its build never reads ``[lo, hi)``: Emomyst's
#: chord rows' detune column, which the hand C skips.
UNUSED = {"emomyst": [(0x21CC, 0x21CD), (0x223B, 0x223C)]}
HAND_CYCLES = 20_000_000


def test_same_values():
    """A write is a refresh when its register already holds the value."""
    got = eventlog.same_values([0, 1, 0, 1, 0], [1, 2, 1, 3, 1])
    assert got.tolist() == [False, False, True, False, True]


def test_reference_song_data(tmp_path):
    """Song data: the loaded bytes of no instruction the original never writes (not the
    operands play patches); its reads are the play calls'."""
    sid = _fuzz("smc", 4, tmp_path, "direct")
    at = {k: a for a, k in reversed(_labels(sid))}
    psid = sid.read_bytes()
    load, _, _, image = sidtrace.psid_parts(psid)
    assert np.flatnonzero(eventlog.song_mask(psid)).tolist() == list(
        range(load, load + len(image))
    )
    loaded = eventlog.song_mask(psid, [at["init"], at["sf"]])
    assert not loaded[at["sf"] + 1] and loaded[at["sf"] + 2]
    _, _, reads, mask = eventlog.reference(psid, 20, 0, eventlog.song_mask(psid))
    assert mask[at["song"]] and mask[at["freqlo"]] and not mask[at["sf"] + 1]
    assert len(reads) and (reads["frame"] > 0).all() and mask[reads["addr"]].all()


def test_origins():
    """Region, held span and code span origins of a cgen header."""
    h = "#define R0_ORG 0x00F0\n#define C0_ORG 0x139E\n#define CODE_ORG 0x1000\n"
    h += "#define IMG_ORG 0x0800\n"
    assert eventlog.origins(h) == {"R0": 0xF0, "C0": 0x139E, "CODE": 0x1000}


def test_unfold():
    """Constant bytes the .c reads by name become loads of their original addresses; a
    header without them is unchanged."""
    h = "#ifndef H\n#define CODE_ORG 0x1000\n#define R0_ORG 0x00F0\n"
    h += "#define CODE_KB30 0x06\n#define R0_K1 0x7F\n#define Q_K2 0x01\n#endif\n"
    assert eventlog.unfold(h) == (
        "#ifndef H\n#define CODE_ORG 0x1000\n#define R0_ORG 0x00F0\n"
        "#define CODE_KB30 (cgen_k[0x1B30])\n#define R0_K1 (cgen_k[0x00F1])\n"
        "#define Q_K2 0x01\n"
        "static unsigned char cgen_k[0x10000] = {[0x1B30] = 0x06, [0x00F1] = 0x7F};\n"
        "#endif\n"
    )
    assert eventlog.unfold(
        HEADER.replace("#define CODE_K6 0x02\n", "")
    ) == HEADER.replace("#define CODE_K6 0x02\n", "")


def test_pointer():
    """Constant pointers of IR operands, nested and through struct fields; a value has
    none."""
    gep = "getelementptr inbounds ([4 x i8], ptr @C0, i64 0, i64 3)"
    assert irmodule.pointer(gep) == ("C0", 3)
    nested = f"getelementptr inbounds (i8, ptr {gep}, i64 2)"
    assert irmodule.pointer(nested) == ("C0", 5)
    assert irmodule.pointer("getelementptr (i8, ptr @C0, i64 %x)") == ("C0", None)
    s = "getelementptr inbounds ({ [2 x i8], i16 }, ptr @s, i64 0, i32 1)"
    assert irmodule.pointer(s) == ("s", 2) and irmodule.pointer("%5") is None
    assert irmodule.size("<{ i8, [3 x i16] }>") == 7


PLAYER = """#include <stdint.h>
#include "player.h"
void io_write(uint16_t a, uint8_t v);
uint8_t io_read(uint16_t a);
void trap(uint16_t k);
uint8_t C0[C0_LEN] = {C0_INIT};
uint8_t R0[2];
#define R1 (C0 + (R1_ORG - C0_ORG))
static const uint8_t K0[] = {0, 7};
static __attribute__((noinline)) uint8_t rdC0(uint16_t p, uint8_t y) {
  uint16_t o = p - C0_ORG + y;
  return o < C0_LEN ? C0[o] : 0;
}
static void f1(uint8_t v1) {
  uint8_t n = rdC0(C0_ORG + 4, R1[v1]);
  R1[v1] = (R1[v1] + 1) & 3;
  io_write(0xD400 + K0[v1], C0[n]);
  io_write(0xD404 + K0[v1], 0x41);
}
void cgen_init(uint8_t a) {
  R0[0] = io_read(0xD012);
  R1[0] = R1[1] = a;
}
void cgen_play(uint8_t a) {
  (void)a;
  f1(0);
  f1(1);
  io_write(0xD418, C0[2 + R0[0]]);
  io_write(0xD417, CODE_K6);
}
"""
HEADER = """#define C0_ORG 0x1000
#define C0_LEN 10
#define C0_INIT 0x11, 0x22, 0x33, 0x44, 1, 0, 2, 1, 0, 0
#define R0_ORG 0x0002
#define R1_ORG 0x1008
#define CODE_ORG 0x1000
#define CODE_K6 0x02
"""


def test_synthetic_player(tmp_path):
    """Sites (a byte read by name too, not K0) and kinds: event at the position play
    advances, lookup at its value, else table; each song byte read logged with its copy
    and site, then its stores and SID writes; I/O replayed; init's reads not logged."""
    path = tmp_path / "player.c"
    path.write_text(PLAYER, encoding="ascii")
    path.with_suffix(".h").write_text(HEADER, encoding="ascii")
    song = np.zeros(0x10000, bool)
    song[0x1000:0x1008] = True
    player = eventlog.Player(path, tmp_path, song, ["f1"])
    lines = PLAYER.split("\n")
    assert {(lines[l - 1].strip()[:20], fn, k) for fn, l, _, k in player.sites} == {
        ("uint8_t n = rdC0(C0_", "f1", "event"),
        ("uint8_t n = rdC0(C0_", "f1", "table"),
        ("R1[v1] = (R1[v1] + 1", "f1", "table"),
        ("io_write(0xD400 + K0", "f1", "lookup"),
        ("io_write(0xD418, C0[", "cgen_play", "table"),
        ("io_write(0xD417, COD", "cgen_play", "table"),
    }
    rec = player.run(0, 4, {0: [(0xD012, 1), (0xD012, 9)]})
    evs = list(eventlog.Log(rec, player.sites, player.origins))
    want = []
    for f in range(1, 5):
        n = [1, 0, 2, 1][(f - 1) % 4]
        for v in (0, 1):
            want += [
                (f, v, 0x1004 + (f - 1) % 4, n),
                (f, v, 0x1000 + n, 0x11 * (n + 1)),
            ]
        want += [(f, None, 0x1003, 0x44), (f, None, 0x1006, 2)]
    assert [(e["frame"], e["copy"], e["addr"], e["value"]) for e in evs[1:]] == want
    assert [e["kind"] for e in evs[1:7]] == ["event", "lookup"] * 2 + ["table"] * 2
    assert evs[0]["site"] is None and evs[0]["cells"][0] == [0, 2, 1, 0]
    assert evs[1]["region"] == "C0" and evs[1]["offset"] == 4
    assert evs[1]["cells"] == [[0, 0x1008, 1, 0]] and evs[1]["sid"] == []
    assert evs[2]["sid"] == [[0, 0, 0x22, 0], [0, 4, 0x41, 0]]
    assert evs[4]["sid"] == [[0, 7, 0x22, 0], [0, 11, 0x41, 0]]
    assert evs[8]["sid"][1] == [0, 4, 0x41, 1]
    assert (evs[6]["region"], evs[6]["offset"], evs[6]["sid"]) == (
        "C0",
        6,
        [[0, 23, 2, 0]],
    )


def _fuzz(style, seed, tmp_path, seq=None):
    src = tmp_path / f"{style}{seed}.asm"
    src.write_text(fuzzplayer.player(style, seed, seq=seq), encoding="ascii")
    sid = tmp_path / f"{style}{seed}.sid"
    subprocess.run(["acme", "-o", sid, src], check=True, capture_output=True)
    return sid


def _labels(sid):
    """``[(address, label)]`` of a fuzz player, sorted, its digits dropped."""
    src = sid.with_suffix(".asm")
    lbl = sid.with_suffix(".lbl")
    subprocess.run(["acme", "--symbollist", lbl, "-o", sid, src], check=True)
    text = lbl.read_text(encoding="ascii")
    return sorted(
        (int(a, 16), re.sub(r"\d", "", k))
        for k, a in re.findall(r"^\s*(\w+)\s*=\s*\$([0-9a-f]+)", text, re.M)
    )


FUZZ_FRAMES = 200


@pytest.mark.parametrize("seq", fuzzplayer.SEQ)
@pytest.mark.parametrize("style", ["loop", "jsr3", "fallthrough", "unrolled", "smc"])
@pytest.mark.parametrize("seed", [4, 13])
def test_fuzz(style, seed, seq, tmp_path):
    """Sequencer players (SID writes direct or shadowed): the generated C reads exactly the
    original's song bytes, call by call with values, score as events, frequency table as
    lookups, others (an unpatched operand) as tables; the log rebuilds the writes."""
    sid = _fuzz(style, seed, tmp_path, seq)
    psid, labels = sid.read_bytes(), _labels(sid)
    log, summary, orig, _ = eventlog.run_tune(psid, FUZZ_FRAMES)
    want = {"song": "event", "freqlo": "lookup", "freqhi": "lookup"}
    at = np.array([a for a, _ in labels])
    names = [want.get(k, "table") for _, k in labels]
    label = np.searchsorted(at, log.reads["addr"], "right") - 1
    assert [names[i] for i in label] == log.kinds.tolist()
    got = log.read_set()
    assert np.array_equal(got, orig) and summary["reads"] >= len(got) > 0
    trace = sidtrace.trace_psid(psid, FUZZ_FRAMES)
    assert np.array_equal(log.sid(), eventlog.flat(trace))


def _spans(tune):
    """The hand C's ``{symbol: (origin, length)}``."""
    h = (ROOT / "tunes" / tune / "build" / f"{tune}_data.h").read_text(encoding="ascii")
    defs = dict(re.findall(r"#define (\w+) ((?:[^\n\\]|\\\n)*)", h))
    labels = sidtrace.parse_labels(
        (COMMANDO / "ref.labels").read_text(encoding="ascii")
    )
    return {
        s: (
            int(defs[o], 16) if o in defs else labels[o],
            len(re.findall(r"0x[0-9a-fA-F]+", defs[n])),
        )
        for s, (o, n) in HAND[tune].items()
    }


@pytest.mark.slow
@pytest.mark.parametrize("tune", sorted(PSIDS))
def test_hand(tune):
    """Over the song the generated C's song data reads are the original's; those in the
    -O0 hand C's arrays (its SID writes the original's) are the hand C's play reads, but for
    the DISPATCH (value stored to a code cell) and UNUSED (to a cell) bytes it never reads.
    """
    prg = ROOT / "tunes" / tune / "build" / "hand-O0.prg"
    if not (PSIDS[tune].exists() and prg.exists()):
        pytest.skip("needs `make` builds")
    psid = PSIDS[tune].read_bytes()
    song = sidtrace.default_song(psid)
    frames = 6000 if tune == "commando" else sidtrace.song_calls(psid, song, FIX)
    log, summary, orig, mask = eventlog.run_tune(psid, frames)
    writes, hand = eventlog.prg_reads(
        prg, frames, song, _spans(tune), mask, HAND_CYCLES
    )
    trace = sidtrace.trace_psid(psid, frames, song=song)
    assert writes == trace
    assert np.array_equal(log.sid(), eventlog.flat(trace))
    table, unused = np.zeros(0x10000, bool), np.zeros(0x10000, bool)
    for lo, hi in DISPATCH.get(tune, ()):
        table[lo:hi] = True
    for lo, hi in UNUSED.get(tune, ()):
        unused[lo:hi] = True
    code = np.zeros(0x10000, bool)
    code[summary["code_cells"]] = True
    assert log.stored(code)[table[log.reads["addr"]]].all()
    assert log.stored(np.ones(0x10000, bool))[unused[log.reads["addr"]]].all()
    table |= unused
    gen = log.read_set()
    assert np.isin(eventlog.keys(gen), eventlog.keys(orig)).all()
    held = np.zeros(0x10000, bool)
    for lo, n in _spans(tune).values():
        held[lo : lo + n] = True
    gen = gen[held[gen["addr"]] & ~table[gen["addr"]]]
    assert not table[hand["addr"]].any()
    assert np.array_equal(eventlog.keys(gen, False), eventlog.keys(hand, False))


def test_main(tmp_path):
    """The CLI writes the event and summary files."""
    sid = _fuzz("loop", 14, tmp_path, "direct")
    out = tmp_path / "log"
    assert eventlog.main([str(sid), str(out), "--frames", "50"]) == 0
    events = [json.loads(l) for l in (out / "events.jsonl").read_text().splitlines()]
    assert events[0]["site"] is None and len(events) > 1
    assert {e["copy"] for e in events[1:]} == {None}
    summary = json.loads((out / "summary.json").read_text())
    assert summary["frames"] == 51 and summary["reads"] == len(events) - 1
