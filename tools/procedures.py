"""Procedure boundaries of the original by its control edges: which functions of a lift are
shared tails its procedures end in (docs/residue.md, `stray-function`)."""

from jennings.coverage.cfg import LEN
from jennings.opcodes import OPCODES

JMP, JMP_IND, RTS = 0x4C, 0x6C, 0x60
BRANCHES = frozenset(op for op, (_, mode) in OPCODES.items() if mode == "rel")
#: Opcodes control never leaves by falling through: JMP, JMP (ind), RTS, RTI, BRK.
NO_FALL = frozenset((JMP, JMP_IND, RTS, 0x40, 0x00))


def enters(mem, jumps, s, e):
    """Whether the instruction at `s` enters `e` by a JMP, a computed JMP's arm (`jumps`), a
    branch or falling through."""
    op = int(mem[s])
    if op == JMP:
        return int(mem[s + 1]) | int(mem[s + 2]) << 8 == e
    if op == JMP_IND:
        return e in jumps.get(s, ())
    if op in BRANCHES:
        return e in (s + 2, (s + 2 + (int(mem[s + 1]) ^ 0x80) - 0x80) & 0xFFFF)
    return op not in NO_FALL and s + int(LEN[op]) == e


def shared_tails(mem, info, procs):
    """`procs` and the entries of the lift's functions that are shared tails of the original:
    code outside one enters it without a JSR, and it returns by an RTS of its own or ends by
    entering a procedure or another shared tail."""
    jumps = {int(k): v for k, v in info["jumps"].items()}
    fns = [f for f in info["provenance"]["functions"].values() if not f["index"]]
    entered = [
        f
        for f in fns
        if any(
            enters(mem, jumps, s, f["entry"])
            for s in info["starts"]
            if s not in f["pcs"]
        )
    ]
    out = set(procs) | {
        f["entry"] for f in entered if any(int(mem[p]) == RTS for p in f["pcs"])
    }
    grew = True
    while grew:
        grew = False
        for f in (f for f in entered if f["entry"] not in out):
            if any(enters(mem, jumps, p, e) for p in f["pcs"] for e in out):
                out.add(f["entry"])
                grew = True
    return out
