# Value-flow graphs and state machines

How the song bytes and state of a generated `.c` become SID writes, as a graph per player
family, and the state machines under it. Tools: `tools/irflow.py` (value flow of the LLVM
IR), `tools/flowgraph.py` (graph), `tools/flowsegment.py` (state machines),
`tools/condition.py` (lift, hand conditioning, trace check and graphs per family).

    python tools/condition.py [family ...]        # build/flow/<family>/{gen.c,graph.*,machines.*,row.json}
    python tools/flowgraph.py PSID OUT            # whole-program graph of a lift
    python tools/flowsegment.py GEN.c OUT         # play's state machines of a (conditioned) .c

All need `CLANG` (clang 22 with the llvm-mos-compatible IR, e.g. `../bin/clang22`), `CC`
(`mos-c64-clang`) for the trace check, `dot`, and `HVSC` (default
`/scratch/datasets/hvsc/C64Music`).

## Graph

`irmodule.lower(..., passes=True)` lowers the `.c` at `-O0` with names and line tables;
`irflow.sroa` promotes locals so each C read is one `load`. `irflow.Flow` records per load,
store and call the loads, arguments and returns its value and address derive from
(arguments and returns followed through every call). Nodes:

- **cell**: a global plus constant offset, its index dropped, named by original address
  (`$1063`) or symbol (`cgen_ptr+4`); a cell some store writes.
- **song**: a cell no store writes; a read through a pointer is `Cj[...]` of its address
  cells.
- **sid**: the register of an `io_write` (`SID+$05`), its voice index dropped.

Edges: **value** (solid) from what a stored or written value derives from, **address**
(dashed) from what its address derives from.

## State machines (`flowsegment.segment`)

1. **Play only** (`entries=["cgen_play"]`): what the functions play reaches read, store
   and write.
2. **Def-use webs** (`split=True`, `irflow.Webs`): reaching definitions per function,
   each call applying its callee's summary (the stores reaching its exit per cell,
   computed once per function); live-in values of a callee are those at its call sites,
   play's are kept from earlier calls. A cell whose stores and loads form separate webs is
   a node per web, `cell@line` of its store; the web kept between calls keeps the cell's
   name. This splits scratch cells (SID-Wizard's `$02/$03`, reused as a pointer to every
   table) that otherwise tie unrelated reads into one cycle.
3. **Register cells** (`registers`): the `rvN` globals the lift passes registers between
   functions in are contracted, each source feeding each target (address if either edge
   is).
4. **Machines** (`machines`): every cell outside a cycle is contracted the same way. What
   stays is a state machine: a cell holding a value derived from its own (a position,
   counter, phase), with the song tables and SID registers.
5. **Classes** (`classes`, `quotient`): signatures by dynamic programming over the
   condensation (strongly connected components in topological order): back from the SID
   registers (a register by its offset in its voice, `voice+0..6`, filter and volume by
   address), then forward from the sources. Nodes with equal signatures are one node
   (`a, b (x2)`): per-voice code copies, pointer table halves, patch sites of one pointer.
6. **Roles** (`roles`): each machine by its edges: a `sequencer` indexes song tables and
   feeds other machines, a `program` indexes song tables feeding only registers, a
   `modulator` feeds registers from its own value, a `timer` feeds only machines, a `flag`
   feeds neither (it acts through control flow, which the graph has no edges for). With
   the register groups it drives (`freq`, `pulse`, `control`, `adsr`, `cutoff`,
   `resonance`, `volume`).

## Input conditioning

The graphs need a lift with no residual `rd`/`wr`/`codechk` on play's path and play's song
pointers as variables. Where the lift (deity-informant 0.25.0 at 0af67f3) falls short,
`condition.py` edits the generated C by hand; each edit keeps the trace exact over the whole
song.

| Family | Tune | Conditioning | Lift issue |
|---|---|---|---|
| GoatTracker V2 | `DEMOS/0-9/10_Orbyte.sid` | none | |
| defMON | `DEMOS/A-F/Fading_Lights.sid` | none | |
| DMC | `DEMOS/0-9/2Pac_Intro.sid` | none | |
| JCH NewPlayer | `DEMOS/0-9/1st_Chaff.sid` | none | |
| Future Composer | `MUSICIANS/A/A-Man/Future_Composer_4-Demo_07.sid` | none | lift 43 s (deity #457) |
| SID-Wizard 1.x | `fixtures/Emomyst.sid` | `sidwizard` | relocated operands (deity #454) |
| Music Assembler | `DEMOS/0-9/7_Years.sid` | `direct` | voice index lost (deity #455) |
| Soundmonitor | `DEMOS/0-9/1988_Carat_tune_1.sid` | PSID tune | RSID rejected (gravity-inversion #46) |
| Blackbird | `MUSICIANS/F/Freqvibez/Arhymetriarchy.sid` | not done | SMC jump target set in play (deity #456) |

- **`sidwizard`**: the relocator patches about 30 instruction operands from header words
  through its table (`SW_INDEX`, `SW_OFFSET`, `SW_SITE`: per site the header word index, a
  signed offset and the operand address). Each operand pair read becomes
  `cgen_ptr[P_<word>] + offset`; the 12 pointers (`seq_ptr0-2`, `subtune_tempo`, `ptn_lo/hi`,
  `ins_lo/hi`, `chords`, `chord_ptr`, `tempo`, `tempo_prg`, the hand C's) are set where the
  relocator ends. deity-informant 0.26.0 (#459) merges these pairs in the lift; with it the
  step should be dropped.
- **`direct`**: play calls its voice routine with X = 0, 7, 14 through return addresses it
  pushes, so the lift loses X's range and every per-voice store into code goes through
  `wr`/`codechk`. Each `wr(a, v)` becomes a store into the span holding `a` with every
  variable 0, each `codechk(...)` 0.
- **Soundmonitor**: RSID files do not lift; a PSID tune of the same player is used.
- **Blackbird**: its `JMP`/`RTS` at `$12EB` and its target are written by the decompressor
  during play; only target `$1018` is lifted, `$1043` traps. Conditioning would translate
  that path by hand and bound the decompressor's `wr` stores to its unpack buffers.

## Next

- Rerun on deity-informant 0.26.0 and drop `sidwizard`; drop each step as its issue closes.
- Map each family's machines to its tracker's definitions and find one state machine
  definition all the graphs fit.
