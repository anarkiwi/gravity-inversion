# Code-size costs

`tools/costs.py` reads clang's AST of a generated translation and counts the constructs llvm-mos
compiles to more code than the 6502 idiom they translate (`--sites` lists each by line);
`tests/test_costs.py` requires none and records each that remains, with its cause, as a strict
xfail. cgen's runtime (`rd`, `wr`, the bounds-checked span readers `rdCj`, ...) is not
counted. The size bounds themselves are in [sizes.md](sizes.md); what each mechanism costs,
measured by ablation, in [ablate.md](ablate.md).

    python3 tools/costs.py tunes/ghouls/build/gen/ghouls.c -I tunes/ghouls/build/gen -I c64 --sites

| Kind | Construct | 6502 idiom | Form that costs no more |
|---|---|---|---|
| K1 | `rd()`/`wr()` whose address depends on an instance index | folded copies' own state: operand cells and zero-page fields at a per-copy stride, or pointers they hold | the region array indexed by `base + stride * k`, the copy's offsets known to lie in one region; a pointer's values bounded to one region |
| K2 | a multiplication by a value derived from the instance index | the copy stride | the offsets read once per call from a `const` table indexed by `k`, in 8 bits where they fit, or passed as the parameter |
| K3 | an instance index compared with a constant as a value, not a branch condition (through parentheses, casts and a comma expression's value) | a per-copy constant that is not affine in the index (`v0 + (k == 1) * (v1 - v0) ...`) | a `const` table indexed by `k` |
| K4 | a function returning a struct | registers live across `JSR`/`RTS` | registers as static variables, as the 6502 holds them across calls |
| K5 | a region index computed from a 16-bit variable | 8-bit indexed addressing | an 8-bit index where the offset's interval fits; copies further apart through a per-copy pointer indexed by a byte, `(zp),Y` |
| K6 | a read whose value is discarded | the 6502's dummy read on a page-crossing indexed access, kept where the index or pointer may reach an I/O register whose read has a side effect | a check where the index or pointer is set that it cannot reach I/O |

## Where each remains

| Tune | Kinds | 6502 cause |
|---|---|---|
| Commando | none | |
| Incoherent | none | |
| Automatas | none | each row copy's operand pointer, advanced by its row's length, keeps its table's least base |
| Ghouls | none | each voice's song pointer may hold 0 (init clears zero page; a loop end restores the cleared loop pointer) or only a high byte an effect list wrote, yet its in-image values lie in the song: its reads are the song span's bounds-checked reads, `oob_rd` (a trap; with `CGEN_OOB_EXACT` the 6502's byte, zero page's live one) outside it. Loaded from song data by a jump command and popped from the return stack a call command pushes at an unbounded depth, it points into the song; the pushes are bounds-checked against the stack (`oob_wr` past it) |
| Emomyst | K5 | init's relocator is lifted as code: its stores through its patch-site table write 30 operands scattered through the player, one held span's bytes, indexed by 16 bits (K5). The operands it patches from the SWP header words are pointers into the song, and play reads through them with the span's bounds-checked reader |

## Fuzz players

`tests/test_costs.py` checks every `tools/fuzzplayer.py` style and seed the same way, where the
ideal is known by construction; every one is free of every kind.
