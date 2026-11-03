"""Fixtures: the original PSID and the C build produced by `make`."""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ghouls_support import PSID  # pylint: disable=C0413


@pytest.fixture(scope="session", name="psid")
def fixture_psid():
    """Original PSID."""
    return PSID.read_bytes()
