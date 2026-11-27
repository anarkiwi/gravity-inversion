"""Tests for tools/prgsize.py: each generated tune's PRG bytes, the `prg` metric."""

import json
import tomllib

import pytest
from virtuous_cycles.adapters import get_adapter

import prgsize
from constructs import ROOT


def tune(tunes, name, gen, size=None):
    """A tune directory under `tunes`, its Makefile setting GEN when `gen`, with a
    build/gen.prg of `size` bytes when given."""
    d = tunes / name
    (d / "build").mkdir(parents=True)
    body = "GEN := 1\n" if gen else ""
    (d / "Makefile").write_text(f"FIXTURE := x\n{body}include ../../common.mk\n")
    if size is not None:
        (d / "build" / "gen.prg").write_bytes(bytes(size))


def test_sizes_of_generated_tunes_only(tmp_path):
    """Only tunes setting GEN are measured, each by its gen.prg's bytes."""
    tune(tmp_path, "b", True, 7)
    tune(tmp_path, "a", True, 3)
    tune(tmp_path, "hand", False, 99)
    assert prgsize.sizes(tmp_path) == {"a": 3, "b": 7}


def test_unbuilt_tune_fails(tmp_path, capsys):
    """A generated tune with no PRG is an error, not a key dropped from the ratchet."""
    tune(tmp_path, "a", True, 3)
    tune(tmp_path, "b", True)
    with pytest.raises(FileNotFoundError):
        prgsize.sizes(tmp_path)
    assert prgsize.main(["--tunes", str(tmp_path)]) == 1
    assert "gen.prg" in capsys.readouterr().err


def test_main_prints_json(tmp_path, capsys):
    """The CLI prints {tune: bytes}, the JSON the command adapter reads."""
    tune(tmp_path, "a", True, 5)
    assert prgsize.main(["--tunes", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out) == {"a": 5}


def test_ratchet_config_runs_the_tool(tmp_path):
    """The `prg` metric's adapter reads the tool's output as keys per tune."""
    tune(tmp_path, "a", True, 11)
    cfg = tomllib.loads((ROOT / "virtuous-cycles.toml").read_text(encoding="utf-8"))
    metric = next(m for m in cfg["metric"] if m["name"] == "prg")
    run = f"{metric['options']['run']} --tunes {tmp_path}"
    got = get_adapter(metric["adapter"])(dict(metric["options"], run=run, cwd=ROOT))
    assert got == {"a": 11.0}
