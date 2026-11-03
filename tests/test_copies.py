"""Tests for tools/copies.py on a synthetic two-voice player."""

import copies

PLAYER = [
    *(0xA2, 0x00),  # 1000 LDX #$00
    *(0x20, 0x07, 0x10),  # 1002 JSR $1007
    *(0xA2, 0x07),  # 1005 LDX #$07, falls into $1007
    *(0xA5, 0x20, 0x8D, 0x00, 0xD4, 0xA5, 0x21, 0x8D, 0x01, 0xD4),  # 1007 voice 0
    *(0xA5, 0x22, 0x8D, 0x07, 0xD4, 0xA5, 0x23, 0x8D, 0x08, 0xD4),  # 1011 voice 1
    0x60,  # 101B RTS
]


def _psid(code):
    fields = b"PSID\0\2\0\x7c\x10\0\x10\0\x10\0\0\1\0\1"
    return fields + bytes(0x7C - len(fields)) + bytes(code)


def test_copies_and_fall_through(tmp_path, capsys):
    """Voice copies report their per-field strides; the last voice falls into the JSR target."""
    sid = tmp_path / "p.sid"
    sid.write_bytes(_psid(PLAYER))
    copies.main([str(sid), "--frames", "2", "--min-len", "4"])
    out = capsys.readouterr().out.splitlines()
    assert "copy $1007 -> $1011: 4 insns; deltas +$2x2, +$7x2" in out
    assert "jsr $1007: 1 calls ['$1002']; fall-through from $1005" in out


def test_instructions_skip_data():
    """Data lines are not instructions; operands parse as numbers or labels."""
    src = "1000  4C 05 10  L1000: JMP L1005\n1003                   .byte $00,$01\n"
    insns = copies.instructions(src)
    assert insns == [(0x1000, 3, "JMP", "L1005")]
    assert copies.operand(insns[0]) == 0x1005 and copies.shape(insns[0]) == (
        "JMP",
        3,
        "n",
    )
