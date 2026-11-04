"""Map of each tune's machine-code constructs to their hand translation, checked by coverage.

tunes/<tune>/constructs.json lists each deliberately translated construct with its 6502 addresses,
the hand C's form and why; `check` validates it statically and reports jennings' run coverage.
"""

import argparse
import json
import pathlib
import re

import numpy as np
from jennings.coverage import EXEC
from jennings.coverage.cfg import LEN, successors

from copies import instructions
from disasm import coverage, listing
from sidtrace import psid_parts

ROOT = pathlib.Path(__file__).resolve().parent.parent
JSR = 0x20
ADDRESS = re.compile(r"\$[0-9A-Fa-f]{4}")


def _conv(v):
    if isinstance(v, str) and ADDRESS.fullmatch(v):
        return int(v[1:], 16)
    if isinstance(v, list):
        return [_conv(x) for x in v]
    if isinstance(v, dict):
        return {k: _conv(x) for k, x in v.items()}
    return v


def load(tune):
    """The construct map of a tune, `$xxxx` strings as integers."""
    path = ROOT / "tunes" / tune / "constructs.json"
    return _conv(json.loads(path.read_text(encoding="utf-8")))


def memory(data):
    """The PSID image as a 64 KiB array."""
    load_addr, _, _, image = psid_parts(data)
    mem = np.zeros(0x10000, np.uint8)
    mem[load_addr : load_addr + len(image)] = np.frombuffer(image, np.uint8)
    return mem


class Checker:
    """Checks entries statically against the listing; records what the run covered."""

    def __init__(self, mem, cov, starts):
        self.mem, self.cov, self.starts = mem, cov, starts
        self.covered = True

    def at(self, pc):
        """Errors unless pc is an instruction start; notes whether it ran."""
        self.covered &= bool(self.cov.flags[pc] & EXEC)
        return [] if pc in self.starts else [f"${pc:04X} is not an instruction"]

    def edge(self, a, b):
        """Errors unless b is a successor of a; notes whether the run took it."""
        out = self.at(a) + self.at(b)
        if (a, b) in self.cov.edges:
            return out
        flags = {t: f for f, t in successors(self.mem, a)[0]}
        if b not in flags:
            self.covered = False
            return out + [f"${b:04X} is not a successor of ${a:04X}"]
        self.covered &= flags[b] == EXEC or bool(self.cov.flags[a] & flags[b])
        return out

    def calls(self, c):
        """Errors of a call, fall-through call or tail call entry."""
        out = self.at(c["target"])
        for s in c.get("sites", []):
            word = int(self.mem[s + 1]) | int(self.mem[s + 2]) << 8
            if self.mem[s] != JSR or word != c["target"]:
                out.append(f"${s:04X} is not JSR ${c['target']:04X}")
            out += self.at(s)
        for a in c.get("falls", []) + c.get("jumps", []):
            out += self.edge(a, c["target"])
        return out

    def unrolled(self, c):
        """Errors unless the copies have the same opcodes, but for listed inserted ones."""
        skip, starts, n = set(c.get("inserted", [])), c["copies"], c["insns"]

        def ops(start):
            out, a = [], start
            while len(out) < n:
                if a not in skip:
                    out.append(int(self.mem[a]))
                a += int(LEN[self.mem[a]])
            return out

        out = [e for s in starts for e in self.at(s)]
        same = all(ops(s) == ops(starts[0]) for s in starts)
        return out + ([] if same else ["copies differ in opcodes"])

    def smc(self, c):
        """Errors unless the cell is a byte of the reader; notes whether the run used it."""
        r, cell = c["reader"], c["cell"]
        out = self.at(c["writer"]) + self.at(r)
        if not r <= cell < r + int(LEN[self.mem[r]]):
            out.append(f"${cell:04X} is not a byte of ${r:04X}")
        self.covered &= (c["writer"], cell, r) in self.cov.defuse
        return out

    def check(self, c):  # pylint: disable=too-many-return-statements
        """Errors of one construct entry."""
        k = c["kind"]
        if k == "loop":
            return self.edge(c["back"], c["head"])
        if k == "multi_entry_loop":
            edges = c["edges"] + [[c["back"], c["head"]]] + c.get("copies", [])
            return [e for a, b in edges for e in self.edge(a, b)]
        if k in ("call", "fall_call", "tail_call"):
            return self.calls(c)
        if k == "unrolled":
            return self.unrolled(c)
        if k == "smc":
            return self.smc(c)
        if k in ("goto", "multi_entry", "join"):
            return [e for a, b in c["edges"] for e in self.edge(a, b)]
        if k == "short_circuit":
            return [e for b in c["branches"] for e in self.edge(b, c["target"])]
        if k == "switch":
            seen = {b for a, b in self.cov.edges if a == c["dispatch"]}
            seen |= {v for p, v in self.cov.variants if p == c["dispatch"]}
            self.covered &= len(seen) > 1
            edges = [e for a, b in c.get("edges", []) for e in self.edge(a, b)]
            return self.at(c["dispatch"]) + edges
        return [e for a in c.get("pcs", []) for e in self.at(a)]


def run(tune, jobs=1):
    """(map, PSID, coverage of init and play, instruction starts) of a tune's original."""
    m = load(tune)
    data = (ROOT / m["psid"]).read_bytes()
    songs = m.get("songs", [0])
    cov = coverage(data, [m["frames"]] * len(songs), songs, jobs, init=True)
    return m, data, cov, {i[0] for i in instructions(listing(data, 0, cov=cov).source)}


def check(tune, jobs=1):
    """{construct id: (errors, covered)} of a tune's map: static errors, and whether the
    original's run (init and play) executed it."""
    m, data, cov, starts = run(tune, jobs)
    mem = memory(data)
    out = {}
    for c in m["constructs"]:
        checker = Checker(mem, cov, starts)
        errors = checker.check(c)
        out[c["id"]] = (errors, checker.covered)
    return out


def main(argv=None):
    """CLI entry point: validate tunes' maps; print errors and coverage per entry."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tunes", nargs="+")
    ap.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args(argv)
    bad = 0
    for t in args.tunes:
        for cid, (errors, covered) in check(t, args.jobs).items():
            bad += bool(errors)
            seen = "covered" if covered else "not covered"
            print(f"{t}:{cid}: {'; '.join(errors) or 'ok'} ({seen})")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
