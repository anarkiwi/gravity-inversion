"""Fixtures: the original PSID and the build produced by `make`."""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from incoherent_support import SID  # pylint: disable=C0413


@pytest.fixture(scope="session", name="psid")
def fixture_psid():
    """The original PSID."""
    return SID.read_bytes()
