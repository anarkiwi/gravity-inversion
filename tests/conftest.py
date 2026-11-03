"""Fixtures for the shared tools: the assembled Commando reference as a sample PSID."""

import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def sample_psid():
    """A real PSID built by `make` (tunes/commando)."""
    return (ROOT / "tunes" / "commando" / "build" / "ref.sid").read_bytes()
