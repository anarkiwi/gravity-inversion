"""Tests for tools/procedures.py: how one instruction enters another."""

import numpy as np

from procedures import enters


def _mem(at, code):
    mem = np.zeros(0x10000, np.uint8)
    mem[at : at + len(code)] = code
    return mem


def test_fall_through_and_jumps():
    """Plain instructions fall into the next one; JMP, branches and JMP (ind) arms enter
    their targets."""
    assert enters(_mem(0x1000, [0xA9, 0x01]), {}, 0x1000, 0x1002)
    assert enters(_mem(0x1000, [0x4C, 0x34, 0x12]), {}, 0x1000, 0x1234)
    assert not enters(_mem(0x1000, [0x4C, 0x34, 0x12]), {}, 0x1000, 0x1003)
    branch = _mem(0x1000, [0xD0, 0xFE])
    assert enters(branch, {}, 0x1000, 0x1000) and enters(branch, {}, 0x1000, 0x1002)
    ind = _mem(0x1000, [0x6C, 0x00, 0x20])
    assert enters(ind, {0x1000: [0x3000]}, 0x1000, 0x3000)
    assert not enters(ind, {0x1000: [0x3000]}, 0x1000, 0x2000)


def test_exits_do_not_fall():
    """RTS, RTI, BRK and a JAM never fall into the byte after them."""
    for op in (0x60, 0x40, 0x00, 0x02, 0xF2):
        assert not enters(_mem(0x1000, [op]), {}, 0x1000, 0x1001)
