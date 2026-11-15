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
| original instructions | instruction bytes of jennings' listing of the PSID image |
| original footprint | the image's non-instruction bytes and the RAM outside the image the run writes (I/O and the stack excluded), from jennings coverage |

Bytes; deity-informant 0.28.2, llvm-mos SDK v23.4.0, `-Os`.

| Tune | Original instructions | Hand code | Generated code | Original footprint | Hand data | Generated data | Hand top | Generated top |
|---|---|---|---|---|---|---|---|---|
| Commando | 926 | 1416 | 2115 | 1981 | 1963 | 2189 | `$15B1` | `$194E` |
| Incoherent | 1070 | 2603 | 2589 | 5861 | 5839 | 5840 | `$297C` | `$2966` |
| Automatas | 1600 | 2655 | 2702 | 6563 | 6832 | 6862 | `$2D8A` | `$2DB2` |
| Ghouls | 2665 | 3552 | 3581 | 23279 | 30523 | 30844 | `$8D8A` | `$8ECB` |
| Emomyst | 2173 | 4519 | 5099 | 3111 | 2862 | 3142 | `$2536` | `$2889` |

Where the generated PRGs' bytes go is measured by ablation in [ablate.md](ablate.md).
