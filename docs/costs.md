# Code-size costs

`tools/costs.py` reads clang's AST of a generated translation and counts the constructs llvm-mos
compiles to more code than the 6502 idiom they translate; `tests/test_costs.py` requires none and
records each that remains, with its cause, as a strict xfail. The size bounds themselves are in
[sizes.md](sizes.md).

| Kind | Construct | 6502 idiom | Form that costs no more |
|---|---|---|---|
| K1 | `rd()`/`wr()` whose address depends on an instance index | folded copies' own state: operand cells and zero-page fields at a per-copy stride | the region array indexed by `base + stride * k`, the copy's offsets known to lie in one region |
| K2 | a multiplication by a value derived from the instance index | the copy stride | the offset computed once per call, in 8 bits where it fits, or passed as the parameter |
| K3 | an instance index compared with a constant inside arithmetic | a per-copy constant that is not affine in the index (`v0 + (k == 1) * (v1 - v0) ...`) | a `const` table indexed by `k` |
| K4 | a function returning a struct | registers live across `JSR`/`RTS` | registers as static variables, as the 6502 holds them across calls |
| K5 | a region index computed from a 16-bit variable | 8-bit indexed addressing | an 8-bit index where the offset's interval fits |
| K6 | a read whose value is discarded | the 6502's dummy read on a page-crossing indexed access, kept where the index or pointer may reach a read-sensitive I/O register | a check where the index or pointer is set that it cannot reach I/O |

## Where each remains

| Tune | Kinds | 6502 cause |
|---|---|---|
| Commando | K6 | `STA $D400,Y` with Y = `sid,x` and `(TRACK)`/`(PATT),Y` song pointers: the table's values and the pointers are unbounded to the analysis |
| Incoherent | K4, K6 | the channel routine and command handlers return X/Y to their callers; `(zp),Y` pattern pointers |
| Automatas | K1, K2, K3, K6 | band, row and sidTAB copies keep their voice's state in their own operands, `$31` and `$88` apart; per-copy constants not affine in the voice |
| Ghouls | K1, K2, K3, K4, K5, K6 | voice copies' zero-page fields (+1, +2), own operands (+`$1ED`) and SID registers (+7); `CMP #v` and handler tables per voice |
| Emomyst | K1, K4, K5, K6 | track routines returning registers to their callers; `(zp),Y` reads through song pointers and the voice index the relocator's stores leave unbounded |

## Fuzz players

`tests/test_costs.py` checks every `tools/fuzzplayer.py` style and seed the same way, where the
ideal is known by construction. What they show beyond the tunes:

| Style | Kinds | Cause |
|---|---|---|
| `loop` and the instruction-class motifs | K6 | `STA $D4xx,X` always makes a dummy read; X steps 14, 7, 0 by `SBC #7`, which the analysis does not bound, so the read may reach `$D419-$D41C` |
| every simple style, seed 2 | K2, K4 | three statements loading fields `$20` apart fold as an instance class `f(..., k)`: a coincidental fold, costing a multiply and a struct return the plain code does not have |
| `unrolled` | K2, K5 | the copies' state offsets multiplied by the copy index and indexed in 16 bits |
| `smc` | K1, K2, K3 | the copies' operand cells through `rd`/`wr`, the copy offsets multiplied, per-copy constants as select-sums |
