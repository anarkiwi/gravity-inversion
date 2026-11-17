"""Residue census of generated C: each site where the lift kept the 6502's machine model
instead of the program's meaning, by kind (docs/residue.md).

Detection reads the C's clang AST, the generated header and the original PSID; cgen's `info`
attributes a site to the original's addresses (traps, gotos, switches, cells, functions) and
classifies each trap by what it guards (`trap_info`).
"""

import argparse
import concurrent.futures
import functools
import importlib.metadata
import json
import multiprocessing
import pathlib
import re
import sys
from collections import Counter
from typing import NamedTuple

import numpy as np
from jennings.acme import assemble
from jennings.coverage import EXEC, WRITE
from jennings.coverage.cfg import LEN
from jennings.opcodes import OPCODES
from pysidtracker.header import parse_sid_header
from tqdm import tqdm
from vagrant_moses.c64 import poweron_ram

import cflow
import costs
import fuzzplayer
from constructs import ROOT, load, memory
from disasm import coverage
from sidtrace import psid_parts
from transliterate import c64_init, transliterate

KINDS = ("D1", "D2", "D3", "D4", "A1", "A2", "A3", "C1", "C1r", "C2", "C3")
#: The informational kind: traps guarding invalid input, listed but no residue.
GUARD = "C1g"
#: Kinds no site of which the generated C and `info` let the census attribute.
UNDETECTABLE = {
    "D3": "needs each access's address set and the instruction of each C access "
    "(info has neither)",
}
#: trap_info guard kinds that trap input the original's meaning does not define.
GUARD_KINDS = frozenset(("entry", "objchk", "window", "jam"))
#: The child holding a statement's condition, by statement kind.
CONDITION = dict.fromkeys(("IfStmt", "WhileStmt", "ConditionalOperator"), 0)
CONDITION.update(DoStmt=1, ForStmt=2)
#: Statements after which control does not reach the next.
#: Extents of an `index` guard's table that end the data it indexes.
DATA_ENDS = frozenset(("loaded", "object", "code", "column"))
TUNES = ("commando", "incoherent", "automatas", "ghouls", "emomyst")
SEEDS = range(3)
FUZZ = tuple(f"fuzz-{s}-{n}" for s in fuzzplayer.STYLES for n in SEEDS)
#: SID registers each tune's voice code alone writes (written through the voice index).
VOICE_REGS = {
    "commando": (0xD407, 0xD40E),
    "incoherent": (0xD40E, 0xD413, 0xD414),
    "automatas": (0xD409, 0xD410),
    "ghouls": (0xD407, 0xD40E),
    "emomyst": (0xD407, 0xD40E),
}
DRIVER = ROOT / "c64"
OUT = ROOT / "out" / "residue"
MANIFEST = ROOT / "tests" / "residue_manifest.json"
PINS = ("deity-informant", "vagrant-moses", "jennings", "pysidtracker")
DISPATCH = frozenset(("rd", "wr", "oob_rd", "oob_wr", "codechk", "cgen_rd"))
MACHINE_ORGS = ("CODE_ORG", "IMG_ORG")
IO = (0xD000, 0xE000)
INIT_TABLES = {
    "CGEN_IO_N": "I/O writes",
    "CGEN_LIT_N": "literal bytes",
    "CGEN_CP_N": "block copies",
}
BRANCHES = frozenset(op for op, (_, mode) in OPCODES.items() if mode == "rel")
JSR = 0x20
RTS = 0x60
COMPARE = frozenset(("==", "!=", "<", "<=", ">", ">="))
ADDRESS_OPS = frozenset(("+", "-", "|", "&", ">>", "<<"))
WRAPS = cflow.WRAPS
LOCAL = re.compile(r"\bv\d+\b")


class Site(NamedTuple):
    """One residue: its kind, the original address that causes it and what it is."""

    kind: str
    pc: int
    detail: str

    def row(self):
        """[pc, detail] as the manifest stores it."""
        return [f"${self.pc:04X}", self.detail]


class Cell(NamedTuple):
    """A promoted variable on an instruction byte: its lvalue, address, instruction, role
    (`opcode`, `immediate`, `transfer` or `address`) and whether `cgen_var` reaches it.
    """

    lv: str
    at: int
    insn: int
    role: str
    reached: bool


def cell_role(a, s, mnemonic, mode):
    """The role of byte `a` of the instruction at `s`: its opcode, an immediate, the operand
    of a branch or JMP/JSR, or an address operand."""
    if a == s:
        return "opcode"
    if mode == "imm":
        return "immediate"
    if mode == "rel" or mnemonic in ("JMP", "JSR"):
        return "transfer"
    return "address"


def _guards_input(g):
    """Whether a trap_info guard traps input the original does not define: an entry
    register outside its declared values, a pointer outside its object or window, a JAM, an
    index past the end of the data its table is (docs/residue.md)."""
    if g["kind"] in GUARD_KINDS:
        return True
    return (
        g["kind"] == "index"
        and bool(g["tables"])
        and all(t["by"] in DATA_ENDS for t in g["tables"])
    )


def trap_class(meta):
    """(kind, detail) of a trap from its trap_info: C1g implied by the entry guard alone or
    every guard on invalid input, C1r otherwise implied or proven, else C1."""
    if meta is None:
        return "C1", "no trap_info"
    kind = meta["kind"]
    if meta["implied_by"] == ["entry"]:
        return GUARD, f"{kind} implied by the entry guard"
    if meta["implied_by"] is not None:
        by = ", ".join(f"trap({k})" for k in meta["implied_by"]) or "constants"
        return "C1r", f"{kind} implied by {by}"
    if meta["proven"]:
        return "C1r", f"{kind} proven"
    if meta["guards"] and all(_guards_input(g) for g in meta["guards"]):
        return GUARD, kind
    return "C1", kind


class Lift(NamedTuple):
    """A generated translation with what the census reads of it."""

    psid: bytes
    text: str
    header: str
    info: dict
    tree: dict
    unit: cflow.Unit
    defs: dict


def generate(psid, out):
    """Lift of `psid`: its C and header written to out/gen.c and .h and parsed by clang."""
    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)
    c, h, info = transliterate(psid, "gen.h")
    (out / "gen.c").write_text(c, encoding="utf-8")
    (out / "gen.h").write_text(h, encoding="utf-8")
    defs, _ = cflow.parse_header(h)
    tree = costs.ast(out / "gen.c", [out, DRIVER])
    return Lift(psid, c, h, info, tree, cflow.Unit(c, tree, cflow.regions(defs)), defs)


def norm(text):
    """C text with its whitespace collapsed."""
    return " ".join(text.split())


def runs(mask):
    """[(first, last)] of the runs of True in a boolean array."""
    edges = np.flatnonzero(np.diff(np.concatenate(([0], mask.view(np.int8), [0]))))
    return [(int(a), int(b) - 1) for a, b in zip(edges[::2], edges[1::2])]


class Access(NamedTuple):
    """A memory access of a tune function: who, what, the address interval, its direction."""

    fn: str
    what: str
    lo: int
    hi: int
    store: bool


def _clip(lo, hi):
    return max(lo, 0), min(hi, 0xFFFF)


class Census:  # pylint: disable=too-many-instance-attributes
    """Residue sites of one lift (`tune` names its construct map and hand translation)."""

    def __init__(self, lf, tune=None):
        self.lf, self.tune, self.info = lf, tune, lf.info
        self.unit, self.defs = lf.unit, lf.defs
        self.load, self.init, self.play, image = psid_parts(lf.psid)
        self.image = memory(lf.psid)
        self.inside = np.zeros(0x10000, bool)
        self.inside[self.load : self.load + len(image)] = True
        prov = self.info["provenance"]["functions"]
        self.entry = {n: f["entry"] for n, f in prov.items()}
        self.entry.setdefault("cgen_init", self.init)
        self.nodes = {
            n: list(costs.walk(d))
            for n, d in costs.functions(lf.tree)
            if n in self.entry
        }
        self.held = [
            (a, self.defs[f"{a}_ORG"], self.defs[f"{a}_LEN"])
            for a in cflow.header_list(lf.header, "CGEN_ARRAYS")
        ]
        self._accesses = self._cells = None
        self._via = set()

    def _at(self, fn):
        """Original entry of a C function."""
        return self.entry[fn]

    # Memory.

    def _base(self, node):
        """Address interval of a pointer expression into a header array, or None."""
        node = cflow.strip(node, WRAPS)
        kind = node.get("kind")
        if kind == "DeclRefExpr":
            org = self.defs.get(f"{node['referencedDecl']['name']}_ORG")
            return None if org is None else (org, org)
        if kind == "BinaryOperator" and node["opcode"] in ("+", "-"):
            a, b = node["inner"]
            if "*" not in a["type"]["qualType"]:
                a, b = b, a
            p = self._base(a)
            if p is None or "*" in b["type"]["qualType"]:
                return None
            i = cflow.interval(b)
            if node["opcode"] == "-":
                i = (-i[1], -i[0])
            return p[0] + i[0], p[1] + i[1]
        return None

    def _within(self, base, lo, hi):
        """[lo, hi] clipped to the held array holding address `base` (16 bits outside)."""
        for _, org, n in self.held:
            if org <= base < org + n:
                return max(lo, org), min(hi, org + n - 1)
        return _clip(lo, hi)

    def _subscript(self, fn, node):
        p = self._base(node["inner"][0])
        if p is None:
            return []
        i = cflow.interval(node["inner"][1])
        lo, hi = self._within(p[0], p[0] + i[0], p[1] + i[1])
        what = norm(self.unit.src(node["inner"][0])).strip("()")
        par = self.unit.parent.get(node["id"], {})
        lhs = par.get("inner", [{}])[0].get("id") == node["id"]
        store = lhs and (
            par.get("kind") == "CompoundAssignOperator"
            or par.get("opcode") in cflow.ASSIGNS
        )
        reads = not (store and par.get("opcode") == "=")
        out = [Access(fn, what, lo, hi, True)] if store else []
        return out + ([Access(fn, what, lo, hi, False)] if reads else [])

    def _call(self, fn, node):
        name = costs.callee(node)
        args = node["inner"][1:]
        if costs.CHECKED.match(name or "") and len(args) == 2:
            p, y = cflow.interval(args[0]), cflow.interval(args[1])
            span = self.defs[f"{name[2:]}_ORG"], self.defs[f"{name[2:]}_LEN"]
            lo, hi = _clip(p[0] + y[0], p[1] + y[1])
            lo, hi = max(lo, span[0]), min(hi, sum(span) - 1)
            return [Access(fn, name, lo, hi, False)] if lo <= hi else []
        if name in DISPATCH - {"codechk"} and args:
            lo, hi = _clip(*cflow.interval(args[0]))
            return [Access(fn, name, lo, hi, name in ("wr", "oob_wr"))]
        return []

    def accesses(self):
        """Every array access and dispatch call of the tune functions."""
        if self._accesses is None:
            out = []
            for fn, nodes in self.nodes.items():
                for n, _ in nodes:
                    if n.get("kind") == "ArraySubscriptExpr":
                        out += self._subscript(fn, n)
                    elif n.get("kind") == "CallExpr":
                        out += self._call(fn, n)
            dst = cflow.header_list(self.lf.header, "CGEN_CP_DST")
            for d, n in zip(dst, cflow.header_list(self.lf.header, "CGEN_CP_LEN")):
                m = re.fullmatch(r"(\w+) \+ (\w+)", d)
                if m and f"{m[1]}_ORG" in self.defs and int(n, 0):
                    lo = self.defs[f"{m[1]}_ORG"] + int(m[2], 0)
                    out.append(
                        Access("cgen_init", "CP_DST", lo, lo + int(n, 0) - 1, True)
                    )
            self._accesses = out
        return self._accesses

    def _readers(self, lo, hi):
        """Who accesses any byte of [lo, hi], as `what@entry`."""
        who = sorted(
            {
                f"{a.what}@${self._at(a.fn):04X}"
                for a in self.accesses()
                if a.lo <= hi and lo <= a.hi
            }
        )
        return ", ".join(who) or "no access"

    def _held_mask(self):
        """{array: bool mask of its bytes}."""
        out = {}
        for name, org, n in self.held:
            m = np.zeros(0x10000, bool)
            m[org : org + n] = True
            out[name] = m
        return out

    @functools.cached_property
    def post_init(self):
        """Memory after init of the first song on sidtrace's C64."""
        mem = bytearray(poweron_ram())
        _, _, _, image = psid_parts(self.lf.psid)
        mem[self.load : self.load + len(image)] = image
        c64_init(mem, self.init, 0)
        return np.frombuffer(bytes(mem), np.uint8)

    def _init_cov(self):
        """jennings coverage of init over every song."""
        n = parse_sid_header(self.lf.psid).songs
        return coverage(self.lf.psid, [0] * n, list(range(n)), init=True)

    def _instruction_mask(self, cov):
        """Bytes of the lift's instructions and of those init executes."""
        mask = np.zeros(0x10000, bool)
        for mem, starts in (
            (self.post_init, self.info["starts"]),
            (self.image, np.flatnonzero(cov.flags & EXEC)),
        ):
            for s in starts:
                mask[s : s + int(LEN[mem[s]])] = True
        return mask

    def d1_d2(self):
        """D1 instruction bytes held in a data array; D2 bytes held outside the image that
        init does not write and no store of the C may reach."""
        cov = self._init_cov()
        code = self._instruction_mask(cov)
        stored = np.zeros(0x10000, bool)
        for a in self.accesses():
            if a.store:
                stored[a.lo : a.hi + 1] = True
        unwritten = ~self.inside & ~((cov.flags & WRITE) != 0) & ~stored
        unwritten[IO[0] : IO[1]] = False
        out = []
        for name, m in self._held_mask().items():
            for kind, mask in (("D1", m & code), ("D2", m & unwritten)):
                cause = {}
                for lo, hi in runs(mask):
                    cause.setdefault(self._readers(lo, hi), []).append((lo, hi))
                for who, rs in cause.items():
                    n = sum(hi - lo + 1 for lo, hi in rs)
                    spans = ", ".join(
                        f"${lo:04X}" if lo == hi else f"${lo:04X}-${hi:04X}"
                        for lo, hi in rs
                    )
                    out.append(Site(kind, rs[0][0], f"{name} {spans} ({n} B): {who}"))
        return out

    def d4(self):
        """Init's effect replayed from tables instead of translated."""
        return [
            Site("D4", self.init, f"{self.defs[k]} {what}")
            for k, what in INIT_TABLES.items()
            if self.defs.get(k)
        ]

    def cells(self):
        """[Cell] of each promoted variable on a byte of one of the lift's instructions."""
        if self._cells is None:
            mem = self.post_init
            owner = {}
            for s in self.info["starts"]:
                for a in range(s, s + int(LEN[mem[s]])):
                    owner[a] = s
            reached = self._var_cases()
            out = []
            for lv, a in sorted(self.info["state"]["vars"].items(), key=lambda x: x[1]):
                if a in owner:
                    s = owner[a]
                    role = cell_role(a, s, *OPCODES[int(mem[s])])
                    out.append(Cell(lv, a, s, role, a in reached))
            self._cells = out
        return self._cells

    def counted(self):
        """[(Cell, where its role is counted)]: A1, A2, C3 or plain state."""
        sites = self.sites()
        a2 = {s.pc for s in sites if s.kind == "A2"}
        c3 = {s.detail.split()[2] for s in sites if s.detail.startswith("opcode cell")}
        out = []
        for c in self.cells():
            if (
                c.role != "transfer"
                and c.reached
                or (c.role == "address" and c.insn in self._via)
            ):
                where = "A1"
            elif c.role == "transfer":
                where = "A2" if c.insn in a2 else "state"
            else:
                where = "C3" if c.lv in c3 else "state"
            out.append((c, where))
        return out

    def _var_cases(self):
        """Addresses `cgen_var` finds a variable at: the cells reached by address."""
        org = self.defs.get("CODE_ORG", 0)
        return {
            org + cflow.constant(c["inner"][0])
            for n, d in costs.functions(self.lf.tree)
            if n == "cgen_var"
            for c, _ in costs.walk(d)
            if c.get("kind") == "CaseStmt"
        }

    def _cell_sites(self, kind, role, what):
        """One site per instruction whose operand cells have `role`."""
        per = {}
        for c in self.cells():
            if c.role == role:
                per.setdefault(c.insn, []).append(c.lv)
        return [
            Site(kind, s, f"{what} of ${s:04X}: {', '.join(lvs)}")
            for s, lvs in per.items()
        ]

    # Control and addressing.

    def _inlined(self):
        """{function: [functions all of whose instructions it also holds]}: the copies the
        printer inlined into it."""
        prov = self.info["provenance"]["functions"]
        pcs = {n: set(f["pcs"]) for n, f in prov.items() if n in self.nodes}
        return {
            f: [g for g, q in pcs.items() if g != f and q and q <= p]
            for f, p in pcs.items()
        }

    def _once(self, per_fn):
        """Sites of `{function: [Site]}`, a function's site dropped where it repeats one of a
        function inlined into it (equal up to the names of locals `v<n>`)."""
        inlined = self._inlined()
        out = []
        for fn, sites in per_fn.items():
            copies = Counter(
                LOCAL.sub("v", s.detail)
                for g in inlined.get(fn, ())
                for s in per_fn.get(g, ())
            )
            for s in sites:
                key = LOCAL.sub("v", s.detail)
                if copies[key]:
                    copies[key] -= 1
                else:
                    out.append(s)
        return out

    def a1(self):
        """Address dispatch: calls of cgen's dispatchers (one through address operand cells
        at each of their instructions), subscripts by a 16-bit machine address and cells
        reached by address."""
        pointers = {}
        for c in self.cells():
            if c.role == "address":
                pointers.setdefault(c.lv.split("[")[0], set()).add(c.insn)
        per_fn = {}
        for fn, nodes in self.nodes.items():
            for n, _ in nodes:
                kind = n.get("kind")
                if kind == "CallExpr" and (
                    costs.callee(n) in DISPATCH
                    or costs.CHECKED.match(costs.callee(n) or "")
                ):
                    what = norm(self.unit.src(n))
                    names = cflow.names(n["inner"][1]) if len(n["inner"]) > 1 else set()
                    pcs = set().union(*(pointers.get(v, ()) for v in names))
                    self._via |= pcs
                    per_fn.setdefault(fn, []).extend(
                        Site("A1", pc, what) for pc in sorted(pcs or {self._at(fn)})
                    )
                elif kind == "ArraySubscriptExpr" and self._machine_index(n):
                    what = "subscript by address " + norm(self.unit.src(n))
                    per_fn.setdefault(fn, []).append(Site("A1", self._at(fn), what))
        transfers = {c.insn for c in self.cells() if c.role == "transfer"}
        return self._once(per_fn) + [
            Site("A1", c.at, f"{c.lv} reached by address")
            for c in self.cells()
            if c.reached and c.insn not in transfers
        ]

    def _machine_index(self, node):
        """Whether a subscript of a held array `X` adds a non-constant 16-bit machine address
        less `X_ORG`, not an offset into `X`."""
        base = cflow.strip(node["inner"][0], WRAPS)
        terms = cflow.terms(node["inner"][1])
        if base.get("opcode") in ("+", "-"):
            terms += cflow.terms(base)
        arrays = [t for _, t in terms if "*" in t["type"]["qualType"]]
        terms = [(s, t) for s, t in terms if "*" not in t["type"]["qualType"]]
        if base.get("kind") == "DeclRefExpr":
            arrays.append(base)
        org = next(
            (
                self.defs.get(f"{a['referencedDecl']['name']}_ORG")
                for a in arrays
                if a.get("kind") == "DeclRefExpr"
            ),
            None,
        )
        if org is None or all(cflow.constant(t) is not None for _, t in terms):
            return False
        return any(s < 0 and cflow.constant(t) == org for s, t in terms)

    def _machine_literal(self, node):
        """Which machine origin an integer literal spells (CODE_ORG, IMG_ORG), or None."""
        at = costs.offset(node)
        if node.get("kind") != "IntegerLiteral" or at is None:
            return None
        return next((o for o in MACHINE_ORGS if self.lf.text.startswith(o, at)), None)

    def _address_expr(self, node):
        """The largest address expression around a machine literal, and its use."""
        par = self.unit.parent
        top = node
        while par.get(top["id"], {}).get("kind") in WRAPS or (
            par.get(top["id"], {}).get("opcode") in ADDRESS_OPS
        ):
            top = par[top["id"]]
        use = par.get(top["id"], {})
        if use.get("opcode") in COMPARE:
            return top, "compared"
        if use.get("kind") == "CallExpr":
            return top, "argument"
        if use.get("opcode") in ("=",) or use.get("kind") == "VarDecl":
            return top, "stored"
        return top, "value"

    def _switch_pc(self, cases):
        """The printed switch's computed jump whose targets best hold `cases`, or None."""
        jumps = {int(k): set(v) for k, v in self.info["jumps"].items()}
        cand = [
            p
            for p in self.info["provenance"]["switches"]
            if p in jumps and cases <= jumps[p]
        ]
        if not cand:
            return None
        best = min(len(jumps[p] - cases) for p in cand)
        cand = [p for p in cand if len(jumps[p] - cases) == best]
        return cand[0] if len(cand) == 1 else None

    def a2(self):
        """Machine addresses as values: CODE_ORG/IMG_ORG in tune code (a switch over code
        addresses one site) and the operand cells of transfers without such a switch."""
        out = set()
        for fn, nodes in self.nodes.items():
            switches = {}
            for n, _ in nodes:
                if self._machine_literal(n) is None:
                    continue
                sw = cflow.switch_of(self.unit.parent, n)
                if sw is not None:
                    switches[sw["id"]] = sw
                    continue
                top, use = self._address_expr(n)
                inner = top
                while inner.get("opcode") not in ("+", "-") and inner.get("inner"):
                    if inner.get("kind") in WRAPS or inner.get("opcode") in ADDRESS_OPS:
                        inner = inner["inner"][0]
                    else:
                        break
                v = cflow.constant(inner)
                addr = f"${v:04X}" if v is not None else norm(self.unit.src(inner))
                out.add(Site("A2", self._at(fn), f"{use} {addr}"))
            for sw in switches.values():
                cases = {
                    cflow.constant(c["inner"][0])
                    for c, _ in costs.walk(sw)
                    if c.get("kind") == "CaseStmt"
                }
                pc = self._switch_pc(cases)
                out.add(
                    Site(
                        "A2",
                        self._at(fn) if pc is None else pc,
                        f"switch over {len(cases)} code addresses",
                    )
                )
        switched = {s.pc for s in out if s.detail.startswith("switch over")}
        cells = self._cell_sites("A2", "transfer", "transfer operand")
        return sorted(out | {s for s in cells if s.pc not in switched})

    def a3(self):
        """The status register packed as a byte: an `|` of the constant bits $30 with
        shifted flags."""
        per_fn = {}
        for fn, nodes in self.nodes.items():
            for n, _ in nodes:
                if (
                    n.get("opcode") != "|"
                    or cflow.outer(self.unit.parent, n).get("opcode") == "|"
                ):
                    continue
                leaves = list(cflow.or_leaves(n))
                if any(cflow.constant(x) == 0x30 for x in leaves) and any(
                    cflow.strip(x, WRAPS).get("opcode") == "<<" for x in leaves
                ):
                    site = Site("A3", self._at(fn), norm(self.unit.src(n)))
                    per_fn.setdefault(fn, []).append(site)
        return self._once(per_fn)

    def c1(self):
        """Each trap of the C once, by what it stands for (`trap_info`): a translatable
        deopt (C1), a redundant guard (C1r) or a guard on invalid input (C1g)."""
        traps = {int(k): v for k, v in self.info["traps"].items()}
        meta = {int(k): v for k, v in self.info.get("trap_info", {}).items()}
        out = {}
        for fn, nodes in self.nodes.items():
            for n, _ in nodes:
                if n.get("kind") == "CallExpr" and costs.callee(n) == "trap":
                    k = cflow.constant(n["inner"][1])
                    if k not in out and k not in traps:
                        out[k] = Site("C1", self._at(fn), f"trap({k}) untabled")
                    elif k not in out:
                        kind, why = trap_class(meta.get(k))
                        out[k] = Site(kind, traps[k][1], f"trap({k}) {why}")
        return list(out.values())

    def c2(self):
        """Gotos beyond the construct map's, functions for no procedure of the original,
        voice registers written at constant addresses."""
        prov = self.info["provenance"]
        gotos = {tuple(e) for e in prov["gotos"]}
        mapped = self._mapped_gotos()
        out = [Site("C2", a, f"goto ${b:04X}") for a, b in sorted(gotos - mapped)]
        procs = self._procedures()
        out += [
            Site("C2", f["entry"], f"function {n} at no procedure entry")
            for n, f in sorted(prov["functions"].items(), key=lambda x: x[1]["entry"])
            if n in self.nodes
            and not f["index"]
            and f["entry"] not in procs
            and not self._jsr_dispatch(f)
            and not self._tail_called(n, f)
        ]
        if self.tune is None:
            return out
        regs = set(VOICE_REGS[self.tune])
        for fn, nodes in self.nodes.items():
            for n, _ in nodes:
                if n.get("kind") == "CallExpr" and costs.callee(n) == "io_write":
                    a = cflow.leading(n["inner"][1])
                    if a in regs:
                        out.append(
                            Site(
                                "C2", self._at(fn), f"voice register ${a:04X} constant"
                            )
                        )
        return out

    def _procedures(self):
        """The original's procedure entries: init, play and every target of a JSR of the
        lift's instructions, its operand in the loaded image or as init leaves it, or each
        target `info["jumps"]` gives a JSR whose operand the program writes."""
        out = {self.init, self.play}
        jumps = {int(k): v for k, v in self.info["jumps"].items()}
        for mem in (self.image, self.post_init):
            for s in self.info["starts"]:
                if int(mem[s]) == JSR:
                    out.add(int(mem[s + 1]) | int(mem[s + 2]) << 8)
                    out.update(jumps.get(s, ()))
        return out

    def _jsr_dispatch(self, f):
        """Whether a function is the dispatch of a computed JSR over its targets: entered at
        that JSR, every JSR reaching it with the same target set (`info["jumps"]`)."""
        mem, e = self.post_init, f["entry"]
        targets = {int(k): set(v) for k, v in self.info["jumps"].items()}
        edges = [s for s, t in f["calls"] if t == e] or [None]
        return int(mem[e]) == JSR and all(
            s is not None
            and int(mem[s]) == JSR
            and targets.get(s) == targets.get(e, ())
            for s in edges
        )

    def _tail_called(self, name, f):
        """Whether a function returning by RTS is called, and only in tail position."""
        calls = [
            n
            for nodes in self.nodes.values()
            for n, _ in nodes
            if n.get("kind") == "CallExpr" and costs.callee(n) == name
        ]
        return any(int(self.post_init[p]) == RTS for p in f["pcs"]) and all(
            cflow.tail(self.unit.parent, n) for n in calls or [None]
        )

    def _mapped_gotos(self):
        """The construct map's goto edges from the lifted code (none without a map)."""
        if self.tune is None:
            return set()
        starts = set(self.info["starts"])
        return {
            tuple(e)
            for c in load(self.tune)["constructs"]
            if c["kind"] == "goto"
            for e in c["edges"]
            if e[0] in starts
        }

    def c3(self):
        """Decision points that are no original branch: tests of an opcode cell, and per
        function those beyond the original's branches (trap tests are C1's, bounds tests
        before a dispatcher A1's)."""
        starts = set(self.info["starts"])
        opcode_cells = {
            lv for lv, a in self.info["state"]["vars"].items() if a in starts
        }
        flags = self._flag_cells()
        prov = self.info["provenance"]["functions"]
        jumps = {int(k) for k in self.info["jumps"]}
        out = []
        for fn, nodes in self.nodes.items():
            atoms = tested = 0
            for n, up in nodes:
                for atom in self._decisions(n, up):
                    names = cflow.names(atom)
                    cells = sorted(names & opcode_cells)
                    if cells and not set(cells) <= flags:
                        cell = next(c for c in cells if c not in flags)
                        a = self.info["state"]["vars"][cell]
                        out.append(Site("C3", a, f"opcode cell {cell} tested"))
                    elif cells:
                        atoms, tested = atoms + 1, tested + 1
                    elif not _dispatch_guard(n):
                        atoms += 1
            if fn not in prov:
                continue
            pcs = prov[fn]["pcs"]
            branches = tested + sum(
                int(self.image[p]) in BRANCHES or p in jumps for p in pcs
            )
            if atoms > branches:
                out.append(
                    Site(
                        "C3",
                        self._at(fn),
                        f"{atoms} decision points, {branches} original branches",
                    )
                )
        return out

    def _flag_cells(self):
        """The opcode cells whose value set `info` bounds: each with a finite value set in
        `state.opcodes`, else one an `opcode` guard checks (trap_info), its `ok` the values
        the C takes as the program's, a trap the rest."""
        valued = {
            int(a)
            for a, o in self.info["state"].get("opcodes", {}).items()
            if o.get("values")
        }
        guarded = [
            g["addr"]
            for t in self.info.get("trap_info", {}).values()
            for g in t["guards"]
            if g["kind"] == "opcode" and g.get("addr") and g.get("ok")
        ]
        return {
            lv
            for lv, a in self.info["state"]["vars"].items()
            if a in valued or any(lo <= a <= hi for lo, hi in guarded)
        }

    def _decisions(self, n, up=None):
        """The atoms of the condition a statement or expression decides; none for a test
        with an arm that traps, the statement after an `if` whose arm leaves its arm too.
        """
        kind, inner = n.get("kind"), n.get("inner", ())
        if kind == "SwitchStmt":
            return [inner[0]]
        if kind == "IfStmt" and any(
            _traps(s) for s in inner[1:] + cflow.after_if(n, up)
        ):
            return []
        at = CONDITION.get(kind)
        if at is None or at >= len(inner) or not inner[at]:
            return []
        return [a for a in cflow.atoms(inner[at]) if cflow.constant(a) is None]

    def sites(self):
        """Every residue site of the lift, sorted."""
        found = self.d1_d2() + self.d4() + self.a1() + self.a2() + self.a3()
        found += self.c1() + self.c2() + self.c3()
        order = KINDS + (GUARD,)
        return sorted(found, key=lambda s: (order.index(s.kind), s.pc, s.detail))


def _traps(stmt):
    """Whether a statement starts with a trap call."""
    s = cflow.first(stmt)
    return s.get("kind") == "CallExpr" and costs.callee(s) == "trap"


def _dispatch_guard(n):
    """Whether an `if` or conditional has an arm that is a dispatcher call (a bounds test
    before it)."""
    if n.get("kind") not in ("IfStmt", "ConditionalOperator"):
        return False
    return any(
        cflow.first(a).get("kind") == "CallExpr"
        and costs.callee(cflow.first(a)) in DISPATCH
        for a in n["inner"][1:]
    )


def fuzz_psid(name):
    """The PSID of fuzz target `fuzz-<style>-<seed>`."""
    _, style, seed = name.split("-")
    return assemble(fuzzplayer.player(style, int(seed)))[0]


def target(name, out=OUT):
    """(psid, tune or None) of a target: a tune name, `fuzz-<style>-<seed>`, or a PSID path."""
    out = pathlib.Path(out) / pathlib.Path(name).stem
    if name in TUNES:
        return (ROOT / load(name)["psid"]).read_bytes(), name, out
    if name.startswith("fuzz-"):
        return fuzz_psid(name), None, out
    return pathlib.Path(name).read_bytes(), None, out


def census(name, out=OUT, cells=False):
    """Sorted residue sites of a target (`cells`: its instruction-byte cells, counted)."""
    psid, tune, where = target(name, out)
    c = Census(generate(psid, where), tune)
    return c.counted() if cells else c.sites()


def group(name):
    """The manifest's cause group of a target: the tune, or the fuzz style."""
    if name.startswith("fuzz-"):
        return name.rsplit("-", 1)[0]
    return pathlib.Path(name).stem


def by_kind(sites):
    """{kind: [[pc, detail], ...]} of sites, in order."""
    out = {}
    for s in sites:
        out.setdefault(s.kind, []).append(s.row())
    return out


def run_all(names, jobs, out=OUT, cells=False):
    """{name: census(name, out, cells)} of targets, `jobs` at a time."""
    if jobs <= 1 or len(names) == 1:
        return {
            n: census(n, out, cells) for n in tqdm(names, desc="residue", unit="lift")
        }
    found = {}
    with concurrent.futures.ProcessPoolExecutor(
        jobs, multiprocessing.get_context("forkserver")
    ) as ex, tqdm(total=len(names), desc="residue", unit="lift") as progress:
        futs = {ex.submit(census, n, out, cells): n for n in names}
        for f in concurrent.futures.as_completed(futs):
            found[futs[f]] = f.result()
            progress.update()
    return {n: found[n] for n in names}


def read_manifest(path=MANIFEST):
    """The committed manifest, or an empty one."""
    if not path.exists():
        return {"pins": {}, "causes": {}, "sites": {}, "informational": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def recorded(m, name):
    """{kind: rows} a manifest records for a target: its residue and informational sites."""
    return {**m["sites"].get(name, {}), **m.get("informational", {}).get(name, {})}


def manifest(found, old):
    """A manifest of `found` sites, residue (`sites`) apart from the informational guards,
    keeping `old` causes of each (group, kind) still present; a new one's cause is empty
    until written."""
    sites = {n: by_kind(x for x in s if x.kind != GUARD) for n, s in found.items()}
    info = {n: by_kind(x for x in s if x.kind == GUARD) for n, s in found.items()}
    causes = {}
    for n in found:
        g = group(n)
        for k in {**sites[n], **info[n]}:
            causes.setdefault(g, {})[k] = old.get("causes", {}).get(g, {}).get(k, "")
    return {
        "pins": {p: importlib.metadata.version(p) for p in PINS},
        "causes": {g: dict(sorted(c.items())) for g, c in sorted(causes.items())},
        "sites": sites,
        "informational": {n: rows for n, rows in info.items() if rows},
    }


def diff(expected, got):
    """(missing, new) site rows of one target's {kind: rows} against the manifest's."""
    e = Counter((k, *r) for k, rows in expected.items() for r in rows)
    g = Counter((k, *r) for k, rows in got.items() for r in rows)
    return sorted((e - g).elements()), sorted((g - e).elements())


def report(name, sites, kinds):
    """Text lines of a target's sites grouped by kind."""
    lines = [f"{name}:"]
    per = by_kind(s for s in sites if not kinds or s.kind in kinds)
    for k in KINDS + (GUARD,):
        if kinds and k not in kinds:
            continue
        if k in UNDETECTABLE:
            lines.append(f"  {k}: not detectable: {UNDETECTABLE[k]}")
            continue
        rows = per.get(k, [])
        note = " (informational, not residue)" if k == GUARD else ""
        lines.append(f"  {k}: {len(rows)}{note}")
        lines += [f"    {pc} {d}" for pc, d in rows]
    return lines


def main(argv=None):
    """CLI entry point: print each target's sites, or write the manifest."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("targets", nargs="*", help="tune, fuzz-<style>-<seed> or PSID")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--kind", action="append", choices=KINDS + (GUARD,), default=[])
    ap.add_argument("--write-manifest", action="store_true", help="every tune and fuzz")
    ap.add_argument(
        "--cells", action="store_true", help="instruction-byte cells and their kind"
    )
    ap.add_argument("--out", type=pathlib.Path, default=OUT)
    ap.add_argument("-j", "--jobs", type=int, default=8)
    args = ap.parse_args(argv)
    names = args.targets or (list(TUNES + FUZZ) if args.write_manifest else [])
    if not names:
        ap.error("no targets")
    found = run_all(names, args.jobs, args.out, args.cells and not args.write_manifest)
    if args.cells and not args.write_manifest:
        for n, rows in found.items():
            print(f"{n}:", Counter(w for _, w in rows))
            for c, w in rows:
                print(f"  ${c.at:04X} {c.lv}: {c.role} of ${c.insn:04X} -> {w}")
        return 0
    if args.write_manifest:
        new = manifest(found, read_manifest())
        MANIFEST.write_text(json.dumps(new, indent=1) + "\n", encoding="utf-8")
        todo = [
            f"{g} {k}" for g, c in new["causes"].items() for k, v in c.items() if not v
        ]
        print(
            f"{MANIFEST}: {len(found)} targets; causes to write: {', '.join(todo) or 'none'}"
        )
        return 0
    if args.json:
        out = {
            n: by_kind(s for s in sites if not args.kind or s.kind in args.kind)
            for n, sites in found.items()
        }
        print(json.dumps(out, indent=1))
        return 0
    for n, sites in found.items():
        print("\n".join(report(n, sites, args.kind)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
