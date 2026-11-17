# Residue census

Every construct of a generated translation either translates an original instruction or is
**residue**: a place where the lift kept the 6502's machine model instead of the program's
meaning. `tools/residue.py` lists each residue **site** `(kind, address, detail)` of a
translation, and apart from them the traps that guard invalid input (kind `C1g`,
informational). Detection reads the C's clang AST, the generated header and the original PSID
(jennings coverage of init, sidtrace's C64 for the memory init leaves); cgen's `info`
attributes a site to the original's addresses and classifies each trap (`trap_info`).

    python3 tools/residue.py ghouls fuzz-rti-0 path/to/tune.sid [--kind D1 ...] [--json]
    python3 tools/residue.py ghouls --cells
    python3 tools/residue.py --write-manifest

A target is a tune, a fuzz player `fuzz-<style>-<seed>` (`tools/fuzzplayer.py`) or a PSID path.
`--cells` lists each promoted variable on an instruction byte with its role and the kind that
counts it. Code-size constructs (K1-K6) are no machine model and are reported by
`tools/costs.py` ([costs.md](costs.md)), not here.

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

A **cell** is a promoted variable (`info["state"]["vars"]`) on a byte of one of the lift's
instructions (`info["starts"]`, lengths from the memory init leaves). Its role is the byte's
in the instruction: the opcode, an immediate, a **transfer** operand (a branch offset, a `JMP`
or `JSR` operand) or an **address** operand (any other mode). It is **reached by address**
when `cgen_var` finds it (a case of its switch). A cell's own value is program state; it is
residue only through the role below. An address operand cell is a pointer variable, the
program's meaning: the accesses through it count where they are printed as residue.

| Kind | Site | Address | Detail |
|---|---|---|---|
| D1 code held as data | the held array bytes that are instruction bytes: of the lift's instructions or of those jennings sees init execute for any song; one site per array and set of accesses whose interval meets a run of them | the first byte | array, runs, bytes, the accesses |
| D2 unwritten memory held | the held array bytes outside the loaded image and the I/O page that init writes for no song and no store's interval meets; one site per array and set of accesses | the first byte | array, runs, bytes, the accesses |
| D3 hull gaps | not detectable (*Attribution `info` lacks*) | | |
| D4 replayed init | a non-empty `CGEN_IO`, `CGEN_LIT` or `CGEN_CP` table: init's effect replayed, not translated | init's entry | entry count |
| A1 address dispatch | a call of `rd`, `wr`, `rdCj`, `oob_rd`, `oob_wr`, `codechk` or `cgen_rd`; a subscript by a machine address; a cell reached by address of an instruction with no transfer operand cell | function entry, or each instruction whose address operand cells the call's address reads; the cell | the call or subscript; `cell reached by address` |
| A2 machine addresses as values | a literal spelled `CODE_ORG` or `IMG_ORG`: one site per switch whose condition or case labels hold one, else per (function, use, address) of the `+`/`-` expression around it; each instruction with a transfer operand cell and no such switch | a switch: the printed switch (`info["provenance"]["switches"]`) whose computed jump's targets (`info["jumps"]`) hold the cases with the fewest others, if one; else function entry; the instruction | `switch over n code addresses`, `compared`/`stored`/`argument`/`value` and the address, or `transfer operand of $pc: cells` |
| A3 status register as a value | a maximal `\|` expression one of whose operands is the constant `0x30` and another a `<<` | function entry | the expression |
| C1 translatable deopt | a trap of `info["traps"]` whose `trap_info` is neither C1r nor C1g, or untabled | the trap's pc; function entry if untabled | `trap(k)` and its kind |
| C1r redundant guard | a trap whose `trap_info` has `implied_by` set or is `proven`, or one `IMPLIED` records | the trap's pc | `trap(k)`, kind, what implies it |
| C2 unstructured control | a goto edge of `info["provenance"]["gotos"]` that is no edge of the tune's construct map (`tunes/<tune>/constructs.json` `goto` entries from lifted code; any goto for a PSID or fuzz player); a function of no procedure of the original; an `io_write` whose address starts with a constant voice register of the tune (`VOICE_REGS`) | the edge's source; the function's entry; function entry | `goto` target; function; register |
| C3 lift complexity | decision points that are no original branch: an atom of a condition naming an opcode cell that is no flag; and per function, the atoms beyond its original branches | the cell; function entry | the cell; `n decision points, m original branches` |
| C1g guard on invalid input (informational) | a trap every guard of which traps input the original's meaning does not define (below) | the trap's pc | `trap(k)` and its kind |

A **subscript by a machine address** is a subscript of held array `X` whose index (with the
offset of a pointer base `X + o`) is a sum with a non-constant term and the term `- X_ORG`:
the C forms the 16-bit machine address and converts it to an offset of `X` (`C1[v7 + v6 -
C1_ORG]`, `CGEN_IX(C1, p - C1_ORG, y)`), where an object offset (`R0[0x22 + v5]`) is none. A
dispatcher call whose address reads an address operand cell (`rdC2(V1_f3D[v1] << 8 |
V1_f3C[v1], y)`) translates that cell's instruction and is counted there, at its pc and per copy.

A function is a **procedure** of the original when its entry is init, play or the target of a
`JSR` of the lift's instructions (its operand in the loaded image or as init leaves it, or each
target `info["jumps"]` gives for a `JSR` whose operand the program writes), or when it is an
instance function (`index`: the copies of an unrolled body, [constructs.md](constructs.md)'s
*Rule*). A function entered at a `JSR` whose operand the program writes is the procedure that
dispatches over its targets when every `JSR` reaching it (`calls`) has the same target set
(`info["jumps"]`). A function whose `pcs` hold an `RTS` and that the C calls only in tail
position (the call is returned, or ends the body, or a `return` of no computed value or a
`break` of a statement in tail position follows it, through compound statements, `if` and
`switch` bodies, cases and labels) is a procedure the original enters by a tail call: the C
returns where its `RTS` does, whether a `JMP`, a computed `JMP`'s arm, a branch or a
fall-through enters it. Any other function (a shared tail the C does not call in tail
position, or one that returns by no `RTS` of its own) cuts the original's procedure where it
has no boundary.

An opcode cell is a **flag** when `info` bounds the values the program writes to it: an
`opcode` guard of `trap_info` checks it (`addr`), its `ok` the values the C takes as the
program's own and a trap (C1) the rest. Testing a flag is the original's decision, an atom
and an original branch of its function. An opcode cell of unknown values is a site: the C
would interpret opcodes.

C3's atoms are the leaves of `&&`, `\|\|` and `!` (through casts and a comma's value) of each
`if`, `while`, `do`, `for` and `?:` condition, non-constant, and one per `switch`; a test with
an arm that starts with `trap` has none (C1 lists it), nor one with an arm that is a
dispatcher call (A1 lists the call). A function's original branches are the conditional
branches and computed jumps (`info["jumps"]`) among its `pcs`; each atom stands for at least
one of them (a folded function's atom for each copy's or a shared one), so a function with
more atoms than branches has at least the difference of its own.

cgen's traps take only `k`: no C restores machine state, so every A2 and A3 construct counts.

### Traps

Each trap `k` of the C is one site (however many `trap(k)` calls print), classified by
`info["trap_info"][k]` in this order:

1. **C1r** when its `implied_by` is set (other traps or constants already keep every guard's
   register inside what it lets pass) or it is `proven` (the lift's values at every guard lie
   inside): the guard should not exist. `IMPLIED` adds the traps whose implication trap_info
   does not find, each with its derivation.
2. **C1g** when every guard traps input the original's meaning does not define:
   - `entry`: an entry register outside its declared values (init's `A` outside the songs);
   - `index` whose every table's extent ends the data (`by` `loaded`, `object`, `code` or
     `column`): past it the original reads or writes beyond its data (the loaded program, the
     next object, code, a byte another column reads);
   - `objchk`, `window`: a pointer outside the object it points into, or into a zone's window;
   - `jam`: the translation of `JAM`, which stops the machine.
3. **C1** otherwise: `opcode`, `codechk`, `path`, `ind`, `transfer`, `return`, `txs`,
   `decimal`, `sp`, `bank`, `stack`, `zone`, `entry_sp`, `port`, an `index` whose table ends at
   a `target` or `wrap`s, a `mixed` trap with any guard of these, or a trap without
   `trap_info`: a deopt the lift could translate.

## Manifest

`tests/residue_manifest.json` holds every tune's and fuzz player's residue sites by kind
(`sites`), its C1g sites (`informational`, outside the residue total), a cause per group and
kind (`causes`: the tune, or `fuzz-<style>` over its seeds) and the pins it was made on.
`tests/test_residue.py` requires each target's detected sites to equal its manifest entries:
a new site fails as a regression, a removed one until the manifest records the improvement.
`--write-manifest` rewrites the sites for the installed pins, keeps the cause of each (group,
kind) still present, and lists the new ones to write; a pin bump's change to the lift is then
the manifest's diff.

Informational in every fuzz player by construction: init's song guard (a trap for `A`
outside the PSID header's songs), and the `jam` motif's JAM trap. The `rmw` motif's ISC
immediate and the `smc` style's operand cells are immediates: state, no site. The manifest's
causes name the rest, which are lift residue.

## Undercounted and accepted constructs

- A data address stored as a pointer's seed (`R0[0] = (uint8_t)(R4_ORG)`, `S_C9 =
  (uint8_t)(R63_ORG + 0xAB)`) is no site of its own: the pointer counts where it is used (a
  subscript by a machine address, a dispatcher call). Only
  `CODE_ORG`/`IMG_ORG` literals are A2. A dispatcher call with `Rk_ORG + c` in its address
  (`rdC0(R3_ORG + 0x52B, v)`) is one A1 site as a call.
- A computed device register address (`io_write(0xD402 + v, ...)`, a register of a voice
  indexed by the voice) is accepted: the 6502 writes the SID through indexed addressing and
  the C keeps one I/O write per original store. Only a voice register written at a constant
  address in a voice function (C2, `VOICE_REGS`) counts.
- A redundant guard trap_info does not find implied is C1g unless `IMPLIED` records it.

## Attribution `info` lacks

- Each printed operation's original instruction (a C source range to pc map): A1 (other than
  calls through address operand cells, and cells reached by address), A2 (other than
  switches and transfer operand cells), A3, C3 sites name their function's entry. A function
  whose instructions all lie in another's (`pcs`) is inlined there; the other's A1 and A3
  sites equal to its own up to the names of locals (`v<n>`) are its copies, counted once.
  Equal calls within one function may be one instruction duplicated or several; each counts.
- Each access's address set and the instruction of each C access (`Program.addr_sets`,
  `reach`, `proven`, `bounded`; `info["ptr_sets"]` gives pointer word sets by instruction,
  with no C access to join them to): D1 and D2 name the C accesses whose type-bounded intervals
  meet a run, D2 counts a byte unwritten only when no such interval meets it, and D3 (held
  bytes inside an access's hull that its address set lacks) is not detected.
- Each folded copy's instructions (`copy_pcs`): C3 compares a folded function's atoms with
  all its copies' branches.
- The value set of an opcode cell no `opcode` guard checks: such a cell's test is a C3 site
  although the lift may know its values (they are no `info` key; the C's own stores to a cell
  `cgen_var` reaches are not all its writes).
- Which instruction consumes a pushed status byte (a push to pull pairing of the stack, and
  each A3 expression's `PHP`, `BRK` or interrupt): A3 counts every composed status byte,
  a `PLP`/`RTI` round trip and one the program reads as data alike.
