# Event log

`tools/eventlog.py` logs every read of song data by a translated player's play calls, in order,
each with its kind, the state cell stores and the SID writes that follow it up to the next read.
It needs no knowledge of the player: song data and the kinds of its reads come from the original's
run and the generated C's value flow.

    python3 tools/eventlog.py PSID OUT (--frames N | --hvsc HVSC_ROOT) [--song S]

This writes `OUT/events.jsonl` and `OUT/summary.json`.

## Method

1. **Song data.** `tools/transliterate.py` runs init on the host and lifts play from the state it
   leaves (the `.h` holds that state, the `.c` the code). Song data is every byte the PSID loads
   that is in no instruction the lift translates (`info["starts"]`) and that the original's run
   (init and the play calls) never writes: `eventlog.song_mask`, `eventlog.reference`. The bytes
   init sets are `.h` variables, not song data.
2. **Sites.** `tools/irmodule.py` lowers the `.c` to LLVM IR with clang, so each C array read is
   one `load` with its source location. A site is a load from a region or held span (`Rk`, `Cj`)
   or through a pointer value; a load inside the read helpers `rd`/`rdCj`/`oob_rd` reports the
   site of the call that entered it. A byte the lift proves constant is named in the `.h`
   (`X_K<offset>`, the byte at `X_ORG` + offset) and read by name in the `.c`; the IR is lowered
   with each name defined as a load of that original address from one array (`eventlog.unfold`),
   so its uses are sites too.
3. **Kinds.** `irflow.classify` labels each site from the IR's value flow (`tools/irflow.py`,
   shared with `tools/flowgraph.py`). A cell (a global and constant offset, its index dropped) is
   a *position* when play stores to it a value derived from its own load and may load it before
   storing it: state the player advances across calls, starting from the value init left. A
   position some store restarts from another position's event or lookup is a *machine*'s (an
   instrument or effect table program). An `event` reads at an address derived from a score
   position, a `machine` read at one derived only from machine positions; a `lookup`, or a
   `machine` read, at one derived from their values, directly or through the cells they are
   stored to. Any other site is a `table` (a player table indexed by a loop counter, or state).
4. **Native run.** The instrumented IR, linked with `tools/eventlog.c`, runs init and the play
   calls. It logs each site's read of a song data byte (the original address from the array and
   the `.h` origin) and each store to a region, span or promoted variable (the cell's original
   address, value and old value), each with the copy index. A promoted variable (`S_<offset>`,
   an element of a field array `V<s>_f<offset>[copy]`) maps to its original address by the
   lift's `info["state"]["vars"]`. Init's reads (its copy of the `.h` state) are dropped.
   A copy index is the first argument of a function the lift lists as `index`
   (`info["provenance"]["functions"]`): one instance of code the original repeats per voice or
   channel. SID writes are logged too.
5. **Environment.** The I/O reads of each call (VIC raster, SID oscillator 3) are answered with
   the values the original read on the reference machine, in order per address. The native SID
   writes must equal the reference's.

## Output

JSON Lines, one record per song data read, in program order. The first record (`site` null)
holds the writes before the first read.

| Field | Meaning |
|---|---|
| `frame` | play call (0 is init) |
| `site`, `kind`, `function`, `line`, `column` | the read site in the generated `.c` and its kind |
| `region`, `offset`, `addr` | the held span read (`CODE`, the code span, when the `.h` has none), the offset in it and the original address |
| `value` | the byte read |
| `copy` | the copy index of the code reading it, else null |
| `cells` | `[frame, address, value, same]` per state cell store up to the next read |
| `sid` | `[frame, register, value, same]` per SID write up to the next read |

`frame` in `cells` and `sid` counts from the read's call. `same` is 1 when the store or write
leaves its byte or register unchanged, as a shadow buffer refresh does. Concatenating `sid`
over the records rebuilds the reference write stream (`Log.sid`).

In Python, `eventlog.run_tune` gives an `eventlog.Log`: the native records as numpy arrays,
`reads` (one row per read), `kinds`, `sid()` and `read_set()` as arrays, and each record above
built on demand (`log[g]`, iteration), so a long song never holds per-read objects.

`summary.json` gives `frames`, the counts of `reads`, `cell_writes` and `sid_writes`,
`song_bytes`, the lift's self-modified code cells (`code_cells`) and the `sites`.

## Validation

`tests/test_eventlog.py`:

- **Synthetic C.** Site selection and kinds (a byte read by name included), helper and copy
  attribution, the stores and SID writes after each read and the replayed I/O reads.
- **Song data.** The loaded bytes of no instruction the original never writes: not the operands
  play patches.
- **Fuzz players** (`tools/fuzzplayer.py`, every style, sequencer SID writes direct or through a
  shadow buffer copied after the voices or at the next call). The generated C reads exactly the
  song bytes the original's play calls read, as (call, address) with the value: the score's as
  events, the frequency table's as lookups. The log rebuilds the writes.
- **Tunes**, over the song (Commando 6000 calls). Every song data read of the generated C, with
  its value, is a read the original makes in the same call. Against each hand C built at `-O0`
  (`tunes/*/build/hand-O0.prg`, so each C read is one load), whose SID writes must equal the
  original's, the hand C's play reads of its data arrays are mapped to original addresses and
  equal the generated C's reads of those bytes, but for reads the hand C does not make:
  - Emomyst ($19F1–$19FF, $1A6E–$1AAD), Ghouls ($6CB7–$6D34) and Incoherent ($0C4F–$0C63)
    dispatch commands through tables of branch offsets or jump vectors. The original stores
    each value into its own code (a lift code cell) before its next read of one, while the
    hand C uses a `switch` (as does the generated C where the lift folds the patched jump).
  - Emomyst's chord rows' detune column ($21CC, $223B), which the original stores to a cell
    the build never reads; the hand C skips it.
