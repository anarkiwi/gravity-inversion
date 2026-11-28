# Generated songs

`tools/gensongs.py` makes GoatTracker 2 songs from a seed, packs each with the real GoatTracker
2.76 playroutine into a PSID, writes the packer's layout beside it, and runs two checks of GI's
tools on them. The songs are generated, not tune data: the seed and the generator are the
record, and the PSIDs are written only under `out/`.

    gensongs.py generate DIR [--seed S] [--count N] [--size tiny small medium large]
                [--mode fixed|sweep] [--features A,B,...] [--build tailored|full]
    gensongs.py check-a DIR [--jobs N]
    gensongs.py check-b DIR [--jobs N]
    gensongs.py certify DIR... [--jobs N]
    gensongs.py summary DIR
    gensongs.py smoke DIR [--jobs N]        # make songs-smoke

Every subcommand takes `--json PATH` to also write its result there; each check writes its
result into `DIR` (`check_a.json`, `check_b.json`, `certify.json`), which `summary` prints. Jobs
default to the CPUs `os.sched_getaffinity` gives, each process with
`OMP_NUM_THREADS=MKL_NUM_THREADS=OPENBLAS_NUM_THREADS=2`.

## Packing

pygoattracker's `songimage.song_image` lays a `TypedSong` out as GoatTracker's relocator
(greloc) orders it, every table in a fixed slot from `$1000`: the nine instrument columns
(64 bytes each), the wave, pulse, filter and speed tables (256 each, row 1 at the slot's
second byte), the frequency table, the orderlist and pattern pointer tables, then the
orderlists and the packed patterns. `tools/gtpack.py` assembles `src/player.s` from the pinned
GoatTracker 2.76 release archive (`make fixture` fetches it into `fixtures/`, SHA-256 checked)
at `$8000` with those data labels and the relocator defines, over pygoattracker's parse of the
source (`gt2sources.parse`), passes repeated until the labels settle and an operand known
below `$100` taking zero page. The bytes before the first table, which `song_image` fills for
pygoattracker's playspec, are zeroed. Init is `mt_init` (A the subtune), play `mt_play`; the
PSID declares one song per subtune.

A build's defines are the full-featured build's (every `NO...` define 0) with the given ones
over them. `NOWAVEDELAY` packs the waveforms as greloc does (no `+$10`), and `NOEFFECTS` packs
no pattern commands.

## Layout

`DIR/<song>.json` is what the packer placed, for comparing later with a lifter's tables:

| Key | Content |
|---|---|
| `load`, `init`, `play` | the PSID's load address and entries |
| `code` | the playroutine's `[start, end)` (its variables are inside it) |
| `defines` | the relocator defines the playroutine was assembled with |
| `tables` | `{name, address, length}` per table play reads, by its `player.s` label: `mt_ins*` (one byte per instrument), `mt_wavetbl`/`mt_notetbl`, `mt_pulsetimetbl`/`mt_pulsespdtbl`, `mt_filttimetbl`/`mt_filtspdtbl`, `mt_speedlefttbl`/`mt_speedrighttbl` (one byte per row), `mt_songtbllo/hi` (one per orderlist), `mt_patttbllo/hi` (one per pattern), `mt_freqtbllo/hi` |
| `orderlists` | `{name, address, length, subtune, channel}`, each with its `$FF` and restart byte |
| `patterns` | `{name, address, length}`, each with its end mark |

Instruments are laid in greloc's class order (hard restart, none, legato), which the generator
already makes them in.

## Songs

A song is drawn from `numpy.random.default_rng([seed, index])`. Its features, each a
greloc define it clears:

| Feature | Song data | Defines cleared |
|---|---|---|
| `gate` | key-off and key-on rows | `NOGATE` |
| `transpose`, `repeat` | orderlist transposes, repeats | `NOTRANS`, `NOREPEAT` |
| `nohr`, `legato` | an instrument without hard restart, a legato one | (`NUMNOHRINSTR`, `NUMLEGATOINSTR`) |
| `firstwavecmd` | an instrument's first wave none, gate-off or gate-on | `NOFIRSTWAVECMD` |
| `wavedelay`, `wavecmd` | wavetable delays, wavetable commands | `NOWAVEDELAY`, `NOWAVECMD` |
| `pulse`, `pulsemod` | pulse programs, their modulation | `NOPULSE`, `NOPULSEMOD` |
| `filter`, `filtermod` | filter programs, their modulation | `NOFILTER`, `NOFILTERMOD` |
| `instrvib` | instrument vibrato | `NOINSTRVIB`, `NOVIB` |
| `portamento`, `toneporta`, `vibrato` | commands 1/2, 3, 4 | `NOPORTAMENTO`, `NOTONEPORTA`, `NOVIB` |
| `calcspeed`, `zerospeed` | a calculated speed row, a tone portamento of speed 0 | `NOCALCULATEDSPEED`, `NOZEROSPEED` |
| `setad` ... `setvolume`, `timingmark` | commands 5 to D | `NOSETAD` ... `NOSETMASTERVOL` (and `NOPULSE`/`NOFILTER` for 9 to C) |
| `tempo`, `chantempo`, `funktempo` | command F global and per voice, command E | `NOGLOBALTEMPO`, `NOCHANNELTEMPO`, `NOFUNKTEMPO` |
| `multisong` | two or three subtunes | (`NUMSONGS`) |

`NONORMALSPEED` is cleared where a slide or vibrato names a fixed speed row. A song carries
each of its features at least once: the events its features need (each command, gate rows,
each playable instrument) are spread over its patterns before the other rows are drawn, the
first instrument carries the instrument and table features, and every pattern is played by
the first subtune. `gensongs.uses` reads the features back off the song's data, as greloc reads
them (only the instruments the patterns play), independently of the generator.

Size classes set the patterns, rows, orderlist entries and table program rows. In a `tiny`
song every voice plays one pattern of the same rows, one tick each, behind the same transpose
and repeat, and table programs loop to their start, so the joint state comes round quickly;
the larger classes give each voice its own patterns, rests of several rows, orderlists of
several entries and restarts, and programs that stop or loop to a later row.

### Builds and modes

- `--build tailored`: the defines `gensongs.tailored` reads off the song, as greloc sets them:
  each feature the song does not use removed, `NUMSONGS` its subtunes. `NOEFFECTS` stays 0: a
  `NOEFFECTS` build plays some songs (tiny sweep seed 0, index 9) differently from the
  full build and from pygoattracker's playspec, so it is not used.
- `--build full`: `gensongs.FULL`, every feature's code whatever the song uses, and every song
  has an instrument of each class, so the playroutine is the same bytes for every song of any
  size or features.
- `--mode fixed`: every song uses exactly `--features` (and what they need) with the same
  instrument classes and subtune count, so every song is played by one playroutine; `generate`
  fails unless the playroutine bytes are identical across them and the manifest lists its one
  SHA-256.
- `--mode sweep`: `--count` songs per step of the feature list, from none, one feature added a
  step, so the songs between them exercise every feature the generator knows.

`DIR/manifest.json` lists each song's name, size, build, seed, index, features, the features
read back, and its playroutine's SHA-256, and the distinct playroutines.

## Check A: certificate against brute force

For each song state (`liftcheck.song_states`), play is certified and verified as
`forced.proven` does it (`jennings.coverage.certificate.certify` excluding the stack and
`transliterate.VOLATILE`, then `verify`), within the size class's calls
(`songchecks.CALLS`) and CPU seconds (`songchecks.SECONDS`, `setitimer(ITIMER_PROF)`). The
plain orbit runs independently of it: `jennings.memo.orbit` with the registers, the stack page
and the addresses calls store to as the state, those found by rerunning until no call stores to
a new address, to the whole state's first repeat (`songchecks.ORBIT` calls at most). A certified
state is equal where the certificate's access keys (`pc`, load or store, address) and edges are
exactly the orbit's. Each state reports the certificate's form (`Certificate`, `Segmented`,
`Open` or `timeout`), its calls, the verdict, the orbit's end and calls, and the seconds of
each. An unequal state writes the song's seed and the differences to
`DIR/findings/<song>.<state>.json`.

## Check B: coverage against the songs

Over the songs of one playroutine (a full-build sweep holds it constant; otherwise songs are
grouped by their playroutine's SHA-256 and checked per group), the union of the instructions
the songs' plain orbits run is what that player does. From the group's first song, the plain
one, play's instructions are counted certified (`forced.proven`), with forcing
(`forced.report`) and with walking back (`walkback.report`). The result lists both differences
with addresses and their nearest `player.s` label: `tools_only`, code the tools reach that no
song runs, and `songs_only`, code songs run that the tools miss, and walkback's unresolved
guards.

## Timing

`certify` runs check A's certification alone over the songs of one or more directories and
tabulates, per size class, the states, the forms, the calls and seconds (median and most) of
those certified and the states not certified within the class's limits, with their seeds.
Check A's results give the same table. Each result records the host's load averages before
and after.

## Tests

`tests/test_gensongs.py` checks the features read back equal those generated, one playroutine
per fixed set and for the full build, the packed 6502 image's SID writes against
pygoattracker's playspec of the song for each subtune of sweep songs, the layout against the
image (instrument columns, speed and pulse rows, orderlists and patterns read back with
pygoattracker's decoders), every load of play inside the layout or the playroutine, and runs the
smoke run.
