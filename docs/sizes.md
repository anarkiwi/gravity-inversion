# Sizes

`tools/sizes.py` reports a compiled PRG's sizes beside the original 6502 player's, as
information; lift quality is measured as residue ([residue.md](residue.md)), not against a byte
count.

    python3 tools/sizes.py commando tunes/commando/build/gen.prg.elf --runtime N

| Measure | Definition |
|---|---|
| code | the PRG's `.text` less `N`, the `.text` of a player that does nothing (the C runtime and driver) |
| data | all data sections (`.rodata`, `.data`, `.bss`, zero page) |
| top | the highest address of any allocated section |
| original instructions | instruction bytes of jennings' listing of the PSID image, computed transfers resolved through constant tables |
| original footprint | the image's non-instruction bytes and the RAM outside the image the run writes (I/O and the stack excluded), from jennings coverage |

Bytes; deity-informant 0.40.12, jennings 0.14.1, llvm-mos SDK v23.4.0, `-Os`.

| Tune | Original instructions | Hand code | Generated code | Original footprint | Hand data | Generated data | Hand top | Generated top |
|---|---|---|---|---|---|---|---|---|
| Commando | 926 | 1802 | 2590 | 1981 | 1964 | 2035 | `$1732` | `$1A93` |
| Incoherent | 1070 | 2733 | 2573 | 5861 | 5842 | 5886 | `$2A01` | `$2988` |
| Automatas | 1600 | 2505 | 3907 | 6563 | 6647 | 8363 | `$2C33` | `$383D` |
| Ghouls | 3043 | 4106 | 5482 | 22901 | 24262 | 30500 | `$773C` | `$94E3` |
| Emomyst | 2323 | 6179 | 10111 | 2961 | 2864 | 5625 | `$2BA9` | `$45AA` |

Where the generated PRGs' bytes go is measured by ablation in [ablate.md](ablate.md).

## Byte ownership

`tools/owners.py` translates each tune with deity-informant's `#line` markers (its cgen.md,
*Translation cost*), builds it with `-g` and gives each `.text` byte of the translation to
what the DWARF line table names for it (over every unit, as link-time optimization moves code
between them): an original instruction, or an artifact of the lift.

    python3 tools/owners.py commando incoherent automatas ghouls emomyst [--json]

| Measure | Definition |
|---|---|
| B/insn per class | bytes per original instruction printed, over the instructions of each class: load/store (`LDA`, `STA`, ... not through a pointer), ALU (arithmetic, logic, compares, shifts, increments), branch (conditional branches, `JMP`, `JSR`, `RTS`, `RTI`, `BRK`), indirect (`(zp,X)`, `(zp),Y` accesses), other (register transfers, stack, flags) |
| check, trap, access, copy, frame, runtime | bytes of each artifact: checks and their failure arms, `trap(k)` calls, access helpers, block arguments' copies, function frames, the runtime |
| check sites | the check bytes of duplicate-check's checks past one per pointer definition ([residue.md](residue.md)) |
| total | every byte of the translation |
| original, hand | informational: the original's instruction bytes (above) and the hand C's bytes by its line table |

Bytes; deity-informant 0.40.13, llvm-mos SDK v23.4.0, `-Os -g`. B/insn: bytes per instruction
(instructions printed).

| Tune | load/store B/insn | ALU B/insn | branch B/insn | indirect B/insn | other B/insn | check | trap | access | copy | frame | runtime | check sites | total | original | hand |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Commando | 9.7 (94) | 15.5 (38) | 25.9 (16) | 41.4 (5) | - | 158 | 13 | 0 | 3 | 170 | 0 | 75 | 2465 | 926 | 1653 |
| Incoherent | 8.9 (126) | 13.5 (39) | 15.4 (23) | 30.6 (9) | - | 108 | 0 | 0 | 0 | 92 | 0 | 84 | 2483 | 1070 | 2612 |
| Automatas | 9.4 (133) | 14.3 (33) | 11.9 (23) | 29.1 (15) | - | 450 | 0 | 142 | 0 | 163 | 0 | 374 | 3182 | 1600 | 2379 |
| Ghouls | 13.7 (209) | 13.2 (50) | 20.4 (25) | 53.5 (13) | - | 202 | 6 | 274 | 6 | 157 | 0 | 51 | 5381 | 3043 | 3930 |
| Emomyst | 10.3 (231) | 16.6 (82) | 14.3 (70) | 36.8 (11) | - | 1017 | 6 | 1553 | 31 | 290 | 0 | 850 | 8044 | 2323 | 6158 |
