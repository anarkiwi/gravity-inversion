"""Tests for tools/readability.py: the generated C the readability census ratchets."""

import tomllib

from wayward_eagle.census import adapter

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
