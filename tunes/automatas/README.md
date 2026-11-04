# Automatas

Goto80, 2009, defMON player. `src/automatas.c` is the playroutine in C; `gen_automatas.py`
generates `build/automatas_data.h` from the HVSC PSID.

## Data

`DATA` is the original image from `$1554` to its end, byte-identical: the 156-word
note/slide table, sidTAB pointer and wait tables, pattern pointer and arranger tables,
pattern and sidTAB streams. Addresses in the C are the original ones, read through `at()`.
The player keeps most of its state in code operands; init does not reset all of it, so
`VOICE_INIT` and `CUT_SUB` carry the image's initial values (pulse, frequency, slide,
note base, detune, filter routing bits, cutoff step direction).

## Timing

Init sets CIA 1 timer A to `$0998`, about 401 play calls per second; the C sets the same
latch and builds with `FRAME_CIA`. Every eighth call (from the first) also steps the
arranger and pattern rows; the original does this by patching an `RTS` into its own code.

## Behaviour kept from the 6502 code

- Init detects the SID model on raster line `$fc` from bit 0 of `$d41b`: on an 8580 the
  cutoff high byte is shifted left and clamped below at 0, otherwise clamped at 2.
- The cutoff step and the CP extra chain the 6502 carry: low step, high step, then CP.
  An absolute cutoff clears the step but keeps its direction, so a later step of 0 after a
  downward one still subtracts the borrow; the direction also survives init.
- Pitch mode 0 adds the detune to the low byte only, dropping the carry.
- A downward pulse sweep subtracts with the carry left by the pitch computation.
- Pitch and slide reads index the word table up to 291 entries, past its end into the
  following player code, which `DATA` includes.
- A pattern row with bit 7 ends the pattern for all voices after it is applied; the next
  full call reloads every voice's delay from its low nibble.
- An arranger jump is followed once; a sidTAB jump once.

Not reproduced: a sidTAB jump onto another jump row reads the row from page zero, whose
contents depend on the host.

## Voice structure in the original

Most per-voice code is unrolled, each copy holding its voice's state in its own operands: the
SID writes at `$1022`/`$1053`/`$1084` (`$31` apart, chained by `JMP`), the pattern rows at
`$112A`/`$11B2`/`$123A` (`$88` apart, fall-through), and six sidTAB copies from `$12BE`
(`$31` apart, each calling `$168C`). Pitch and pulse is a real loop over X = `$62`, `$31`, 0
(`SBX #$31`). The C loops over `struct voice`. `tools/copies.py` lists the copies;
`tests/test_structure.py` checks the generated C recovers it.

## Verification

`make check` traces the PSID and the C64 PRG on `tools/sidtrace.py`'s emulated C64 (so
init's raster wait runs) and compares every SID write per play call over the HVSC length, converted
to play calls at the CIA rate. The tests also compare through the arranger loop twice, and
patched songs: semitone pitch mode, a plain routing byte, high semitone offsets reading
far past the table, a large detune whose carry is dropped, and an 8580 SID model.

## Generated translation

`make` also builds `build/gen.prg` from `build/gen/automatas.c` and `.h`, generated from the PSID by
`tools/transliterate.py`; `make check` compares it over the HVSC length (built with `FRAME_CIA`), and
the tests through the arranger loop twice, on the patched songs and on an 8580.

## Size

`tests/test_sizes.py` bounds the hand and generated PRGs by the original 6502 player ([docs/sizes.md](../../docs/sizes.md)).
