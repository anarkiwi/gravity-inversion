"""Checks the tunes' tests share: generated header values, traps of the hand C, its data
footprint and the generated C of patched songs."""

import functools
import re

import pytest

import footprint
import sidtrace
import transliterate

#: sidtrace's error for a PRG that jams at the trap whose code matches the format field.
TRAP = r"jam at \$[0-9a-f]+ \(trap {}\)"


def define(header, name):
    """The value of macro `name` in generated header text."""
    return re.search(rf"#define {name} (.*)", header)[1]


def array(header, name):
    """The bytes of initializer-list macro `name` in generated header text."""
    return bytes(int(b, 16) for b in define(header, name).split(","))


def assert_traps(code, prg, *args, **kwargs):
    """`sidtrace.trace_prg(prg, *args, **kwargs)` jams at a trap whose code matches the
    regular expression `code`."""
    with pytest.raises(AssertionError, match=TRAP.format(code)):
        sidtrace.trace_prg(prg, *args, **kwargs)


def song_runs(psid, calls, variants, variant_calls):
    """`footprint.Original` runs of song 0: `psid` for `calls` play calls, then each PSID of
    `variants` for `variant_calls`."""
    return [(psid, [calls], [0])] + [(v, [variant_calls], [0]) for v in variants]


def assert_clean(gen, runs, *labels, jobs=1):
    """The hand C of data generator `gen` holds only the original's data over `runs`
    (`footprint.Original`), generated from the first run's PSID with `labels`."""
    psid = runs[0][0]
    held, named = gen.held(psid, *labels), gen.named(psid, *labels)
    orig = footprint.Original(runs, jobs)
    assert footprint.check(orig, held, named) == footprint.CLEAN


@functools.cache
def _c(psid):
    return transliterate.transliterate(psid, "gen.h")[0]


def assert_same_c(psid, variant):
    """The patched song `variant` of `psid` generates `psid`'s .c: song data is only in the
    .h."""
    assert transliterate.transliterate(variant, "gen.h")[0] == _c(psid)
