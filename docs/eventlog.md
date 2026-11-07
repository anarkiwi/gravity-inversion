# Event log

`tools/eventlog.py` turns a PSID's play calls into a log of note events and the SID register
operations each one starts. A later tool can fit modulators (tables, LFOs, slides, envelopes)
and sequences from it without the player's source. The log names no player structure: it holds
times, voices, pitches and register operations only.

    python3 tools/eventlog.py PSID OUT (--frames N | --hvsc HVSC_ROOT) [--song S] [--c GEN.c]

This writes `OUT/events.jsonl`, `OUT/shared.jsonl` and `OUT/summary.json`. Without `--c`, the
PSID is first translated with `tools/transliterate.py`.

## Method

1. **Reference.** `sidtrace.trace_psid(..., detail=True)` gives each play call's SID writes as
   `(register, value, cycle, pc)`, the cycle counted from the start of the call. Frame 0 is
   init.
2. **IR.** `tools/irmodule.py` compiles the generated C for the host (`-DCGEN_HOST`) to LLVM
   IR with clang (`$CLANG`, default `clang`) at `-O1`, inlining every call it may, with no loop
   unrolling, vectorization or library idioms; llvmlite parses it into op tuples with each
   function's loops, liveness and post-dominators.
3. **Value analysis** (`tools/initiations.py`). Forward over init and repeated play calls:
   value sets per SSA value and memory byte (more values than a byte holds, a 16-bit set that
   grows at a join, or any set that grows at a loop header, is unknown), the cells each value
   derives from, the sequence reads it carries and the bytes stores write. States are
   partitioned by the singleton values of the header phis of the loops holding a block (and
   of a latch's phis), so a voice loop's index keeps each voice apart. A function is analysed
   per call site, argument values and caller partition and returns the caller's state with
   the bytes it may write; one that stores nothing is evaluated in place, as a load.
4. **Sequence reads.** A load is a sequence read when it reads bytes no store in the program
   writes at an address derived from a cursor, a cell the program advances (stores from its
   own value). Its value is song data.
5. **Reach.** Backward over the same graph, the play call's exit feeding its entry: which
   values may reach a write of each voice's frequency registers, through registers, tables,
   memory bytes and shadow buffers copied in the same call or a later one. A comparison
   decides and passes no value; an operand combined with a value derived from an advanced
   cell (a slide's accumulator) modifies that pitch rather than setting it and does not
   flow; a load at an address carrying sequence reads is a lookup of them (the bytes it reads
   do not flow). A note read is a sequence read whose value may reach a frequency register.
6. **Restarted tables.** A pitch edge is a branch edge on which a value carrying a sequence
   read may reach a frequency register and not on another edge of its branch. A cursor some
   store resets in a block control dependent on a pitch edge is a table restarted with a
   note, an instrument's wave or arpeggio table: its reads are no sequence reads. Steps 3 to 6
   repeat until no further cursor is found.
7. **Native run.** `tools/irtags.py` instruments the IR and llvmlite compiles it, linked with
   `tools/eventlog.c`: every value carries a tag, the newest read instance it derives from (a
   sequence read makes a new tag and records its play call and the writes before it; a lookup
   takes its address's tag, another load its bytes', other operations the greatest of their
   flowing operands'; stores keep it per byte), and every SID write reports its value's tag.
   The native writes must equal the reference.
8. **Initiations.** A read instance initiates on a voice when its tag reaches that voice's
   frequency registers, newer than any tag that reached them before: a repeated pitch or a
   retrigger with the gate held is a new read, a note sounding on (vibrato reading it again)
   is not. It is timed by its read; its `delay` counts the play calls to that first write,
   so a shadow buffer copied at the start of the next call gives 1.
9. **Register onsets**, from written values: a gate rise; a test-bit retrigger (the test bit
   set and cleared with the gate held throughout); a hard restart, a gate rise after an AD or
   SR value change that follows the last gate fall. A rewrite of the same value (a shadow
   buffer's refresh) is not a change. The first onset from an initiation's read to its first
   write gives the event's `registers` and the hard restart's preparation (`start`); an onset
   in no such span is a violation.

## Output

JSON Lines: one self-contained record per event, so the log can be streamed and grepped.
Trajectories have ragged lengths, which do not fit a fixed columnar layout.

`events.jsonl`, one record per event per voice, ordered by voice and then time:

| Field | Meaning |
|---|---|
| `voice` | 0–2 |
| `frame`, `cycle` | play call of the initiating read and the cycle of the voice's first write after the read |
| `lead` | the voice's writes before its first initiation |
| `start` | first frame of the event relative to `frame` (negative for a hard restart's preparation) |
| `delay` | play calls from the read to the first frequency write carrying it |
| `frames` | frames from the initiation to the voice's next event start (or the end) |
| `base` | at the end of the call of that first write: `freq`, `note` (MIDI, PAL clock, A4 = 440 Hz), `cents`, `name` |
| `registers` | the first register onset from the read to that write: `gate`, `test`, `hard_restart` |
| `gate_frames` | frames from the initiation until the gate is first off at a frame end, or `null` |
| `ops` | `[frame, cycle, reg, value, pair, delta, cents, same]` per write, in order |

In `ops`, `frame` counts from the initiation and `cycle` from the start of its play call. `reg`
is voice-relative: 0/1 frequency, 2/3 pulse, 4 control, 5 AD, 6 SR. `value` is the byte
written. `pair` is the frequency or 12-bit pulse width the write leaves (the byte itself for
other registers), and `delta` is its change. `cents` is the frequency's offset from `base`.
`same` is 1 when the write leaves its register's value unchanged (a buffer refresh).

`shared.jsonl` holds one record per write to the shared registers ($D415–$D418): `frame`,
`cycle`, `reg`, `value`, `same`, plus `cutoff` (11-bit) and `delta`, `res`/`routing`/`ext`, or
`mode`/`voice3_off`/`volume`. A fitter relates them to events on any voice by time.

`summary.json` gives `frames`, the counts of `initiations` and `register_onsets`, the
`violations` (`[frame, voice]` of register onsets outside every initiation's span) and the
numbers of `note_reads` and `other_reads` in the program.

Sorting every voice op and shared record by (frame, cycle) rebuilds the reference write stream
exactly (`eventlog.rebuild`).

## Validation

`tests/test_eventlog.py`:

- **Register logs.** Gate edge, hard restart by value change and not by a same-value rewrite,
  test-bit retrigger, a legato pitch change, refresh flags and register pair values.
- **Synthetic C players.** A pointer advanced per row with a note byte into a frequency table
  is a note read and a duration byte never reaching a frequency register another read;
  initiations are the rows reading a note, direct and through a shadow buffer copied after
  the voices or at the start of the next call (delay 1).
- **Fuzz players** (`tools/fuzzplayer.py` with a sequencer: notes, legato notes with the gate
  held, repeated pitches and rests; SID writes direct or through a shadow buffer copied after
  the voices or before them). Initiations equal the generated note reads exactly; the log
  rebuilds the writes; no register onset is a violation; no hard restart is found.
- **Commando** (Rob Hubbard). Initiations equal the hand C's `newnote` calls that read a note
  (notelen without the legato bit, which only releases the gate).
- **Emomyst** (SID-Wizard), over its HVSC length, against the rows where the hand C reads a
  note (`CUR_NOTE` set to a pitch): every initiation is such a row. A strict xfail asserts
  equality; it fails on drum rows, whose instrument's wavetable sets absolute pitches, so the
  note value never reaches the frequency registers and only the restart of the table marks
  the note. The gate-on effect ($7D) re-gates a note without reading one; its gate rises are
  the violations besides the drum rows'.
