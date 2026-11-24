"""The .c of every tune is the same whatever song count its PSID header declares: init runs
per song, its state goes in the .h, and the .c checks the song against CGEN_SONG0/CGEN_SONGS.
"""

import functools
import re

import pytest
from pysidtracker.header import parse_sid_header

from constructs import ROOT, load
from transliterate import transliterate

TUNES = ("commando", "incoherent", "automatas", "ghouls", "emomyst")


@functools.cache
def _psid(tune):
    return (ROOT / load(tune)["psid"]).read_bytes()


@functools.cache
def _translated(tune, songs):
    """(.c, .h) of the tune with its header declaring `songs` songs."""
    data = _psid(tune)
    head = parse_sid_header(data)
    data = head.replace(songs=songs).to_bytes() + data[head.data_offset :]
    return transliterate(data, "gen.h")[:2]


@pytest.mark.slow
@pytest.mark.parametrize("songs", [1, 3])
@pytest.mark.parametrize("tune", TUNES)
def test_song_count_in_header(tune, songs):
    """The .h holds the song count the .c's init checks its song against."""
    c, h = _translated(tune, songs)
    assert "CGEN_SONGS" in c
    assert int(re.search(r"^#define CGEN_SONGS (\w+)$", h, re.M)[1], 0) == songs
