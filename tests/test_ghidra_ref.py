"""tools/ghidra_ref.py: seeds, the headless argv, the combined C and JSON index, the image,
and the real pipeline on one fuzz target (needs docker and the image)."""

import json
import os
import pathlib
import shutil
import subprocess
import tempfile
from types import SimpleNamespace

import pytest

import ghidra_ref
import ghidra_types
from residue import target
from sidtrace import psid_parts

FUZZ = "fuzz-jsr3-0"
CLANG = os.environ.get("CLANG", "clang")


def _fn(name, entry, ok=True, globals_=(), calls=()):
    body = f"void {name}(void)\n\n{{\n  return;\n}}\n"
    return {
        "name": name,
        "entry": entry,
        "calls": list(calls),
        "pcs": [entry],
        "ok": ok,
        "error": "" if ok else "Decompiler timed out",
        "signature": f"void {name}(void);",
        "c": body if ok else None,
        "body": body if ok else None,
        "globals": [list(g) for g in globals_],
    }


DUMP = {
    "functions": [
        _fn(
            "FUN_1000", 0x1000, globals_=[("DAT_3000", "undefined1 DAT_3000", 1, False)]
        ),
        _fn(
            "FUN_1040",
            0x1040,
            calls=[0x1000],
            globals_=[
                ("DAT_3000", "undefined2 DAT_3000", 2, False),
                ("_IRQ", "undefined *_IRQ", 2, False),
                ("_IRQ", "code *_IRQ", 2, True),
            ],
        ),
        _fn("FUN_1080", 0x1080, ok=False),
    ],
    "types": "",
}


def test_pin_and_tag(tmp_path):
    """The image is tagged by the deity-informant pin."""
    req = tmp_path / "requirements.txt"
    req.write_text("tqdm==1\ndeity-informant==9.8.7\njennings==2\n", encoding="utf-8")
    assert ghidra_ref.pin(req) == "9.8.7"
    assert ghidra_ref.image_tag("1.2.3") == "gi-ghidra:1.2.3"
    assert ghidra_ref.image_tag() == f"gi-ghidra:{ghidra_ref.pin()}"


def test_seeds_psid_wins_and_zero_play_skipped():
    """Init and play are psid seeds over lifter ones; a zero play is no seed."""
    psid = target(FUZZ)[0]
    _, init, play, _ = psid_parts(psid)
    s = ghidra_ref.seeds(psid, [init, 0x2000])
    assert s == {init: "psid", play: "psid", 0x2000: "lifter"}
    no_play = bytearray(psid)
    no_play[0x0C:0x0E] = b"\0\0"
    assert 0 not in ghidra_ref.seeds(bytes(no_play), [0x2000])
    assert ghidra_ref.seeds(bytes(no_play), [0x2000]) == {
        init: "psid",
        0x2000: "lifter",
    }


def test_headless_argv():
    """The argv loads at the load address and seeds in hex, in order."""
    argv = ghidra_ref.headless("/w", 0x0801, [0x1003, 0x0801])
    assert argv[argv.index("-loader-baseAddr") + 1] == "0x0801"
    pre = argv.index("-preScript")
    assert argv[pre + 1 : pre + 4] == ["GhidraRefSeed.java", "0801", "1003"]
    assert argv[argv.index("-postScript") + 2] == "/w/dump.json"
    assert argv[argv.index("-processor") + 1] == ghidra_ref.PROCESSOR


def test_externs_widest_then_called():
    """The widest typing wins; on a tie, the one called through."""
    assert ghidra_ref.externs(DUMP["functions"]) == [
        "extern undefined2 DAT_3000;",
        "extern code *_IRQ;",
    ]


def test_combined_orders_and_drops_failed_bodies():
    """Header, externs, prototypes, bodies; a failed function keeps only its prototype."""
    c = ghidra_ref.combined(DUMP)
    lines = c.splitlines()
    assert lines[0] == f'#include "{ghidra_ref.HEADER}"'
    assert lines.index("extern undefined2 DAT_3000;") < lines.index(
        "void FUN_1080(void);"
    )
    assert lines.index("void FUN_1080(void);") < lines.index("void FUN_1000(void)")
    assert "void FUN_1040(void)" in lines and "void FUN_1080(void)" not in lines


def test_index_and_write(tmp_path):
    """Index seeds by entry (lifter-only and unseeded too), per-function files, report."""
    seeded = {0x1000: "psid", 0x1040: "lifter", 0x2000: "lifter"}
    js = ghidra_ref.write("t", DUMP, seeded, tmp_path)
    assert json.loads((tmp_path / "t.json").read_text(encoding="utf-8")) == js
    assert js["unseeded"] == [0x2000]
    fs = js["functions"]
    assert [fs[n]["seed"] for n in ("FUN_1000", "FUN_1040", "FUN_1080")] == [
        "psid",
        "lifter",
        "ghidra",
    ]
    assert fs["FUN_1040"]["calls"] == [0x1000]
    assert not fs["FUN_1080"]["ok"] and fs["FUN_1080"]["error"]
    assert sorted(p.name for p in (tmp_path / "t").iterdir()) == ["1000.c", "1040.c"]
    assert (tmp_path / "t.c").read_text(encoding="utf-8") == ghidra_ref.combined(DUMP)
    assert ghidra_ref.counts(js) == (3, 2, 1)
    lines = ghidra_ref.report("t", js)
    assert lines[0] == "t: 3 functions, 2 decompiled, 1 failed"
    assert "FUN_1080 @1080" in lines[1] and lines[2] == "  seed 2000: no function"


@pytest.mark.skipif(not shutil.which(CLANG), reason="needs clang")
def test_header_parses_ghidra_c(tmp_path):
    """ghidra.h declares what Ghidra's C and the token rewrites use."""
    ghidra_ref.write_header(tmp_path)
    src = tmp_path / "x.c"
    src.write_text(
        '#include "ghidra.h"\nextern code *_IRQ;\nextern undefined2 DAT_00fa;\n'
        "byte *p;\nvoid f(byte a) {\n  (*_IRQ)(a);\n  p = CONCAT11(a, a);\n"
        "  (*(undefined1 *)((undefined1 *)&DAT_00fa + 1)) = SUB21(DAT_00fa, 0);\n"
        "  if (CARRY1(a, a) || SBORROW2(DAT_00fa, 1)) halt_baddata();\n}\n",
        encoding="utf-8",
    )
    r = subprocess.run(
        [CLANG, "-fsyntax-only", "-Werror=implicit-function-declaration", str(src)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    assert ghidra_types.bits(3) == 32 and ghidra_types.bits(8) == 64


def test_ensure_image_builds_when_missing(monkeypatch):
    """A missing image is built with the version as build arg."""
    calls = []

    def run(argv, **_):
        calls.append(argv)
        return SimpleNamespace(returncode=int(argv[1] == "image"))

    monkeypatch.setattr(ghidra_ref.subprocess, "run", run)
    assert ghidra_ref.ensure_image("gi-ghidra:1.2.3") == "gi-ghidra:1.2.3"
    assert calls[1][:2] == ["docker", "build"]
    assert "DEITY_INFORMANT=1.2.3" in calls[1]


def test_run_ghidra_failure_cleans_up(monkeypatch, tmp_path):
    """A failed headless run raises with its log and leaves no work dir."""
    monkeypatch.setattr(
        ghidra_ref.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="boom"),
    )
    with pytest.raises(RuntimeError, match="boom"):
        ghidra_ref.run_ghidra(target(FUZZ)[0], {0x1000: "psid"}, tmp_path / "w", "x")
    assert not (tmp_path / "w").exists()


def test_main_reports(monkeypatch, capsys):
    """main prints each target's counts."""
    js = ghidra_ref.index(DUMP, {0x1000: "psid"})
    monkeypatch.setattr(
        ghidra_ref, "generate_all", lambda names, *a: {n: js for n in names}
    )
    assert ghidra_ref.main(["a"]) == 0
    assert capsys.readouterr().out.startswith("a: 3 functions, 2 decompiled, 1 failed")


def _image():
    if not shutil.which("docker"):
        return None
    tag = ghidra_ref.image_tag()
    have = subprocess.run(
        ["docker", "image", "inspect", tag], capture_output=True, check=False
    )
    return None if have.returncode else tag


@pytest.mark.slow
@pytest.mark.skipif(_image() is None, reason="needs docker and the gi-ghidra image")
def test_pipeline_fuzz_target():
    """The real pipeline on one fuzz target: seeds, callees, files, and the C parses."""
    base = ghidra_ref.ROOT / "out"
    base.mkdir(exist_ok=True)
    out = tempfile.mkdtemp(prefix="test-ghidra-ref-", dir=base)
    try:
        js = ghidra_ref.generate_all([FUZZ], out, 1)[FUZZ]
        _, init, play, _ = psid_parts(target(FUZZ)[0])
        fs = js["functions"].values()
        by_entry = {f["entry"]: f for f in fs}
        assert by_entry[init]["seed"] == by_entry[play]["seed"] == "psid"
        assert all(f["ok"] for f in fs) and not js["unseeded"]
        callees = {c for f in fs for c in f["calls"]}
        assert callees and callees <= set(by_entry)
        assert {f["seed"] for f in fs} == {"psid", "lifter"}
        c = os.path.join(out, f"{FUZZ}.c")
        for f in fs:
            one = pathlib.Path(out, FUZZ, f"{f['entry']:04x}.c").read_text(
                encoding="utf-8"
            )
            assert one.strip() in pathlib.Path(c).read_text(encoding="utf-8")
        if shutil.which(CLANG):
            r = subprocess.run(
                [CLANG, "-fsyntax-only", "-I", out, c],
                capture_output=True,
                text=True,
                check=False,
            )
            assert r.returncode == 0, r.stderr
    finally:
        shutil.rmtree(out, ignore_errors=True)


GHIDRA_C = """#include "ghidra.h"
byte DAT_3000;
void FUN_1040(void);
void FUN_1000(void) { if (DAT_3000) { goto out; } FUN_1040(); out:; }
void FUN_1040(void) { DAT_3000 = 1; }
void FUN_2000(void) { DAT_3000 = 2; }
"""
OURS_C = """#include <stdint.h>
uint8_t g;
void tick(void);
void helper(void) { if (g) { tick(); } }
static void f1_x1(void) { if (g) { g = 1; tick(); } }
void f1(void) { f1_x1(); g = 1; tick(); g = 2; g = 1; tick(); }
void f2(void) { if (g) { if (g > 1) { tick(); } } }
"""
INDEX = {
    "functions": {
        "FUN_1000": {"entry": 0x1000, "calls": [0x1040], "pcs": [0x1000, 0x1002]},
        "FUN_1040": {"entry": 0x1040, "calls": [], "pcs": [0x1040]},
        "FUN_2000": {"entry": 0x2000, "calls": [0x1000], "pcs": [0x2000]},
    }
}
PROV = {
    "f1": {"entry": 0x1000, "pcs": [0x1000, 0x1040]},
    "f2": {"entry": 0x2000, "pcs": [0x2000, 0x2003]},
}


def test_owner_takes_the_longest_base():
    """A reducer piece belongs to the longest lifter name it extends by ``_``."""
    names = {"f1": 0, "f12": 0, "cgen_play": 0}
    assert ghidra_ref.owner("f12_1_x2", names) == "f12"
    assert ghidra_ref.owner("f1", names) == "f1"
    assert ghidra_ref.owner("cgen_play_x1", names) == "cgen_play"
    assert ghidra_ref.owner("f1x", names) is None


def test_units_follow_calls_to_inlined_callees():
    """A unit takes callees at no lifter entry, and stops at one."""
    got = ghidra_ref.units(INDEX, {0x1000, 0x2000, 0x3000})
    assert got == {0x1000: {"FUN_1000", "FUN_1040"}, 0x2000: {"FUN_2000"}}


def test_compare_counts_and_better(tmp_path):
    """Totals per class, per-entry counts over each side's functions, ``better`` only where
    Ghidra holds every instruction ours translates."""
    ghidra_ref.write_header(tmp_path)
    (tmp_path / "g.c").write_text(GHIDRA_C, encoding="utf-8")
    (tmp_path / "o.c").write_text(OURS_C, encoding="utf-8")
    ghidra = ghidra_ref.census(tmp_path / "g.c", (tmp_path,))
    ours = ghidra_ref.census(tmp_path / "o.c", ())
    got = ghidra_ref.compare(INDEX, ghidra, {"ours": (ours, PROV)})
    assert got["total"]["ghidra"]["goto"] == 1
    assert got["total"]["ours"]["duplicate-code"] == 1
    one = got["entries"][0x1000]
    assert one["ghidra"] == ["FUN_1000", "FUN_1040"]
    assert one["ours"] == ["f1", "f1_x1"]
    assert one["missing"] == {"ours": 0}
    assert got["entries"][0x2000]["missing"] == {"ours": 1}
    assert got["unmatched"] == {"ours": ["helper"]}
    better = {(e, c) for _, e, c, _, _ in got["better"]}
    assert (0x1000, "duplicate-code") in better
    assert (0x1000, "goto") not in better
    assert all(e == 0x1000 for e, _ in better)
    lines = ghidra_ref.compare_lines("t", got)
    assert lines[0] == "t: ghidra | ours"
    assert any("lacks 1 instructions" in line for line in lines)
    assert any("better than ours @1000" in line for line in lines)


def test_compare_target_reads_dirs(tmp_path, monkeypatch):
    """A target's comparison reads Ghidra's outputs and each labelled directory."""
    ghidra_ref.write_header(tmp_path)
    (tmp_path / "t.c").write_text(GHIDRA_C, encoding="utf-8")
    (tmp_path / "t.json").write_text(json.dumps(INDEX), encoding="utf-8")
    mine = tmp_path / "mine" / "t"
    mine.mkdir(parents=True)
    (mine / "t.c").write_text(OURS_C, encoding="utf-8")
    (mine / "t.provenance.json").write_text(json.dumps(PROV), encoding="utf-8")
    got = ghidra_ref.compare_target(
        "t", {"m": str(tmp_path / "mine" / "{target}")}, tmp_path
    )
    assert got["entries"][0x1000]["m"] == ["f1", "f1_x1"]
    monkeypatch.setattr(ghidra_ref, "ours_c", lambda name, out: mine)
    assert ghidra_ref.compare_target("t", {}, tmp_path)["unmatched"] == {
        "ours": ["helper"]
    }


def test_main_compare(monkeypatch, tmp_path, capsys):
    """``--compare`` writes compare.json and prints each target's lines."""
    got = {
        "total": {"ghidra": {"goto": 1}},
        "entries": {},
        "better": [],
        "unmatched": {},
    }
    monkeypatch.setattr(
        ghidra_ref, "compare_all", lambda names, ours, out, jobs: {"t": got}
    )
    assert (
        ghidra_ref.main(["--compare", "--out", str(tmp_path), "t", "--ours", "a=b"])
        == 0
    )
    assert json.loads((tmp_path / "compare.json").read_text(encoding="utf-8")) == {
        "t": got
    }
    assert "goto: 1" in capsys.readouterr().out


def test_ours_c_writes_provenance(tmp_path):
    """The lifter's C of a target comes with its provenance."""
    out = ghidra_ref.ours_c(FUZZ, tmp_path)
    prov = json.loads((out / f"{FUZZ}.provenance.json").read_text(encoding="utf-8"))
    assert {"entry", "pcs"} <= set(prov["cgen_play"])
    assert (out / f"{FUZZ}.c").read_text(encoding="utf-8").count("cgen_play")
