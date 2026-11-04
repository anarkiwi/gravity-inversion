# Minimum number of passes

The lift procedure (constructs.md) decides each idiom's C form from facts about the program. This
proves how many passes those facts need: five for the idioms of the five tunes, four for a
program without multi-entry loops. `tests/test_passes.py` and `tests/test_constructs.py` measure
the facts each step of the proof rests on.

## Model

A *fact* is a relation over instruction addresses, edges or cells. A *pass* evaluates a set of
facts to their least fixpoint, or computes them in one traversal, with every fact of earlier
passes fixed. Within a pass a fact may depend on facts of the same pass only monotonically:
adding a tuple to one may only add tuples to the other, so evaluation order cannot retract a
result. A fact that is universally quantified over another (*every* path, *every* copy, *no*
other entry) is not monotone in it and must be computed after it is complete, in a later pass
(the stratification of Datalog with negation). A traversal visits each node once in one order: a
fact that needs all of a node's predecessors first (forward) and one that needs all of its
successors first (backward) cannot be completed in one traversal of a graph with a cycle.

## Passes

| Pass | Facts | Map kinds it decides |
|---|---|---|
| P1 | reachable code; value sets (intervals, known bits, table tags); code cells and their stored values; computed-transfer targets; stack heights and pushed values; JSR and BRK targets | `smc`, `switch`, procedure entries |
| P2 | instance classes: consecutive chained copies equal up to operand deltas affine in the copy index | `unrolled`, `per_voice_paths` |
| P3 | per procedure, one depth-first search from its entry not entering another entry: membership, tail-call edges, register-preset entries, retreating edges, short-circuit chains | `call`, `fall_call`, `tail_call`, `multi_entry`, `short_circuit` |
| P4 | dominators, classifying each retreating edge as a natural back edge or a multi-entry loop | `loop` headers, `multi_entry_loop` |
| P5 | backward over the procedure with retreating edges removed: loop bodies and exits, post-dominators | `loop` exits, `join`, `goto` |

The if/else, switch, `break`/`continue`, short-circuit and goto decisions are local functions of
these facts, made while printing; printing is not a fact pass.

P1 is one pass because all of its facts are mutually recursive and monotone once two assumptions
are guarded rather than proved: a table no lifted store is known to reach is taken as constant,
with a check at every store whose address could reach it, and an RTS or RTI has as successors
both the return sites of the calls to its procedure and the targets its pushed values are tagged
with. Proving either instead would depend on the absence of a store or a push, which is not
monotone and would split P1.

## Upper bound

Every map entry's form is decided by facts available after the pass the table gives for its
kind, so five passes suffice for every idiom of the five tunes (`tests/test_constructs.py` checks
the forms against the generated C).

## Lower bound

Each pass needs a complete fact of the one before it, and each such dependency occurs in the
tunes:

1. **P2 after P1.** An instance class holds only if every pair of corresponding instructions has
   operands affine in the copy index, including the targets of computed transfers inside the
   copies; adding a target can break the class, so the class is not monotone in P1's targets.
   Ghouls' voice copies each dispatch commands through a jump whose operand is written from the
   split tables `$6C37`/`$6C76` (`$6374`, `$6561`, `$6750`); the copies pair those jumps by the
   table byte each target is read from, which P1 resolves last.
2. **P3 after P2.** A node belongs to a procedure if its entry reaches it without passing another
   entry, a negation over the entry set, which includes the instance entries P2 finds. Ghouls'
   play is one procedure of three voice copies until P2 names them; lifted without instances
   (deity-informant 0.11.0) it has 73 functions and per-voice constant SID writes, against the
   hand translation's one `play_voice(v)`.
3. **P4 after P3.** A node's dominator depends on the depth-first numbering of nodes visited after
   it (Lengauer and Tarjan's semidominators), so dominators cannot be completed during the search
   that numbers them. Ghouls' pulse sweeps (`$632B -> $62F1` and the copies' `$6518 -> $64DE`,
   `$6707 -> $66CD`) and filter sweep (`$6839 -> $6803`) are retreating edges whose targets do not
   dominate their sources: loops entered at two points by a self-modified direction, which the
   hand C writes as `for (;;)` with a `down` flag. Only dominators tell them from natural loops.
4. **P5 after P4.** Loop bodies and post-dominators flow backward from latches and exits, and need
   to know which retreating edges are natural loops; dominators flow forward. On a procedure with
   a cycle no single traversal order completes both. Emomyst's init loop `$1145-$1160` shows the
   dependency: on the graph with the loop's edges, `$115B` is the immediate post-dominator of the
   branch at `$1151`, so the edge `$1151 -> $115B` looks like an if rejoining; with the loop known,
   `$1151` is in the loop and `$115B` is outside it but is not the loop's follow `$1158`, so the
   edge is a loop's second exit, a goto, as the hand C writes it.

The four dependencies chain P1 through P5, so no fewer than five passes decide every idiom of the
tunes. Without a multi-entry loop, every retreating edge of a depth-first search is a natural back
edge and P4 is not needed: four passes. Knowing that a program has none is itself P4's result, so a
lift that skips P4 must guard the assumption instead.
