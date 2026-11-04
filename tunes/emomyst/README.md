# Emomyst

Hermit, SID-Wizard 1.x, SWP export (player at `$1000`, music at `$1c00`). HVSC
`MUSICIANS/H/Hermit/Emomyst.sid`.

## Data

`gen_emomyst.py` writes `build/emomyst_data.h` from the PSID:

- `MUSIC`: the SWP music data from the base init loads into X/Y (`$1c00`) to the
  end of the image. Its pointers are base-relative; `tune_init` resolves the table
  addresses from the SWP header as the original's relocator does (subtune 0
  orderlists, pattern/instrument pointer tables, chords, tempo table and tempo
  programs). Tempo effects write into the tempo table, so `music[]` is RAM.
- `TABLES`: the player's tables as loaded, `$1830` to 255 past the frequency-low
  table (`$19d0`): the slowdown ADSR conversion tables, the exponent table,
  frequency high/low. 8-bit indexed reads run past the frequency table into the
  player code that follows, and the tune itself reads those bytes as frequencies.

## Behaviour kept from the 6502 code

- Voice state keeps the original 7-byte bunches per voice (`ch[x + field]`), so the
  filter keyboard track reads `PITCH + $0f` (voice 3's instrument column) while no
  voice controls the filter.
- The 6502 carry is modelled where it leaks: AD/SR conversion through the slowdown
  tables, slide/portamento ends (an extra 1 added or subtracted), filter sweeps,
  pulse keyboard tracking, the vibrato amplitude lookup, the `x3` table position
  effects.
- Values the original holds in self-modified operands (filter, resonance, volume,
  cutoff, filter controller and position, instrument control, fx nibble) keep their
  load-time values across `tune_init`, which does not reset them.
- The slowdown dither counter starts at 0, so the first play call after load plays
  nothing. Slowdown, sound effects and the track-tempo orderlist effect are only
  reachable through the host API and are left out.
- The first-frame waveform writes the frequency high byte indexed by the voice offset
  instead of the pitch. A pitch at or above `$80` writes frequency `$0101`. Note `$7f`
  is a portamento like `$78`. An orderlist byte `$80-$fd` restarts the current
  pattern. Big fx `$11` stores the main tempo without its restart bit.
- The note-fx branch operand at `$1952` is mirrored into `TABLES`, since frequency
  reads with pitch `$81` reach it. The big-fx jump operand at `$19d0` (frequency low
  of pitch `$ff`) keeps its load value.

## Voice structure in the original

Play calls the track routine `$124D` with X = `$0E`, `$07`, `$00` (`$11F4-$11FE`); init loops
over the voices (`$1143-$1160`). `seq_read` (`$17C8`) picks one of three per-voice paths by X,
and the slide effects enter a shared tail at `$1AB4` with Y preset. `tests/test_structure.py` checks the generated C recovers it.

## Gotos

The hand C has a goto exactly where the original branches along an edge C has no structured form
for: not a fall-through, not the arm of an if/else rejoining at its immediate post-dominator, not a
loop's single exit (`break`) or back edge (`continue`), not the procedure exit (`return`). Each is one
6502 branch, annotated with its address at the goto. The kinds here:

- a branch past a block other paths join: `done` (past the wave store `$196C`), `pitch` (past
  `$1705`), `end` (past the pitch joins), `note` (past `$12B5`);
- a second entry into a straight-line sequence entered at its top by fall-through: the arpeggio's
  `relative`/`absolute` (`$1760`/`$1764`), the filter and pulse table steps' `advance`, `set_pos`
  and `write` (the pulse step's `advance`, and `write` from `$1688`, have structured forms), and the frequency slide's shared add/subtract `up`/`down`/`store` (`$15BF`, `$15A4`,
  `$15CE`), each reached from the slide, portamento and vibrato decisions;
- a loop's back edge from inside a branch when the loop has other exit targets: `row` (`$1749 JMP
  $16E3`);
- a loop's second exit target: init's `next` (`$1151 BCS $115B`; the first exit is `$1158`).

Structured alternatives copy the code at the target into each source or set a flag tested later;
the goto is the original branch, adding neither code nor a test. `done` is also the 6502 tail call
idiom: `$19AB` is a subroutine the code also enters by `JMP` and fall-through.

## Verification

`make check` compares every SID write of the C build against the PSID under jennings
over the HVSC length and over twice that. The tests also patch the music to run
every branch arm of the player the tune never takes, except those listed as
unreachable in `tests/test_emomyst.py` (`DEAD`), and compare the C on each variant:
orderlist skips, loops to 0 and end, hard-restart gate-off, vibrato types, kept
PW/filter tables, table jumps, arpeggio NOP, chord return, high pitches, all note
fx, gate-off table pointers, every small and big fx including tempo effects.
A mutation check of `src/emomyst.c` (every single-operator and constant mutant,
against `make check` and the variant tests) leaves only equivalent mutants.

## Generated translation

`make` also builds `build/gen.prg` from `build/gen/emomyst.c` and `.h`, generated from the PSID by
`tools/transliterate.py`; `make check` compares it over twice the HVSC length, and the tests on each
patched variant; `gate_off_pointers` is a strict xfail (the generated C steps a filter cutoff the
original holds).

## Size

`tests/test_sizes.py` bounds the hand and generated PRGs by the original 6502 player ([docs/sizes.md](../../docs/sizes.md)).
