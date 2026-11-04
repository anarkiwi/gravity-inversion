"""Roles of every 6502/6510 opcode in the lift procedure, derived from deity-informant's P-code.

Each opcode is lifted alone and its P-code read for memory and stack accesses, registers and flags
written and read, and control; `facts` maps those roles to the T1-T7 steps (docs/constructs.md)
whose facts the opcode can produce."""

import argparse

from deity_informant import lift
from jennings.opcodes import ILLEGAL_OPCODES, MODE_LEN, OPCODES

REGS = {
    0: "A",
    1: "X",
    2: "Y",
    3: "SP",
    8: "C",
    9: "Z",
    10: "I",
    11: "D",
    13: "V",
    14: "N",
}
FLAGS = frozenset("CZIDVN")
UNSTABLE = frozenset(("ANE", "LXA", "SHA", "SHX", "SHY", "TAS"))
ORIGIN = 0x1000


def _derived(ops, var):
    """The registers a temporary's value derives from."""
    found, todo, seen = set(), [var], set()
    while todo:
        v = todo.pop()
        if v[0] == "r":
            found.add(v[1])
            continue
        if v[0] != "u" or tuple(v) in seen:
            continue
        seen.add(tuple(v))
        for op in ops:
            if op[1] is not None and list(op[1]) == list(v):
                todo += [i for i in op[2] if i is not None]
    return found


def roles(op):
    """{"mnemonic", "mode", "illegal", "unstable", "ctrl", "reads", "writes", "stack", "push",
    "pull", "regs_out", "flags_out", "flags_in", "sp_in"} of opcode `op`."""
    mem = bytearray(0x10000)
    mem[ORIGIN : ORIGIN + 3] = bytes([op, 0x10, 0x20])
    r = lift(mem, ORIGIN)
    ops = r["ops"]
    loads = [o for o in ops if o[0] == "LOAD" and o[1] is not None]
    stores = [o for o in ops if o[0] == "STORE"]
    via_sp = [o for o in loads + stores if _derived(ops, o[2][0]) == {3}]
    out = {
        REGS[o[1][1]]
        for o in ops
        if o[1] is not None and o[1][0] == "r" and o[1][1] in REGS
    }
    used = {REGS[i[1]] for o in ops for i in o[2] if i and i[0] == "r" and i[1] in REGS}
    if r["ctrl"][0] == "br":
        used.add(REGS[r["ctrl"][1][1]])
    name, mode = OPCODES[op]
    return {
        "mnemonic": name,
        "mode": mode,
        "illegal": op in ILLEGAL_OPCODES,
        "unstable": name in UNSTABLE,
        "ctrl": r["ctrl"][0],
        "reads": len(loads) > len([o for o in via_sp if o[0] == "LOAD"]),
        "writes": len(stores) > len([o for o in via_sp if o[0] == "STORE"]),
        "stack": bool(via_sp) or r["stk"] is not None,
        "push": any(o[0] == "STORE" for o in via_sp),
        "pull": any(o[0] == "LOAD" for o in via_sp),
        "regs_out": sorted(out - FLAGS),
        "flags_out": sorted(out & FLAGS),
        "flags_in": sorted(used & FLAGS),
        "sp_in": "SP" in used and r["stk"] is None and not via_sp,
    }


def facts(ro):  # pylint: disable=too-many-branches
    """The lift-procedure facts an opcode with roles `ro` can produce."""
    out, ctrl = [], ro["ctrl"]
    if ro["writes"]:
        out.append("T1 code-cell writer")
    if ctrl in ("jmpind", "rts", "rti", "brk"):
        out.append("T1 computed transfer")
    if ctrl in ("jsr", "brk"):
        out.append("T2 call")
    if ctrl in ("rts", "rti"):
        out.append("T2 return")
    if "SP" in ro["regs_out"] and ctrl == "next" and not ro["stack"]:
        out.append("T2 stack switch")
    if ro["sp_in"]:
        out.append("T2 stack read")
    if ctrl in ("br", "jmp"):
        out.append("T4 back edge")
    if ctrl == "br":
        out.append("T5 condition on " + ro["flags_in"][0])
    if ro["flags_out"] and ctrl == "next":
        out.append("T5 sets " + "".join(ro["flags_out"]))
    if ro["push"] and ctrl == "next":
        out.append("T1 pushed data")
    if ro["pull"] and ctrl == "next":
        out.append("T2 pulled data")
    if "D" in ro["flags_in"] and not ro["stack"]:
        out.append("decimal")
    if ctrl == "jam":
        out.append("trap")
    if ctrl == "next" and not ro["writes"] and not ro["regs_out"] and not ro["stack"]:
        if MODE_LEN[ro["mode"]] > 1:
            out.append("skip")
    return out


def table():
    """{opcode: roles with "facts"} of all 256 opcodes."""
    out = {}
    for op in range(256):
        ro = roles(op)
        ro["facts"] = facts(ro)
        out[op] = ro
    return out


def main(argv=None):
    """CLI entry point: print each opcode's roles and facts."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.parse_args(argv)
    for op, ro in table().items():
        print(
            f"${op:02X} {ro['mnemonic']} {ro['mode']}: {', '.join(ro['facts']) or '-'}"
        )


if __name__ == "__main__":
    main()
