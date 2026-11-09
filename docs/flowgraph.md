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
store, call and conditional branch (`br`, `switch`) the loads, arguments and returns its
value, address or condition derives from (arguments and returns followed through every
call). Nodes:

- **cell**: a global plus constant offset, its index dropped, named by original address
  (`$1063`) or symbol (`cgen_ptr+4`); a cell some store writes.
  A pointer loaded from a constant pointer table into one span (a per-voice table, `K4 =
  {C1, C1 + 0x88, ...}`) is that span, its element dropped like an index.
- **song**: a cell no store writes; a read through a pointer is `Cj[...]` of its address
  cells.
- **sid**: the register of an `io_write` (`SID+$05`), its voice index dropped.

Edges: **value** (solid) from what a stored or written value derives from, **address**
(dashed) from what its address derives from, **control** (dotted) from what the condition of
a branch it runs under derives from.

Control dependence (`irflow.gating`) is per function from post-dominators (Ferrante,
Ottenstein and Warren): block B depends on branch block A when B post-dominates a successor
of A but not A, computed as the post-dominance frontier (`networkx.dominance_frontiers` on
the reversed CFG from a virtual exit joined to every `ret` block; a block ending in a
noreturn `trap` reaches no exit, so the lift's bounds checks gate nothing). Dependence is
taken transitively (the iterated frontier), so a nested branch's stores are also under the
outer branch, as a callee's are: a call under a branch puts every store and `io_write` its
callee runs (transitively, once per function) under that branch. A loop's exit test gates
its body and latch, and the header is control dependent on itself; the test gates no store
of its own, so a counter stored in the latch has a control self-loop and nothing more.
Only branches in the functions the entries reach count.

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
   functions in are contracted, each source feeding each target by the kind of the path
   through it: control if either edge is control, else address if either is, else value.
   Parallel edges of different kinds stay apart, so a control path never replaces a value
   edge.
4. **Machines** (`machines`): every cell outside a value or address cycle is contracted the
   same way (a control self-loop makes no machine). What stays is a state machine: a cell
   holding a value derived from its own (a position, counter, phase), with the song tables
   and SID registers; a condition read through a contracted cell becomes a control edge
   from that cell's sources.
5. **Classes** (`classes`, `quotient`): signatures by dynamic programming over the
   condensation (strongly connected components in topological order): back from the SID
   registers (a register by its offset in its voice, `voice+0..6`, filter and volume by
   address), then forward from the sources, over value and address edges; each component's
   signature is then refined by the signatures its control edges come from and go to (by
   kind), so per-voice copies gating the same registers of their voices still merge. Nodes
   with equal signatures are one node (`a, b (x2)`): per-voice code copies, pointer table
   halves, patch sites of one pointer.
6. **Roles** (`roles`): each machine by its value and address edges: a `sequencer` indexes
   song tables and it or its rows set another table machine, a `program` indexes song
   tables otherwise, a `modulator` feeds registers from its own value, a `timer` feeds only
   machines, a `flag` feeds neither (its outgoing edges are control only, or none). With
   the register groups it drives (`freq`, `pulse`, `control`, `adsr`, `cutoff`,
   `resonance`, `volume`), and by its control edges the register groups and machines it
   gates: `(role, groups, tables, gated groups, gated machines)`.

## Input conditioning

The graphs need a lift with no residual `rd`/`wr`/`codechk` on play's path and play's song
pointers as variables. With deity-informant 0.28.2 no family's generated C is edited by
hand; Soundmonitor's input is a PSID tune and Blackbird is not done.

| Family | Tune | Conditioning | Lift issue |
|---|---|---|---|
| GoatTracker V2 | `DEMOS/0-9/10_Orbyte.sid` | none | |
| defMON | `DEMOS/A-F/Fading_Lights.sid` | none | |
| DMC | `DEMOS/0-9/2Pac_Intro.sid` | none | |
| JCH NewPlayer | `DEMOS/0-9/1st_Chaff.sid` | none | |
| Future Composer | `MUSICIANS/A/A-Man/Future_Composer_4-Demo_07.sid` | none | |
| SID-Wizard 1.x | `fixtures/Emomyst.sid` | none | |
| Music Assembler | `DEMOS/0-9/7_Years.sid` | none | |
| Soundmonitor | `DEMOS/0-9/1988_Carat_tune_1.sid` | PSID tune | RSID rejected (gravity-inversion #46) |
| Blackbird | `MUSICIANS/F/Freqvibez/Arhymetriarchy.sid` | not done | SMC jump target set in play (deity #456) |

- **Soundmonitor**: RSID files do not lift; a PSID tune of the same player is used.
- **Blackbird**: its `JMP`/`RTS` at `$12EB` and its target are written by the decompressor
  during play; only target `$1018` is lifted, `$1043` traps. Conditioning would translate
  that path by hand and bound the decompressor's `wr` stores to its unpack buffers.

## Machines by family

Roles per family (`build/flow/<family>/row.json`, `roles`), all traces exact over the song:

| Family | sequencer | program | modulator | timer | flag |
|---|---|---|---|---|---|
| GoatTracker V2 | 1 | 2 | 3 | 0 | 4 |
| defMON | 4 | 1 | 7 | 2 | 4 |
| DMC | 2 | 4 | 9 | 3 | 9 |
| JCH NewPlayer | 1 | 5 | 4 | 2 | 5 |
| Future Composer | 1 | 4 | 7 | 1 | 7 |
| SID-Wizard | 4 | 4 | 7 | 4 | 6 |
| Music Assembler | 2 | 3 | 8 | 2 | 9 |
| Soundmonitor | 1 | 2 | 4 | 1 | 4 |

Each machine keeps its role from value and address edges; control only splits classes that
merged by value alone, such as unrelated flags feeding nothing (DMC's `$1035, $171A, $1768
+3`, Music Assembler's `$1090, $10DD, $10E0 +2`) or a pair gating different machines
(defMON's pattern position `$1186`/`$1187`).

The trackers' own terms, by the tables a machine indexes and the registers it drives:

- **Score** (sequencer, all groups): the orderlist and pattern positions per voice; a row
  restarts the instrument's programs and modulators. GoatTracker's is one component of 22
  cells (orderlist, pattern, instrument and wavetable positions restart each other within
  a voice); SID-Wizard's 17. defMON's is its orderlist (`$10EB/$1C00`) and pattern
  position (`$1186/$1187`, reached through its per-voice pointer table), whose rows
  restart the sidtab programs.
- **Wave/arpeggio program** (freq, control): GoatTracker's wavetable (inside the score
  component), SID-Wizard's WF-ARP table position (`$103D`), JCH's and DMC's wave tables.
- **Pulse program** (pulse): GoatTracker's pulsetable (`$1293`), SID-Wizard's PW table
  (`$103E`), DMC `$1762`.
- **Filter program** (cutoff, resonance, volume): GoatTracker's filtertable (`$104E`),
  SID-Wizard's filter table (`$15D6`), DMC `$1719`.
- **Modulators**: the frequency (vibrato, slide, portamento), pulse width (pulse sweep),
  cutoff (filter sweep), resonance/routing and volume registers each program or score row
  sets and steps. Every family has freq, pulse and cutoff modulators except GoatTracker,
  whose frequency steps inside the score component.
- **Timers**: counters feeding other machines (SID-Wizard `$1064-$1068` per voice, which
  gate its score pointers `$1024/$1025`; Music Assembler `$1141`, whose bits gate adsr,
  control, cutoff and freq and every score and program position). Soundmonitor's `$00AB`,
  JCH's `$1580/$1583`, Future Composer's `$217F` and DMC's `$1726/$1792/$1795` gate
  nothing.
- **Flags** (and timers), by what they gate (`row.json` roles, gated groups and machines):
  - GoatTracker: `$12A6`, the per-voice tick counter (decremented, reloaded from `$12A5`),
    gates adsr, control, freq and pulse and the score, pulse and filter programs; `$1294`
    the pulse table position, `$1052` the filter table position and resonance and volume,
    `$127D` the score component.
  - defMON: `$10D8`, an `RTS` code byte patched during play, gates adsr, control, cutoff,
    freq and volume and nearly every machine; `$12E0` the same groups; `$114A` freq and the
    pattern positions; timer `$10D9` freq and the orderlist and pattern positions.
  - DMC: `$1718`, the tempo counter (counted down, reloaded from the speed `$1716`), and
    `$173B`, the per-voice duration counter, gate every group and nearly every machine;
    `$1786` control, freq and pulse; `$1035/$1771` control and freq; `$1768/$176B/$176E`
    gate each other.
  - JCH: `$153E` and `$1547` (a counter reloaded from `$154A`) per voice gate every group
    but volume and nearly every machine; `$1565` control only.
  - Future Composer: `$2173` (compared with `$211D`) and `$2127` (per voice, compared with
    `$1AF8`) gate every group; `$2176`, a per-voice countdown, adsr, control, freq and
    pulse.
  - SID-Wizard: `$1039`, `$103A` and `$10A4` (per voice) gate every group and nearly every
    machine; `$102A` pulse and the PW table, `$103F` freq and the WF-ARP table, `$15DD`
    cutoff and the filter table.
  - Music Assembler: `$1090`, the tempo counter (reloaded with 2, choosing between its two
    play paths), and `$108A` gate every group but volume and nearly every machine; `$1296`
    cutoff, `$12B9` freq.
  - Soundmonitor: `$CD80` gates every group; `$CD9E/$CD9F` cutoff, `$CDBB` volume.

SID-Wizard's further sequencers (`$103C`, `$103D`, `$107D`) read the song through
pointers (`$02/$03`, the relocated pointers) whose rows set other table positions;
GoatTracker indexes its tables by per-voice registers.

## Common definition

A player is per-voice instances of one pipeline plus global machines:

    score --row--> programs (wave/arp, pulse, filter) --> modulators --> SID
      ^                                                      ^
    timers (tempo, duration)                    flags (control only, dotted)

A **machine** is a cell (or component of cells) kept between play calls whose next value
derives from its own. It has:

- **state**: its cells, one per voice where the player indexes by voice;
- **table**: the song table its state indexes, if any (score and programs);
- **step**: how it derives its next value from its own (advance a position, count, add a
  delta);
- **restart**: the machines and song reads that overwrite its state (a score row restarts
  programs and modulators; a program row restarts modulators);
- **outputs**: the register groups (`freq`, `pulse`, `control`, `adsr`, `cutoff`,
  `resonance`, `volume`) and machines it feeds.

Its role follows from whether it indexes a table, whether it or its rows set another
table machine, and whether it feeds registers (`flowsegment.roles`). All eight families fit; they differ in how many machines share a
component (GoatTracker's score holds its wavetable), and whether tables are reached by index or pointer (SID-Wizard).

## Next

- Drop each conditioning step as its issue closes.
- Restart edges from the score explicitly, so each program and modulator names the score
  row field that restarts it (the control edges from song nodes already show which score
  bytes a branch tests).
- Name flags by what they gate across families (tempo, duration, new-note, gate bit) from
  the gated groups and the machines they reload from.
