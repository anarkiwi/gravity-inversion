# Translation notes

## Data

`gen_ghouls.py` emits the PSID payload as `IMAGE` with its load address
`IMAGE_ORG`. The C keeps it in `ram[]`, spanning `IMAGE_ORG` to `$a000`, and
reads every table and pattern through `peek()` at its original address: the
data holds absolute pointers, and music init copies blocks of the image to
`$730e` (song pointer tables and patterns, over the end of the image) and
`$914f-$9e07`, as the original does. Gosub return stacks, default pulse widths
and hard-restart frequencies live in gaps of the original code and are read and
written in `ram[]` at the same addresses, so stack overflow and underflow reach
the same bytes. Pointer tables, the frequency table and all reads past their
ends (notes above 96, deep gosubs) resolve as in the original.

## Player structure

The original unrolls one voice routine three times with per-voice zero-page
and self-modified operands. The C runs one routine per voice: the zero-page
state is `struct state` in its original order (`$21-$95`); state the original
keeps in instruction operands (vibrato and trill phase, pulse and filter sweep
directions, fixed duration, untransposed-note counter, filter limits) is in
named variables initialised to the image's operand values. Songs 0-10 are
music, 11-31 sound effects; the effect lists name voices by zero-page offset
0, 2 or 4. Commands `$80-$94` dispatch by number; bytes above `$94` index the
original's jump table out of range and are not supported.

## Behaviour kept from the 6502 code

- Vibrato adding into frequency `$ffxx` with a carry falls through into the
  subtract path, leaving the frequency unchanged.
- Pulse and filter sweeps bounce between their bounds within one call, with
  the original's tests: the pulse lower bound checks the sign of the high byte,
  the filter lower bound only fires when the high byte equals the limit, and
  the upper bound accepts any low byte below the limit's.
- Trill adds to and subtracts from the current note itself, and replaces
  portamento on the frame its counter expires.
- Portamento and transpose arithmetic wrap at 8 bits.
- `CMD_DEFAULT_PULSE` stores both argument bytes into the low byte.
- `CMD_RAW_NOTE` decrements a counter: the next note is untransposed when the
  increment at that note reaches 0, so repeated commands transpose again.
- `CMD_REGS` writes `$d400 + register` for any register below `$80`.
- Notes without hard gate write no control register; hard restart writes the
  restart frequency at the note and the note's frequency when it runs out.
- Effect init clears only `$21-$90` and keeps the operand state; music init
  resets only the operands the original resets.
- The filter cutoff high byte is written rotated: `hi << 5 | hi >> 4 | lo >> 3`.

## Verification

`make check` compares every SID write of all 32 subtunes, from init to each
HVSC song length, against the original (`tools/sidtrace.py`).

`tests/test_ghouls.py` also patches song 4 to play one pattern on all three
voices that drives the paths no subtune takes (listed with the pattern), and
asserts with `tools/survey.py` that the subtunes and that variant take every
branch outcome of the original except those no input can reach: the second
branch of each `BCS`/`BCC` pair and the hard-gate test after a hard restart.
`tools/mutate.py` runs edits of the C against these tests; the only survivors
are mutations of state no input observes (the music init filter limits, which
every filter command overwrites before the sweep moves).

## Size

`tests/test_size.py` checks that `src/ghouls.c` is under twice the size of the
jennings listing of the original's executed code over all subtunes.
