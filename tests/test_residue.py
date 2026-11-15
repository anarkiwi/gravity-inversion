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
    missing, new = residue.diff(MANIFEST["sites"].get(name, {}), got)
    assert not missing and not new, {"removed": missing, "new": new}


@pytest.mark.slow
@pytest.mark.parametrize("name", residue.TUNES)
def test_mapped_gotos_generated(name, tmp):
    """Each goto edge of the construct map from the lifted code is a goto of the C (the C's
    other gotos are C2 sites)."""
    c = _census(name, tmp)
    gotos = {tuple(e) for e in c.info["provenance"]["gotos"]}
    assert c._mapped_gotos() <= gotos  # pylint: disable=protected-access


def test_manifest_causes():
    """Every kind a group has sites of has its cause written, and only those."""
    present = {}
    for name, kinds in MANIFEST["sites"].items():
        present.setdefault(residue.group(name), set()).update(kinds)
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
}
"""

HEADER = """#define CODE_ORG 0x1006
#define C0_ORG 0x100B
#define C0_LEN 5
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
uint8_t S_0 = 0;
void cgen_init(uint8_t a) {
  if (a >= 1) {
    trap(2);
  }
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
  R0[0] = v1 < 4 ? R0[v1 & 3] : oob_rd(v1);
}
"""

INFO = {
    "provenance": {
        "functions": {
            "cgen_play": {
                "entry": 0x1006,
                "index": False,
                "calls": [],
                "copies": [],
                "pcs": [0x1006, 0x1008, 0x100B],
            }
        },
        "gotos": [[0x100B, 0x1006]],
        "switches": [0x100D],
    },
    "traps": {1: ("deopt", 0x100B), 2: ("deopt", 0x1000)},
    "jumps": {0x100D: [0x1007, 0x1008, 0x100A]},
    "state": {"vars": {"S_0": 0x1006}},
    "starts": [0x1006, 0x1008, 0x100B, 0x100D],
    "structure": {"functions": 1},
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


@pytest.fixture(scope="module", name="found")
def fixture_found(lift):
    """{kind: [(pc, detail)]} of the synthetic lift."""
    out = {}
    for s in residue.Census(lift).sites():
        out.setdefault(s.kind, []).append((s.pc, s.detail))
    return out


def test_held_code_and_unwritten_memory(found):
    """D1 is C0's instruction bytes; D2 the R0 byte past the image that init, the C's
    stores and init's copies leave unwritten."""
    assert found["D1"] == [(0x100B, "C0 $100B-$100D (3 B): C0@$1006")]
    assert found["D2"] == [(0x3003, "R0 $3003-$3003 (1 B): R0@$1006, rd@$1006")]


def test_init_tables_and_cells(found):
    """D4 counts each init table at init's entry; D5 names the cell's instruction and the
    absolute store that writes it."""
    assert found["D4"] == [(0x1000, "1 block copies"), (0x1000, "2 I/O writes")]
    assert found["D5"] == [(0x1006, "S_0: opcode of $1006, stored by $1008")]


def test_dispatch_and_machine_values(found):
    """A1 is each dispatcher call; A2 a code address compared and a switch over code
    addresses at its computed jump; A3 the packed status byte."""
    assert sorted(d for _, d in found["A1"]) == [
        "oob_rd(v1)",
        "rd(0x3000 + v1)",
        "rd(0x3000)",
    ]
    assert found["A2"] == [
        (0x1006, "compared $1009"),
        (0x100D, "switch over 2 code addresses"),
    ]
    assert found["A3"] == [(0x1006, "v1 | 0x30 | ((v1 == 0) << 1)")]


def test_traps_gotos_decisions(found):
    """C1 attributes each trap by the trap table (an untabled one to its function); C2 is
    every goto without a construct map; C3 the opcode cell test, the bounds test before
    oob_rd and the decision points beyond the function's original branches."""
    assert found["C1"] == [
        (0x1000, "trap(2) deopt"),
        (0x1006, "trap(5) untabled"),
        (0x100B, "trap(1) deopt"),
    ]
    assert found["C2"] == [(0x100B, "goto $1006")]
    assert found["C3"] == [
        (0x1006, "2 decision points, 1 original branches"),
        (0x1006, "bounds test before dispatch"),
        (0x1006, "opcode cell S_0 tested"),
    ]
    assert found["K6"] == [(0x1006, "rd(0x3000)")]


def test_intervals(lift):
    """Value intervals follow C's integer types: a byte, masks, shifts and casts."""
    play = next(d for n, d in costs.functions(lift.tree) if n == "cgen_play")
    subs = [n for n, _ in costs.walk(play) if n.get("kind") == "ArraySubscriptExpr"]
    got = [residue.interval(s["inner"][1]) for s in subs]
    assert (0, 1) in got and (0, 3) in got and (0, 0) in got
    assert residue.runs(np.array([0, 1, 1, 0, 1], bool)) == [(1, 2), (4, 4)]


def test_report_and_manifest(tmp_path, monkeypatch, capsys):
    """The CLI prints sites by kind, D3 as undetectable; the manifest keeps written causes
    and leaves a new kind's empty."""
    site = residue.Site("A1", 0x1006, "rd(0x3000)")
    lines = residue.report("t", [site], [])
    assert "  A1: 1" in lines and "    $1006 rd(0x3000)" in lines
    assert any(line.startswith("  D3: not detectable") for line in lines)
    old = {"causes": {"fuzz-loop": {"A1": "why"}}}
    new = residue.manifest({"fuzz-loop-0": [site], "commando": [site]}, old)
    assert new["causes"] == {"commando": {"A1": ""}, "fuzz-loop": {"A1": "why"}}
    assert residue.diff(new["sites"]["commando"], {}) == (
        [("A1", "$1006", "rd(0x3000)")],
        [],
    )
    path = tmp_path / "m.json"
    monkeypatch.setattr(residue, "MANIFEST", path)
    monkeypatch.setattr(
        residue, "run_all", lambda names, jobs, out: {"commando": [site]}
    )
    assert residue.main(["--write-manifest"]) == 0
    assert json.loads(path.read_text())["sites"] == {
        "commando": {"A1": [["$1006", "rd(0x3000)"]]}
    }
    assert residue.main(["commando", "--json", "--kind", "A1"]) == 0
    assert '"A1"' in capsys.readouterr().out
