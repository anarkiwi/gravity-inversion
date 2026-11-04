"""Generate synthetic three-voice players: one voice routine dispatched in a given style.

The routine body is drawn from a seeded RNG (counters, masked skips, an inner DEY/BPL loop, SID
writes); the style decides how the 6502 reaches it per voice, as real players do, so the ideal
translation is known: one voice routine and no unstructured edges."""

import argparse

import numpy as np

STYLES = ("loop", "jsr3", "fallthrough", "unrolled", "smc")
STATE = 0x3000
VOICE = 7
HEADER = """!cpu 6510
* = 0
!text "PSID"
!be16 2,$7c
!be16 0
!be16 init
!be16 play
!be16 1,1
!be32 0
!fill 96,0
!be16 0,0,0
!wo ADDR
ADDR = $1000
!pseudopc ADDR {
"""


def body(rng, fields):
    """Voice routine statements drawn from `rng` over state `fields`: each kind at least once."""
    kinds = ["count", "write", "skip", "inner"]
    kinds += list(rng.choice(kinds, size=rng.integers(0, 4)))
    out = []
    for kind in rng.permutation(kinds):
        f = int(rng.integers(fields))
        if kind == "count":
            out.append(("count", f, int(rng.integers(1, 0x40))))
        elif kind == "write":
            out.append(("write", f, int(rng.integers(VOICE))))
        elif kind == "skip":
            g = int(rng.integers(fields))
            out.append(
                ("skip", f, 1 << int(rng.integers(8)), g, int(rng.integers(1, 9)))
            )
        else:
            out.append(("inner", f, int(rng.integers(1, 5))))
    return out


class Voice:
    """Operand text of one voice's state and SID registers in a style."""

    def __init__(self, style, k, rng):
        self.style, self.k, self.rng, self.cells = style, k, rng, set()

    def load(self, f):
        """Lines loading field f into A."""
        if self.style != "smc":
            return [f"LDA {self.store(f)}"]
        if f in self.cells:
            return [f"LDA {self.store(f)}"]
        self.cells.add(f)
        return [f"s{self.k}f{f}:", f"LDA #${int(self.rng.integers(256)):02x}"]

    def store(self, f):
        """Operand text of field f."""
        if self.style == "smc":
            return f"s{self.k}f{f}+1"
        if self.style in ("unrolled",):
            return f"${STATE + f * 0x20 + VOICE * self.k:04x}"
        return f"${STATE + f * 0x20:04x},X"

    def sid(self, r):
        """Operand text of SID voice register r."""
        if self.style in ("unrolled", "smc"):
            return f"$d4{r + VOICE * self.k:02x}"
        return f"$d4{r:02x},X"


def emit(stmts, v, tag):
    """ACME lines of `stmts` for voice operands `v`, labels prefixed `tag`."""
    lines = []
    for i, s in enumerate(stmts):
        lab = f"{tag}{i}"
        if s[0] == "count":
            lines += v.load(s[1]) + ["CLC", f"ADC #${s[2]:02x}", f"STA {v.store(s[1])}"]
        elif s[0] == "write":
            lines += v.load(s[1]) + [f"STA {v.sid(s[2])}"]
        elif s[0] == "skip":
            lines += v.load(s[1]) + [f"AND #${s[2]:02x}", f"BEQ {lab}"]
            lines += v.load(s[3]) + ["CLC", f"ADC #${s[4]:02x}", f"STA {v.store(s[3])}"]
            lines += [f"{lab}:"]
        else:
            lines += [f"LDY #${s[2]:02x}", f"{lab}:"] + v.load(s[1])
            lines += ["CLC", "ADC #$01", f"STA {v.store(s[1])}", "DEY", f"BPL {lab}"]
    return lines


def player(style, seed, fields=4):
    """ACME source of a PSID whose voice routine is dispatched in `style`."""
    rng = np.random.default_rng(seed)
    stmts = body(rng, fields)
    init = []
    for f in range(fields):
        for k in range(3):
            init += [
                f"LDA #${int(rng.integers(256)):02x}",
                f"STA ${STATE + f * 0x20 + VOICE * k:04x}",
            ]
    if style in ("unrolled", "smc"):
        play = []
        for k in range(3):
            play += [f"copy{k}:"] + emit(stmts, Voice(style, k, rng), f"c{k}_")
            play += [f"JMP copy{k + 1}"] if k < 2 else ["RTS"]
    else:
        voice = ["voice:"] + emit(stmts, Voice(style, 0, rng), "v") + ["RTS"]
        calls = {
            "loop": [
                "LDX #14",
                "pl:",
                "JSR voice",
                "TXA",
                "SEC",
                "SBC #7",
                "TAX",
                "BPL pl",
                "RTS",
            ],
            "jsr3": [
                "LDX #0",
                "JSR voice",
                "LDX #7",
                "JSR voice",
                "LDX #14",
                "JSR voice",
                "RTS",
            ],
            "fallthrough": ["LDX #0", "JSR voice", "LDX #7", "JSR voice", "LDX #14"],
        }
        play = calls[style] + voice
    lines = ["init:"] + init + ["RTS", "play:"] + play
    text = "\n".join(l if l.endswith(":") else f"        {l}" for l in lines)
    return f"{HEADER}{text}\n}}\n"


def main(argv=None):
    """CLI entry point: write the ACME source."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("style", choices=STYLES)
    ap.add_argument("seed", type=int)
    ap.add_argument("out")
    args = ap.parse_args(argv)
    with open(args.out, "w", encoding="ascii") as f:
        f.write(player(args.style, args.seed))


if __name__ == "__main__":
    main()
