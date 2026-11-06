# Size bounds

A translation must fit in a C64 beside its song data, the driver and the stack, so its compiled
PRG is bounded by the original 6502 player (`tools/sizes.py`, checked by `tests/test_sizes.py` for
the hand and the generated translations):

| Bound | Measure |
|---|---|
| code ≤ 2.5 × the original's instruction bytes | the PRG's `.text` less the `.text` of a player that does nothing (the C runtime and driver); instruction bytes from jennings' listing of the PSID image |
| data ≤ the original's data footprint + the code bytes it reads as data | all data sections (`.rodata`, `.data`, `.bss`, zero page); the footprint is the image's non-instruction bytes and the RAM outside the image the run writes (I/O and the stack excluded), from jennings coverage |
| the PRG loads below `$D000` | the highest address of any allocated section |

2.5 is the smallest half-step every hand translation meets (1.33× to 2.43× of the original's
instructions); it is llvm-mos's cost of 6502 idioms in C: 16-bit pointer arithmetic, flags as
values, register traffic through zero page. The data bound holds every byte the original uses
once and nothing else.

The generated C's constructs that cost code size are detected in [costs.md](costs.md); where
the generated PRGs' bytes go is measured by ablation in [ablate.md](ablate.md).

## Translations against the bounds

Bytes, from `tools/sizes.py`; the hand and generated PRGs all load below `$D000`.

| Tune | Code bound | Hand code | Generated code | Data bound | Hand data | Generated data |
|---|---|---|---|---|---|---|
| Commando | 2315 | 1416 | 2177 | 1981 | 1963 | 2131 |
| Incoherent | 2675 | 2603 | 2652 | 5863 | 5839 | 5835 |
| Automatas | 4000 | 2655 | 3144 | 6583 | 6832 | 7465 |
| Ghouls | 6662 | 3552 | 6587 | 23291 | 30523 | 30294 |
| Emomyst | 5432 | 4490 | 5643 | 3170 | 2862 | 3688 |

Where a translation exceeds a bound, `tests/test_sizes.py` records the cause as a strict xfail.
