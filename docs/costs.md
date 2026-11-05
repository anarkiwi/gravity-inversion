# Code-size costs

`tools/costs.py` reads clang's AST of a generated translation and counts the constructs llvm-mos
compiles to more code than the 6502 idiom they translate; `tests/test_costs.py` requires none and
records each that remains, with its cause, as a strict xfail. The size bounds themselves are in
[sizes.md](sizes.md); what each mechanism costs, measured by ablation, in [ablate.md](ablate.md).

| Kind | Construct | 6502 idiom | Form that costs no more |
|---|---|---|---|
| K1 | `rd()`/`wr()` whose address depends on an instance index | folded copies' own state: operand cells and zero-page fields at a per-copy stride | the region array indexed by `base + stride * k`, the copy's offsets known to lie in one region |
| K2 | a multiplication by a value derived from the instance index | the copy stride | the offsets read once per call from a `const` table indexed by `k`, in 8 bits where they fit, or passed as the parameter |
| K3 | an instance index compared with a constant as a value, not a branch condition | a per-copy constant that is not affine in the index (`v0 + (k == 1) * (v1 - v0) ...`) | a `const` table indexed by `k` |
| K4 | a function returning a struct | registers live across `JSR`/`RTS` | registers as static variables, as the 6502 holds them across calls |
| K5 | a region index computed from a 16-bit variable | 8-bit indexed addressing | an 8-bit index where the offset's interval fits |
| K6 | a read whose value is discarded | the 6502's dummy read on a page-crossing indexed access, kept where the index or pointer may reach a read-sensitive I/O register | a check where the index or pointer is set that it cannot reach I/O |

## Where each remains

| Tune | Kinds | 6502 cause |
|---|---|---|
| Commando | none | |
| Incoherent | none | |
| Automatas | K1, K5 | copies `$88` apart: the offsets do not fit 8 bits, so the copies' fields are indexed in 16 bits; song-data reads through each copy's own operand pointer go through `rd` |
| Ghouls | K1, K3, K5, K6 | song-data reads through each voice's zero-page pointer go through `rd`; copies' operands `$1ED`/`$1EF` apart indexed in 16 bits; per-voice comparisons |
| Emomyst | none | the init's relocator runs once at lift time: its operands are constants and its code is not translated |

## Fuzz players

`tests/test_costs.py` checks every `tools/fuzzplayer.py` style and seed the same way, where the
ideal is known by construction; every one is free of every kind.
