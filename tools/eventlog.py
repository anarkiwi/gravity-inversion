"""Note events and their register operations from a lifted SID player and its write log.

The generated C runs natively under a coverage harness only to time initiations: the blocks
whose per-voice execution shares the most information with register onsets. The log itself
holds register operations alone (docs/eventlog.md)."""

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

import sidtrace

HARNESS = pathlib.Path(__file__).with_suffix(".c")
DRIVER = pathlib.Path(__file__).resolve().parent.parent / "c64"
VOICES, STRIDE, REGS = 3, 7, 25
FREQ, PW, CTRL, AD, SR = 0, 2, 4, 5, 6
CUT, RES, MODE = 0x15, 0x17, 0x18
SHARED = VOICES * STRIDE
GATE, TEST = 0x01, 0x08
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
MAXW, MAXB = 4096, 1 << 22


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
    """(mask of onset write positions `on`, first AD/SR write position) of onsets with an
    AD/SR write (positions `env`) after the last gate fall (positions `fall`)."""
    k = np.searchsorted(fall, on) - 1
    first = np.searchsorted(env, fall[np.maximum(k, 0)]) if fall.size else k
    ok = (k >= 0) & (first < len(env))
    ok[ok] &= env[first[ok]] < on[ok]
    return ok, env[first[ok]]


def register_onsets(flat, frames):
    """Register-visible onsets per (frame, voice): 'gate' (gate rises), 'test' (the test
    bit clears with the gate held since before it was set) and 'hard_restart' (an AD/SR
    write between the gate falling and rising) bool arrays, and 'prep': the frame of that
    AD/SR write, else the onset frame."""
    kinds = ("gate", "test", "hard_restart")
    out = {k: np.zeros((frames, VOICES), bool) for k in kinds}
    out["prep"] = np.repeat(np.arange(frames)[:, None], VOICES, 1)
    pos, frame = np.arange(len(flat)), flat["frame"]
    for v in range(VOICES):
        mine = flat["reg"].astype(int) - v * STRIDE
        ci = pos[mine == CTRL]
        g, t = flat["val"][ci] & GATE, flat["val"][ci] & TEST
        rise = g > np.append(0, g[:-1])
        out["gate"][frame[ci[rise]], v] = True
        out["test"][frame[ci[_retriggers(g, t)]], v] = True
        fall = ci[g < np.append(0, g[:-1])]
        ok, env = _hard_restarts(ci[rise], fall, pos[(mine == AD) | (mine == SR)])
        out["hard_restart"][frame[ci[rise][ok]], v] = True
        out["prep"][frame[ci[rise][ok]], v] = frame[env]
    return out


def mutual_information(x, y):
    """Mutual information (nats) of each boolean column of x (samples, k) with boolean y,
    counted only for columns positively associated with y."""
    n, a, t = len(y), x.sum(0).astype(float), float(y.sum())
    tp = (x & y[:, None]).sum(0).astype(float)
    out = np.zeros(x.shape[1])
    for c, px, py in (
        (tp, a, t),
        (a - tp, a, n - t),
        (t - tp, n - a, t),
        (n - a - t + tp, n - a, n - t),
    ):
        with np.errstate(divide="ignore", invalid="ignore"):
            out += np.where(c > 0, c / n * np.log(c * n / (px * py)), 0)
    return np.where(tp * n > a * t, out, 0.0)


def conditional_mi(x, y):
    """Mutual information of each column of x (frames, voices, k) with y (frames, voices)
    given the voice: the mean of the per-voice informations."""
    return np.mean([mutual_information(x[:, v], y[:, v]) for v in range(y.shape[1])], 0)


def initiation(x, y):
    """(chosen columns, their union) of x (frames, voices, k): greedily the column whose
    union with those chosen has the most information on y given the voice, while a step
    gains more than the Miller-Madow bias of the estimate, 1/(2 frames)."""
    union, best, chosen = np.zeros(y.shape, bool), 0.0, []
    while x.shape[2]:
        m = conditional_mi(x | union[:, :, None], y)
        i = int(np.argmax(m))
        if m[i] - best <= 1 / (2 * len(y)):
            break
        best, union = m[i], union | x[:, :, i]
        chosen.append(i)
    return chosen, union


class Player:  # pylint: disable=too-few-public-methods
    """The generated C built natively with the coverage harness."""

    def __init__(self, c_path, out_dir):
        c_path = pathlib.Path(c_path).resolve()
        lib = pathlib.Path(out_dir).resolve() / "eventlog.so"
        cc = os.environ.get("HOSTCC", "cc")
        flags = ["-std=gnu99", "-O0", "-fPIC", "-w"]
        obj = lib.with_suffix(".o")
        inst = ["-fsanitize-coverage=trace-pc", "-finstrument-functions"]
        inc = [f"-I{c_path.parent}", f"-I{DRIVER}", "-DCGEN_HOST"]
        subprocess.run([cc, *flags, *inst, *inc, "-c", "-o", obj, c_path], check=True)
        subprocess.run(
            [cc, *flags, "-shared", "-o", lib, HARNESS, obj, "-ldl"], check=True
        )
        self.lib = ctypes.CDLL(str(lib))
        self.lib.step.argtypes = [ctypes.c_int, ctypes.c_uint8]

    def _arr(self, ctype, n, name):
        return np.ctypeslib.as_array((ctype * n).in_dll(self.lib, name))

    def run(self, song, frames):
        """(writes, blocks) of init then `frames` play calls: per call its SID (register,
        value) writes and the unique pc * 4 + voice of its voice-attributed blocks."""
        nw, nb = (ctypes.c_uint32.in_dll(self.lib, k) for k in ("nw", "nb"))
        wreg = self._arr(ctypes.c_uint8, MAXW, "wreg")
        wval = self._arr(ctypes.c_uint8, MAXW, "wval")
        bpc = self._arr(ctypes.c_size_t, MAXB, "bpc")
        bvoice = self._arr(ctypes.c_int8, MAXB, "bvoice")
        writes, blocks = [], []
        for f in tqdm(range(frames + 1), desc="native", unit="frame"):
            k = self.lib.step(int(f == 0), song if f == 0 else 0)
            assert k < 0, f"call {f}: trap {k}"
            n, b = nw.value, nb.value
            writes.append(list(zip(wreg[:n].tolist(), wval[:n].tolist())))
            pv = bvoice[:b].astype(np.int64)
            keep = pv >= 0
            blocks.append(np.unique(bpc[:b][keep].astype(np.int64) * 4 + pv[keep]))
        return writes, blocks


def block_matrix(blocks):
    """bool (frames, voices, distinct blocks) of per-frame voice-attributed block codes."""
    codes = np.concatenate(blocks)
    pcs = np.unique(codes >> 2)
    x = np.zeros((len(blocks), VOICES, len(pcs)), bool)
    frame = np.repeat(np.arange(len(blocks)), [len(b) for b in blocks])
    x[frame, codes & 3, np.searchsorted(pcs, codes >> 2)] = True
    return x


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


def onset_cycle(flat, frame, v):
    """Cycle of voice v's first write in `frame`, or None."""
    sel = (flat["frame"] == frame) & (flat["reg"] < SHARED)
    sel &= flat["reg"] // STRIDE == v
    c = flat["cycle"][sel]
    return int(c[0]) if len(c) and c[0] >= 0 else None


def decode_shared(reg, value):
    """Fields of a resonance/routing or mode/volume register byte."""
    if reg == RES:
        return {"res": value >> 4, "routing": value & 7, "ext": value >> 3 & 1}
    if reg == MODE:
        return {"mode": value >> 4 & 7, "voice3_off": value >> 7, "volume": value & 15}
    return {}


class Log:
    """The event log of a detailed reference write log and the native run's blocks."""

    def __init__(self, ref, writes, blocks):
        assert [
            [tuple(w[:2]) for w in f] for f in ref
        ] == writes, "native writes differ from the reference"
        self.flat = flatten(ref)
        self.frames = len(ref)
        self.regs = frame_regs(self.flat, self.frames)
        self.onsets = register_onsets(self.flat, self.frames)
        self.labels = self.onsets["gate"] | self.onsets["test"]
        self.code = np.zeros_like(self.labels)
        _, self.code[1:] = initiation(block_matrix(blocks)[1:], self.labels[1:])
        self.pairs = pair_values(self.flat)

    def freq(self, f, v):
        """Voice v's frequency register at the end of frame f."""
        return int(self.regs[f, v * STRIDE]) | int(self.regs[f, v * STRIDE + 1]) << 8

    def events(self):
        """Per voice, the records of the events its initiations start, after a leading
        record of its writes before the first one."""
        out = []
        for v in range(VOICES):
            on = np.flatnonzero(self.code[:, v] | self.labels[:, v])
            prev = np.append(0, on[:-1] + 1)
            start = np.maximum(np.minimum(self.onsets["prep"][on, v], on), prev)
            if not on.size or start[0]:
                start, on = np.append(0, start), np.append(-1, on)
            reg = self.flat["reg"]
            idx = np.flatnonzero((reg < SHARED) & (reg // STRIDE == v))
            which = np.searchsorted(start, self.flat["frame"][idx], side="right") - 1
            end = np.append(start[1:], self.frames)
            for e, (f0, f1) in enumerate(zip(start, end)):
                out.append(self._event(v, (f0, f1, on[e]), idx[which == e]))
        return out

    def _event(self, v, span, idx):
        f0, f1, on = (int(x) for x in span)
        at = on if on >= 0 else f0
        bf = self.freq(at, v)
        note, cents = note_of(bf)
        gate = self.regs[at:f1, v * STRIDE + CTRL] & GATE
        w = self.flat[idx]
        val = self.pairs[0][idx]
        fq = ((w["reg"] % STRIDE) <= 1) & (val > 0)
        off = np.full(len(idx), np.nan)
        off[fq] = CENT * np.log2(val[fq] / max(bf, 1))
        return {
            "voice": v,
            "frame": at,
            "cycle": onset_cycle(self.flat, at, v) if on >= 0 else None,
            "lead": on < 0,
            "start": f0 - at,
            "frames": f1 - at,
            "base": {
                "freq": bf,
                "note": int(note),
                "cents": round(float(cents), 2),
                "name": name_of(int(note)),
            },
            "registers": [
                k
                for k in ("gate", "test", "hard_restart")
                if on >= 0 and self.onsets[k][on, v]
            ],
            "initiated": bool(on >= 0 and self.code[on, v]),
            "gate_frames": None if gate.all() else int(np.argmin(gate)),
            "ops": [
                [*map(int, row[:6]), None if np.isnan(row[6]) else round(row[6], 2)]
                for row in zip(
                    w["frame"] - at,
                    w["cycle"],
                    w["reg"] % STRIDE,
                    w["val"],
                    val,
                    self.pairs[1][idx],
                    off.tolist(),
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
            rec.update(value=val, **decode_shared(reg, val))
            if reg <= CUT + 1:
                rec.update(cutoff=int(self.pairs[0][i]), delta=int(self.pairs[1][i]))
            out.append(rec)
        return out

    def summary(self):
        """Initiations against the register onsets."""
        hit = self.code & self.labels
        return {
            "frames": self.frames,
            "register_onsets": int(self.labels.sum()),
            "initiations": int(self.code.sum()),
            "precision": float(hit.sum() / max(self.code.sum(), 1)),
            "recall": float(hit.sum() / max(self.labels.sum(), 1)),
            "unseen": np.argwhere(self.code & ~hit).tolist(),
            "uninitiated": np.argwhere(self.labels & ~hit).tolist(),
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
        writes, blocks = Player(c_path, tmp).run(song, frames)
    ref = sidtrace.trace_psid(psid, frames, song=song, detail=True)
    log = Log(ref, writes, blocks)
    return log.events(), log.shared(), log.summary()


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
        f"initiations, precision {s['precision']:.3f} recall {s['recall']:.3f} against "
        f"{s['register_onsets']} register onsets; {len(s['unseen'])} unseen in registers"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
