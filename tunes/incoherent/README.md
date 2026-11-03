# Incoherent Nightmare (tune 2)

Sascha Zeidler (Linus) & Fanta, 2016, GoatTracker 2 export. `src/incoherent.c` is the
playroutine in C; `gen_incoherent.py` generates `build/incoherent_data.h` from the HVSC PSID.

## Data

`DATA` is the original image from `$0ccf` to its end, byte-identical: frequency tables,
order list and pattern pointer tables, instrument columns, wave, pulse, filter and speed
tables, order lists and patterns. Addresses in the C are the original ones, read through
`at()`; tables indexed from 1 keep their base minus one, as in the 6502 code.

The player keeps state in code operands (filter pointer, step time, cutoff, control and
type; the init flag) and in variables its init does not clear (order list number, pattern,
note, gate mask, vibrato phase and delay, wave delay counter, frequency, pulse, AD/SR,
gate timer, last note; funktempo). `VOICE_INIT`, `FILTER_INIT`, `INIT_FUNKTEMPO` and
`INIT_SONG` carry the image's values; `INIT_TEMPO` and `HR_LIMIT` (the first instrument
without hard restart) are read from the code.

## Behaviour kept from the 6502 code

- Init runs inside the first play call after `tune_init` (song number stored in an
  operand); a song number with bit 7 set skips it, playing from the image's state.
- Order list `$ff` jumps read the target entry, which may itself be a transpose.
- Command dispatch follows the export's table: `B` and `C` both set the cutoff, `D` and
  `E` both set funktempo from the speed table; `F` with bit 7 sets one voice's tempo.
- A byte below `$40` after an instrument is taken as a command, like `$40-$4f`.
- The instrument vibrato (effect 0) tests the zero flag left by the speed load: the speed
  table's right column for absolute speeds, the speed index for note-relative ones. Speed
  index 0 reads the byte before each speed column.
- Note-relative speeds read the frequency table at the last note and the next, past the
  table's end for high notes.
- Toneportamento steps on overshoot with the carry of the overshoot test.
- Vibrato with an absolute speed drops the speed's high byte.
- The wave delay counter is not reset on new notes; a counter above the delay counts up
  to its wrap before the table advances.
- Wave table commands 0-4 replace the tick's effect; a wave note does as well; pulse is
  skipped on the tick the next pattern row is read and on tick 0 while the pattern
  position is 0.

## Voice structure in the original

The channel routine `$0996` is X-indexed: play calls it with `JSR` for X = 0 and 7, then sets
X = `$0E` and falls into it, its `RTS` returning from play; init does the same into `$0922`. The
C calls `voice_play` per voice. `tests/test_structure.py` xfails while the generated C
specialises the fall-through copy.

## Verification

`make check` traces the PSID and the C64 PRG under jennings and compares every SID write
per play call over the HVSC length. The tests compare over twice that, through the order
list jumps, without init, and on patched songs: instruments without a first waveform or
with a gate-only one, effects from the wave table, note-relative speeds with a widened
frequency table (toneportamento carries, speed index 0), a nonzero byte before the speed
right column, a wave delay below the counter, vibrato rows between porta rows, and a
command byte below `$40`.

`tests/test_size.py` checks `src/incoherent.c` is under twice the size of the jennings
listing's instruction lines for the image.
