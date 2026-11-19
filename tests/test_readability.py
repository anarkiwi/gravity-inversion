"""Tests for tools/readability.py: the generated C the readability census ratchets."""

import tomllib

from wayward_eagle.census import adapter

import censusdiff
import readability
from constructs import ROOT


def test_generate_writes_c_and_header(tmp_path):
    """A fuzz player's translation is written as <name>.c including <name>.h."""
    path = readability.generate("fuzz-loop-0", tmp_path)
    assert path == tmp_path / "fuzz-loop-0.c"
    assert '#include "fuzz-loop-0.h"' in path.read_text(encoding="utf-8")
    assert (tmp_path / "fuzz-loop-0.h").is_file()


def test_ratchet_config_counts_the_generated_c(tmp_path):
    """The ratchet's adapter options give each generated file's classes and complexity."""
    readability.main(["fuzz-loop-0", "fuzz-jsr3-0", "--out", str(tmp_path), "-j", "2"])
    cfg = tomllib.loads((ROOT / "virtuous-cycles.toml").read_text(encoding="utf-8"))
    (metric,) = cfg["metric"]
    assert metric["adapter"] == "wayward-census"
    options = dict(metric["options"], files=[str(tmp_path / "*.c")])
    options["include"] = [str(tmp_path), str(ROOT / "c64")]
    got = adapter(options)
    assert got["fuzz-loop-0:goto"] == 0
    assert got["fuzz-jsr3-0:cognitive-complexity"] > 0


def test_censusdiff_lists_the_functions_that_differ(tmp_path, capsys):
    """Two directories of one player's C, one with a function given an extra local: only
    that function's locals and lines differ."""
    readability.main(["fuzz-loop-0", "--out", str(tmp_path / "a"), "-j", "1"])
    (tmp_path / "b").mkdir()
    for f in (tmp_path / "a").iterdir():
        (tmp_path / "b" / f.name).write_bytes(f.read_bytes())
    path = tmp_path / "b" / "fuzz-loop-0.c"
    text = path.read_text(encoding="utf-8")
    at = text.index("void cgen_play(uint8_t a) {") + len("void cgen_play(uint8_t a) {")
    path.write_text(
        text[:at] + "\n  volatile uint8_t extra = 0;\n  (void)extra;" + text[at:]
    )
    censusdiff.main([str(tmp_path / "a"), str(tmp_path / "b"), "fuzz-loop-0"])
    rows = [line.split() for line in capsys.readouterr().out.splitlines()]
    assert {(r[1], r[2]) for r in rows} >= {("cgen_play", "locals")}
    assert {r[1] for r in rows} == {"cgen_play"}
    old, new = censusdiff.report(tmp_path / "a" / "fuzz-loop-0.c"), censusdiff.report(
        path
    )
    assert not censusdiff.changes(old, old)
    [row] = [r for r in censusdiff.changes(old, new) if r[1] == "locals"]
    assert row[3] == row[2] + 1
