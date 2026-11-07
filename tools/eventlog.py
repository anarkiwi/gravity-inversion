"""Note events and their register operations from a translated SID player and its write log.

Initiations come from the generated C: tools/initiations.py finds its song data reads statically
in the C's LLVM IR, tools/irtags.py tags each read instance through every value, and the native
run records which tags reach each voice's frequency registers. The log holds register
operations alone (docs/eventlog.md).
"""

import argparse
import ctypes
import json
import os
import pathlib
import subprocess
import sys
import tempfile

import numpy as np
from tqdm import tqdm
from vagrant_moses.memmap import PAL

import initiations
import irmodule
import irtags
import sidtrace

HARNESS = pathlib.Path(__file__).with_suffix(".c")
DRIVER = pathlib.Path(__file__).resolve().parent.parent / "c64"
VOICES, STRIDE, REGS = 3, 7, 25
FREQ, PW, CTRL, AD, SR = 0, 2, 4, 5, 6
CUT, RES, MODE = 0x15, 0x17, 0x18
SHARED = VOICES * STRIDE
GATE, TEST = 0x01, 0x08
SID_BASE, SID_END, SID_MIRROR = 0xD400, 0xD800, 0x20
MASKS = {FREQ: 0xFFFF, PW: 0xFFF}
PAIR_LO = [
    (
        (r - r % STRIDE % 2 if r % STRIDE < CTRL else -1)
        if r < SHARED
        else CUT if r <= CUT + 1 else -1
    )
    for r in range(REGS)
]
NAMES = ("C-", "C#", "D-", "D#", "E-", "F-", "F#", "G-", "G#", "A-", "A#", "B-")
A4, A4_NOTE, OCTAVE, CENT = 440.0, 69, 12, 1200
MAXW = MAXE = 4096
ONSETS = ("gate", "test", "hard_restart")


def note_of(freq, clock=PAL.clock_hz):
    """(nearest MIDI note, cents from it) of SID frequency register values; -1 for 0."""
    hz = np.asarray(freq, float) * clock / (1 << 24)
    ok = hz > 0
    x = A4_NOTE + OCTAVE * np.log2(np.where(ok, hz, A4) / A4)
    near = np.round(x).astype(int)
    return np.where(ok, near, -1), np.where(ok, 100 * (x - near), 0.0)


def freq_of(note, cents=0.0, clock=PAL.clock_hz):
    """SID frequency register value (unrounded) of a MIDI note plus cents."""
    rel = (np.asarray(note, float) + np.asarray(cents) / 100 - A4_NOTE) / OCTAVE
    return A4 * 2**rel * (1 << 24) / clock


def name_of(note):
    """Tracker-style name of a MIDI note, e.g. 'C-4'."""
    return f"{NAMES[note % OCTAVE]}{note // OCTAVE - 1}" if note >= 0 else "---"


def frequency_registers():
    """``{address: voice}`` of the SID's frequency registers and their mirrors."""
    return {
        a: (a - SID_BASE) % SID_MIRROR // STRIDE
        for a in range(SID_BASE, SID_END)
        if (a - SID_BASE) % SID_MIRROR < SHARED
        and (a - SID_BASE) % SID_MIRROR % STRIDE < 2
    }


def flatten(writes):
    """Structured array of a per-frame write log: frame, reg, val, cycle (-1 unknown).

    Accepts sidtrace's (register, value) or detailed (register, value, cycle, pc) lists.
    """
    dt = [("frame", "i4"), ("reg", "u1"), ("val", "u1"), ("cycle", "i4")]
    rows = [
        (f, w[0], w[1], w[2] if len(w) > 2 else -1)
        for f, ws in enumerate(writes)
        for w in ws
    ]
    return np.array(rows, dt)


def frame_regs(flat, frames):
    """(frames, 25) register values at the end of each frame, 0 before the first write."""
    last = np.full((frames, REGS), -1)
    last[flat["frame"], flat["reg"]] = np.arange(len(flat))
    last = np.maximum.accumulate(last, axis=0)
    return np.append(flat["val"], 0).astype(np.uint8)[last]


def same_values(flat):
    """Per write, whether it leaves its register's value unchanged (a buffer refresh)."""
    reg, val = flat["reg"].astype(int), flat["val"].astype(int)
    out = np.zeros(len(flat), bool)
    for r in np.unique(reg):
        idx = np.flatnonzero(reg == r)
        out[idx[1:]] = val[idx[1:]] == val[idx[:-1]]
    return out


def _retriggers(g, t):
    """Indices of control writes (gate bits g, test bits t) clearing the test bit with the
    gate held since before the test bit was set."""
    gp, tp = np.append(0, g[:-1]), np.append(0, t[:-1])
    rises, sets = np.cumsum(g > gp), np.flatnonzero(t > tp)
    clear = np.flatnonzero((tp > t) & (g > 0))
    if not sets.size:
        return clear[:0]
    s = sets[np.maximum(np.searchsorted(sets, clear) - 1, 0)]
    return clear[(gp[s] > 0) & (rises[clear] == rises[s])]


def _hard_restarts(on, fall, env):
    """(mask of onset write positions `on`, first AD/SR change position) of onsets with an
    AD/SR value change (positions `env`) after the last gate fall (positions `fall`)."""
    k = np.searchsorted(fall, on) - 1
    first = np.searchsorted(env, fall[np.maximum(k, 0)]) if fall.size else k
    ok = (k >= 0) & (first < len(env))
    ok[ok] &= env[first[ok]] < on[ok]
    return ok, env[first[ok]]


def register_onsets(flat, frames):
    """Register-visible onsets per (frame, voice), from written values: 'gate' (the gate
    bit rises), 'test' (the test bit clears with the gate held since before it was set)
    and 'hard_restart' (AD or SR changes value between the gate falling and rising) bool
    arrays, and 'prep': the frame of that change, else the onset frame."""
    out = {k: np.zeros((frames, VOICES), bool) for k in ONSETS}
    out["prep"] = np.repeat(np.arange(frames)[:, None], VOICES, 1)
    pos, frame = np.arange(len(flat)), flat["frame"]
    changed = ~same_values(flat)
    for v in range(VOICES):
        mine = flat["reg"].astype(int) - v * STRIDE
        ci = pos[mine == CTRL]
        g, t = flat["val"][ci] & GATE, flat["val"][ci] & TEST
        rise = g > np.append(0, g[:-1])
        out["gate"][frame[ci[rise]], v] = True
        out["test"][frame[ci[_retriggers(g, t)]], v] = True
        fall = ci[g < np.append(0, g[:-1])]
        env = pos[((mine == AD) | (mine == SR)) & changed]
        ok, first = _hard_restarts(ci[rise], fall, env)
        out["hard_restart"][frame[ci[rise][ok]], v] = True
        out["prep"][frame[ci[rise][ok]], v] = frame[first]
    return out


class Player:  # pylint: disable=too-few-public-methods
    """The generated C, analysed for sequence reads and built natively with read tags."""

    def __init__(self, c_path, out_dir):
        c_path = pathlib.Path(c_path).resolve()
        out = pathlib.Path(out_dir).resolve()
        text = irmodule.lower(c_path, [c_path.parent, DRIVER], ["CGEN_HOST"])
        module = irmodule.Module(text)
        entries = ["cgen_play", "cgen_init"]
        analysis = initiations.Analysis(
            module, frequency_registers(), entries[:1], entries[1:]
        )
        self.kinds = analysis.analyze()
        reads = {k: i for i, k in enumerate(sorted(self.kinds))}
        obj, lib = out / "eventlog.o", out / "eventlog.so"
        irmodule.compile_object(
            irtags.instrument(module, reads, entries, analysis.flows), obj
        )
        cc = os.environ.get("HOSTCC", "cc")
        subprocess.run(
            [cc, "-O1", "-fPIC", "-shared", "-o", lib, HARNESS, obj], check=True
        )
        self.lib = ctypes.CDLL(str(lib))
        self.lib.step.argtypes = [ctypes.c_int, ctypes.c_uint8, ctypes.c_uint32]

    def _arr(self, ctype, n, name):
        return np.ctypeslib.as_array((ctype * n).in_dll(self.lib, name))

    def run(self, song, frames):
        """``(writes, tags, reads)`` of init then `frames` play calls: per call its SID
        (register, value) writes and the tag of each value, and per tag ``(call, writes
        before)`` of its read."""
        nw = ctypes.c_uint32.in_dll(self.lib, "nw")
        wreg, wval = self._arr(ctypes.c_uint8, MAXW, "wreg"), self._arr(
            ctypes.c_uint8, MAXW, "wval"
        )
        wtag = self._arr(ctypes.c_uint32, MAXW, "wtag")
        writes, tags = [], []
        for f in tqdm(range(frames + 1), desc="native", unit="frame", mininterval=2):
            k = self.lib.step(int(f == 0), song if f == 0 else 0, f)
            assert k < 0, f"call {f}: trap {k}"
            n = nw.value
            writes.append(list(zip(wreg[:n].tolist(), wval[:n].tolist())))
            tags.append(wtag[:n].tolist())
        n = ctypes.c_uint32.in_dll(self.lib, "ntag").value + 1
        reads = np.stack(
            [
                self._arr(ctypes.c_uint32, n, "tcall"),
                self._arr(ctypes.c_uint32, n, "tpos"),
            ],
            1,
        )
        return writes, tags, reads


def initiations_of(writes, tags, reads):
    """``{(frame, voice): (writes before, delay)}``: each read whose value reaches a
    voice's frequency register newer than any read that reached it before initiates there,
    timed by its read; ``delay`` counts the calls to the first such write."""
    last, out = {}, {}
    for f, (ws, ts) in enumerate(zip(writes, tags)):
        for (r, _), t in zip(ws, ts):
            if not t or r >= SHARED or r % STRIDE > FREQ + 1:
                continue
            v = r // STRIDE
            if t > last.get(v, 0):
                last[v] = t
                call, pos = (int(x) for x in reads[t])
                out.setdefault((call, v), (pos, f - call))
    return out


def _pair(value, lo):
    """A register pair's value: cutoff from its 3 + 8 bits, else its masked word."""
    if lo == CUT:
        return (value & 7) | (value >> 8) << 3
    return value & MASKS[lo % STRIDE]


def pair_values(flat):
    """(value, delta) per write: the frequency, pulse width or cutoff the write leaves and
    its change, for writes to those register pairs; else the byte and 0."""
    regs = np.zeros(REGS, int)
    value, delta = flat["val"].astype(int), np.zeros(len(flat), int)
    for i, r in enumerate(flat["reg"].tolist()):
        lo = PAIR_LO[r]
        old = regs[lo] | regs[lo + 1] << 8 if lo >= 0 else 0
        regs[r] = value[i]
        if lo >= 0:
            new = _pair(regs[lo] | regs[lo + 1] << 8, lo)
            value[i], delta[i] = new, new - _pair(old, lo)
    return value, delta


def decode_shared(reg, value):
    """Fields of a resonance/routing or mode/volume register byte."""
    if reg == RES:
        return {"res": value >> 4, "routing": value & 7, "ext": value >> 3 & 1}
    if reg == MODE:
        return {"mode": value >> 4 & 7, "voice3_off": value >> 7, "volume": value & 15}
    return {}


class Log:  # pylint: disable=too-many-instance-attributes
    """The event log of a detailed reference write log and the initiations."""

    def __init__(self, ref, writes, initiated):
        assert [
            [tuple(w[:2]) for w in f] for f in ref
        ] == writes, "native writes differ from the reference"
        self.flat = flatten(ref)
        self.frames = len(ref)
        self.regs = frame_regs(self.flat, self.frames)
        self.onsets = register_onsets(self.flat, self.frames)
        self.labels = self.onsets["gate"] | self.onsets["test"]
        self.initiated = {k: v for k, v in initiated.items() if k[0] > 0}
        self.pairs = pair_values(self.flat)
        self.same = same_values(self.flat)

    def freq(self, f, v):
        """Voice v's frequency register at the end of frame f."""
        return int(self.regs[f, v * STRIDE]) | int(self.regs[f, v * STRIDE + 1]) << 8

    def _cycle(self, f, v, pos):
        """Cycle of voice v's first write at or after write ``pos`` of frame f, or None."""
        sel = np.flatnonzero(
            (self.flat["frame"] == f)
            & (self.flat["reg"] < SHARED)
            & (self.flat["reg"] // STRIDE == v)
        )
        sel = sel[sel - np.searchsorted(self.flat["frame"], f) >= pos]
        return int(self.flat["cycle"][sel[0]]) if len(sel) else None

    def matches(self, v):
        """``{frame: (arrival, onset)}`` of voice v's initiations: the call whose write
        first carries the read, and the first register onset from the read to it (None).
        """
        out = {}
        for (f, vv), (_, delay) in sorted(self.initiated.items()):
            if vv == v:
                arrival = min(f + delay, self.frames - 1)
                hit = np.flatnonzero(self.labels[f : arrival + 1, v])
                out[f] = (arrival, f + int(hit[0]) if hit.size else None)
        return out

    def events(self):
        """Per voice, the records of the events its initiations start, after a leading
        record of its writes before the first one."""
        out = []
        for v in range(VOICES):
            at = self.matches(v)
            on = np.array(sorted(at), int)
            reg = np.array([at[f][0] for f in on], int)
            onset = [at[f][1] for f in on]
            prep = np.array(
                [
                    self.onsets["prep"][o, v] if o is not None else f
                    for f, o in zip(on, onset)
                ],
                int,
            )
            prev = np.append(0, on[:-1] + 1)
            start = np.maximum(np.minimum(prep, on), prev)
            if not on.size or start[0]:
                start, on, reg = (
                    np.append(0, start),
                    np.append(-1, on),
                    np.append(-1, reg),
                )
            r = self.flat["reg"]
            idx = np.flatnonzero((r < SHARED) & (r // STRIDE == v))
            which = np.searchsorted(start, self.flat["frame"][idx], side="right") - 1
            end = np.append(start[1:], self.frames)
            for e, (f0, f1) in enumerate(zip(start, end)):
                out.append(self._event(v, (f0, f1, on[e], reg[e]), idx[which == e]))
        return out

    def _event(self, v, span, idx):
        f0, f1, on, reg = (int(x) for x in span)
        at, rf = (on, reg) if on >= 0 else (f0, f0)
        onset = self.matches(v).get(on, (0, None))[1] if on >= 0 else None
        bf = self.freq(rf, v)
        note, cents = note_of(bf)
        gate = self.regs[rf:f1, v * STRIDE + CTRL] & GATE
        w = self.flat[idx]
        val = self.pairs[0][idx]
        fq = ((w["reg"] % STRIDE) <= 1) & (val > 0)
        off = np.full(len(idx), np.nan)
        off[fq] = CENT * np.log2(val[fq] / max(bf, 1))
        pos = self.initiated.get((on, v), (0,))[0]
        return {
            "voice": v,
            "frame": at,
            "cycle": self._cycle(at, v, pos) if on >= 0 else None,
            "lead": on < 0,
            "start": f0 - at,
            "delay": rf - at,
            "frames": f1 - at,
            "base": {
                "freq": bf,
                "note": int(note),
                "cents": round(float(cents), 2),
                "name": name_of(int(note)),
            },
            "registers": [
                k for k in ONSETS if onset is not None and self.onsets[k][onset, v]
            ],
            "gate_frames": None if gate.all() else int(np.argmin(gate)) + rf - at,
            "ops": [
                [
                    *map(int, row[:6]),
                    None if np.isnan(row[6]) else round(row[6], 2),
                    int(row[7]),
                ]
                for row in zip(
                    w["frame"] - at,
                    w["cycle"],
                    w["reg"] % STRIDE,
                    w["val"],
                    val,
                    self.pairs[1][idx],
                    off.tolist(),
                    self.same[idx],
                )
            ],
        }

    def shared(self):
        """Records of the shared (filter and volume) register writes."""
        out = []
        for i in np.flatnonzero(self.flat["reg"] >= SHARED):
            w = self.flat[i]
            reg, val = int(w["reg"]), int(w["val"])
            rec = {"frame": int(w["frame"]), "cycle": int(w["cycle"]), "reg": reg}
            rec.update(value=val, same=int(self.same[i]), **decode_shared(reg, val))
            if reg <= CUT + 1:
                rec.update(cutoff=int(self.pairs[0][i]), delta=int(self.pairs[1][i]))
            out.append(rec)
        return out

    def violations(self):
        """``[frame, voice]`` of register onsets (gate rise, test retrigger) between no
        initiation's read and the first write carrying it."""
        hit = np.zeros_like(self.labels)
        for v in range(VOICES):
            for f, (arrival, _) in self.matches(v).items():
                hit[f : arrival + 1, v] = True
        bad = self.labels & ~hit
        bad[0] = False
        return np.argwhere(bad).tolist()

    def summary(self, kinds=None):
        """Initiation, register onset and sequence read counts, and the onsets no
        initiation explains."""
        kinds = list((kinds or {}).values())
        return {
            "frames": self.frames,
            "initiations": len(self.initiated),
            "register_onsets": int(self.labels[1:].sum()),
            "violations": self.violations(),
            "note_reads": kinds.count("note"),
            "other_reads": kinds.count("other"),
        }


def rebuild(events, shared):
    """[(frame, register, value)] of the log's writes in order of frame and cycle."""
    rows = [
        (ev["frame"] + op[0], op[1], ev["voice"] * STRIDE + op[2], op[3])
        for ev in events
        for op in ev["ops"]
    ]
    rows += [(s["frame"], s["cycle"], s["reg"], s["value"]) for s in shared]
    return [(f, r, v) for f, _, r, v in sorted(rows)]


def run_tune(psid, frames, song=None, c_path=None):
    """(events, shared, summary) of a PSID over init and `frames` play calls, translating
    it unless `c_path` names its generated C (with the .h beside it)."""
    import transliterate  # pylint: disable=import-outside-toplevel

    song = sidtrace.default_song(psid) if song is None else song
    with tempfile.TemporaryDirectory() as tmp:
        if c_path is None:
            c, h, _ = transliterate.transliterate(psid, "gen.h")
            c_path = pathlib.Path(tmp) / "gen.c"
            c_path.write_text(c, encoding="utf-8")
            c_path.with_suffix(".h").write_text(h, encoding="utf-8")
        player = Player(c_path, tmp)
        writes, tags, reads = player.run(song, frames)
    ref = sidtrace.trace_psid(psid, frames, song=song, detail=True)
    log = Log(ref, writes, initiations_of(writes, tags, reads))
    return log.events(), log.shared(), log.summary(player.kinds)


def main(argv=None):
    """CLI: write OUT/events.jsonl, OUT/shared.jsonl and OUT/summary.json for a PSID."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("psid")
    ap.add_argument("out", help="output directory")
    ap.add_argument("--c", help="generated .c, its .h beside it (default: translate)")
    ap.add_argument("--song", type=int, help="0-based subtune (default: start song)")
    end = ap.add_mutually_exclusive_group(required=True)
    end.add_argument("--frames", type=int)
    end.add_argument(
        "--hvsc", help="HVSC root (DOCUMENTS/Songlengths.md5): song length"
    )
    args = ap.parse_args(argv)
    psid = pathlib.Path(args.psid).read_bytes()
    song = sidtrace.default_song(psid) if args.song is None else args.song
    frames = args.frames or sidtrace.song_calls(psid, song, args.hvsc)
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    events, shared, s = run_tune(psid, frames, song, args.c)
    for name, rows in (("events", events), ("shared", shared)):
        with open(out / f"{name}.jsonl", "w", encoding="utf-8") as f:
            f.writelines(json.dumps(r, separators=(",", ":")) + "\n" for r in rows)
    (out / "summary.json").write_text(json.dumps(s), encoding="utf-8")
    print(
        f"{len(events)} events, {len(shared)} shared writes; {s['initiations']} "
        f"initiations, {s['register_onsets']} register onsets, "
        f"{len(s['violations'])} outside an initiation"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
