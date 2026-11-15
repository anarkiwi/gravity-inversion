# Residue census

Every construct of a generated translation either translates an original instruction or is
**residue**: a place where the lift kept the 6502's machine model instead of the program's
meaning. `tools/residue.py` lists each residue **site** `(kind, address, detail)` of a
translation. Detection reads the C's clang AST, the generated header and the original PSID
(jennings coverage of init, sidtrace's C64 for the memory init leaves); cgen's `info` is read
only to attribute a site to the original's addresses.

    python3 tools/residue.py ghouls fuzz-rti-0 path/to/tune.sid [--kind D1 ...] [--json]
    python3 tools/residue.py --write-manifest

A target is a tune, a fuzz player `fuzz-<style>-<seed>` (`tools/fuzzplayer.py`) or a PSID path.

## Kinds

Tune code is every function of the `.c` with an entry in `info["provenance"]["functions"]`,
and `cgen_init` (the PSID's init address); cgen's runtime (`rd`, `wr`, `rdCj`, `oob_*`,
`codechk`, `cgen_var`, ...) is not. A site's address is exact where the kind's definition
gives one; otherwise it is the original entry of the tune function holding the construct,
and the detail names the construct by its C text.

An access's **interval** is the hull of the addresses its C expression can take, each variable
over its C type: a subscript of a header array (`R*`, `C*`, a region slice, `CGEN_IX`) its base's
origin plus the index's interval, within the held array holding the base; `rdCj(p, y)` the
interval of `p + y` within span `Cj`; `rd`/`wr`/`oob_rd`/`oob_wr` that of their address; each
`CGEN_CP_DST` entry its destination and `CGEN_CP_LEN` bytes. The held arrays are
`CGEN_ARRAYS`.

| Kind | Site | Address | Detail |
|---|---|---|---|
| D1 code held as data | a run of held array bytes that are instruction bytes: of the lift's instructions (`info["starts"]`, lengths from the memory init leaves) or of those jennings sees init execute for any song | the run's first byte | array, run, the accesses whose interval meets it |
| D2 unwritten memory held | a run of held array bytes outside the loaded image and the I/O page that init writes for no song and no store's interval meets | the run's first byte | array, run, the accesses whose interval meets it |
| D3 hull gaps | not detectable: needs each access's value set, which `info` lacks | | |
| D4 replayed init | a non-empty `CGEN_IO`, `CGEN_LIT` or `CGEN_CP` table: init's effect replayed, not translated | init's entry | entry count |
| D5 operand state | a promoted variable (`info["state"]["vars"]`) whose byte belongs to one of the lift's instructions | the byte | lvalue, opcode or operand of which instruction, the `zp`/`abs` stores (and RMW, legal or not) whose operand is the byte |
| A1 address dispatch | a call of `rd`, `wr`, `rdCj`, `oob_rd`, `oob_wr`, `codechk` or `cgen_rd` | function entry | the call |
| A2 machine addresses as values | a literal spelled `CODE_ORG` or `IMG_ORG`: one site per switch whose condition or case labels hold one, else per (function, use, address) of the `+`/`-` expression around it | a switch: the printed switch (`info["provenance"]["switches"]`) whose computed jump's targets (`info["jumps"]`) hold the cases with the fewest others, if one; else function entry | `switch over n code addresses`, or `compared`/`stored`/`argument`/`value` and the address |
| A3 status register as a value | a maximal `\|` expression one of whose operands is the constant `0x30` and another a `<<` | function entry | the expression |
| C1 traps | a `trap(k)` call | `info["traps"][k]`'s address; function entry if untabled | `trap(k)` and its deopt kind |
| C2 unstructured control | a goto edge of `info["provenance"]["gotos"]` that is no edge of the tune's construct map (`tunes/<tune>/constructs.json` `goto` entries from lifted code; any goto for a PSID or fuzz player); more functions than the hand translation (`info["structure"]["functions"]`); an `io_write` whose address starts with a constant voice register of the tune (`VOICE_REGS`) | the edge's source; play's entry; function entry | `goto` target; function counts; register |
| C3 lift complexity | decision points that are no original branch: an atom of a condition naming a promoted opcode cell; an `if` or `?:` one of whose arms is a dispatcher call (a bounds test before it); and per function, the atoms beyond its original branches | the cell; function entry | the cell; `bounds test before dispatch`; `n decision points, m original branches` |
| K1-K6 | `tools/costs.py`'s kinds ([costs.md](costs.md)) | function entry | the construct |

C3's atoms are the leaves of `&&`, `\|\|` and `!` (through casts and a comma's value) of each
`if`, `while`, `do`, `for` and `?:` condition, non-constant, and one per `switch`; a test with
an arm that starts with `trap` has none (C1 lists it). A function's original branches are the
conditional branches and computed jumps (`info["jumps"]`) among its `pcs`; each atom stands
for at least one of them (a folded function's atom for each copy's or a shared one), so a
function with more atoms than branches has at least the difference of its own.

cgen's traps take only `k`: no C restores machine state, so every A2 and A3 construct counts.

## Manifest

`tests/residue_manifest.json` holds every tune's and fuzz player's sites by kind (`sites`),
a cause per group and kind (`causes`: the tune, or `fuzz-<style>` over its seeds) and the
pins it was made on. `tests/test_residue.py` requires each target's detected sites to equal
its manifest entry: a new site fails as a regression, a removed one until the manifest
records the improvement. `--write-manifest` rewrites the sites for the installed pins,
keeps the cause of each (group, kind) still present, and lists the new ones to write; a pin
bump's change to the lift is then the manifest's diff.

Residue of a fuzz player by construction: init's song guard (a trap for `A` outside the PSID
header's songs, in every target), the `jam` motif's JAM trap, the `rmw` motif's ISC immediate
and the `smc` style's operand cells (real state). The manifest's causes name the rest, which
are lift residue.

## Attribution `info` lacks

- Each printed operation's original instruction (a C source range to pc map): A1, A2 (other
  than switches), A3, C3 and K1-K6 sites name their function's entry.
- Each access's address set or hull (`Program.addr_sets`, `reach`, `proven`, `bounded`):
  D1 and D2 name the C accesses whose type-bounded intervals meet a run, D2 counts a byte
  unwritten only when no such interval meets it, D3 is not detected, and D5 names a cell's
  indexed and indirect stores only as such.
- Each folded copy's instructions (`copy_pcs`): C3 compares a folded function's atoms with
  all its copies' branches.
