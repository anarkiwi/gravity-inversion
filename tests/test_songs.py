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
def test_song_count_c_identical(tune, songs):
    """The .c is the original's for another song count; the .h holds the count."""
    c = transliterate(_psid(tune), "gen.h")[0]
    other = _translated(tune, songs)
    assert other[0] == c and "CGEN_SONGS" in c
    assert int(re.search(r"^#define CGEN_SONGS (\w+)$", other[1], re.M)[1], 0) == songs
