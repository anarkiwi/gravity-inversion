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

Bytes; deity-informant 0.40.9, jennings 0.14.1, llvm-mos SDK v23.4.0, `-Os`.

| Tune | Original instructions | Hand code | Generated code | Original footprint | Hand data | Generated data | Hand top | Generated top |
|---|---|---|---|---|---|---|---|---|
| Commando | 926 | 1802 | 2510 | 1981 | 1964 | 2035 | `$1732` | `$1A43` |
| Incoherent | 1070 | 2733 | 2833 | 5861 | 5842 | 5919 | `$2A01` | `$2AAA` |
| Automatas | 1600 | 2505 | 3449 | 6563 | 6647 | 6740 | `$2C33` | `$301E` |
| Ghouls | 3043 | 4106 | 5285 | 22901 | 24262 | 30472 | `$773C` | `$9406` |
| Emomyst | 2323 | 6179 | 8948 | 2961 | 2864 | 5465 | `$2BA9` | `$4097` |

Where the generated PRGs' bytes go is measured by ablation in [ablate.md](ablate.md).
