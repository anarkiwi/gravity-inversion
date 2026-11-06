"""The .c of every tune is the same whatever song count its PSID header declares: the lift
takes any song, and init checks the song against the .h's CGEN_SONG0 and CGEN_SONGS."""

import functools
import re

import pytest

from constructs import ROOT, load
from transliterate import transliterate

TUNES = ("commando", "incoherent", "automatas", "ghouls", "emomyst")


@functools.cache
def _psid(tune):
    return (ROOT / load(tune)["psid"]).read_bytes()


@functools.cache
def _translated(tune, songs):
    """(.c, .h) of the tune with its header declaring `songs` songs."""
    data = bytearray(_psid(tune))
    data[0x0E:0x10] = songs.to_bytes(2, "big")
    return transliterate(bytes(data), "gen.h")[:2]


@pytest.mark.slow
@pytest.mark.parametrize("songs", [1, 3])
@pytest.mark.parametrize("tune", TUNES)
def test_song_count_c_identical(tune, songs):
    """The .c is the original's for another song count; the .h holds the count."""
    c = transliterate(_psid(tune), "gen.h")[0]
    other = _translated(tune, songs)
    assert other[0] == c and "CGEN_SONGS" in c
    assert re.search(rf"#define CGEN_SONGS 0x{songs:02X}\b", other[1])
