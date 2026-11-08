# Construct maps

`tunes/<tune>/constructs.json` maps each machine-code construct the hand translation deals with
deliberately to the C form it takes, so translation decisions can be read rather than asked. Each
map is checked against the original by `tools/constructs.py` (jennings' static listing and its run
coverage of init and play, and of play alone) and against the generated C by
`tests/test_constructs.py`. A construct only init runs is not in the generated C: init runs on the
host and the state it leaves is in the `.h`, so none of its instructions are translated.

## Entry

| Field | Meaning |
|---|---|
| `id` | `function.label` for gotos, otherwise a name |
| `kind` | see below |
| addresses | `$xxxx`: the 6502 instructions, edges `[from, to]`, cells |
| `hand` | the hand C's function and form or label |
| `why` | the 6502 control or data flow that makes the form the right one |
| `lift` | the form the generated C must take (the rule below) |
| `hand_differs` | where the hand C departs from the rule, and how; the rule wins |
| `transform` | the lift step below that decides the form (`null`: readability, not lifted) |
| `covered` | `false` when the song's run never takes the construct; `uncovered` says what does |

## Kinds

| Kind | 6502 | C |
|---|---|---|
| `loop` | back edge `back` → `head` | a loop |
| `call` | `JSR` `sites` → `target` | a function called per site |
| `fall_call` | `JSR` sites and a fall-through (`falls`) into `target` | one function; the fall-through is a call then return |
| `tail_call` | `JMP`s, branches or fall-throughs into a `JSR` target | a call then return |
| `multi_entry_loop` | a cycle entered by `edges` at two points, `back` -> `head` (`copies`: the same loop in other instances) | one loop with an entry selector |
| `unrolled` | `copies` of one body, operands at strides (`inserted`: instructions only some copies have) | one function with an instance index |
| `smc` | `writer` stores into `cell`, a byte of `reader` | a variable |
| `goto` | `edges` with no structured form | a goto per edge |
| `join` | edges rejoining at the post-dominator of their branch | if/else, no goto |
| `short_circuit` | `branches` sharing `target` | one `&&`/`\|\|` condition |
| `switch` | a computed `dispatch` (written operand, branch offset or table); `edges`: arms leaving for the join after it | a switch; those arms `break` |
| `multi_entry` | entries setting a register before a shared tail | one function taking that value |
| `region`, `inline_repeat`, `unrolled_stores`, `per_voice_paths` | code the hand C names or folds for readability | not required of the generated C |

## Rule

An edge is a goto exactly when it has no structured form: not a fall-through, an if/else arm
rejoining at its post-dominator, a short-circuit chain, a loop's single exit or back edge, or the
procedure exit. An edge into a real procedure (a `JSR` target) is a tail call, not a goto. A
function is formed only for a procedure, an unrolled class or a register-parameterised shared tail;
naming other single-entry regions is a readability choice.

## Lift procedure

The map's entries group by the fact that decides their form, and each fact only decides correctly
on the graph the earlier steps leave: a short-circuit chain must be one condition before
post-dominators can tell a structured join, loop back edges and exits must be gone before the
acyclic structure exists, and tail calls must be cut before either. The steps, in order:

| Step | Facts (inputs) | Transform | Entries |
|---|---|---|---|
| T1 | stores into instruction bytes (def-use); target sets of computed transfers (constant tables, written operands, branch offsets) | code cells become variables; a computed transfer with a known target set becomes a switch | 15 |
| T2 | procedure entries: `init`, `play`, every `JSR` target; immediate loads before a jump into a shared tail | an edge into an entry from another procedure (`JMP`, branch, fall-through) becomes a call then return; register-preset entries into one tail become one function taking the register | 6 |
| T3 | copy classes: consecutive, chained copies equal up to operand deltas affine in the copy index | one function `f(k)`; per-copy cells and constants become state and tables indexed by `k` | 6 |
| T4 | dominator tree per procedure (back edges, including fall-through back edges of rotated loops entered at their test) | natural loops: back edge `continue`, the exit to the loop's follow `break`, exits to the procedure's exit `return` | 24 |
| T5 | chains of conditional branches sharing a target, each falling into the next | one `&&`/`\|\|` condition, to a fixpoint | 4 |
| T6 | post-dominator tree of the reduced graph | a branch whose arms rejoin at its immediate post-dominator becomes if/else; a multiway branch on one selector a switch | 6 |
| T7 | none | every remaining edge is a goto: a branch past a joined block, an entry part-way into a shared sequence, a loop's second exit | 21 |

Four entries are readability choices the hand C makes (naming a single-entry region, extracting
repeated inline code) and are not lifted. Seventeen entries record where the hand C departs from
the procedure (`hand_differs`): a loop back edge written as a goto, nested copy loops written as
one, a shared suffix outlined as a helper, gotos into a subroutine, gotos at joins with a
structured form, and flags or outlined helpers where an edge has none. There the rule wins: the
generated C must take the rule's form (`tests/test_structure.py` compares its gotos with the map's
`goto` entries), and the hand C's form is a readability choice that costs size.

The number of passes the facts need is proved in [passes.md](passes.md).

## Opcodes

`tools/opclass.py` lifts each of the 256 opcodes alone and reads its P-code for the facts the
steps consume; `tests/test_opclass.py` checks the result against jennings' mnemonic tables. The
rules cover every opcode by role, not by name:

| Role | Opcodes | Step |
|---|---|---|
| store or read-modify-write in a memory mode | 88, legal and illegal (`SAX`, `SH*`, `TAS`, `SLO`/`RLA`/`SRE`/`RRA`/`DCP`/`ISC`) | T1 cell writer |
| `JMP (ind)`, `RTS`, `RTI`, `BRK` | 4 | T1 computed transfer |
| `PHA`, `PHP` / `PLA`, `PLP` | 2 / 2 | T1 pushed data / T2 pulled data |
| `JSR`, `BRK` / `RTS`, `RTI` | 2 / 2 | T2 call / return |
| `TXS`, `TAS`, `LAS` write SP; `TSX`, `TAS`, `LAS` read it | 3 / 3 | T2 stack switch / read |
| conditional branches, `JMP abs` | 9 | T4 back edge, T5 condition (flag per branch) |
| every flag writer | 177 | T5 condition producer |
| `ADC`, `SBC`, `RRA`, `ISC`, `ARR` | 32 | arithmetic decided by D |
| operand-bearing instructions writing no register or memory (`NOP`, `BIT`, `CMP`, `CPX`, `CPY`) | 37 | skip: entered part-way, its bytes decode as other instructions |
| `JAM` | 12 | trap |

The five tunes and the Commando permutations execute 104 opcodes. For the roles only unobserved
opcodes fill, `tools/fuzzplayer.py` adds players (`tests/test_fuzz.py`): RTI dispatch through a
table, BRK through a written vector, a TXS non-local exit, a JSR with an inline argument the
callee skips by adjusting its return address, a BIT skip into an instruction's operand, decimal
arithmetic, ISC/DCP on cells and counters, BIT with BVC/BMI chains, per-voice (zp,X) pointers, and
a JAM guarded by data. Each player must also translate to the same C when assembled at another
origin. The unstable opcodes (`ANE`, `LXA`, `SH*`, `TAS`) are lifted as the machine model defines
them; their hardware results vary, so they are not fuzzed.
