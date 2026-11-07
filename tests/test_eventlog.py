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
HAND_CYCLES = 20_000_000


def test_same_values():
    """A write is a refresh when its register already holds the value."""
    got = eventlog.same_values([0, 1, 0, 1, 0], [1, 2, 1, 3, 1])
    assert got.tolist() == [False, False, True, False, True]


def test_song_mask():
    """Song data: held span bytes outside the regions and the instructions."""
    image = bytearray(0x10000)
    image[0x1000:0x1004] = b"\x4c\x00\x10\xea"
    info = {
        "code_held": [(0x1000, 0x1010)],
        "regions": [(0x1008, 2)],
        "starts": [0x1000, 0x1003],
    }
    mask = eventlog.song_mask(image, info)
    want = [0x1004, 0x1005, 0x1006, 0x1007] + list(range(0x100A, 0x1010))
    assert np.flatnonzero(mask).tolist() == want


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
    """Sites are the loads that may read song data (a region slice at a variable offset
    too, a song byte the .c reads by name, not K0 or a constant state offset); each song
    byte read is logged with its copy, in rdC0 at the call's site, followed by its stores
    and SID writes; I/O reads replayed.
    """
    path = tmp_path / "player.c"
    path.write_text(PLAYER, encoding="ascii")
    path.with_suffix(".h").write_text(HEADER, encoding="ascii")
    song = np.zeros(0x10000, bool)
    song[0x1000:0x1008] = True
    player = eventlog.Player(path, tmp_path, song, ["f1"])
    lines = PLAYER.split("\n")
    assert {(lines[l - 1].strip()[:20], fn) for fn, l, _ in player.sites} == {
        ("uint8_t n = rdC0(C0_", "f1"),
        ("R1[v1] = (R1[v1] + 1", "f1"),
        ("io_write(0xD400 + K0", "f1"),
        ("io_write(0xD418, C0[", "cgen_play"),
        ("io_write(0xD417, COD", "cgen_play"),
    }
    rec = player.run(0, 4, {0: [(0xD012, 1), (0xD012, 9)]})
    evs = eventlog.events(rec, player.sites, player.origins)
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


FUZZ_FRAMES = 200


@pytest.mark.parametrize("seq", fuzzplayer.SEQ)
@pytest.mark.parametrize("style", ["loop", "jsr3", "fallthrough", "unrolled", "smc"])
@pytest.mark.parametrize("seed", [4, 13])
def test_fuzz(style, seed, seq, tmp_path):
    """Sequencer players, SID writes direct or through a shadow buffer copied after the
    voices or at the next call: the generated C reads exactly the song bytes the original
    reads, call by call, with their values; the log rebuilds the writes."""
    psid = _fuzz(style, seed, tmp_path, seq).read_bytes()
    evs, summary, orig, _ = eventlog.run_tune(psid, FUZZ_FRAMES)
    got = {((e["frame"], e["addr"]), e["value"]) for e in evs[1:]}
    assert got == orig.items() and summary["reads"] >= len(got) > 0
    trace = sidtrace.trace_psid(psid, FUZZ_FRAMES)
    want = [(f, r, v) for f, ws in enumerate(trace) for r, v in ws]
    assert eventlog.rebuild(evs) == want


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
    """Over the song (Commando 6000 calls) the generated C's song data reads (call, address)
    are the original's, with its values; those of bytes in the -O0 hand C's data arrays
    equal the hand C's, whose SID writes equal the original's, but for the DISPATCH tables
    (each read's value stored to a lift code cell, none read by the hand C)."""
    prg = ROOT / "tunes" / tune / "build" / "hand-O0.prg"
    if not (PSIDS[tune].exists() and prg.exists()):
        pytest.skip("needs `make` builds")
    psid = PSIDS[tune].read_bytes()
    song = sidtrace.default_song(psid)
    frames = 6000 if tune == "commando" else sidtrace.song_calls(psid, song, FIX)
    evs, summary, orig, mask = eventlog.run_tune(psid, frames)
    writes, hand = eventlog.prg_reads(
        prg, frames, song, _spans(tune), mask, HAND_CYCLES
    )
    trace = sidtrace.trace_psid(psid, frames, song=song)
    assert writes == trace
    assert eventlog.rebuild(evs) == [
        (f, r, v) for f, ws in enumerate(trace) for r, v in ws
    ]
    table = np.zeros(0x10000, bool)
    for lo, hi in DISPATCH.get(tune, ()):
        table[lo:hi] = True
    dispatch = [e for e in evs[1:] if table[e["addr"]]]
    code = set(summary["code_cells"])
    assert all(
        any(c[1] in code and c[2] == e["value"] for c in e["cells"]) for e in dispatch
    )
    gen = {((e["frame"], e["addr"]), e["value"]) for e in evs[1:]}
    assert gen <= orig.items()
    held = np.zeros(0x10000, bool)
    for lo, n in _spans(tune).values():
        held[lo : lo + n] = True
    gen = {k for k, _ in gen if held[k[1]] and not table[k[1]]}
    assert not any(table[a] for _, a in hand)
    assert gen == hand


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
