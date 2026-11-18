# Ghidra reference decompilation

`tools/ghidra_ref.py` decompiles each target (tune or fuzz target) with Ghidra headless, as a
reference beside the lifter's C. The PSID's image is loaded at its load address with every
other address of the 64 KiB space as uninitialized RAM, so references print as Ghidra's
`DAT_xxxx` globals.

    python3 tools/ghidra_ref.py [targets...] [--out out/ghidra] [-j 8] [--image TAG]

With no targets it runs every tune and fuzz target (`residue.TUNES + residue.FUZZ`), one
container per target, `-j` in parallel.

## Steps

1. Seeds: the PSID's init and play (a zero play is none), and the original entry address of
   each function of the lifter (`info["provenance"]["functions"]` of `tools/transliterate.py`).
2. `tools/ghidra/GhidraRefSeed.java` (pre-script): fills the address space with uninitialized
   blocks, creates a function at each seed in initialized memory, and enables the
   *Decompiler Parameter ID* analyzer. Auto-analysis then finds the rest (`JSR` targets).
3. `tools/ghidra/GhidraRefDecompile.java` (post-script): decompiles every function, commits
   each decompiled signature and decompiles again until no signature changes, so callers and
   callees agree; writes per function its C, callees and the names it uses undeclared.
4. The driver writes the outputs below.

## Outputs

All under `--out` (gitignored `out/ghidra/`; tune-derived, never committed):

| File | Content |
|---|---|
| `ghidra.h` | Ghidra's types and operations (`tools/ghidra_types.py`): sized `undefinedN`/`byte`/`word`..., `code`, `CONCAT`, `SUB`, `ZEXT`, `SEXT`, `CARRY`, `SCARRY`, `SBORROW`, `halt_baddata`, clang's `-Wint-conversion` off |
| `<target>/<entry>.c` | one function's C as Ghidra prints it; `<entry>` is 4 lowercase hex digits |
| `<target>.c` | `#include "ghidra.h"`, Ghidra's composite types, an `extern` per global, a prototype per function, then the body of each decompiled function |
| `<target>.json` | `{"functions": {name: {"entry", "seed", "calls", "pcs", "ok", "error"}}, "unseeded": [...]}` |

`seed` is `psid`, `lifter` or `ghidra` (found by analysis); a function both the PSID and the
lifter seed is `psid`. `calls` are the entries of its callees (`Function.getCalledFunctions`,
and a thunk's target); `pcs` the addresses of the instructions of its body.
A function Ghidra cannot decompile has `ok` false and the decompiler's `error`, and only its
prototype in `<target>.c`. `unseeded` lists seeds Ghidra holds no function at.

### Declarations

Each name a body uses that neither it nor the program declares gets an `extern`, typed from
the decompiler's high variable (else its high symbol, else the listing's data): of all
functions' typings of a name, the widest, then one called through (`code *`, an operand of
`CALLIND`). A call to an address with no function (`func_0x...`) is declared without a
prototype, returning its call's output type.

### Token rewrites in `<target>.c`

The bodies are rendered from the decompiler's token markup as `PrettyPrinter` does, with
these rewrites so they parse; `<target>/<entry>.c` keeps Ghidra's text:

| Ghidra prints | `<target>.c` has | Where |
|---|---|---|
| `x._o_s_` (bytes `o..o+s-1` of `x`) | `(*(undefineds *)((undefined1 *)&x + o))` | partial access of a variable |
| a function's name as a variable | `<name>_data`, an `undefined1` array when its address is used | code read or written as data (self-modifying code) |
| `code` as a value type | `undefined1` | an instruction byte as data |

## Comparison

    python3 tools/ghidra_ref.py --compare [targets...] [--ours LABEL=DIR]...

runs wayward-eagle's readability census (`wayward_eagle.census`, docs in wayward-eagle's
`docs/census.md`) on Ghidra's `<target>.c` and on ours, and writes `compare.json` under
`--out`. Each `DIR` (`{target}` expands to the target's name) holds `<target>.c` with its
headers and `<target>.provenance.json`, `{function: {"entry", "pcs"}}` of the lift that made
it (deity-informant's `info["provenance"]["functions"]`); with no `--ours`, the lifter's C and
provenance are written to `out/ghidra/ours/`.

Functions are matched by original entry:

- ours: a function and the reducer's pieces of it (`f1_x1`, `f12_1`: the longest lifter
  name a function's name extends by `_<suffix>`); runtime helpers with no entry are
  `unmatched`;
- Ghidra's: its function at the entry and every function it reaches by calls through
  functions at no lifter entry, which the lifter inlined.

Per target and label, `compare.json` holds the census counts per class in total and per
entry (cognitive complexity summed), `missing` (instructions ours translates at an entry that
Ghidra's functions there do not hold), and `better`: each `(label, entry, class, Ghidra's,
ours)` where Ghidra's C has fewer sites than ours and none are missing.

## Image

`docker/ghidra.Dockerfile`: a cached stage downloads Ghidra; the run stage installs
deity-informant from PyPI at `DEITY_INFORMANT` (build arg) and emits its 6510 SLEIGH into
Ghidra (`deity-informant emit-sleigh`), world-readable. The driver builds it when missing as
`gi-ghidra:<deity-informant pin in requirements.txt>`; the scripts in `tools/ghidra/` are
bind-mounted read-only.

    docker build -f docker/ghidra.Dockerfile --build-arg DEITY_INFORMANT=<pin> -t gi-ghidra:<pin> docker
