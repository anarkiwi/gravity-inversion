"""Fixtures: the reference build produced by `make`."""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from support import BUILD  # pylint: disable=C0413
from sidtrace import parse_labels  # pylint: disable=C0413


@pytest.fixture(scope="session", name="ref_psid")
def fixture_ref_psid():
    """Assembled original PSID."""
    return (BUILD / "ref.sid").read_bytes()


@pytest.fixture(scope="session", name="labels")
def fixture_labels():
    """Labels of the assembled original."""
    return parse_labels((BUILD / "ref.labels").read_text(encoding="ascii"))
