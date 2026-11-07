"""Song data reads of a translated SID player, each with the state cell and SID writes after it.

Song data is the lift's constant data in the .h: held span bytes in no region and no instruction.
tools/irmodule.py instruments the generated C's read sites that may read it; the native run
(tools/eventlog.c) logs each read with the stores and SID writes up to the next (docs/eventlog.md).
"""

import argparse
import ctypes
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

import numpy as np
from deity_informant.cgen.regions import code_bytes
from pysidtracker.emu import observe_data_access
from tqdm import tqdm
from vagrant_moses.c64 import poweron_ram

import irmodule
import sidtrace

HARNESS = pathlib.Path(__file__).with_suffix(".c")
DRIVER = pathlib.Path(__file__).resolve().parent.parent / "c64"
READ, CELL, SIDW = 0, 1, 2
IO = range(0xD000, 0xE000)
MAXE, MAXIO = 1 << 18, 1 << 16
COLS = (
    ("kind", "ek", "u1"),
    ("site", "es", "u2"),
    ("addr", "ea", "u2"),
    ("value", "ev", "u1"),
    ("old", "eo", "u1"),
    ("copy", "ec", "u1"),
)
REC = [("frame", "i4")] + [(n, t) for n, _, t in COLS]
ORG = re.compile(r"^#define (\w+)_ORG 0x([0-9A-F]+)$", re.M)
KONST = re.compile(r"^#define (\w+)_K([0-9A-F]+) (0x[0-9A-F]+)$", re.M)


def loaded_image(psid):
    """The 64 KiB the lift starts from: power-on RAM with the PSID's image."""
    load, _, _, image = sidtrace.psid_parts(psid)
    ram = bytearray(poweron_ram())
    ram[load : load + len(image)] = image
    return ram


def song_mask(image, info):
    """Per original address, whether it is song data: a byte of a held span (``Cj``) in no
    region (``Rk``) and no instruction, by the lift's ``info``."""
    mask = np.zeros(0x10000, bool)
    for lo, hi in info["code_held"]:
        mask[lo:hi] = True
    for org, n in info["regions"]:
        mask[org : org + n] = False
    return mask & ~code_bytes(image, info["starts"])


def origins(h_text):
    """``{array: origin}`` of the regions, held spans and code span a cgen .h defines."""
    return {
        m[1]: int(m[2], 16)
        for m in ORG.finditer(h_text)
        if re.fullmatch(r"[CR]\d+|CODE", m[1])
    }


def unfold(h_text):
    """The .h with each constant byte the .c reads by name (``X_K<offset>``, the byte at
    ``X_ORG`` + offset) read from ``cgen_k``, the bytes at their original addresses, so that
    each read is a load from memory."""
    org = {m[1]: int(m[2], 16) for m in ORG.finditer(h_text)}
    init = []

    def load(m):
        if m[1] not in org:
            return m[0]
        a = (org[m[1]] + int(m[2], 16)) & 0xFFFF
        init.append(f"[0x{a:04X}] = {m[3]}")
        return f"#define {m[1]}_K{m[2]} (cgen_k[0x{a:04X}])"

    text = KONST.sub(load, h_text)
    if not init:
        return text
    head, end, tail = text.rpartition("#endif")
    decl = f"static unsigned char cgen_k[0x10000] = {{{', '.join(init)}}};\n"
    return head + decl + end + tail


class Player:  # pylint: disable=too-few-public-methods
    """The generated C built natively with its song data read sites instrumented."""

    def __init__(self, c_path, out_dir, song, copies=()):
        c_path = pathlib.Path(c_path).resolve()
        out = pathlib.Path(out_dir).resolve()
        h_text = c_path.with_suffix(".h").read_text(encoding="utf-8")
        src = out / "unfolded" / c_path.name
        src.parent.mkdir(exist_ok=True)
        src.write_text(c_path.read_text(encoding="utf-8"), encoding="utf-8")
        src.with_suffix(".h").write_text(unfold(h_text), encoding="utf-8")
        text = irmodule.lower(src, [c_path.parent, DRIVER], ["CGEN_HOST"])
        helpers = set(re.findall(r"^define [^@]*@(rd(?:C\d+)?)\(", text, re.M))
        self.origins = origins(h_text)
        text, self.sites = irmodule.instrument(
            text, {**self.origins, "cgen_k": 0}, song, helpers, set(copies)
        )
        obj, lib = out / "eventlog.o", out / "eventlog.so"
        irmodule.compile_object(text, obj)
        cc = os.environ.get("HOSTCC", "cc")
        subprocess.run(
            [cc, "-O1", "-fPIC", "-shared", "-o", lib, HARNESS, obj], check=True
        )
        self.lib = ctypes.CDLL(str(lib))
        self.lib.step.argtypes = [ctypes.c_int, ctypes.c_uint8]
        self._arr("u1", 0x10000, "song")[:] = song

    def _arr(self, dtype, n, name):
        ctype = np.ctypeslib.as_ctypes_type(np.dtype(dtype))
        return np.ctypeslib.as_array((ctype * n).in_dll(self.lib, name))

    def run(self, song, frames, io=None):
        """Records (``REC``) of init then `frames` play calls in order; ``io`` gives each
        call's I/O reads ``[(address, value)]`` to answer from."""
        ne, nio = (ctypes.c_uint32.in_dll(self.lib, n) for n in ("ne", "nio"))
        cols = [(n, self._arr(t, MAXE, c)) for n, c, t in COLS]
        ioa, iov = self._arr("u2", MAXIO, "ioa"), self._arr("u1", MAXIO, "iov")
        out = []
        for f in tqdm(range(frames + 1), desc="native", unit="frame", mininterval=2):
            reads = np.array((io or {}).get(f, []), int).reshape(-1, 2)
            nio.value = len(reads)
            ioa[: len(reads)], iov[: len(reads)] = reads.T
            k = self.lib.step(int(f == 0), song if f == 0 else 0)
            assert k < 0, f"call {f}: trap {k}"
            rec = np.zeros(ne.value, REC)
            rec["frame"] = f
            for n, col in cols:
                rec[n] = col[: ne.value]
            out.append(rec)
        return np.concatenate(out)


def _observe(mpu, log, on_read, reads):
    """Route the reads of ``reads`` to ``on_read`` while the SID write ``log`` keeps its
    writes: the machine has one data access observer."""
    # pylint: disable-next=protected-access
    observe_data_access(mpu, on_read, log._write, reads=reads, writes=log.registers)


def reference(psid, frames, song, mask):
    """``(writes, io, reads)`` of the original over init and `frames` play calls: its SID
    writes per call, its I/O reads ``{call: [(address, value)]}`` and the ``{(call,
    address): value}`` of its reads of the bytes ``mask`` marks."""
    io, reads = {}, {}

    def observe(mpu, mem, log):
        def on_read(a):
            if a in IO:
                io.setdefault(len(log.calls) - 1, []).append((a, mem[a]))
            else:
                reads[len(log.calls) - 1, a] = mem[a]

        _observe(mpu, log, on_read, set(IO) | set(np.flatnonzero(mask).tolist()))

    writes = sidtrace.trace_psid(psid, frames, song=song, observe=observe)
    return writes, io, reads


def prg_reads(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    prg, frames, song, spans, mask, max_cycles=sidtrace.MAX_CYCLES
):
    """``(writes, reads)``: a C64 build's SID writes per call and the ``{(call, original
    address)}`` of its reads of song data; ``spans`` maps its symbols to the ``(origin,
    length)`` of the original bytes they hold."""
    out = set()

    def observe(mpu, syms, log):
        where = {
            syms[s] + i: org + i
            for s, (org, n) in spans.items()
            for i in range(n)
            if mask[org + i]
        }

        def on_read(a):
            if log.calls:
                out.add((len(log.calls) - 1, where[a]))

        _observe(mpu, log, on_read, set(where))

    writes = sidtrace.trace_prg(
        prg, frames, song, observe=observe, max_cycles=max_cycles
    )
    return writes, out


def same_values(reg, val):
    """Per write, whether it leaves its register's value unchanged (a buffer refresh)."""
    reg, val = np.asarray(reg, int), np.asarray(val, int)
    out = np.zeros(len(reg), bool)
    for r in np.unique(reg):
        idx = np.flatnonzero(reg == r)
        out[idx[1:]] = val[idx[1:]] == val[idx[:-1]]
    return out


def _read(r, sites, spans):
    """The fields of read record ``r``: its site, region and offset, address, value, copy."""
    org, name = spans[np.searchsorted([o for o, _ in spans], r["addr"], "right") - 1]
    fn, line, col = sites[r["site"]]
    return {
        "frame": int(r["frame"]),
        "site": int(r["site"]),
        "function": fn,
        "line": line,
        "column": col,
        "region": name,
        "offset": int(r["addr"]) - org,
        "addr": int(r["addr"]),
        "value": int(r["value"]),
        "copy": int(r["copy"]) - 1 if r["copy"] else None,
    }


def events(rec, sites, origins_):
    """Per song data read, a record of its site, address, value and copy with the state cell
    stores and SID writes up to the next read, after a record of those before the first.
    """
    reads = np.flatnonzero(rec["kind"] == READ)
    sid = rec["kind"] == SIDW
    same = rec["value"] == rec["old"]
    same[sid] = same_values(rec["addr"][sid], rec["value"][sid])
    spans = sorted(
        ((org, name) for name, org in origins_.items() if name[0] == "C"),
        key=lambda s: (s[0], s[1] != "CODE"),
    )
    cut = np.concatenate([[0], reads + 1, [len(rec) + 1]])
    out = []
    for g, (lo, hi) in enumerate(zip(cut[:-1], cut[1:] - 1)):
        ev = _read(rec[reads[g - 1]], sites, spans) if g else {"frame": 0, "site": None}
        sel = rec[lo:hi]
        rows = np.stack(
            [sel["frame"] - ev["frame"], sel["addr"], sel["value"], same[lo:hi]], 1
        )
        ev["cells"] = rows[sel["kind"] == CELL].tolist()
        ev["sid"] = rows[sel["kind"] == SIDW].tolist()
        out.append(ev)
    return out


def rebuild(evs):
    """[(frame, register, value)] of the log's SID writes in order."""
    return [(e["frame"] + f, r, v) for e in evs for f, r, v, _ in e["sid"]]


def run_tune(psid, frames, song=None):
    """``(events, summary, reads, mask)`` of a PSID over init and `frames` play calls,
    translated by tools/transliterate.py: ``reads`` is the ``{(call, address): value}`` of
    the original's reads of the song data ``mask`` marks."""
    import transliterate  # pylint: disable=import-outside-toplevel

    song = sidtrace.default_song(psid) if song is None else song
    with tempfile.TemporaryDirectory() as tmp:
        c, h, info = transliterate.transliterate(psid, "gen.h")
        c_path = pathlib.Path(tmp) / "gen.c"
        c_path.write_text(c, encoding="utf-8")
        c_path.with_suffix(".h").write_text(h, encoding="utf-8")
        mask = song_mask(loaded_image(psid), info)
        copies = [f for f, v in info["provenance"]["functions"].items() if v["index"]]
        player = Player(c_path, tmp, mask, copies)
        ref, io, orig = reference(psid, frames, song, mask)
        rec = player.run(song, frames, io)
    sid = rec[rec["kind"] == SIDW]
    got = list(zip(sid["frame"].tolist(), sid["addr"].tolist(), sid["value"].tolist()))
    assert got == [
        (f, r, v) for f, ws in enumerate(ref) for r, v in ws
    ], "native writes differ from the reference"
    kinds = np.bincount(rec["kind"], minlength=3)
    summary = {
        "frames": frames + 1,
        "reads": int(kinds[READ]),
        "cell_writes": int(kinds[CELL]),
        "sid_writes": int(kinds[SIDW]),
        "song_bytes": int(mask.sum()),
        "code_cells": info["provenance"]["cells"],
        "sites": [list(s) for s in player.sites],
    }
    return events(rec, player.sites, player.origins), summary, orig, mask


def main(argv=None):
    """CLI: write OUT/events.jsonl and OUT/summary.json for a PSID."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("psid")
    ap.add_argument("out", help="output directory")
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
    evs, s, _, _ = run_tune(psid, frames, song)
    with open(out / "events.jsonl", "w", encoding="utf-8") as f:
        f.writelines(json.dumps(r, separators=(",", ":")) + "\n" for r in evs)
    (out / "summary.json").write_text(json.dumps(s), encoding="utf-8")
    print(
        f"{s['reads']} song data reads at {len(s['sites'])} sites, "
        f"{s['cell_writes']} cell writes, {s['sid_writes']} SID writes"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
