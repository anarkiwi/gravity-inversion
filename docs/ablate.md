# Ablation

`tools/ablate.py` measures where a generated translation's PRG bytes go. Each step rewrites one
mechanism of the generated C through clang's AST source ranges (`tools/cflow.py` models the
AST: integer evaluation, regions from the header, the call graph, a must-equal value flow);
the steps apply cumulatively and the PRG's `.text` is measured after each. A step the tune
does not have changes nothing.

    make -C tunes/<tune> && make -C tunes/<tune> build/gen.prg
    python3 tools/ablate.py commando incoherent automatas ghouls emomyst --verify [--json]

An **exact** step keeps the run's semantics: the default song over its HVSC length.
`--verify` builds the chain of exact steps alone, from the baseline, and compares each build's
SID writes with the PSID's (`tools/sidtrace.py`). A **representative** step gives the hand C's
addressing cost without its semantics; it is measured, never run.

| Step | Kind | Rewrite | Evidence |
|---|---|---|---|
| identity returns | exact | a read of a return global (`rv*`) that holds a value the reader has uses that value; a call of a function that returns an argument is followed by the argument | value flow over each function to a fixed point over the call graph; a call that never returns (`trap`) ends its path |
| `rd()` direct | representative | `rd()` indexes the largest data region it dispatches to, as the hand C indexes its music array | |
| `wr()`, `codechk()`, guards direct | representative | `wr()` stores into that region, `codechk()` returns 0, page-1/I-O guards are 0 | a guard is a condition of one variable true for exactly 1 and `$D0`-`$DF` |
| zero-page pointer reads take Y | exact | `rd(P + y)`, `P` a zero-page pointer `R[i] \| (uint16_t)R[i + 1] << 8` or a variable holding it while its cells are unchanged and `y` a byte, calls one helper per pointer with `y`, the 6502's `(zp),Y` | value flow with zero-page cells as locations |
| cells stored once with a constant | exact | reads of a cell the run writes once, by a top-level constant store in an init-only function, after the store and in play-only functions, are the constant; then `rd()`/`wr()` of a constant, or a constant plus a byte, index the region array holding every address it can reach (`wr()` where the bitmap marks each as data) | PSID run: init write counts, play writes and values after init of every region cell |
| cells play never writes | exact | reads in functions play reaches of a cell play never writes are its value after init; in a function init also runs, only where that function's reads of it in init follow init's last write to it; then as above | PSID run; init of a `-fno-inline` baseline build, each region read attributed to its function by PC |
| byte switch | exact | a switch on an expression of one byte switches on the byte, each case labelled with the bytes that select it (the self-modified branch offset's sign-extended 16-bit target) | the condition evaluated for all 256 bytes |
| dead init loops | exact | a loop of an init-only function is removed when, once reads after it of cells only it writes are folded to their values, the run never reads its stores and it touches no I/O; it must not return, jump other than to the statement after it, or assign locals used outside it | run of a C build with each candidate loop storing its number in a marker while it runs, observing every data load and store of the file's objects |
| alike branches merged | exact | an if / else if / else chain whose arms differ only in integer literals and their own locals' names becomes one body selecting each literal by the conditions; a function the build kept out of line is kept so | structural match of the arms' ASTs |

## Data accounting

For each code-kept region (`C*`) of the generated header, from jennings coverage of init and the
play calls: its bytes; instruction bytes (the listing's instructions); of those, bytes the run
also reads or writes as data (operands, self-modified or read); non-instruction bytes the run
accesses; bytes neither executed nor accessed; and the `C*_DATA` bitmap's bytes in the ELF. The
hand-equivalent needs the accessed bytes: operands accessed plus data accessed.

## Results

`.text` bytes after each step, cumulative, with its delta; every exact step verified on every
tune (`--verify`, default song over its HVSC length). deity-informant 0.17.0, llvm-mos SDK
v23.4.0, `-Os`.

| Step | Kind | Commando | Incoherent | Automatas | Ghouls | Emomyst |
|---|---|---|---|---|---|---|
| baseline | exact | 2511 | 3421 | 4206 | 7091 | 7777 |
| identity returns | exact | 2511 (+0) | 3421 (+0) | 4206 (+0) | 7091 (+0) | 7700 (-77) |
| rd() direct | representative | 2393 (-118) | 2839 (-582) | 3435 (-771) | 6923 (-168) | 7635 (-65) |
| wr(), codechk(), guards direct | representative | 2393 (+0) | 2839 (+0) | 3435 (+0) | 6012 (-911) | 7023 (-612) |
| zero-page pointer reads take Y | exact | 2327 (-66) | 2784 (-55) | 3435 (+0) | 5947 (-65) | 6422 (-601) |
| cells stored once with a constant | exact | 2327 (+0) | 2784 (+0) | 3435 (+0) | 5947 (+0) | 6296 (-126) |
| cells play never writes | exact | 2317 (-10) | 2784 (+0) | 3390 (-45) | 5947 (+0) | 6109 (-187) |
| byte switch | exact | 2317 (+0) | 2780 (-4) | 3390 (+0) | 5947 (+0) | 6028 (-81) |
| dead init loops | exact | 2317 (+0) | 2780 (+0) | 3390 (+0) | 5947 (+0) | 5690 (-338) |
| alike branches merged | exact | 2317 (+0) | 2780 (+0) | 3390 (+0) | 5947 (+0) | 5582 (-108) |
| hand | | 1534 | 2721 | 2773 | 3670 | 4608 |
| residual | | +783 | +59 | +617 | +2277 | +974 |

Code-kept regions, bytes:

| Tune | Region | Origin | Bytes | Instruction | Operands accessed | Data accessed | Untouched | Bitmap |
|---|---|---|---|---|---|---|---|---|
| Commando | C0 | $139E | 2136 | 0 | 0 | 1734 | 402 | 0 |
| Incoherent | C0 | $0C4F | 5826 | 0 | 0 | 5710 | 116 | 0 |
| Automatas | C0 | $1019 | 135 | 108 | 24 | 21 | 6 | 0 |
| Automatas | C1 | $1129 | 380 | 380 | 39 | 0 | 0 | 0 |
| Automatas | C2 | $12BF | 261 | 243 | 12 | 6 | 12 | 0 |
| Automatas | C3 | $14D4 | 576 | 264 | 9 | 206 | 106 | 0 |
| Automatas | C4 | $177F | 6193 | 0 | 0 | 5632 | 561 | 0 |
| Ghouls | C0 | $0000 | 256 | 1 | 0 | 123 | 132 | 0 |
| Ghouls | C1 | $2980 | 24206 | 2789 | 72 | 12309 | 9108 | 2226 |
| Emomyst | C0 | $1000 | 5282 | 2173 | 85 | 2157 | 952 | 403 |

Data sections, bytes:

| | Commando | Incoherent | Automatas | Ghouls | Emomyst |
|---|---|---|---|---|---|
| generated | 2143 | 5852 | 7610 | 30288 | 5768 |
| after the last step | 2143 | 5841 | 7602 | 28058 | 5340 |
| hand | 1963 | 5839 | 6832 | 30523 | 2862 |
