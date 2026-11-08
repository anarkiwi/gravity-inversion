"""Account for a generated translation's PRG bytes by cumulative ablation (docs/ablate.md).

Each step rewrites one mechanism of the generated C through clang's AST source ranges and
measures .text: exact steps keep the run's SID writes (`--verify` checks them), representative
ones cost what the hand C's addressing costs. Data: code-kept regions by jennings coverage.
"""

import argparse
import itertools
import json
import os
import pathlib
import re
import subprocess
from collections import Counter, defaultdict
from typing import NamedTuple

import numpy as np
from elftools.elf.elffile import ELFFile
from jennings.coverage import READ, WRITE
from jennings.coverage.cfg import LEN
from pysidtracker.emu import observe_data_access, run_to_rts
from tqdm import tqdm

from cflow import (
    Flow,
    RET,
    Unit,
    alike,
    chain,
    constant,
    ctype,
    effects,
    evaluate,
    is_byte,
    parse_header,
    regions,
    rewrite,
    scopes,
    strip,
    summarize,
    wrap,
)
from constructs import ROOT, load, memory
from copies import instructions
from costs import ast, callee, refs, walk
from disasm import coverage, listing
from sidtrace import (
    MAX_CYCLES,
    compare,
    default_song,
    machine,
    psid_parts,
    song_calls,
    trace_prg,
)
from sizes import DATA_SECTIONS, sections
from transliterate import trap_source

DRIVER = ROOT / "c64"
GUARD = frozenset({1, *range(0xD0, 0xE0)})


class Facts(NamedTuple):
    """Cell writes of the PSID's run: counts in init, addresses play writes, values after
    init."""

    init: Counter
    play: set
    post: dict


def cell_facts(psid, frames, song, addrs):
    """Facts of `addrs` over init and `frames` play calls of 0-based `song`."""
    load_addr, init, play, image = psid_parts(psid)
    mem, mpu = machine(load_addr, image)
    counts, played, phase = Counter(), set(), [0]

    def on_write(addr, _):
        if phase[0]:
            played.add(addr)
        else:
            counts[addr] += 1

    observe_data_access(mpu, on_write=on_write, writes=addrs)
    run_to_rts(mpu, mem, init, song, MAX_CYCLES)
    post = {a: mem[a] for a in addrs}
    phase[0] = 1
    for _ in tqdm(range(frames), desc="cells", unit="frame", leave=False):
        run_to_rts(mpu, mem, play, 0, MAX_CYCLES)
    return Facts(counts, played, post)


def identity_returns(unit, _ctx):
    """Reads of a return global that always holds a value the reader has use that value;
    calls returning an argument are followed by that argument."""
    summary, mod = summarize(unit)
    edits = []
    for f in unit.fns:
        flow = Flow(unit, summary, mod)
        flow.run(f)
        scope = scopes(unit, f)
        for ref, cands in flow.sites.values():
            at = unit.span(ref)
            subs = sorted(hex(c[1]) for c in cands if isinstance(c, tuple)) + sorted(
                scope[c][0]
                for c in cands
                if not isinstance(c, tuple)
                and c in scope
                and scope[c][1] < at[0] < scope[c][2]
            )
            if subs:
                edits.append((*at, subs[0]))
        for n, p in walk(unit.fns[f]):
            if n.get("kind") != "CallExpr" or p.get("kind") == "CompoundStmt":
                continue
            j = summary["ident"].get(callee(n), {}).get(RET)
            arg = (
                None
                if j is None
                else strip(n["inner"][1 + j], ("ParenExpr", "ImplicitCastExpr"))
            )
            if arg and (
                arg.get("kind") == "IntegerLiteral"
                or arg.get("kind") == "DeclRefExpr"
                and arg["referencedDecl"]["id"] in scope
            ):
                b, e = unit.span(n)
                edits += [(b, b, "("), (e, e, f", {unit.src(n['inner'][1 + j])})")]
    return edits


def _largest(unit, fn):
    """The largest region array a runtime function indexes, data regions before code."""
    used = {
        unit.src(n).strip()
        for n, _ in walk(unit.fns[fn])
        if n.get("kind") == "DeclRefExpr"
    }
    arrays = sorted(
        used & unit.arrays, key=lambda a: (a.startswith("R"), unit.regs[a][1])
    )
    return arrays[-1] if arrays else None


def _body(unit, fn, text):
    b, e = unit.span(unit.body(fn))
    return [(b, e, "{\n  " + text + "\n}")]


def direct_reads(unit, _ctx):
    """rd() indexes one data region, as the hand C indexes its music array."""
    if "rd" not in unit.fns or not (arr := _largest(unit, "rd")):
        return []
    a = unit.params("rd")[0]["name"]
    return _body(unit, "rd", f"return {arr}[(uint16_t)({a} - {arr}_ORG)];")


def direct_writes(unit, _ctx):
    """wr() stores into one data region, codechk() passes, and the page-1/I-O address guards
    are false."""
    edits = []
    if "wr" in unit.fns and (arr := _largest(unit, "wr")):
        a, v = (p["name"] for p in unit.params("wr"))
        edits += _body(unit, "wr", f"{arr}[(uint16_t)({a} - {arr}_ORG)] = {v};")
    if "codechk" in unit.fns:
        voids = " ".join(f"(void){p['name']};" for p in unit.params("codechk"))
        edits += _body(unit, "codechk", f"{voids}\n  return 0;")
    for f in unit.fns:
        for n, _ in walk(unit.fns[f]):
            ids = refs(n) if n.get("opcode") == "||" and not effects(n) else ()
            if len(ids) == 1:
                key = next(iter(ids))
                if {x for x in range(256) if constant(n, {key: x})} == GUARD:
                    edits.append((*unit.span(n), "0"))
    return edits


def zero_page_reads(unit, _ctx):
    """rd() through a zero-page pointer, or a variable still holding it, plus a byte passes
    the byte to one helper per pointer that adds it, as the 6502's (zp),Y does."""
    if "rd" not in unit.fns:
        return []
    summary, mod = summarize(unit)
    edits, helpers = [], {}
    for f in set(unit.fns) - {"rd"}:
        flow = Flow(unit, summary, mod)
        flow.run(f)
        for n, cell, y, ok in flow.ptrs.values():
            if ok:
                r, i = cell
                helpers[f"rdz_{r}_{i}"] = f"({r}[{i}] | ((uint16_t){r}[{i + 1}] << 8))"
                arg = "0" if y is None else unit.src(y)
                edits.append((*unit.span(n), f"rdz_{r}_{i}({arg})"))
    end = unit.span(unit.fns["rd"])[1]
    for name, ptr in sorted(helpers.items()):
        edits.append(
            (
                end,
                end,
                f"\nstatic __attribute__((noinline)) uint8_t {name}(uint8_t y) {{\n"
                f"  return rd({ptr} + y);\n}}",
            )
        )
    return edits


def constant_stores(unit, ctx):
    """{address: (value, function, offset after which it holds)} of the cells the run
    writes once, by a top-level store of a constant in an init-only function."""
    stores = Counter(
        unit.address(n["inner"][0])
        for d in unit.fns.values()
        for n, _ in walk(d)
        if n.get("opcode") == "="
    )
    out = {}
    for f in unit.only("tune_init", "tune_play"):
        first = min(
            (
                unit.span(n)[0]
                for n, _ in walk(unit.fns[f])
                if n.get("kind") in ("LabelStmt", "ReturnStmt", "GotoStmt")
            ),
            default=len(unit.text),
        )
        for s in unit.body(f).get("inner", ()):
            a = unit.address(s["inner"][0]) if s.get("opcode") == "=" else None
            v = None if a is None else constant(s["inner"][1])
            if v is not None and unit.span(s)[1] < first and stores[a] == 1:
                if _once(ctx.facts, a, v & 0xFF):
                    out[a] = (v & 0xFF, f, unit.span(s)[1])
    return out


def _once(facts, a, v):
    """Whether the run writes `a` once, in init, with `v`."""
    return facts.init[a] == 1 and a not in facts.play and facts.post.get(a) == v


def _fold(unit, value_of):
    """Edits replacing each constant-index region read with value_of(function, address,
    offset) where that is not None."""
    edits = []
    for f in unit.fns:
        for n, _ in walk(unit.fns[f]):
            if n.get("kind") == "ArraySubscriptExpr" and unit.rvalue(n):
                a = unit.address(n)
                v = None if a is None else value_of(f, a, unit.span(n)[0])
                if v is not None:
                    edits.append((*unit.span(n), hex(v)))
    return edits


def fold_constant_stores(unit, ctx):
    """Reads of a cell stored once with a constant, after the store or in play-only
    functions, are that constant."""
    cells, play = constant_stores(unit, ctx), unit.only("tune_play", "tune_init")

    def value_of(f, a, at):
        if a in cells and (f in play or (f == cells[a][1] and at > cells[a][2])):
            return cells[a][0]
        return None

    return _fold(unit, value_of)


def fold_play_constants(unit, ctx):
    """Reads reachable from play of a cell play never writes are its value after init,
    in a function init also runs only where it reads the cell after init's last write.
    """
    play, init, facts = unit.reach("tune_play"), unit.reach("tune_init"), ctx.facts

    def value_of(f, a, _):
        if f not in play or a in facts.play:
            return None
        if f in init and (f not in ctx.early["functions"] or f in ctx.early.get(a, ())):
            return None
        return facts.post.get(a)

    return _fold(unit, value_of)


def early_reads(prg, song, regs, arrays):
    """{address: functions reading it before init's last write to it}, and "functions":
    those in the ELF, of the region arrays `arrays` in a build's init."""
    syms = symbols(f"{prg}.elf")
    funcs = sorted(
        (s["st_value"], s["st_value"] + s["st_size"], s.name)
        for s in syms
        if s["st_info"]["type"] == "STT_FUNC" and s["st_size"]
    )
    starts = [x[0] for x in funcs]
    where = array_map(syms, regs, arrays)
    reads, last, seq = defaultdict(list), {}, itertools.count()

    def owner(pc):
        i = int(np.searchsorted(starts, pc, "right")) - 1
        return funcs[i][2] if i >= 0 and pc < funcs[i][1] else None

    def observe(mpu, _syms, log):
        def on_read(addr):
            if log.calls:
                reads[where[addr]].append((next(seq), owner(mpu.pc)))

        def on_write(addr, _):
            if log.calls:
                last[where[addr]] = next(seq)

        observe_data_access(mpu, on_read, on_write, reads=set(where), writes=set(where))

    trace_prg(prg, 0, song, observe=observe)
    out = {a: {f for t, f in r if t < last.get(a, -1)} for a, r in reads.items()}
    return {**out, "functions": {x[2] for x in funcs}}


def _split(node, env):
    """(constant, byte expression or None) of `C` or `C + byte`, or None."""
    node = strip(node)
    v = constant(node, env)
    if v is not None:
        return v & 0xFFFF, None
    if node.get("opcode") == "+":
        for c, y in (node["inner"], node["inner"][::-1]):
            v = constant(c, env)
            if v is not None and is_byte(y):
                return v & 0xFFFF, y
    return None


def constant_addresses(unit, ctx):
    """rd()/wr() of a constant address, or a constant plus a byte, index the region array
    holding every address it can reach; wr() only where the bitmap marks each as data (every
    byte past the bitmap is data).
    """
    edits = []
    for f in set(unit.fns) - {"rd", "wr", "codechk"}:
        env = unit.consts(f)
        for n, _ in walk(unit.fns[f]):
            name = callee(n) if n.get("kind") == "CallExpr" else None
            s = _split(n["inner"][1], env) if name in ("rd", "wr") else None
            if s is None:
                continue
            a, y = s
            hi = a + (0 if y is None else 0xFF)
            arr = unit.array_at(a, hi)
            if arr is None:
                continue
            org = unit.regs[arr][0]
            bits = ctx.inits.get(f"{arr}_DATA")
            if name == "wr" and bits is not None:
                if not all(
                    (x - org) >> 3 >= len(bits)
                    or bits[(x - org) >> 3] >> ((x - org) & 7) & 1
                    for x in range(a, hi + 1)
                ):
                    continue
            ref = f"{arr}[0x{a - org:X}" + (
                "]" if y is None else f" + ({unit.src(y)})]"
            )
            if name == "wr":
                ref = f"({ref} = ({unit.src(n['inner'][2])}))"
            edits.append((*unit.span(n), ref))
    return edits


def byte_switch(unit, _ctx):
    """A switch on a function of one byte switches on the byte, each case labelled with the
    bytes that select it."""
    edits = []
    for f in unit.fns:
        for n, _ in walk(unit.fns[f]):
            cond = n["inner"][0] if n.get("kind") == "SwitchStmt" else None
            if cond is None or len(refs(cond)) != 1 or effects(cond):
                continue
            var = next(r for r, _ in walk(cond) if r.get("kind") == "DeclRefExpr")
            if ctype(var) != (8, False) or strip(cond) is var:
                continue
            key, image = var["referencedDecl"]["id"], defaultdict(list)
            for x in range(256):
                image[constant(cond, {key: x})].append(x)
            if None in image:
                continue
            edits.append((*unit.span(cond), var["referencedDecl"]["name"]))
            edits += _labels(unit, n, cond, image)
    return edits


def _labels(unit, switch, cond, image):
    """Edits relabelling a switch's cases by the bytes `image` maps to each value; a case
    no byte selects loses its label."""
    edits = []
    for c in switch["inner"][-1].get("inner", ()):
        while c.get("kind") in ("CaseStmt", "DefaultStmt"):
            if c["kind"] == "CaseStmt":
                b, e = unit.span(c["inner"][0])
                xs = image.get(wrap(evaluate(c["inner"][0], {}), cond))
                if xs:
                    edits.append((b, e, ": case ".join(f"0x{x:X}" for x in xs)))
                else:
                    edits.append((unit.span(c)[0], unit.text.index(":", e) + 1, ""))
            c = c["inner"][-1]
    return edits


def symbols(elf):
    """ELF symbols of a build."""
    with open(elf, "rb") as f:
        return list(ELFFile(f).get_section_by_name(".symtab").iter_symbols())


def array_map(syms, regs, arrays):
    """{build address: 6502 address} of the region arrays `arrays`."""
    base = {s.name: s["st_value"] for s in syms if s.name in arrays}
    return {b + i: regs[n][0] + i for n, b in base.items() for i in range(regs[n][1])}


def alike_branches(unit, ctx):
    """An if / else if / else chain whose bodies differ only in integer literals and the
    names of their own locals becomes one body selecting each literal by the conditions;
    a function the build kept out of line stays so."""
    edits, merged = [], set()
    for f in unit.fns:
        nested = set()
        for n, _ in walk(unit.fns[f]):
            arms = chain(n) if n.get("id") not in nested else []
            if not arms or any(c is not None and effects(c) for c, _ in arms):
                continue
            nested |= {x.get("id") for x, _ in walk(n)}
            text = _merged(unit, arms)
            if text is not None:
                edits.append((*unit.span(n), text))
                merged.add(f)
    if merged:
        prg = ctx.build(unit.text, "alike branches unmerged")
        kept = {
            s.name for s in symbols(f"{prg}.elf") if s["st_info"]["type"] == "STT_FUNC"
        }
        for f in merged & kept:
            b = unit.span(unit.fns[f])[0]
            edits.append((b, b, "__attribute__((noinline)) "))
    return edits


def _merged(unit, arms):
    """The first arm's body with each literal the arms differ in selected by their
    conditions, or None when the bodies are not alike."""
    first, sel = arms[0][1], defaultdict(list)
    for _, body in arms[1:]:
        lits = []
        if not alike(first, body, {}, lits):
            return None
        for lit, v in lits:
            sel[lit["id"]].append((lit, v))
    b0, e0 = unit.span(first)
    inner = []
    for vals in sel.values():
        values = [int(vals[0][0]["value"])] + [v for _, v in vals]
        if len(set(values)) > 1:
            pick = str(values[-1])
            for (c, _), v in zip(arms[-2::-1], values[-2::-1]):
                pick = f"({unit.src(c)}) ? {v} : {pick}"
            b, e = unit.span(vals[0][0])
            inner.append((b - b0, e - b0, f"({pick})"))
    return rewrite(unit.text[b0:e0], inner)


class Step(NamedTuple):
    """One ablation: its name, whether it is exact, and its rewriting passes."""

    name: str
    exact: bool
    passes: tuple


BASELINE = Step("baseline", True, ())
STEPS = (
    Step("identity returns", True, (identity_returns,)),
    Step("rd() direct", False, (direct_reads,)),
    Step("wr(), codechk(), guards direct", False, (direct_writes,)),
    Step("zero-page pointer reads take Y", True, (zero_page_reads,)),
    Step(
        "cells stored once with a constant",
        True,
        (fold_constant_stores, constant_addresses),
    ),
    Step("cells play never writes", True, (fold_play_constants, constant_addresses)),
    Step("byte switch", True, (byte_switch,)),
    Step("alike branches merged", True, (alike_branches,)),
)


class Ablation:  # pylint: disable=too-many-instance-attributes
    """Cumulative ablation of one generated translation, its C beside its header."""

    def __init__(self, c_path, *, work, flags=()):
        self.c = pathlib.Path(c_path)
        self.work, self.flags = pathlib.Path(work), list(flags)
        self.cc = os.environ.get("CC", "mos-c64-clang")
        self.work.mkdir(parents=True, exist_ok=True)
        header = self.c.with_suffix(".h")
        htext = header.read_text(encoding="utf-8")
        (self.work / header.name).write_text(htext, encoding="utf-8")
        defs, self.inits = parse_header(htext)
        self.regs = regions(defs)
        self.psid, self.frames, self.song = None, 0, 0
        self.facts, self.early = Facts(Counter(), set(), {}), {"functions": set()}

    def observe(self, psid, frames, song=None):
        """Facts of the PSID's run of `frames` play calls and the baseline build's init."""
        self.psid, self.frames = psid, frames
        self.song = default_song(psid) if song is None else song
        addrs = {a for o, n in self.regs.values() for a in range(o, o + n)}
        self.facts = cell_facts(psid, frames, self.song, addrs)
        base = self.c.read_text(encoding="utf-8")
        prg = self.build(base, "baseline calls", ["-fno-inline"])
        self.early = early_reads(prg, self.song, self.regs, self.unit(base).arrays)
        return self

    def path(self, tag):
        """The work file of `tag`."""
        return self.work / f"{re.sub(r'\W+', '_', tag).strip('_')}.c"

    def unit(self, text, tag="ast"):
        """The Unit of C text."""
        src = self.path(tag)
        src.write_text(text, encoding="utf-8")
        return Unit(text, ast(src, [self.work, DRIVER], self.cc), self.regs)

    def build(self, text, tag, flags=()):
        """PRG of C text built with the driver; symbols in PRG.elf."""
        src = self.path(tag)
        src.write_text(text, encoding="utf-8")
        prg = src.with_suffix(".prg")
        subprocess.run(
            [
                self.cc,
                "-Os",
                *self.flags,
                *flags,
                f"-I{self.work}",
                f"-I{DRIVER}",
                "-o",
                prg,
                src,
            ]
            + [trap_source(self.work), DRIVER / "driver.c"],
            check=True,
        )
        return prg

    def apply(self, step, text):
        """`text` after a step's passes."""
        for p in step.passes:
            edits = p(self.unit(text), self)
            text = rewrite(text, edits) if edits else text
        return text

    def run(self, verify=False):
        """[{step, kind, text, delta, verified}] of the cumulative chain, and its last PRG.
        With `verify` each exact step's build of the exact chain is compared with the PSID
        over the run."""
        base = self.c.read_text(encoding="utf-8")
        checked, text = {}, base
        exact = [BASELINE] + [s for s in STEPS if s.exact]
        for s in tqdm(exact, desc="exact", unit="step", leave=False):
            text = self.apply(s, text)
            if verify:
                prg = self.build(text, f"exact {s.name}")
                checked[s.name] = (
                    compare(self.psid, str(prg), self.frames, self.song)[2] is None
                )
        rows, text, prev, prg = [], base, None, None
        for s in tqdm((BASELINE, *STEPS), desc="ablate", unit="step", leave=False):
            text = self.apply(s, text)
            prg = self.build(text, s.name)
            size = sections(f"{prg}.elf")[0].get(".text", 0)
            rows.append(
                {
                    "step": s.name,
                    "kind": "exact" if s.exact else "representative",
                    "text": size,
                    "delta": None if prev is None else size - prev,
                    "verified": checked.get(s.name),
                }
            )
            prev = size
        return rows, prg


def code_bytes(psid, cov):
    """Mask of the PSID image's instruction bytes from a jennings listing over `cov`."""
    starts = {i[0] for i in instructions(listing(psid, 0, cov=cov).source)}
    mem = memory(psid)
    code = np.zeros(0x10000, bool)
    for a in starts:
        code[a : a + int(LEN[mem[a]])] = True
    return code


def accounting(psid, frames, song, elf, regs):
    """[{region, origin, bytes, instruction, operands, accessed, untouched, bitmap}] of each
    code-kept region (C*): instruction bytes, those also accessed as data, other bytes
    accessed, bytes neither, and its bitmap's bytes in the ELF; coverage of init and play.
    """
    cov = coverage(psid, [frames], [song], init=True)
    code, data = code_bytes(psid, cov), (cov.flags & (READ | WRITE)) != 0
    bitmaps = {s.name: s["st_size"] for s in symbols(elf) if s.name.endswith("_DATA")}
    out = []
    for r, (org, n) in sorted(regs.items(), key=lambda x: x[1]):
        if re.fullmatch(r"C\d+", r):
            c, d = code[org : org + n], data[org : org + n]
            out.append(
                {
                    "region": r,
                    "origin": org,
                    "bytes": n,
                    "instruction": int(c.sum()),
                    "operands": int((c & d).sum()),
                    "accessed": int((~c & d).sum()),
                    "untouched": int((~c & ~d).sum()),
                    "bitmap": bitmaps.get(f"{r}_DATA", 0),
                }
            )
    return out


def data_bytes(elf):
    """Bytes of an ELF's data sections."""
    size = sections(elf)[0]
    return sum(size.get(n, 0) for n in DATA_SECTIONS)


def ablate(  # pylint: disable=too-many-arguments
    psid, c_path, *, frames, work, hand=None, song=None, flags=(), verify=False
):
    """Report of one translation: steps, hand .text and residual, data accounting."""
    ab = Ablation(c_path, work=work, flags=flags).observe(psid, frames, song)
    rows, prg = ab.run(verify)
    base = pathlib.Path(work) / "baseline.prg.elf"
    out = {
        "steps": rows,
        "data": accounting(psid, frames, ab.song, base, ab.regs),
        "data_bytes": data_bytes(base),
        "final_data_bytes": data_bytes(f"{prg}.elf"),
    }
    if hand:
        out["hand"] = sections(hand)[0].get(".text", 0)
        out["hand_data_bytes"] = data_bytes(hand)
        out["residual"] = rows[-1]["text"] - out["hand"]
    return out


def table(name, report):
    """Markdown tables of a report."""
    lines = [
        f"### {name}",
        "",
        "| Step | Kind | .text | Delta | Verified |",
        "|---|---|---|---|---|",
    ]
    for r in report["steps"]:
        delta = "" if r["delta"] is None else f"{r['delta']:+d}"
        ok = {None: "", True: "yes", False: "NO"}[r["verified"]]
        lines.append(f"| {r['step']} | {r['kind']} | {r['text']} | {delta} | {ok} |")
    if "hand" in report:
        lines.append(
            f"| hand | | {report['hand']} | {report['residual']:+d} residual | |"
        )
    lines += [
        "",
        "| Region | Origin | Bytes | Instruction | Operands accessed | Data accessed "
        "| Untouched | Bitmap |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for d in report["data"]:
        lines.append(
            f"| {d['region']} | ${d['origin']:04X} | {d['bytes']} | {d['instruction']} "
            f"| {d['operands']} | {d['accessed']} | {d['untouched']} | {d['bitmap']} |"
        )
    hand = f", hand {report['hand_data_bytes']}" if "hand_data_bytes" in report else ""
    lines += [
        "",
        f"Data sections: generated {report['data_bytes']}, ablated "
        f"{report['final_data_bytes']}{hand}.",
        "",
    ]
    return "\n".join(lines)


def tune_flags(tune):
    """The tune Makefile's CFLAGS_TUNE."""
    out = subprocess.run(
        ["make", "-s", "--no-print-directory", "-C", ROOT / "tunes" / tune]
        + ["--eval", "ablate-cflags: ; @echo $(CFLAGS_TUNE)", "ablate-cflags"],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.split()


def main(argv=None):
    """CLI entry point: ablate tunes' generated translations (make build/gen.prg first)."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "tunes", nargs="+", help="tunes/<name> with build/gen/<name>.c and .h"
    )
    ap.add_argument("--hvsc", default=ROOT / "fixtures", help="HVSC root: song lengths")
    ap.add_argument("--frames", type=int, help="play calls (default: HVSC length)")
    ap.add_argument(
        "--verify", action="store_true", help="compare each exact step's build"
    )
    ap.add_argument("--json", action="store_true", help="print JSON")
    ap.add_argument("--work", type=pathlib.Path, default=ROOT / "build" / "ablate")
    args = ap.parse_args(argv)
    out = {}
    for tune in tqdm(args.tunes, desc="tunes", unit="tune"):
        psid = (ROOT / load(tune)["psid"]).read_bytes()
        song = default_song(psid)
        build = ROOT / "tunes" / tune / "build"
        hand = build / f"{tune}.prg.elf"
        out[tune] = ablate(
            psid,
            build / "gen" / f"{tune}.c",
            frames=args.frames or song_calls(psid, song, args.hvsc),
            work=args.work / tune,
            hand=hand if hand.exists() else None,
            flags=tune_flags(tune),
            verify=args.verify,
        )
    print(
        json.dumps(out, indent=1)
        if args.json
        else "\n".join(table(t, r) for t, r in out.items())
    )
    return (
        0
        if all(r["verified"] is not False for t in out.values() for r in t["steps"])
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
