"""Tests for tools/footprint.py: the per-byte check of held data against coverage."""

import numpy as np
from jennings.coverage import EXEC, READ, WRITE

import footprint
import sidtrace


def _original(code=(), image=(), flags=None):
    """An Original from masks: `code` and `image` address spans, `flags` {address: flags}."""
    orig = footprint.Original.__new__(footprint.Original)
    orig.code, orig.image = footprint.mask(code), footprint.mask(image)
    orig.flags = np.zeros(0x10000, np.uint8)
    for a, f in (flags or {}).items():
        orig.flags[a] = f
    orig.used = (orig.flags & (READ | WRITE)) != 0
    orig.used &= ~footprint.mask([footprint.IO, footprint.STACK])
    return orig


def test_runs_and_fmt():
    """Runs are half-open and print inclusive."""
    m = footprint.mask([(0x10, 0x12), (0x20, 0x21)])
    assert footprint.runs(m) == [(0x10, 0x12), (0x20, 0x21)]
    assert footprint.fmt(footprint.runs(m)) == "$0010-$0011 $0020-$0020"
    assert footprint.fmt([]) == "none"


def test_check():
    """Instruction bytes held but not used as data and RAM held that nothing writes are
    extra; a byte read and neither held, named, an operand nor zero page is missing."""
    flags = {
        0x1000: EXEC,
        0x1001: READ,
        0x1002: EXEC | WRITE,
        0x2000: READ,
        0x3000: WRITE,
        0x0080: READ,
        0xD400: WRITE,
    }
    orig = _original([(0x1000, 0x1003)], [(0x1000, 0x2001)], flags)
    clean = {"code_read": (0x1001,), "data": range(0x1003, 0x2001), "ram": (0x3000,)}
    assert footprint.check(orig, clean, {}) == footprint.CLEAN
    got = footprint.check(orig, clean | {"code": (0x1000,), "free": (0x4000,)}, {})
    assert got["extra"] == [(0x1000, 0x1001), (0x4000, 0x4001)]
    held = {"data": range(0x1003, 0x2000)}
    assert footprint.check(orig, held, {})["missing"] == [
        (0x1001, 0x1002),
        (0x2000, 0x2001),
    ]
    named = {"table": (0x1001, 0x2000)}
    assert footprint.check(orig, held, named) == footprint.CLEAN
    overlap = [(0x1001, 0x1002), (0x2000, 0x2001)]
    assert footprint.check(orig, clean, named)["overlap"] == overlap


def test_original(sample_psid):
    """A run's code mask holds init's instructions; I/O writes are not data."""
    orig = footprint.Original([(sample_psid, [5], [0])], 1)
    load, init, _, image = sidtrace.psid_parts(sample_psid)
    assert orig.code[init] and orig.image[load] and orig.image[load + len(image) - 1]
    assert not orig.used[0xD418] and orig.used.any()
    assert not (orig.footprint() & orig.code & ~orig.used).any()


def test_maps():
    """A tune's generator gives its held arrays and named addresses, disjoint."""
    path, runs = footprint.default_runs("ghouls")
    assert len(runs[0][2]) == 32 and path.name == "Ghouls_n_Ghosts.sid"
    held, named = footprint.maps("ghouls", path)
    assert not (footprint.address_mask(held) & footprint.address_mask(named)).any()
    held, _ = footprint.maps("commando", footprint.default_runs("commando")[0])
    assert set(held) == {"song", "v", "sid"}


def test_main(capsys):
    """The CLI reports each tune's held bytes and no extra or missing ones."""
    assert footprint.main(["commando", "--jobs", "1"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("commando: held ") and "extra none; missing none" in out
