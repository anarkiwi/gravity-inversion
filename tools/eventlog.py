"""Song data reads of a translated SID player, each with the state cell and SID writes after it.

Song data is the loaded bytes of no instruction that the original never writes. tools/irflow.py
classifies the generated C's reads by what their addresses derive from: an event reads at a
position the player advances across calls (from the state init left in the .h), a lookup at a
value an event or lookup read. The native run (tools/eventlog.c) logs each play call's read of
song data with its kind and the stores and SID writes up to the next (docs/eventlog.md).
"""

import argparse
import array
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

import irflow
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
#: A read: its call, original address and value.
READS = np.dtype([("frame", "u4"), ("addr", "u2"), ("value", "u1")])
ORG = re.compile(r"^#define (\w+)_ORG 0x([0-9A-F]+)$", re.M)
KONST = re.compile(r"^#define (\w+)_K([0-9A-F]+) (0x[0-9A-F]+)$", re.M)


def song_mask(psid, starts=()):
    """Per original address, whether the PSID loads it and it is no byte of an instruction
    at ``starts`` (song data where the program never writes it)."""
    load, _, _, image = sidtrace.psid_parts(psid)
    mem = np.zeros(0x10000, np.uint8)
    mem[load : load + len(image)] = np.frombuffer(image, np.uint8)
    mask = np.zeros(0x10000, bool)
    mask[load : load + len(image)] = True
    return mask & ~code_bytes(mem, starts)


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
    """The generated C built natively with its song data read sites instrumented; ``cells``
    maps its promoted variables (``S_8``, ``V0_f00[1]``) to their original addresses."""

    def __init__(
        self, c_path, out_dir, song, copies=(), cells=None
    ):  # pylint: disable=too-many-arguments,too-many-positional-arguments
        c_path = pathlib.Path(c_path).resolve()
        out = pathlib.Path(out_dir).resolve()
        h_text = c_path.with_suffix(".h").read_text(encoding="utf-8")
        src = out / "unfolded" / c_path.name
        src.parent.mkdir(exist_ok=True)
        src.write_text(c_path.read_text(encoding="utf-8"), encoding="utf-8")
        src.with_suffix(".h").write_text(unfold(h_text), encoding="utf-8")
        text = irmodule.lower(src, [c_path.parent, DRIVER], ["CGEN_HOST"], passes=True)
        helpers = set(re.findall(rf"^define [^@]*@({irmodule.HELPERS})\(", text, re.M))
        self.origins = origins(h_text)
        regions = {g: {0: org} for g, org in {**self.origins, "cgen_k": 0}.items()}
        for ref, addr in (cells or {}).items():
            m = re.fullmatch(r"(\w+)(?:\[(\d+)\])?", ref)
            regions.setdefault(m[1], {})[int(m[2] or 0)] = addr
        text, self.sites = irmodule.instrument(
            text,
            regions,
            irflow.classify(text),
            helpers,
            set(copies),
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
        """Records (``REC``) of init then `frames` play calls in order, init's reads (its copy
        of the state the original's init left) dropped; ``io`` gives each call's I/O reads
        ``[(address, value)]`` to answer from."""
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
            out.append(rec[rec["kind"] != READ] if f == 0 else rec)
        return np.concatenate(out)


def _observe(mpu, log, on_read, reads, *, on_write=None, writes=()):
    """Route the reads of ``reads`` to ``on_read`` and the writes of ``writes`` to
    ``on_write`` while the SID write ``log`` keeps its writes: the machine has one data
    access observer."""

    def write(addr, value):
        if on_write and addr in writes:
            on_write(addr, value)
        if addr in log.registers:
            log._write(addr, value)  # pylint: disable=protected-access

    regs = set(log.registers) | set(writes)
    observe_data_access(mpu, on_read, write, reads=reads, writes=regs)


def _reads(calls, addrs, values=None):
    """``READS`` of the reads ``(call, address[, value])``, one per call and address, sorted."""
    key = np.frombuffer(calls, np.uint32).astype(np.uint64) << 16 | np.frombuffer(
        addrs, np.uint16
    )
    key, first = np.unique(key, return_index=True)
    out = np.zeros(len(key), READS)
    out["frame"], out["addr"] = key >> 16, key & 0xFFFF
    if values is not None:
        out["value"] = np.frombuffer(values, np.uint8)[first]
    return out


def reference(psid, frames, song, loaded):
    """``(writes, io, reads, mask)`` of the original over init and `frames` play calls: SID
    writes per call, I/O reads ``{call: [(address, value)]}``, the play calls' ``READS`` of
    song data ``mask``, the bytes of ``loaded`` it never writes."""
    io, written = {}, np.zeros(0x10000, bool)
    calls, addrs, values = array.array("I"), array.array("H"), array.array("B")
    watched = set(np.flatnonzero(loaded).tolist())

    def observe(mpu, mem, log):
        def on_read(a):
            call = len(log.calls) - 1
            if a in IO:
                io.setdefault(call, []).append((a, mem[a]))
            elif call:
                calls.append(call)
                addrs.append(a)
                values.append(mem[a])

        def on_write(a, _):
            written[a] = True

        _observe(
            mpu, log, on_read, set(IO) | watched, on_write=on_write, writes=watched
        )

    writes = sidtrace.trace_psid(psid, frames, song=song, observe=observe)
    mask = loaded & ~written
    reads = _reads(calls, addrs, values)
    return writes, io, reads[mask[reads["addr"]]], mask


def prg_reads(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    prg, frames, song, spans, mask, max_cycles=sidtrace.MAX_CYCLES
):
    """``(writes, reads)``: a C64 build's SID writes per call and the ``READS`` (values 0) of
    its play calls' reads of song data; ``spans`` maps its symbols to the ``(origin,
    length)`` of the original bytes they hold."""
    calls, addrs = array.array("I"), array.array("H")

    def observe(mpu, syms, log):
        where = {
            syms[s] + i: org + i
            for s, (org, n) in spans.items()
            for i in range(n)
            if mask[org + i]
        }

        def on_read(a):
            if len(log.calls) > 1:
                calls.append(len(log.calls) - 1)
                addrs.append(where[a])

        _observe(mpu, log, on_read, set(where))

    writes = sidtrace.trace_prg(
        prg, frames, song, observe=observe, max_cycles=max_cycles
    )
    return writes, _reads(calls, addrs)


def keys(reads, value=True):
    """Per ``READS`` row one integer: its call, address and (with ``value``) value."""
    k = reads["frame"].astype(np.uint64) << 16 | reads["addr"]
    return k << 8 | reads["value"] if value else k


def flat(writes):
    """``(frame, register, value)`` rows of SID writes per call."""
    n = np.fromiter(map(len, writes), np.int64, len(writes))
    out = np.zeros((int(n.sum()), 3), np.int64)
    out[:, 0] = np.repeat(np.arange(len(writes)), n)
    out[:, 1:] = np.array([w for ws in writes for w in ws], np.int64).reshape(-1, 2)
    return out


def same_values(reg, val):
    """Per write, whether it leaves its register's value unchanged (a buffer refresh)."""
    reg, val = np.asarray(reg, int), np.asarray(val, int)
    out = np.zeros(len(reg), bool)
    for r in np.unique(reg):
        idx = np.flatnonzero(reg == r)
        out[idx[1:]] = val[idx[1:]] == val[idx[:-1]]
    return out


class Log:
    """A run's records (``REC``) as song data reads, each with the state cell stores and SID
    writes up to the next; entry 0 holds those before the first read."""

    def __init__(self, rec, sites, origins_):
        self.rec, self.sites = rec, sites
        read = rec["kind"] == READ
        self.at = np.flatnonzero(read)
        self.reads = rec[self.at]
        sid = rec["kind"] == SIDW
        self.same = rec["value"] == rec["old"]
        self.same[sid] = same_values(rec["addr"][sid], rec["value"][sid])
        self.spans = sorted(
            ((org, name) for name, org in origins_.items() if name[0] == "C"),
            key=lambda s: (s[0], s[1] != "CODE"),
        )
        self.kinds = np.array([k for *_, k in sites] or [""])[self.reads["site"]]

    def __len__(self):
        return len(self.at) + 1

    def __iter__(self):
        return (self[g] for g in range(len(self)))

    def __getitem__(self, g):
        """Entry ``g`` as a record of its read's site, address, value and copy, ``cells``
        and ``sid`` ``[frame, address, value, same]`` with frames from the read's call.
        """
        ev = self._read(self.rec[self.at[g - 1]]) if g else {"frame": 0, "site": None}
        lo = self.at[g - 1] + 1 if g else 0
        hi = self.at[g] if g < len(self.at) else len(self.rec)
        sel = self.rec[lo:hi]
        rows = np.stack(
            [sel["frame"] - ev["frame"], sel["addr"], sel["value"], self.same[lo:hi]], 1
        )
        ev["cells"] = rows[sel["kind"] == CELL].tolist()
        ev["sid"] = rows[sel["kind"] == SIDW].tolist()
        return ev

    def _read(self, r):
        org, name = self.spans[
            np.searchsorted([o for o, _ in self.spans], r["addr"], "right") - 1
        ]
        fn, line, col, kind = self.sites[r["site"]]
        return {
            "frame": int(r["frame"]),
            "site": int(r["site"]),
            "kind": kind,
            "function": fn,
            "line": line,
            "column": col,
            "region": name,
            "offset": int(r["addr"]) - org,
            "addr": int(r["addr"]),
            "value": int(r["value"]),
            "copy": int(r["copy"]) - 1 if r["copy"] else None,
        }

    def sid(self):
        """``(frame, register, value)`` rows of the SID writes in order."""
        w = self.rec[self.rec["kind"] == SIDW]
        return np.stack([w["frame"], w["addr"], w["value"]], 1).astype(np.int64)

    def read_set(self):
        """The ``READS`` of the logged reads, one per call and address."""
        key, first = np.unique(keys(self.reads, False), return_index=True)
        out = np.zeros(len(key), READS)
        out["frame"], out["addr"] = key >> 16, key & 0xFFFF
        out["value"] = self.reads["value"][first]
        return out

    def stored(self, cells):
        """Per read, whether a state cell store up to the next read writes its value to an
        address ``cells`` marks."""
        c = (self.rec["kind"] == CELL) & cells[self.rec["addr"]]
        group = np.cumsum(self.rec["kind"] == READ)
        done = group[c].astype(np.int64) << 8 | self.rec["value"][c]
        want = np.arange(1, len(self.at) + 1, dtype=np.int64) << 8 | self.reads["value"]
        return np.isin(want, done)


def run_tune(psid, frames, song=None):
    """``(log, summary, reads, mask)`` of a PSID over init and `frames` play calls,
    translated by tools/transliterate.py; ``reads`` and the song data ``mask`` are
    :func:`reference`'s, of the loaded bytes of no instruction the lift translates."""
    import transliterate  # pylint: disable=import-outside-toplevel

    song = sidtrace.default_song(psid) if song is None else song
    with tempfile.TemporaryDirectory() as tmp:
        c, h, info = transliterate.transliterate(psid, "gen.h")
        c_path = pathlib.Path(tmp) / "gen.c"
        c_path.write_text(c, encoding="utf-8")
        c_path.with_suffix(".h").write_text(h, encoding="utf-8")
        ref, io, orig, mask = reference(
            psid, frames, song, song_mask(psid, info["starts"])
        )
        copies = [f for f, v in info["provenance"]["functions"].items() if v["index"]]
        player = Player(c_path, tmp, mask, copies, info["state"].get("vars"))
        rec = player.run(song, frames, io)
    log = Log(rec, player.sites, player.origins)
    assert np.array_equal(
        log.sid(), flat(ref)
    ), "native writes differ from the reference"
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
    return log, summary, orig, mask


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
