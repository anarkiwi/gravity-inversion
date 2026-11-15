# Translation notes

## Data

`gen_ghouls.py` emits each array of the original's data with its first address
(`<NAME>_ORG`):

| Array | Addresses | Content |
|---|---|---|
| `blocks` | `$29e7-$60b1` | music init's block tables and the blocks it copies |
| `tables` | `$6cb7-$730d` | effect lists, frequency tables, command jump table, effect patterns |
| `pulse_default` | `$6a05-$6a0a` | default pulse width per voice, in a gap of the code |
| `stack` | `$6b1f-$6b30` | gosub return stacks, 3 low then 3 high bytes per voice |
| `hr_freq` | `$6bda` + `$1b` × voice | hard restart frequency per voice, in gaps of the code |
| `music` | `$730e-$86e8` | copied: song pointer tables and patterns |
| `patterns` | `$914f-$9e06` | copied: each song's patterns |

`music` and `patterns` are the unions of the blocks' destinations; init fills them.
`peek()` and `poke()` resolve an original address to its array through `segments[]`, so
the data's absolute pointers, reads past a table's end (notes above 96, deep gosubs) and
return stack overflow and underflow reach the same bytes as in the original. Other
addresses (the original's code, RAM it never uses) read 0 and ignore writes.
`tests/test_ghouls.py` checks per byte that the arrays hold only the original's data and
every byte it reads as data ([docs/footprint.md](../../docs/footprint.md)).

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

## Voice structure in the original

The voice routine is unrolled three times, at `$6234`/`$6421`/`$6610` chained by `JMP`; the
copies are identical up to operand strides (+1 zero-page bytes, +2 words, +7 SID, +`$1ED`/`$1EF`
own operands) and a `CMP #v` voice 0 omits. Command handlers `$6858-$6C75` are per-voice copies
too, and effect init patches the copies' operands through the table at `$622E`/`$6231`. The C
runs `play_voice(v)` per voice. `tools/copies.py` lists the copies; `tools/residue.py` (kind C2) checks the generated C recovers it.

## Verification

`make check` compares every SID write of all 32 subtunes, from init to each
HVSC song length, against the original (`tools/sidtrace.py`).

`tests/test_ghouls.py` also patches song 4 to play one pattern, at the start of its second
copy block, on all three
voices that drives the paths no subtune takes (listed with the pattern), and
asserts with `tools/survey.py` that the subtunes and that variant take every
branch outcome of the original except those no input can reach: the second
branch of each `BCS`/`BCC` pair and the hard-gate test after a hard restart.
`tools/mutate.py` runs edits of the C against these tests; the only survivors
are mutations of state no input observes (the music init filter limits, which
every filter command overwrites before the sweep moves).

## Gotos

The hand C has none. The rule ([docs/constructs.md](../../docs/constructs.md)) has three in each
voice copy, where the hand C uses a readability form: the vibrato's ADC arm past the SBC arm
(`$626F`/`$6272` → `$627A`; a `down` flag), portamento past and into the pitch store
(`$62C0`/`$62C8` → `$62ED`, `$62CE`/`$62D6` → `$62DA`; `set_note()`), and the pulse sweep's
second exit (`$630B` → `$6338`; `return` from `pulse_sweep()`).

## Generated translation

`make` also builds `build/gen.prg` from `build/gen/ghouls.c` and `.h`, generated from the PSID by
`tools/transliterate.py`; `make check` compares every subtune, and the tests the patched song.

## Residue

`tests/test_residue.py` checks the generated C's residue sites against `tests/residue_manifest.json` ([docs/residue.md](../../docs/residue.md)); PRG sizes are in [docs/sizes.md](../../docs/sizes.md).
