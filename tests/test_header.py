"""Tests for tools/header.py: each tune's data generator CLI."""

import pytest

import constructs
import footprint

TUNES = sorted(p.parent.name for p in (constructs.ROOT / "tunes").glob("*/gen_*.py"))


@pytest.mark.parametrize("tune", TUNES)
def test_generator_cli(tune, tmp_path):
    """The CLI writes the header generated from the tune's PSID, with the build's ACME labels
    where it assembles them."""
    psid = constructs.ROOT / constructs.load(tune)["psid"]
    labels = psid.with_suffix(".labels")
    out = tmp_path / "t.h"
    gen = footprint.generator(tune)
    gen.main([str(p) for p in (psid, labels) if p.exists()] + [str(out)])
    text = gen.generate(psid.read_bytes(), *footprint.labels(psid))
    assert out.read_text(encoding="ascii") == text
