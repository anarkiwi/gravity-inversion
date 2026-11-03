# Translation notes

## Data

`SONG_DATA` is the assembled image from `_songdata` to the end: track lists,
pattern pointer tables (`patpl`/`patph`), patterns and instruments, unchanged. The
pointer tables keep their original 16-bit addresses; `at()` resolves them relative
to `SONG_ORG`. The frequency table and voice offset table (`sid`) are likewise copied
from the image.

Instrument pulse widths are modulated in place, as in the original, so `song[]` is
writable.

## Behaviour kept from the 6502 code

- Vibrato takes the depth from bytes `freq+3` and `freq+4` relative to the note
  (`freqsh_2`/`freqsl_2`), i.e. the high byte of the next note and the low byte of the
  note after. For the top note this reads past the table into `posoff`, so the C state
  struct places the per-voice arrays directly after the table in the original order.
- Pulse-width-low modulation (`fx & 8`) adds the 6502 carry left by the vibrato code:
  bit 5 of the instrument number when vibrato is off, otherwise the comparison or the
  last 16-bit add of the vibrato sweep.
- Drum, skydive, pitch bend and pulse sweep write registers in the original order,
  including the skydive write of the frequency before its increment.
- A `$fe` track byte stops the tune; the next play silences the voices and sets
  volume `$f`.

## Verification

`tools/sidtrace.py` runs the reference PSID (init, then one play per frame) and the
C64 PRG (one play per return from `frame_wait`) on the same emulated C64, recording
every write to `$d400-$d41c`. The tune never stops, so comparison runs to
the frame at which the last voice wraps its track list.

The tests also patch the assembled PSID to exercise paths the tune does not use:
a stop byte, and skydive, vibrato with pulse-width-low modulation, a 6 frame
vibrato note and the top note (read past the table) on the bass instrument.

## Generated translation

`make` also builds `build/gen.prg` from `build/gen/commando.c` and `.h`, generated from
the PSID by `tools/transliterate.py`; `make check` compares it to the tune loop.
`tests/test_gen.py` checks that:

- the song variants above give the same `.c` (only the `.h` differs) and identical writes;
- the code permutations in `permutations.json` (self-modified pattern reads and an
  opcode cell, `JMP (ind)` and RTS dispatch, illegal opcodes), each and all together,
  keep the original's writes, and their generated C does too, alone and crossed with
  both variants;
- assembled at `$2345` the `.c` is unchanged and the writes match that PSID's own (the
  original reads an address high byte as data, so its writes depend on the load address);
- the `.c` has no `goto` and is under twice the playroutine source.

## Size

`tests/test_size.py` checks that `src/commando.c` is under twice the size of the
original playroutine source, from `TRACK` to the frequency table, comments included.
