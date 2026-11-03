"""Find repeated playroutine code in a PSID: unrolled copies, subroutines and fall-through entries.

A copy is a maximal run of executed instructions repeated with the same mnemonics and modes;
its operand deltas show each field's stride. A subroutine is a JSR target with its call sites;
a fall-through entry reaches a JSR target from the instruction before it."""

import argparse
import re
from collections import Counter, defaultdict

from disasm import listing

INSN = re.compile(
    r"([0-9A-F]{4})  ((?:[0-9A-F]{2} )+)\s+(?:L[0-9A-F]{4}: )?(\w{3})\s*(\S*)"
)
NUMBER = re.compile(r"\$([0-9a-fA-F]+)|L([0-9A-F]{4})")
STOPS = {"JMP", "RTS", "RTI", "BRK"}


def instructions(source):
    """(address, length, mnemonic, operand text) of each decoded instruction of a listing."""
    out = []
    for line in source.splitlines():
        m = INSN.match(line)
        if m and ".byte" not in line:
            out.append((int(m[1], 16), len(m[2].split()), m[3], m[4]))
    return out


def shape(insn):
    """Mnemonic, length and operand with numbers abstracted."""
    return insn[2], insn[1], NUMBER.sub("n", insn[3])


def operand(insn):
    """The numeric operand of an instruction, or None."""
    m = NUMBER.search(insn[3])
    return None if m is None else int(m[1] or m[2], 16)


def copies(insns, min_len):
    """Maximal repeated runs: (start index, copy start index, length), longest first."""
    shapes = [shape(i) for i in insns]
    grams = defaultdict(list)
    for i in range(len(shapes) - min_len + 1):
        grams[tuple(shapes[i : i + min_len])].append(i)
    runs = set()
    for starts in grams.values():
        for a, i in enumerate(starts):
            for j in starts[a + 1 :]:
                if i and shapes[i - 1] == shapes[j - 1] and j - i > 1:
                    continue
                n = 0
                while (
                    j + n < len(shapes) and i + n < j and shapes[i + n] == shapes[j + n]
                ):
                    n += 1
                if n >= min_len:
                    runs.add((i, j, n))
    return sorted(runs, key=lambda r: (-r[2], r[0], r[1]))


def deltas(insns, i, j, n):
    """Counter of operand deltas (copy minus original) over a run."""
    out = Counter()
    for a, b in zip(insns[i : i + n], insns[j : j + n]):
        x, y = operand(a), operand(b)
        if x is not None and y is not None:
            out[(y - x) & 0xFFFF] += 1
    return out


def calls(insns):
    """{JSR target: [call site, ...]} and the fall-through entries {target: previous address}."""
    targets = defaultdict(list)
    for insn in insns:
        if insn[2] == "JSR":
            targets[operand(insn)].append(insn[0])
    falls = {}
    for prev, insn in zip(insns, insns[1:]):
        if insn[0] in targets and prev[2] not in STOPS and prev[0] + prev[1] == insn[0]:
            falls[insn[0]] = prev[0]
    return dict(targets), falls


def report(insns, min_len):
    """Text report of copies, subroutines and fall-through entries."""
    lines = []
    for i, j, n in copies(insns, min_len):
        d = ", ".join(f"+${k:X}x{c}" for k, c in deltas(insns, i, j, n).most_common())
        lines.append(
            f"copy ${insns[i][0]:04X} -> ${insns[j][0]:04X}: {n} insns; deltas {d}"
        )
    targets, falls = calls(insns)
    for t, sites in sorted(targets.items()):
        fall = f"; fall-through from ${falls[t]:04X}" if t in falls else ""
        lines.append(
            f"jsr ${t:04X}: {len(sites)} calls {[f'${s:04X}' for s in sites]}{fall}"
        )
    return "\n".join(lines)


def main(argv=None):
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("psid")
    ap.add_argument("--frames", type=int, default=300)
    ap.add_argument("--min-len", type=int, default=12, help="shortest run reported")
    args = ap.parse_args(argv)
    with open(args.psid, "rb") as f:
        data = f.read()
    print(report(instructions(listing(data, args.frames).source), args.min_len))


if __name__ == "__main__":
    main()
