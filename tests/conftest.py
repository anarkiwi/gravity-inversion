"""Shared fixtures: the reference build produced by `make`."""

import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
sys.path.insert(0, str(ROOT / "tools"))

from gen_data import generate, parse_labels  # pylint: disable=C0413


@pytest.fixture(scope="session", name="ref_psid")
def fixture_ref_psid():
    """Assembled original PSID."""
    return (BUILD / "ref.sid").read_bytes()


@pytest.fixture(scope="session", name="labels")
def fixture_labels():
    """Labels of the assembled original."""
    return parse_labels((BUILD / "ref.labels").read_text(encoding="ascii"))


@pytest.fixture(scope="session")
def ref_prg():
    """C translation built by `make`."""
    return (BUILD / "commando.prg").read_bytes()


def build_prg(psid, labels, out_dir):
    """Compile the C player against data generated from `psid`; return the PRG."""
    out_dir = pathlib.Path(out_dir)
    (out_dir / "commando_data.h").write_text(generate(psid, labels), encoding="ascii")
    prg = out_dir / "commando.prg"
    subprocess.run(
        [os.environ.get("CC", "mos-c64-clang"), "-Os", f"-I{out_dir}", "-o", prg]
        + [str(ROOT / "src" / f) for f in ("main.c", "commando.c")],
        check=True,
    )
    return prg.read_bytes()
