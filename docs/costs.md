# Code-size costs

`tools/costs.py` reads clang's AST of a generated translation and counts the constructs llvm-mos
compiles to more code than the 6502 idiom they translate; `tests/test_costs.py` requires none and
records each that remains, with its cause, as a strict xfail. The size bounds themselves are in
[sizes.md](sizes.md).

| Kind | Construct | 6502 idiom | Form that costs no more |
|---|---|---|---|
| K1 | `rd()`/`wr()` whose address depends on an instance index | folded copies' own state: operand cells and zero-page fields at a per-copy stride | the region array indexed by `base + stride * k`, the copy's offsets known to lie in one region |
| K2 | a multiplication by a value derived from the instance index | the copy stride | the offset computed once per call, in 8 bits where it fits, or passed as the parameter |
| K3 | an instance index compared with a constant as a value, not a branch condition | a per-copy constant that is not affine in the index (`v0 + (k == 1) * (v1 - v0) ...`) | a `const` table indexed by `k` |
| K4 | a function returning a struct | registers live across `JSR`/`RTS` | registers as static variables, as the 6502 holds them across calls |
| K5 | a region index computed from a 16-bit variable | 8-bit indexed addressing | an 8-bit index where the offset's interval fits |
| K6 | a read whose value is discarded | the 6502's dummy read on a page-crossing indexed access, kept where the index or pointer may reach a read-sensitive I/O register | a check where the index or pointer is set that it cannot reach I/O |

## Where each remains

| Tune | Kinds | 6502 cause |
|---|---|---|
| Commando | none | |
| Incoherent | none | |
| Automatas | K1, K2, K5 | copies `$88` apart: the stride does not fit 8 bits, so it is multiplied and the copies' fields indexed in 16 bits; song-data reads through each copy's own operand pointer go through `rd` |
| Ghouls | K1, K2, K3, K5, K6 | song-data reads through each voice's zero-page pointer go through `rd`; copies' operands `$1ED`/`$1EF` apart indexed in 16 bits; per-voice comparisons |
| Emomyst | K1, K5 | the relocator's `LDA $1111,Y` at `$1163` through its written operand, called with Y = 0 and 1; the relocator's `($02),Y` stores into the image, indexed by the 16-bit pointer |

## Fuzz players

`tests/test_costs.py` checks every `tools/fuzzplayer.py` style and seed the same way, where the
ideal is known by construction. What they show beyond the tunes:

| Style | Kinds | Cause |
|---|---|---|
| `loop` and the instruction-class motifs | K6 | `STA $D4xx,X` always makes a dummy read; X steps 14, 7, 0 by `SBC #7`, which the analysis does not bound, so the read may reach `$D419-$D41C` |
| every simple style, seed 2 | K2 | three statements loading fields `$20` apart fold as an instance class `f(..., k)`: a coincidental fold, costing a multiply the plain code does not have |
| `unrolled` | K2 | the copies' state offset multiplied by the copy index |
| `smc` | K2, K3 | the copy offset multiplied by the copy index; per-copy constants compared with the index |
