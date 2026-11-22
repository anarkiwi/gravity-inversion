"""Fixtures: the reference build produced by `make`."""

import pathlib
import sys

import pytest
from jennings.acme import read_symbol_list

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from support import BUILD  # pylint: disable=C0413


@pytest.fixture(scope="session", name="ref_psid")
def fixture_ref_psid():
    """Assembled original PSID."""
    return (BUILD / "ref.sid").read_bytes()


@pytest.fixture(scope="session", name="labels")
def fixture_labels():
    """Labels of the assembled original."""
    return read_symbol_list((BUILD / "ref.labels").read_text(encoding="ascii"))
