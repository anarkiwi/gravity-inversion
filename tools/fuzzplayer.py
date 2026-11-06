"""Generate synthetic three-voice players: one voice routine dispatched in a given style.

The routine body is drawn from a seeded RNG (counters, masked skips, an inner DEY/BPL loop, SID
writes); the style decides how the 6502 reaches it per voice, as real players do, so the ideal
translation is known: one voice routine and no unstructured edges. A sequencer (``seq``) reads
song rows per voice through a pointer: notes, legato notes and rests, its SID writes direct or
to a shadow buffer copied to the SID after the voices or at the start of the next call.
"""

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
SEQ = ("direct", "shadow", "shadow_next")
SHADOW, PTR, SIDREGS = 0x3200, 0xF0, 25
DUR, NOTE, START_LO, START_HI = 4, 5, 6, 7
NOTES, REST, LEGATO, JUMP = 32, 0x80, 0x60, 0xFF
FREQ = [round(0x0400 * 2 ** (n / 12)) for n in range(NOTES)]
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


def body(rng, fields, regs=VOICE):
    """Voice routine statements drawn from `rng` over state `fields`, writing SID voice
    registers `regs` (a count or a sequence): each kind at least once."""
    kinds = ["count", "write", "skip", "inner"]
    kinds += list(rng.choice(kinds, size=rng.integers(0, 4)))
    out = []
    for kind in rng.permutation(kinds):
        f = int(rng.integers(fields))
        if kind == "count":
            out.append(("count", f, int(rng.integers(1, 0x40))))
        elif kind == "write":
            r = rng.integers(regs) if isinstance(regs, int) else rng.choice(regs)
            out.append(("write", f, int(r)))
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

    def __init__(self, style, k, rng, shadow=False):
        self.style, self.k, self.rng, self.cells = style, k, rng, set()
        self.shadow = shadow

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

    def fixed(self):
        """Whether the copy addresses its voice by fixed operands rather than ``X``."""
        return self.style in ("unrolled", "smc")

    def sid(self, r):
        """Operand text of SID voice register r (its shadow byte with a shadow buffer)."""
        base = SHADOW if self.shadow else 0xD400
        if self.fixed():
            return f"${base + r + VOICE * self.k:04x}"
        return f"${base + r:04x},X"

    def cell(self, f):
        """Operand text of sequencer field f: a plain cell in every style."""
        if self.fixed():
            return f"${STATE + f * 0x20 + VOICE * self.k:04x}"
        return f"${STATE + f * 0x20:04x},X"

    def pointer(self, half=0):
        """Operand text of a half of the voice's song pointer."""
        if self.fixed():
            return f"${PTR + half + VOICE * self.k:02x}"
        return f"${PTR + half:02x},X"

    def read(self):
        """Lines loading the byte at the song pointer into A."""
        if self.fixed():
            return ["LDY #0", f"LDA (${PTR + VOICE * self.k:02x}),Y"]
        return [f"LDA (${PTR:02x},X)"]


def song(rng, rows=24):
    """Song bytes of one voice: notes (a few pitches, so repeats occur) with a duration,
    legato notes with a duration, rests, and the jump back to the start."""
    pitches = rng.choice(NOTES, size=4, replace=False)
    out = []
    for _ in range(rows):
        kind = rng.choice(3, p=[0.2, 0.5, 0.3])
        if kind == 0:
            out.append(REST | int(rng.integers(1, 5)))
        else:
            out += [
                int(rng.choice(pitches)) + (LEGATO if kind == 2 else 0),
                int(rng.integers(6)),
            ]
    return out + [JUMP]


def sequencer(v, tag):
    """ACME lines of one voice's sequencer: a row when its duration runs out."""
    adv = [
        f"INC {v.pointer()}",
        f"BNE {tag}a{{n}}",
        f"INC {v.pointer(1)}",
        f"{tag}a{{n}}:",
    ]

    def step(n):
        return [x.replace("{n}", str(n)) for x in adv]

    return (
        [f"DEC {v.cell(DUR)}", f"BPL {tag}done", f"{tag}row:"]
        + v.read()
        + [f"CMP #${JUMP:02x}", f"BNE {tag}nj"]
        + [f"LDA {v.cell(START_LO)}", f"STA {v.pointer()}"]
        + [
            f"LDA {v.cell(START_HI)}",
            f"STA {v.pointer(1)}",
            f"JMP {tag}row",
            f"{tag}nj:",
        ]
        + [f"CMP #${REST:02x}", f"BCC {tag}note", f"AND #${REST - 1:02x}"]
        + [f"STA {v.cell(DUR)}", "LDA #$40", f"STA {v.sid(4)}"]
        + step(0)
        + [f"JMP {tag}done", f"{tag}note:", f"STA {v.cell(NOTE)}"]
        + [f"CMP #${LEGATO:02x}", "PHP", f"AND #${NOTES - 1:02x}", "TAY"]
        + ["LDA freqlo,Y", f"STA {v.sid(0)}", "LDA freqhi,Y", f"STA {v.sid(1)}"]
        + ["PLP", f"BCS {tag}leg", "LDA #$41", f"STA {v.sid(4)}", f"{tag}leg:"]
        + step(1)
        + v.read()
        + [f"STA {v.cell(DUR)}"]
        + step(2)
        + [f"{tag}done:"]
    )


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


def player(style, seed, fields=4, seq=None):  # pylint: disable=too-many-locals
    """ACME source of a PSID whose voice routine is dispatched in `style`, with a
    sequencer writing the SID `seq` (one of SEQ) when given."""
    rng = np.random.default_rng(seed)
    stmts = body(rng, fields, (2, 3) if seq else VOICE)
    shadow = seq in ("shadow", "shadow_next")
    init, tables = [], []
    for f in range(fields):
        for k in range(3):
            init += [
                f"LDA #${int(rng.integers(256)):02x}",
                f"STA ${STATE + f * 0x20 + VOICE * k:04x}",
            ]
    if seq:
        if shadow:
            init += [
                "LDA #0",
                f"LDX #{SIDREGS - 1}",
                "sc:",
                f"STA ${SHADOW:04x},X",
                "DEX",
                "BPL sc",
            ]
        for k in range(3):
            v = Voice("unrolled", k, rng, shadow)
            tables += [f"song{k}: !byte " + ", ".join(f"${b:02x}" for b in song(rng))]
            for half, f in ((0, START_LO), (1, START_HI)):
                op = "<" if half == 0 else ">"
                init += [
                    f"LDA #{op}song{k}",
                    f"STA {v.cell(f)}",
                    f"STA {v.pointer(half)}",
                ]
            init += ["LDA #0", f"STA {v.cell(DUR)}", "LDA #$09", f"STA {v.sid(5)}"]
            init += ["LDA #$a0", f"STA {v.sid(6)}"]
        init += ["LDA #$0f", f"STA ${(SHADOW if shadow else 0xD400) + 0x18:04x}"]
        tables += ["freqlo: !byte " + ", ".join(f"${x & 0xFF:02x}" for x in FREQ)]
        tables += ["freqhi: !byte " + ", ".join(f"${x >> 8:02x}" for x in FREQ)]
    if style in ("unrolled", "smc"):
        play = []
        for k in range(3):
            v = Voice(style, k, rng, shadow)
            play += [f"copy{k}:"] + (sequencer(v, f"q{k}") if seq else [])
            play += emit(stmts, v, f"c{k}_")
            play += [f"JMP copy{k + 1}"] if k < 2 else ["RTS"]
    else:
        subs = []
        v = Voice(style, 0, rng, shadow)
        code = (sequencer(v, "q") if seq else []) + emit(stmts, v, "v")
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
    if shadow:
        copy = [
            "LDX #24",
            "cp:",
            f"LDA ${SHADOW:04x},X",
            "STA $d400,X",
            "DEX",
            "BPL cp",
        ]
        first = copy if seq == "shadow_next" else []
        last = copy if seq == "shadow" else []
        play = first + ["JSR main"] + last + ["RTS", "main:"] + play
    lines = ["init:"] + init + ["RTS", "play:"] + play + tables
    text = "\n".join(l if re.match(r"\w+:", l) else f"        {l}" for l in lines)
    return f"{HEADER}{text}\n}}\n"


def main(argv=None):
    """CLI entry point: write the ACME source."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("style", choices=STYLES)
    ap.add_argument("seed", type=int)
    ap.add_argument("out")
    ap.add_argument("--seq", choices=SEQ, help="add a sequencer writing the SID so")
    args = ap.parse_args(argv)
    with open(args.out, "w", encoding="ascii") as f:
        f.write(player(args.style, args.seed, seq=args.seq))


if __name__ == "__main__":
    main()
