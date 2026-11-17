"""Residue census (tools/residue.py): every tune's and fuzz player's sites equal the committed
manifest (tests/residue_manifest.json, `python3 tools/residue.py --write-manifest`), and each
detector finds exactly the sites of a small synthetic lift."""

import functools
import json

import numpy as np
import pytest
from jennings.acme import assemble

import cflow
import costs
import fuzzplayer
import residue

MANIFEST = residue.read_manifest()


@functools.cache
def _census(name, tmp):
    psid, tune, _ = residue.target(name)
    return residue.Census(residue.generate(psid, tmp / name), tune)


@pytest.fixture(scope="module", name="tmp")
def fixture_tmp(tmp_path_factory):
    """A directory for the generated sources."""
    return tmp_path_factory.mktemp("residue")


@pytest.mark.slow
@pytest.mark.parametrize("name", residue.TUNES + residue.FUZZ)
def test_sites_match_manifest(name, tmp):
    """The detected sites are the manifest's: a new site is a regression, a removed one an
    improvement the manifest must record."""
    got = residue.by_kind(_census(name, tmp).sites())
    missing, new = residue.diff(residue.recorded(MANIFEST, name), got)
    assert not missing and not new, {"removed": missing, "new": new}


@pytest.mark.slow
@pytest.mark.parametrize("name", residue.TUNES)
def test_mapped_gotos_generated(name, tmp):
    """Each goto edge of the construct map from the lifted code is a goto of the C (the C's
    other gotos are goto sites)."""
    c = _census(name, tmp)
    gotos = {tuple(e) for e in c.info["provenance"]["gotos"]}
    assert c._mapped_gotos() <= gotos  # pylint: disable=protected-access


def test_manifest_causes():
    """Every kind a group has residue or informational sites of has its cause written, and
    only those; informational sites are invalid-input-guard alone."""
    present = {}
    for name in MANIFEST["sites"]:
        present.setdefault(residue.group(name), set()).update(
            residue.recorded(MANIFEST, name)
        )
    assert all(set(k) == {residue.GUARD} for k in MANIFEST["informational"].values())
    assert not any(residue.GUARD in k for k in MANIFEST["sites"].values())
    causes = {g: set(c) for g, c in MANIFEST["causes"].items()}
    assert causes == present
    assert all(v for c in MANIFEST["causes"].values() for v in c.values())
    assert set(MANIFEST["sites"]) == set(residue.TUNES + residue.FUZZ)


ASM = """init:
        LDA #$00
        STA $3001
        RTS
play:
        LDA #$07
        STA $1006
        BNE done
done:   RTS
        !byte $11, $22
        JSR done
}
"""

HEADER = """#define CODE_ORG 0x1006
#define C0_ORG 0x100B
#define C0_LEN 8
#define R0_ORG 0x3000
#define R0_LEN 4
#define CGEN_ARRAYS C0, R0
#define CGEN_IO_N 2
#define CGEN_LIT_N 0
#define CGEN_CP_N 1
#define CGEN_CP_DST R0 + 2
#define CGEN_CP_LEN 1
"""

SOURCE = """#include <stdint.h>
#include "gen.h"
uint8_t rd(uint16_t a);
uint8_t oob_rd(uint16_t a);
__attribute__((noreturn)) void trap(uint16_t k);
void io_write(uint16_t a, uint8_t v);
uint8_t C0[C0_LEN];
uint8_t R0[R0_LEN];
uint8_t S_0 = 0, S_1 = 7, S_3 = 6, S_4 = 0x10, S_6 = 0, S_I1 = 0, S_I3 = 0x30;
static uint8_t *cgen_var(uint16_t a) {
  switch ((uint16_t)(a - CODE_ORG)) {
  case 0x1:
    return &S_1;
  case 0x3:
    return &S_3;
  }
  return 0;
}
void cgen_init(uint8_t a) {
  if (a >= 1) {
    trap(2);
  }
}
uint8_t f1(uint8_t v2) {
  R0[2] = v2 | 0x30 | ((v2 == 0) << 1);
  return rd(0x3000 + v2);
}
void f4(void) { R0[2] = 1; }
void f5(void) { R0[2] = 1; }
void f2(void) {
  f5();
  R0[2] = S_I3;
}
void f3(uint8_t v1) {
  R0[v1 & 1] = 0;
  f4();
}
void cgen_play(uint8_t a) {
  uint8_t v1 = C0[a & 1];
  R0[0] = v1;
  if (S_0 == 0x69) {
    R0[0] = 1;
  }
  if (v1 > 3) {
    trap(1);
  }
  if (v1 == 9) {
    trap(5);
  }
  if (v1 == 8) {
    trap(3);
  }
  if (v1 == 7) {
    trap(4);
  }
  if (v1 == 6) {
    trap(6);
  }
  if (v1 == 5) {
    trap(6);
  }
  if (v1 == 4) {
    trap(7);
  }
  if (v1 == 2) {
    trap(8);
  }
  R0[1] = v1 | 0x30 | ((v1 == 0) << 1);
  if ((((uint16_t)C0[1] << 8) | v1) == CODE_ORG + 3) {
    R0[0] = 2;
  }
  switch (v1 | CODE_ORG) {
  case CODE_ORG + 1:
    R0[0] = 3;
    break;
  case CODE_ORG + 2:
    R0[0] = 4;
    break;
  }
  io_write(0xD400 + a, rd(0x3000 + v1));
  rd(0x3000);
  rd(S_3 | (S_4 << 8));
  R0[2] = C0[(uint16_t)((((uint16_t)C0[1] << 8) | v1) - C0_ORG)];
  R0[2] = C0[C0_ORG + 2 - C0_ORG];
  R0[0] = v1 < 4 ? R0[v1 & 3] : oob_rd(v1);
  R0[1] = v1 | 0x30 | ((v1 == 0) << 1);
}
"""


def _fn(entry, pcs, index=False):
    return {"entry": entry, "index": index, "calls": [], "copies": [], "pcs": pcs}


def _guard(kind, *by):
    return {"kind": kind, "tables": [{"by": b} for b in by]}


def _trap(kind, guards, implied=None, proven=False):
    return {"kind": kind, "guards": guards, "implied_by": implied, "proven": proven}


INFO = {
    "emission": {"copies": {}, "checks": [], "traps": {}, "defs": []},
    "provenance": {
        "functions": {
            "cgen_play": _fn(0x1006, [0x1006, 0x1008, 0x100B, 0x100D]),
            "f1": _fn(0x100D, [0x100D]),
            "f2": _fn(0x1008, [0x1008]),
            "f3": _fn(0x100B, [0x100B], index=True),
            "f4": _fn(0x100E, []),
            "f5": _fn(0x100F, [0x1005]),
        },
        "gotos": [[0x100B, 0x1006]],
        "switches": [0x1010],
    },
    "traps": {
        1: ("deopt", 0x100B),
        2: ("deopt", 0x1000),
        3: ("deopt", 0x1008),
        4: ("deopt", 0x1006),
        6: ("deopt", 0x100D),
        7: ("deopt", 0x1002),
        8: ("deopt", 0x1005),
    },
    "trap_info": {
        1: _trap("index", [_guard("index", "object", "target")]),
        2: _trap("entry", [_guard("entry")]),
        3: _trap("index", [_guard("index", "object")], implied=[2]),
        4: _trap("opcode", [_guard("opcode")], proven=True),
        6: _trap("index", [_guard("index", "loaded", "column")]),
        7: _trap("mixed", [_guard("jam"), _guard("codechk")]),
    },
    "jumps": {0x1010: [0x1007, 0x1008, 0x100A]},
    "state": {
        "vars": {
            "S_I1": 0x1001,
            "S_I3": 0x1003,
            "S_0": 0x1006,
            "S_1": 0x1007,
            "S_3": 0x1009,
            "S_4": 0x100A,
            "S_6": 0x100C,
        }
    },
    "starts": [0x1000, 0x1002, 0x1006, 0x1008, 0x100B, 0x100D, 0x1010],
    "structure": {"functions": 5},
}


@pytest.fixture(scope="module", name="lift")
def fixture_lift(tmp_path_factory):
    """A hand-written lift of a small PSID: its C, header and info."""
    out = tmp_path_factory.mktemp("synthetic")
    psid = assemble(fuzzplayer.HEADER + ASM)[0]
    (out / "gen.c").write_text(SOURCE, encoding="ascii")
    (out / "gen.h").write_text(HEADER, encoding="ascii")
    tree = costs.ast(out / "gen.c", [out])
    defs, _ = cflow.parse_header(HEADER)
    unit = cflow.Unit(SOURCE, tree, cflow.regions(defs))
    return residue.Lift(psid, SOURCE, HEADER, INFO, tree, unit, defs)


def _found(lift):
    out = {}
    for s in residue.Census(lift).sites():
        out.setdefault(s.kind, []).append((s.pc, s.detail))
    return out


@pytest.fixture(scope="module", name="found")
def fixture_found(lift):
    """{kind: [(pc, detail)]} of the synthetic lift."""
    return _found(lift)


def test_held_code_and_unwritten_memory(found):
    """code-as-data is C0's instruction bytes, its two runs one site for their one set of
    accesses; unwritten-held the R0 byte past the image that init, the C's stores and init's
    copies leave unwritten."""
    assert found["code-as-data"] == [
        (0x100B, "C0 $100B-$100D, $1010-$1012 (6 B): C0@$1006, rd@$1006")
    ]
    assert found["unwritten-held"] == [
        (0x3003, "R0 $3003 (1 B): R0@$1006, rd@$1006, rd@$100D")
    ]


def test_init_tables(found):
    """replayed-init counts each init table at init's entry; no cell is a kind of its own."""
    assert found["replayed-init"] == [
        (0x1000, "1 block copies"),
        (0x1000, "2 I/O writes"),
    ]
    assert set(found) <= {*residue.KINDS, residue.GUARD}


def test_cells(lift):
    """Each instruction-byte cell has its role and the kind that counts it: an address
    operand a dispatcher call reads address-dispatch, one the C reads as a variable state, a
    transfer operand address-value, a tested opcode extra-decisions, a cell reached by
    address address-dispatch, an immediate state."""
    got = {
        c.lv: (c.role, c.insn, c.reached, w) for c, w in residue.Census(lift).counted()
    }
    assert got == {
        "S_I1": ("immediate", 0x1000, False, "state"),
        "S_I3": ("address", 0x1002, False, "state"),
        "S_0": ("opcode", 0x1006, False, "extra-decisions"),
        "S_1": ("immediate", 0x1006, True, "address-dispatch"),
        "S_3": ("address", 0x1008, True, "address-dispatch"),
        "S_4": ("address", 0x1008, False, "address-dispatch"),
        "S_6": ("transfer", 0x100B, False, "address-value"),
    }


def test_address_dispatch(found):
    """address-dispatch: dispatcher calls (the call through the address operand cells at their
    instruction, play's copy of f1's call once, in f1), the subscript by a machine address
    (not the constant one) and the cells cgen_var reaches; an address operand cell the C
    reads as a pointer variable (S_I3) is none."""
    assert sorted(found["address-dispatch"]) == [
        (0x1006, "oob_rd(v1)"),
        (0x1006, "rd(0x3000)"),
        (
            0x1006,
            "subscript by address C0[(uint16_t)((((uint16_t)C0[1] << 8) | v1) - C0_ORG)]",
        ),
        (0x1007, "S_1 reached by address"),
        (0x1008, "rd(S_3 | (S_4 << 8))"),
        (0x1009, "S_3 reached by address"),
        (0x100D, "rd(0x3000 + v2)"),
    ]


def test_machine_values(found):
    """address-value: a code address compared, a switch over code addresses at its computed
    jump, the branch offset cell; flags-value the packed status byte, play's copy of f1's
    once and its own."""
    assert found["address-value"] == [
        (0x1006, "compared $1009"),
        (0x100B, "transfer operand of $100B: S_6"),
        (0x1010, "switch over 2 code addresses"),
    ]
    assert found["flags-value"] == [
        (0x1006, "v1 | 0x30 | ((v1 == 0) << 1)"),
        (0x100D, "v2 | 0x30 | ((v2 == 0) << 1)"),
    ]


def test_switched_transfer_cell(lift):
    """A transfer operand cell of a computed jump printed as a switch is that switch's site."""
    info = {
        **INFO,
        "jumps": {0x100B: [0x1007, 0x1008]},
        "provenance": {**INFO["provenance"], "switches": [0x100B]},
    }
    assert _found(lift._replace(info=info))["address-value"] == [
        (0x1006, "compared $1009"),
        (0x100B, "switch over 2 code addresses"),
    ]


def test_traps(found):
    """Each trap once by trap_info: implied or proven implied-guard; every guard on invalid
    input invalid-input-guard; a deopt kind, an extent ending at a target, a mixed trap with
    a deopt guard, no trap_info or untabled deopt."""
    assert found["deopt"] == [
        (0x1002, "trap(7) mixed"),
        (0x1005, "trap(8) no trap_info"),
        (0x1006, "trap(5) untabled"),
        (0x100B, "trap(1) index"),
    ]
    assert found["implied-guard"] == [
        (0x1006, "trap(4) opcode proven"),
        (0x1008, "trap(3) index implied by trap(2)"),
    ]
    assert found[residue.GUARD] == [
        (0x1000, "trap(2) entry"),
        (0x100D, "trap(6) index"),
    ]


def test_implied_by_entry():
    """A trap only the entry guard implies is invalid-input-guard, named by the entry guard;
    one other traps or constants imply is implied-guard."""
    assert residue.trap_class(_trap("index", [_guard("index")], implied=["entry"])) == (
        residue.GUARD,
        "index implied by the entry guard",
    )
    assert (
        residue.trap_class(_trap("index", [], implied=["entry", 3]))[0]
        == "implied-guard"
    )
    assert residue.trap_class(_trap("entry", [], implied=[])) == (
        "implied-guard",
        "entry implied by constants",
    )
    assert residue.trap_class(_trap("index", [_guard("index")]))[0] == "deopt"


def test_machine_objchk():
    """An objchk guard of the machine model's rule (`def` machine) is invalid-input-guard."""
    g = dict(_guard("objchk"), **{"def": "machine"})
    assert residue.trap_class(_trap("objchk", [g])) == (residue.GUARD, "objchk")
    assert residue.trap_class(_trap("mixed", [g, _guard("index")]))[0] == "deopt"


def _ref(name):
    return {"kind": "DeclRefExpr", "referencedDecl": {"id": name, "name": name}}


def _if(*arms):
    return {"kind": "IfStmt", "inner": [_ref("v1"), *arms]}


def test_guard_after_leaving_arm():
    """A test whose arm leaves and whose next statement traps decides nothing; one whose arm
    falls through, or that the statement after does not trap, decides its condition."""
    trap = {"kind": "CallExpr", "inner": [_ref("trap")]}
    leave = {"kind": "CompoundStmt", "inner": [{"kind": "ContinueStmt"}]}
    fall = {"kind": "CompoundStmt", "inner": [{"kind": "NullStmt"}]}
    decide = residue.Census._decisions  # pylint: disable=protected-access

    def decided(n, *rest):
        return len(decide(None, n, {"kind": "CompoundStmt", "inner": [n, *rest]}))

    assert decided(_if(leave), trap) == 0
    assert decided(_if(fall), trap) == 1
    assert decided(_if(leave), fall) == 1
    assert decided(_if(leave)) == 1
    assert decided(_if(leave, fall), trap) == 1
    assert len(decide(None, _if(leave), None)) == 1


def test_control(found):
    """goto is every goto without a construct map and stray-function each function at no
    procedure entry (a JSR target, a computed JSR's target and an instance function are
    procedures; f4 is called in tail position but returns by no RTS, f5 returns by RTS but
    is called before a store); extra-decisions the opcode cell test and the decision points
    beyond the function's original branches, the bounds test before oob_rd none."""
    assert found["goto"] == [(0x100B, "goto $1006")]
    assert found["stray-function"] == [
        (0x100E, "function f4 at no procedure entry"),
        (0x100F, "function f5 at no procedure entry"),
    ]
    assert found["extra-decisions"] == [
        (0x1006, "2 decision points, 1 original branches"),
        (0x1006, "opcode cell S_0 tested"),
    ]
    assert not set(found) & set(costs.KINDS)


def test_intervals(lift):
    """Value intervals follow C's integer types: a byte, masks, shifts and casts."""
    play = next(d for n, d in costs.functions(lift.tree) if n == "cgen_play")
    subs = [n for n, _ in costs.walk(play) if n.get("kind") == "ArraySubscriptExpr"]
    got = [cflow.interval(s["inner"][1]) for s in subs]
    assert (0, 1) in got and (0, 3) in got and (0, 0) in got
    assert cflow.runs(np.array([0, 1, 1, 0, 1], bool)) == [(1, 2), (4, 4)]


def test_report_and_manifest(tmp_path, monkeypatch, capsys):
    """The CLI prints sites by kind, hull-gap as undetectable and invalid-input-guard as
    informational; the manifest keeps written causes, leaves a new kind's empty and records
    invalid-input-guard apart from the residue."""
    site = residue.Site("address-dispatch", 0x1006, "rd(0x3000)")
    guard = residue.Site(residue.GUARD, 0x1000, "trap(0) entry")
    lines = residue.report("t", [site, guard], [])
    assert "  address-dispatch: 1" in lines and "    $1006 rd(0x3000)" in lines
    assert "  invalid-input-guard: 1 (informational, not residue)" in lines
    assert any(line.startswith("  hull-gap: not detectable") for line in lines)
    old = {"causes": {"fuzz-loop": {"address-dispatch": "why"}}}
    new = residue.manifest({"fuzz-loop-0": [site], "commando": [site, guard]}, old)
    assert new["causes"] == {
        "commando": {"address-dispatch": "", "invalid-input-guard": ""},
        "fuzz-loop": {"address-dispatch": "why"},
    }
    assert new["informational"] == {
        "commando": {"invalid-input-guard": [["$1000", "trap(0) entry"]]}
    }
    assert residue.recorded(new, "commando") == {
        "address-dispatch": [["$1006", "rd(0x3000)"]],
        "invalid-input-guard": [["$1000", "trap(0) entry"]],
    }
    assert residue.diff(new["sites"]["commando"], {}) == (
        [("address-dispatch", "$1006", "rd(0x3000)")],
        [],
    )
    path = tmp_path / "m.json"
    monkeypatch.setattr(residue, "MANIFEST", path)
    monkeypatch.setattr(
        residue, "run_all", lambda names, jobs, out, cells: {"commando": [site]}
    )
    assert residue.main(["--write-manifest"]) == 0
    assert json.loads(path.read_text())["sites"] == {
        "commando": {"address-dispatch": [["$1006", "rd(0x3000)"]]}
    }
    assert residue.main(["commando", "--json", "--kind", "address-dispatch"]) == 0
    assert '"address-dispatch"' in capsys.readouterr().out
    cell = residue.Cell("S_1", 0x1007, 0x1006, "immediate", True)
    monkeypatch.setattr(
        residue,
        "run_all",
        lambda names, jobs, out, cells: {"t": [(cell, "address-dispatch")]},
    )
    assert residue.main(["t", "--cells"]) == 0
    assert (
        "$1007 S_1: immediate of $1006 -> address-dispatch" in capsys.readouterr().out
    )


def _with_fn(name, **fields):
    """INFO with provenance function `name` given `fields`."""
    fns = INFO["provenance"]["functions"]
    prov = {**INFO["provenance"], "functions": {**fns, name: {**fns[name], **fields}}}
    return {**INFO, "provenance": prov}


def test_computed_jsr_dispatch(lift):
    """A function entered at a computed JSR is its dispatch when every JSR reaching it has
    its target set; a branch reaching it too leaves it at no procedure entry."""
    edge = [0x1010, 0x1010]
    info = _with_fn("f4", entry=0x1010, calls=[edge])
    assert (0x1010, "function f4 at no procedure entry") not in _found(
        lift._replace(info=info)
    )["stray-function"]
    info = _with_fn("f4", entry=0x1010, calls=[edge, [0x100B, 0x1010]])
    assert (0x1010, "function f4 at no procedure entry") in _found(
        lift._replace(info=info)
    )["stray-function"]


def test_shared_tail(lift):
    """A function returning by RTS that code outside it enters only by a branch or falling
    through is a shared tail of the original (f1 at $100D past BNE $100B, its JSR left out of
    the lift); one with no RTS is not, nor one no code outside it enters."""

    def stray(starts, **fields):
        info = {**_with_fn("f1", **fields), "starts": starts, "jumps": {}}
        site = (0x100D, "function f1 at no procedure entry")
        return site in _found(lift._replace(info=info))["stray-function"]

    starts = [s for s in INFO["starts"] if s != 0x1010]
    assert not stray(starts, pcs=[0x100D])
    assert stray(starts, pcs=[0x100E])
    assert stray([s for s in starts if s != 0x100B], pcs=[0x100D])


TAILS = """#include <stdint.h>
uint8_t g(uint8_t v1);
void h(void);
uint8_t t(uint8_t v1) {
  switch (v1) {
  case 1: {
    g(1);
    break;
  }
  case 2:
    g(2);
  case 3:
    return g(3);
  }
  while (v1) {
    g(4);
    v1--;
  }
  if (v1) {
    g(5);
    return v1;
  }
  uint8_t v2 = g(6);
  g(7);
  return v2 + 1;
}
void u(uint8_t v1) {
  if (v1) {
    h();
  } else {
    v1 = 0;
    L1:
      h();
  }
}
void w(uint8_t v1) {
  switch (v1) {
  case 1: {
    h();
    break;
  }
  default:
    break;
  }
}
"""


def test_tail_position(tmp_path):
    """A call is in tail position when control leaves the function after it: ending the
    body through `if` arms and labels, before a `return` of no computed value or the
    `return` of the call, or a `break` of a switch that ends the body; not in a loop, a
    declaration, before more statements or a case it falls through to."""
    (tmp_path / "t.c").write_text(TAILS, encoding="ascii")
    tree = costs.ast(tmp_path / "t.c", [tmp_path])
    parent = cflow.Unit(TAILS, tree, {}).parent
    got = [
        (name, cflow.tail(parent, n))
        for name, d in costs.functions(tree)
        for n, _ in costs.walk(d)
        if n.get("kind") == "CallExpr"
    ]
    assert got == [
        ("t", x) for x in (False, False, True, False, True, False, False)
    ] + [
        ("u", True),
        ("u", True),
        ("w", True),
    ]


def test_opcode_flag(lift):
    """An opcode cell an opcode guard bounds is a flag: its test is no site, an atom and an
    original branch of its function, the cell state."""
    traps = {
        **INFO["trap_info"],
        4: _trap(
            "opcode",
            [
                {
                    **_guard("opcode"),
                    "addr": (0x1006, 0x1006),
                    "ok": [(0x69, 0x69), (0xE9, 0xE9)],
                }
            ],
            proven=True,
        ),
    }
    census = residue.Census(lift._replace(info={**INFO, "trap_info": traps}))
    assert [s for s in census.sites() if s.kind == "extra-decisions"] == [
        residue.Site(
            "extra-decisions", 0x1006, "3 decision points, 2 original branches"
        )
    ]
    assert {c.lv: w for c, w in census.counted()}["S_0"] == "state"


@pytest.mark.parametrize(
    "values, sites",
    [
        ([0x60, 0xEA], [(0x1006, "3 decision points, 2 original branches")]),
        (
            [],
            [
                (0x1006, "2 decision points, 1 original branches"),
                (0x1006, "opcode cell S_0 tested"),
            ],
        ),
    ],
)
def test_opcode_values(lift, values, sites):
    """An opcode cell with a finite value set in `state.opcodes` is a flag without an opcode
    guard; one with no value set is still an extra-decisions site."""
    state = {
        **INFO["state"],
        "opcodes": {
            str(0x1006): {"values": values, "name": "S_0", "enum": ["S_0_RTS"]}
        },
    }
    census = residue.Census(lift._replace(info={**INFO, "state": state}))
    assert [
        (s.pc, s.detail) for s in census.sites() if s.kind == "extra-decisions"
    ] == sites


BLOCK_HEADER = """#define CODE_ORG 0x1006
#define B0_ORG 0x1008
#define B0_LEN 16
#define C0_ORG 0x100B
#define C0_LEN 8
#define R1_ORG 0x100E
#define R1_LEN 2
#define R0_ORG 0x3000
#define R0_LEN 4
#define CGEN_OBJ0_LO 0x100C, 0x1013
#define CGEN_OBJ0_LIM 0x1, 0x0
#define CGEN_OBJ1_ORG 0x100B
#define CGEN_OBJ1_LIM 0x2
#define C0 (B0 + (C0_ORG - B0_ORG))
#define R1 (C0 + (R1_ORG - C0_ORG))
#define CGEN_ARRAYS R0, C0
"""

BLOCK_SOURCE = """#include <stdint.h>
#include "gen.h"
__attribute__((noreturn)) void trap(uint16_t k);
uint8_t cgen_out0(uint16_t p, uint8_t k);
uint8_t B0c0(uint16_t p, uint8_t y, uint8_t k);
uint8_t B0[B0_LEN];
uint8_t R0[R0_LEN];
void cgen_play(uint16_t v1, uint8_t v2) {
  if (!cgen_out0(v1, 0)) {
    R0[0] = B0[(uint16_t)(v1 - B0_ORG)];
  } else {
    trap(9);
  }
  R0[1] = B0c0(v1, v2, 10);
  R0[2] = C0[(uint16_t)(v1 - C0_ORG)];
  R0[3] = R1[(uint16_t)(v1 - R1_ORG)];
}
"""


@pytest.fixture(scope="module", name="block")
def fixture_block(tmp_path_factory, lift):
    """A lift whose arrays are slices of one block `B0`, with objects and padding."""
    out = tmp_path_factory.mktemp("block")
    (out / "gen.c").write_text(BLOCK_SOURCE, encoding="ascii")
    (out / "gen.h").write_text(BLOCK_HEADER, encoding="ascii")
    tree = costs.ast(out / "gen.c", [out])
    defs, _ = cflow.parse_header(BLOCK_HEADER)
    unit = cflow.Unit(BLOCK_SOURCE, tree, cflow.regions(defs))
    info = {
        **INFO,
        "blocks": {"0": {"org": 0x1008, "len": 16, "padding": [(0x1010, 0x1010)]}},
        "traps": {9: ("deopt", 0x1004)},
        "trap_info": {9: _trap("objchk", [{**_guard("objchk"), "at": 0x1009}])},
    }
    return residue.Census(
        residue.Lift(lift.psid, BLOCK_SOURCE, BLOCK_HEADER, info, tree, unit, defs)
    )


def test_objects():
    """A `cgen_out<j>` object runs from each `_LO` (or the one `_ORG`) to it plus `_LIM`."""
    defs, _ = cflow.parse_header(BLOCK_HEADER)
    assert cflow.objects(BLOCK_HEADER, defs) == {
        0: [(0x100C, 0x100D), (0x1013, 0x1013)],
        1: [(0x100B, 0x100D)],
    }


def test_block_reads(block):
    """A block read past its object check (`cgen_out<j>`, or a checked block read
    `B<b>c<j>`) reaches only the object's runs; a slice (`C0`, the region `R1`) from its
    origin to its held array's end."""
    got = sorted((a.what, a.lo, a.hi) for a in block.accesses() if not a.store)
    assert got == [
        ("B0", 0x100C, 0x100D),
        ("B0", 0x1013, 0x1013),
        ("B0c0", 0x100C, 0x100D),
        ("B0c0", 0x1013, 0x1013),
        ("C0", 0x100B, 0x1012),
        ("R1", 0x100E, 0x1012),
    ]


def test_block_sites(block):
    """address-dispatch is the subscript of the held slice `C0` by an address less `C0_ORG`:
    no block read (`B0`, `B0c0`) nor region subscript (`R1`); code-as-data leaves the
    block's padding out; a hoisted check's invalid-input-guard lies at its trap's
    instruction, the store completing the pointer."""
    sites = block.sites()
    assert [(s.pc, s.detail) for s in sites if s.kind == "address-dispatch"] == [
        (0x1006, "subscript by address C0[(uint16_t)(v1 - C0_ORG)]")
    ]
    assert [(s.pc, s.detail) for s in sites if s.kind == "code-as-data"] == [
        (0x100B, "C0 $100B-$100D (3 B): B0@$1006, B0c0@$1006, C0@$1006"),
        (0x1011, "C0 $1011-$1012 (2 B): C0@$1006, R1@$1006"),
    ]
    assert [(s.pc, s.detail) for s in sites if s.kind == residue.GUARD] == [
        (0x1004, "trap(9) objchk")
    ]
