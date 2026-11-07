# Event log

`tools/eventlog.py` logs every read of song data by a translated player, in order, each with the
state cell stores and SID writes that follow it up to the next read. It needs no knowledge of
the player: the lift has already split song data from the player's state and code.

    python3 tools/eventlog.py PSID OUT (--frames N | --hvsc HVSC_ROOT) [--song S]

This writes `OUT/events.jsonl` and `OUT/summary.json`.

## Method

1. **Song data.** `tools/transliterate.py` lifts the PSID. The `.c` is the code alone, and the
   `.h` holds the data: regions `Rk` (state, the bytes the program writes) and held spans
   `Cj` (constant bytes, with the regions inside them as slices). Song data is every held span
   byte that is in no region and in no instruction (`info["starts"]`):
   `eventlog.song_mask`.
2. **Sites.** `tools/irmodule.py` lowers the `.c` to LLVM IR with clang at `-O0`, so each C
   array read is one `load` with its source line. A site is a load from a `Cj`, at a byte of
   song data when its offset is constant, or through a pointer value. Loads from locals,
   `K` constants and state at constant offsets are not sites. A load inside the read helpers
   `rd`/`rdCj` reports the site of the call that entered it. A byte the lift proves constant
   is named in the `.h` (`X_K<offset>`, the byte at `X_ORG` + offset) and read by name in
   the `.c`; the IR is lowered with each name defined as a load of that original address
   from one array (`eventlog.unfold`), so its uses are sites too.
3. **Native run.** The instrumented IR, linked with `tools/eventlog.c`, runs init and the play
   calls. It logs each site's read of a song data byte (the original address from the array
   and the `.h` origin) and each store to a region or span (the cell's original address, value
   and old value), each with the copy index. A copy index is the first argument of a function
   the lift lists as `index` (`info["provenance"]["functions"]`): one instance of code the
   original repeats per voice or channel. SID writes are logged too.
4. **Environment.** The I/O reads of each call (VIC raster, SID oscillator 3) are answered
   with the values the original read on the reference machine, in order per address. The
   native SID writes must equal the reference's.

## Output

JSON Lines, one record per song data read, in program order. The first record (`site` null)
holds the writes before the first read.

| Field | Meaning |
|---|---|
| `frame` | play call (0 is init) |
| `site`, `function`, `line`, `column` | the read site in the generated `.c` |
| `region`, `offset`, `addr` | the held span read (`CODE`, the code span, when the `.h` has none), the offset in it and the original address |
| `value` | the byte read |
| `copy` | the copy index of the code reading it, else null |
| `cells` | `[frame, address, value, same]` per state cell store up to the next read |
| `sid` | `[frame, register, value, same]` per SID write up to the next read |

`frame` in `cells` and `sid` counts from the read's call. `same` is 1 when the store or write
leaves its byte or register unchanged, as a shadow buffer refresh does. Concatenating `sid`
over the records rebuilds the reference write stream (`eventlog.rebuild`).

`summary.json` gives `frames`, the counts of `reads`, `cell_writes` and `sid_writes`,
`song_bytes`, the lift's self-modified code cells (`code_cells`) and the `sites`.

## Validation

`tests/test_eventlog.py`:

- **Synthetic C.** Site selection (a byte read by name included), helper and copy
  attribution, the stores and SID writes after each read and the replayed I/O reads.
- **Fuzz players** (`tools/fuzzplayer.py`, every style, sequencer SID writes direct or
  through a shadow buffer copied after the voices or at the next call). The generated C
  reads exactly the song bytes the original reads, as (call, address) with the value, and
  the log rebuilds the writes.
- **Tunes**, over the song (Commando 6000 calls). Every song data read of the generated C,
  with its value, is a read the original makes in the same call; the original reads more
  where the lift forwards a value through state. Against each hand C built at `-O0`
  (`tunes/*/build/hand-O0.prg`, so each C read is one load), whose SID writes must equal the
  original's, the hand C's reads of its data arrays are mapped to original addresses and
  compared with the generated C's reads of those bytes. Commando and Automatas are equal
  exactly. Elsewhere the hand C differs from the original in structure:
  - Emomyst ($19F1–$19FF, $1A6E–$1AAD), Ghouls ($6CB7–$6D34) and Incoherent ($0C4F–$0C63)
    dispatch commands through tables of branch offsets or jump vectors. The original stores
    each value into its own code (a lift code cell), while the hand C uses a `switch`.

  Without exactly those reads the sets are equal. Emomyst's generated init runs the
  original's relocator, reading the SWP header words the hand init reads, and its offset
  and patch-site tables ($1B55, $1B92), which lie outside the hand C's arrays.
