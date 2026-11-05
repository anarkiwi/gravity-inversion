# Event log

`tools/eventlog.py` turns a PSID's play calls into a log of note events and the SID register
operations each one starts. A later tool can fit modulators (tables, LFOs, slides, envelopes)
from it without the player's source. The log names no player structure. It holds times,
voices, pitches and register values only.

    python3 tools/eventlog.py PSID OUT (--frames N | --hvsc HVSC_ROOT) [--song S] [--c GEN.c]

This writes `OUT/events.jsonl`, `OUT/shared.jsonl` and `OUT/summary.json`. Without `--c`, the
PSID is first translated with `tools/transliterate.py`.

## Method

1. **Reference.** `sidtrace.trace_psid(..., detail=True)` gives each play call's SID writes
   as `(register, value, cycle, pc)`. The cycle counts from the start of the call. Frame 0
   is init.
2. **Native run.** The generated C is compiled for the host (`$HOSTCC`, default `cc`) with
   `-fsanitize-coverage=trace-pc -finstrument-functions` and linked with `tools/eventlog.c`.
   For each call, the harness logs the SID writes and the basic blocks executed. The native
   writes must equal the reference.
3. **Block to voice.** Each executed block is assigned to the voice of the innermost dynamic
   call below the entry whose SID writes all go to one voice. If there is no such call, it
   goes to the voice of the next voice-register write in the play call. This needs no
   addresses, works for routines called once per voice with an index, and also works for
   unrolled copies.
4. **Register onsets** label (frame, voice) samples:
   - a gate rise;
   - a test-bit retrigger (the test bit is set and cleared with the gate held throughout);
   - a hard restart: a gate rise after an AD/SR write that follows the last gate fall.
     Its preparation frame becomes the event's `start`.
5. **Initiations.** These are the blocks whose per-voice execution carries the most
   information about the onsets, given the voice (conditional mutual information, counting
   only positive association). Blocks are added greedily while the union gains more than
   the Miller–Madow bias, 1/(2·frames). The init call is excluded. Because executions mark
   initiations, the log catches a same-pitch repeat or a retrigger with the gate already on,
   which the registers do not show. Events start at the union of initiations and register
   onsets.

## Output

JSON Lines: one self-contained record per event, so the log can be streamed and grepped.
Trajectories have ragged lengths, which do not fit a fixed columnar layout.

`events.jsonl`, one record per event per voice, ordered by voice and then time:

| Field | Meaning |
|---|---|
| `voice` | 0–2 |
| `frame`, `cycle` | onset play call and the cycle of the voice's first write in it |
| `lead` | the voice's writes before its first initiation (no onset) |
| `start` | first frame of the event relative to `frame` (negative for a hard restart's preparation) |
| `frames` | frames from the onset to the voice's next event start (or the end) |
| `base` | `freq` (register value at the end of the onset frame), `note` (MIDI, PAL clock, A4 = 440 Hz), `cents`, `name` |
| `registers` | register onsets at this initiation: `gate`, `test`, `hard_restart`; empty when only the code shows it |
| `initiated` | the initiation blocks ran for this voice at the onset |
| `gate_frames` | frames from the onset until the gate is first off at a frame end, or `null` |
| `ops` | `[frame, cycle, reg, value, pair, delta, cents]` per write, in order |

In `ops`, `frame` counts from the onset and `cycle` from the start of its play call. `reg`
is voice-relative: 0/1 frequency, 2/3 pulse, 4 control, 5 AD, 6 SR. `value` is the byte
written. `pair` is the frequency or 12-bit pulse width the write leaves (the byte itself for
other registers), and `delta` is its change. `cents` is the frequency's offset from `base`.

`shared.jsonl` holds one record per write to the shared registers ($D415–$D418): `frame`,
`cycle`, `reg`, `value`, plus `cutoff` (11-bit) and `delta`, `res`/`routing`/`ext`, or
`mode`/`voice3_off`/`volume`. A fitter relates them to events on any voice by time.

`summary.json` gives the counts of initiations and register onsets, the initiations'
precision and recall against those onsets, `unseen` (initiations with no register onset)
and `uninitiated` (register onsets outside the initiation blocks).

Sorting every voice op and shared record by (frame, cycle) rebuilds the reference write
stream exactly (`eventlog.rebuild`).

## Validation

`tests/test_eventlog.py`:

- **Synthetic register logs.** Gate edge, hard restart, test-bit retrigger, a legato pitch
  change (not a register onset), and register pair values.
- **Synthetic block matrices.** Blocks that run every frame, run only for one voice, or run
  against the onsets are rejected. Two onset paths are united.
- **Emomyst** (SID-Wizard), over its HVSC length. Initiations equal the hand C's gate-on
  points, where its `GATE` field is set to `$FF`: note starts and the gate-on note effect.
  The hand C's remaining pitch sets are legato ties with no register onset.
- **Commando** (Rob Hubbard). Initiations equal the hand C's `newnote` calls that read a
  note. A legato-bit row only drops the gate.
- **Fuzz players** (`tools/fuzzplayer.py`). The log rebuilds the writes, and every register
  onset is an event.

## Limits

- Ties and slides that run no retrigger path are not initiations. Emomyst's legato ties are
  pitch sets the criterion leaves inside the previous event.
- The next-write fallback misassigns code that follows a voice's last write in unrolled or
  self-modifying copies to the next voice. Register onsets still delimit those events.
- An initiation is timed to the frame it executes in. Players that read a row some frames
  before sounding it show the read as a preparation only when it is a hard restart.
