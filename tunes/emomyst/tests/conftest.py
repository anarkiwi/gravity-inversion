"""Fixtures: the original PSID, this tune's modules and the C build from `make`."""

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import emomyst_swp  # pylint: disable=C0413
import gen_emomyst  # pylint: disable=C0413
import sidtrace  # pylint: disable=C0413

PSID = ROOT.parent.parent / "fixtures" / "Emomyst.sid"


@pytest.fixture(scope="session")
def gen():
    """gen_emomyst.py."""
    return gen_emomyst


@pytest.fixture(scope="session")
def swp():
    """emomyst_swp.py."""
    return emomyst_swp


@pytest.fixture(scope="session")
def psid_path():
    """Path of the original PSID, in the HVSC root (DOCUMENTS/Songlengths.md5)."""
    return PSID


@pytest.fixture(scope="session")
def frames():
    """Play calls over the tune's HVSC length."""
    return sidtrace.song_calls(PSID.read_bytes(), 0, PSID.parent)


@pytest.fixture(scope="session")
def psid():
    """The original PSID."""
    return PSID.read_bytes()


@pytest.fixture(scope="session")
def prg():
    """The C build of the original's data."""
    return ROOT / "build" / "emomyst.prg"
