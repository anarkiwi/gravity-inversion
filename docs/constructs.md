# Construct maps

`tunes/<tune>/constructs.json` maps each machine-code construct the hand translation deals with
deliberately to the C form it takes, so translation decisions can be read rather than asked. Each
map is checked against the original by `tools/constructs.py` (jennings' static listing and its run
coverage of init and play) and against the generated C by `tests/test_constructs.py`.

## Entry

| Field | Meaning |
|---|---|
| `id` | `function.label` for gotos, otherwise a name |
| `kind` | see below |
| addresses | `$xxxx`: the 6502 instructions, edges `[from, to]`, cells |
| `hand` | the hand C's function and form or label |
| `why` | the 6502 control or data flow that makes the form the right one |
| `lift` | the form the generated C must take (the rule below) |
| `hand_differs` | where the hand C departs from the rule, and how |
| `covered` | `false` when the song's run never takes the construct; `uncovered` says what does |

## Kinds

| Kind | 6502 | C |
|---|---|---|
| `loop` | back edge `back` → `head` | a loop |
| `call` | `JSR` `sites` → `target` | a function called per site |
| `fall_call` | `JSR` sites and a fall-through (`falls`) into `target` | one function; the fall-through is a call then return |
| `tail_call` | `JMP`s, branches or fall-throughs into a `JSR` target | a call then return |
| `unrolled` | `copies` of one body, operands at strides (`inserted`: instructions only some copies have) | one function with an instance index |
| `smc` | `writer` stores into `cell`, a byte of `reader` | a variable |
| `goto` | `edges` with no structured form | a goto per edge |
| `join` | edges rejoining at the post-dominator of their branch | if/else, no goto |
| `short_circuit` | `branches` sharing `target` | one `&&`/`\|\|` condition |
| `switch` | a computed `dispatch` (written operand, branch offset or table) | a switch |
| `multi_entry` | entries setting a register before a shared tail | one function taking that value |
| `region`, `inline_repeat`, `unrolled_stores`, `per_voice_paths` | code the hand C names or folds for readability | not required of the generated C |

## Rule

An edge is a goto exactly when it has no structured form: not a fall-through, an if/else arm
rejoining at its post-dominator, a short-circuit chain, a loop's single exit or back edge, or the
procedure exit. An edge into a real procedure (a `JSR` target) is a tail call, not a goto. A
function is formed only for a procedure, an unrolled class or a register-parameterised shared tail;
naming other single-entry regions is a readability choice.
