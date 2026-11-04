"""tools/opclass.py: opcode roles from P-code agree with jennings' mnemonic tables and the ISA."""

import functools

from jennings.isa import RMW_MNEMONICS, STORE_MNEMONICS
from jennings.opcodes import MEM_MODES, OPCODES

import opclass


@functools.cache
def table():
    """All 256 opcodes' roles."""
    return opclass.table()


def _with(fact):
    return {
        o for o, r in table().items() if any(f.startswith(fact) for f in r["facts"])
    }


def test_memory_writers_are_stores_and_rmw():
    """Code-cell writers are exactly jennings' stores and read-modify-writes in memory modes."""
    expect = {
        o
        for o, (name, mode) in OPCODES.items()
        if mode in MEM_MODES and name in STORE_MNEMONICS | RMW_MNEMONICS
    }
    assert _with("T1 code-cell writer") == expect


def test_control():
    """Calls, returns, computed transfers, branches and traps are the ISA's."""
    assert _with("T2 call") == {0x00, 0x20}
    assert _with("T2 return") == {0x40, 0x60}
    assert _with("T1 computed transfer") == {0x00, 0x40, 0x60, 0x6C}
    assert _with("T5 condition") == {0x10, 0x30, 0x50, 0x70, 0x90, 0xB0, 0xD0, 0xF0}
    assert _with("T4 back edge") == _with("T5 condition") | {0x4C}
    assert len(_with("trap")) == 12 and all(
        OPCODES[o][0] == "JAM" for o in _with("trap")
    )


def test_stack():
    """Pushes, pulls, stack pointer writes and reads."""
    assert _with("T1 pushed data") == {0x08, 0x48}
    assert _with("T2 pulled data") == {0x28, 0x68}
    assert _with("T2 stack switch") == {0x9A, 0x9B, 0xBB}
    assert _with("T2 stack read") == {0x9B, 0xBA, 0xBB}


def test_flags():
    """Branch conditions name their flag; decimal mode affects the BCD arithmetic only."""
    cond = {o: table()[o]["facts"][-1][-1] for o in _with("T5 condition")}
    assert cond == {
        0x10: "N",
        0x30: "N",
        0x50: "V",
        0x70: "V",
        0x90: "C",
        0xB0: "C",
    } | {
        0xD0: "Z",
        0xF0: "Z",
    }
    names = {table()[o]["mnemonic"] for o in _with("decimal")}
    assert names == {"ADC", "SBC", "RRA", "ISC", "ARR"}


def test_skip():
    """Skip instructions change no register or memory: every NOP with an operand, BIT, CMP."""
    names = {table()[o]["mnemonic"] for o in _with("skip")}
    assert names == {"NOP", "BIT", "CMP", "CPX", "CPY"}
    assert {o for o in _with("skip") if OPCODES[o][0] == "NOP"} == {
        o for o, (n, m) in OPCODES.items() if n == "NOP" and m != "impl"
    }


def test_main(capsys):
    """The CLI prints every opcode."""
    opclass.main([])
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 256 and out[0].startswith("$00 BRK impl:")
