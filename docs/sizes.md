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

Bytes; deity-informant 0.37.3, jennings 0.14.1, llvm-mos SDK v23.4.0, `-Os`.

| Tune | Original instructions | Hand code | Generated code | Original footprint | Hand data | Generated data | Hand top | Generated top |
|---|---|---|---|---|---|---|---|---|
| Commando | 926 | 1802 | 2269 | 1981 | 1964 | 2036 | `$1732` | `$194F` |
| Incoherent | 1070 | 2733 | 2877 | 5861 | 5842 | 5894 | `$2A01` | `$2ABB` |
| Automatas | 1600 | 2505 | 2849 | 6563 | 6647 | 6746 | `$2C33` | `$2DC6` |
| Ghouls | 3043 | 4106 | 5644 | 22901 | 24262 | 30710 | `$773C` | `$9669` |
| Emomyst | 2323 | 6179 | 7271 | 2961 | 2864 | 6284 | `$2BA9` | `$3D32` |

Where the generated PRGs' bytes go is measured by ablation in [ablate.md](ablate.md).
