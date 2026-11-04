"""Generate synthetic three-voice players: one voice routine dispatched in a given style.

The routine body is drawn from a seeded RNG (counters, masked skips, an inner DEY/BPL loop, SID
writes); the style decides how the 6502 reaches it per voice, as real players do, so the ideal
translation is known: one voice routine and no unstructured edges."""

import argparse
import re

import numpy as np

MOTIFS = (
    "rti",
    "brk",
    "stackswitch",
    "inlineargs",
    "skip",
    "decimal",
    "rmw",
    "vflag",
    "indx",
    "jam",
)
STYLES = ("loop", "jsr3", "fallthrough", "unrolled", "smc") + MOTIFS
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


def _f(f):
    return f"${STATE + f * 0x20:04x},X"


def motif(name, rng):  # pylint: disable=too-many-return-statements
    """(voice body lines, subroutines, init lines) exercising instruction class `name`."""
    f, g, k = int(rng.integers(4)), int(rng.integers(4)), int(rng.integers(1, 0x40))
    bump = [f"LDA {_f(g)}", "CLC", f"ADC #${k:02x}", f"STA {_f(g)}"]
    if name == "rti":
        code = (
            [
                f"LDA {_f(f)}",
                "AND #1",
                "TAY",
                "LDA mrh,Y",
                "PHA",
                "LDA mrl,Y",
                "PHA",
                "PHP",
                "RTI",
                "mr0:",
            ]
            + bump
            + ["JMP mrj", "mr1:", f"INC {_f(g)}", "mrj:"]
        )
        return code, ["mrl: !byte <mr0, <mr1", "mrh: !byte >mr0, >mr1"], []
    if name == "brk":
        handler = ["mbrk:"] + bump + ["RTI"]
        init = [
            "LDA #$35",
            "STA $01",
            "LDA #<mbrk",
            "STA $fffe",
            "LDA #>mbrk",
            "STA $ffff",
        ]
        return ["BRK", "!byte $ea"], handler, init
    if name == "stackswitch":
        code = ["STX mx", "TSX", "STX msp", "LDX mx", "JSR mdeep", "mafter:"]
        subs = (
            ["mdeep:", "JSR mdeeper", "RTS", "mdeeper:"]
            + bump
            + ["LDX msp", "TXS", "LDX mx", "JMP mafter", "mx: !byte 0", "msp: !byte 0"]
        )
        return code, subs, []
    if name == "inlineargs":
        sub = [
            "marg:",
            "PLA",
            "STA $f0",
            "PLA",
            "STA $f1",
            "LDY #1",
            "LDA ($f0),Y",
            "CLC",
            f"ADC {_f(g)}",
            f"STA {_f(g)}",
            "LDA $f0",
            "CLC",
            "ADC #1",
            "TAY",
            "LDA $f1",
            "ADC #0",
            "PHA",
            "TYA",
            "PHA",
            "RTS",
        ]
        return ["JSR marg", f"!byte ${k:02x}"], sub, []
    if name == "skip":
        code = [
            f"LDA {_f(f)}",
            "AND #1",
            "BEQ ms1",
            f"LDA #${k:02x}",
            "!byte $2c",
            "ms1:",
            f"LDA #${k + 0x40:02x}",
            f"STA {_f(g)}",
        ]
        return code, [], []
    if name == "decimal":
        return ["SED"] + bump + ["CLD"], [], []
    if name == "rmw":
        code = [
            "mc:",
            f"LDA #${k:02x}",
            f"STA {_f(g)}",
            "ISC mc+1",
            f"LDA #${k:02x}",
            f"DCP {_f(f)}",
            "BNE mr",
            f"INC {_f(g)}",
            "mr:",
        ]
        return code, [], []
    if name == "vflag":
        code = (
            [f"LDA {_f(f)}", "STA $f2", "BIT $f2", "BVC mv", "BMI mv"] + bump + ["mv:"]
        )
        return code, [], []
    if name == "indx":
        init = []
        for v in range(3):
            init += [
                f"LDA #<${STATE + g * 0x20 + VOICE * v:04x}",
                f"STA ${0x40 + VOICE * v:02x}",
                f"LDA #>${STATE + g * 0x20 + VOICE * v:04x}",
                f"STA ${0x41 + VOICE * v:02x}",
            ]
        code = ["LDA ($40,X)", "CLC", f"ADC #${k:02x}", "STA ($40,X)"]
        return code, [], init
    code = [f"LDA ${STATE + 0x1F0:04x}", "BEQ mok", "!byte $02", "mok:"] + bump
    return code, [], []


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
        subs = []
        code = emit(stmts, Voice(style, 0, rng), "v")
        if style in MOTIFS:
            body_lines, subs, extra = motif(style, rng)
            at = int(rng.integers(len(code) + 1))
            while at < len(code) and code[at].endswith(":"):
                at += 1
            code = code[:at] + body_lines + code[at:]
            init += extra
        voice = ["voice:"] + code + ["RTS"] + subs
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
        play = calls.get(style, calls["loop"]) + voice
    lines = ["init:"] + init + ["RTS", "play:"] + play
    text = "\n".join(l if re.match(r"\w+:", l) else f"        {l}" for l in lines)
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
