# Data relocation

`tools/relocate.py` moves a player's data off its code's pages after init, by whole pages, and
packages the result as a play-only PSID of one song, or a PSID of several songs (`--songs`).
The lifter bounds a pointer by whole pages (its high byte from known tables, its low byte plus
Y anything), so data sharing pages with code lets play's pointer reads reach the code; after
relocation they cannot. `tools/playtables.py` traces play and finds its tables and the
instructions reading past them; `tools/pointers.py` points entry pointers at what they reach.

## Tables and the overrun rule

Every table play indexes is bounded to its own extent: a read past its end reads the table's
last valid entry (of a record table, the matching byte of its last record, the record
repeated), and a write past it writes nothing. The tune keeps playing, and its output may
differ from the original's where play reads past a table.

| Step | What |
|---|---|
| starts | the bases of play's indexed accesses: an `abs,X`/`abs,Y` operand, each `(zp),Y`/`(zp,X)` instruction's least pointer word; a base joins the table before it when it lies below every address that table reached (`LDA tbl+4,Y`), or less than its stride past the table's start (fields of one record, `LDA ins_ctrl,X` beside `LDA ins_pwl,X`); the stride is the greatest common divisor of the distances between the addresses each instruction reached, over the instructions whose addresses are not consecutive (a loop clearing every byte shows no records) |
| extent | from a start to the next start or code byte, ending at the last address an indexed access based in it reached there, or that its instruction can reach by deity-informant's lift of the original (`Program.addr_sets`: bounds from code and read-only data, such as a list through its terminator), whichever is further; an unindexed access lies in the table holding its address, else is a table of its own byte |
| last record | a table's last whole stride when its stride is a record's (more than one byte, within the table), else its last byte |
| overruns | per instruction with an access past its table's end: its address hull, its tables and their last records (`{pc, addr, ok, last}`, `last` a (first byte, length) per run of `ok`) |
| rule | sidtrace's `overrun_remap` (a jennings `Remap` of `overrun_sources`, the pointer bytes of `(zp,X)` and `(zp),Y` read as they are): an access outside a site's runs reads the last record of the nearest run below it, repeated (`last` an address: that byte; no `last`: 0), and writes nothing; play is traced again under the rule until the sites it finds are those it ran under |

## Mechanism

| Step | What |
|---|---|
| state | init of each song (default the start song) on sidtrace's C64 (`transliterate.c64_init`): the memory it leaves, its volatile writes, the addresses it stores |
| trace | play over each song on its state under the rule: each data access by instruction with its base (jennings' native access log, `run_to_rts_accesses(..., remap, bases=True)`, the rule a `Remap`), the instructions run, over all songs |
| blocks | tables whose accesses' address high bytes sum a common byte (a pointer walking several tables, one `LDA #>` for two) must move by one Δ, so join, as do a table and the one a store past its end lands in (the store must land there), and tables a joined hull overlaps; a block holding code stays; a block's pad is the bytes past its end its tables can reach (an absolute indexed base plus 255, an indirect access as traced; a page for a block an entry pointer's copy goes in), holding its last table's last record repeated. An instruction reading past its table only within its own block reads as it stands |
| stays | a block holding ROM play reads (its bytes stored to RAM when the CPU port maps RAM there), or bytes play patches a branch offset or jump operand from (the lifter reads those targets over the table's static index range, which a moved table's pad would not answer); the instructions of a block that stays read past it as they stand, and the blocks they reach stay too |
| free | every byte but zero page and the stack, $0200-$07FF (below the lowest PSID load), code pages (play's, and init's for several songs), I/O at $D000-$DFFF, the vectors $FFFA-$FFFF, the bytes play accesses outside the blocks that move and, for several songs, every byte init touches; BASIC and KERNAL ROM where init leaves them mapped and play runs them. When play runs no ROM init maps, the stub (or fix-up) sets the CPU port to RAM with I/O (`$35`, init's other port bits kept), freeing $A000-$CFFF and $E000-$FFF9 but for the ROM bytes play reads, which it stores to RAM there with the ROM's values. Bytes vacated by moved blocks are free |
| moves | in address order, each block stays in place (Δ 0) when it and the bytes past its end its accesses reached (its pad there) lie on free bytes (for several songs, bytes init touches aside: the fix-up remakes what init changes); else by Δ, a multiple of 256: the least putting it and its pad past the previous block's pad from the image's end, else from $0800, on free bytes; touching blocks without pads keep one Δ. A block no free memory holds raises, naming the reasons holding the window it comes closest at |
| sources | `Sources`, deity-informant's `postinit.Run` with forms for tags under the rule: each value is a sum of image bytes (an immediate is its operand's byte); carries come from low bytes, which page moves keep; runs of every song merged |
| rewrite | per access, its address's high byte must move by its block's Δ>>8 (the block of the table its base lies in; 0 for others): `solve` shifts the bytes that sum, a byte when every constraint holding it asks the same shift, the most shared first; a compare whose flags a shift changes adds its bound (`CMP #>end`) |
| fixpoint | a block an unsolved access reaches stays (`address cannot move`): the accesses no form explains, those left unsatisfied, and those whose high byte sums a byte of an unsatisfied constraint; placement and rewrite repeat until none is left |
| entries | each zero-page pointer play reads through by `(zp),Y` or `(zp,X)` whose post-init word play never reads (it stores the pointer first, so no shift holds it), with the offsets its accesses used: when the bytes the word reaches lie in moved blocks of one Δ, its high byte moves by that Δ; when some lie outside every block (code, typically) or in moved blocks, and all are RAM play never writes, they are copied a whole-page move away into the pad of a moved block the pointer's instructions access, its high byte moved to match; otherwise it is left (`left` in the summary). The lifter takes init's word as the pointer's value on entry to play, so a word in the loads' object keeps those loads bounded to it |
| package | init goes in the first free bytes past the last pad (else below) that no ROM is over while init runs (CPU port `$37`); the image spans the original's, the moved blocks (of one song, those in place too) and init; pads repeat their block's last record (copies made like the blocks'), freed bytes are 0. One song: load image the relocated post-init memory; init a stub storing the bytes init stored (or a shift rewrote, entries' high bytes among them) outside the image, init's volatile writes in order, then the CPU port; songs 1, the song's speed, the original play. Several songs: load image the original's with the shifts applied and the blocks and overrun bytes copied by Δ (old bytes kept, for init); init calls the original init, then a fix-up: each byte outside free memory, the stack and I/O that init leaves differing from the original's post-init state with the shifts applied, by one difference every song leaves (found by running each song's init on the image), gets it added in place (`CLC`/`ADC #`), then the copies whose source or destination some song's init changes are made again, then the CPU port; the header's songs and speeds |

An address high byte no form explains (a shift, a mask, a decimal add) keeps its block where
it is; an access the fixpoint cannot keep is reported unsolved and nothing is written.

One relocation serves several songs when its trace and provenance cover all of them: the
tables span every song's accesses, entries need one Δ for every song's word, and the fix-up
holds no per-song state, so its size does not grow with what each song's init leaves. A byte
whose difference depends on the song is an error.

A read inside a table of a byte the relocation shifts (a pointer's high byte an unindexed
load names, read by a neighbour's index) hears the shifted byte: no rule covers it, and the
oracle under the rule reports it. A pad holds the last record as the relocation leaves it,
so a read past a table whose last record play rewrites hears the value it had then, where
the rule reads the current one.

## Proof

`--check` runs each song of the relocated PSID with each vacated byte (not reused by a moved
block or pad) subscribed, and poisoned (`$FF`) for one song (the original init of several
reads them): any data access there by play is reported with its pc, address and frame. Its
SID writes are compared with the original's under the rule of its tables (`overruns` in the
summary, sidtrace's `overrun_remap` on the original): a mismatch fails. The comparison with
the original as it stands is reported, not judged: it differs where play reads past a table.
With `--captures ROOT` (and `--hvsc`) that comparison renders the relocation
(`pysidtracker.survey.file_oracle`) against the precomputed sidtrace capture of the original
song, `ROOT/<HVSC path>/<tune>.<song>.csv.zst` (found by the tune's MD5 in Songlengths.md5),
over the capture's play calls (`aligned_match`); a song without one, or whose original does not render as it, is traced.

## Use

    python tools/relocate.py fixtures/Emomyst.sid tunes/emomyst/build/reloc.sid \
        --hvsc fixtures --times 2 --check --report tunes/emomyst/build/reloc.json

Lengths come from `--frames`, `--hvsc` or `--wrap LABELS LABEL` (as sidtrace's, the frame by
which every voice's track list has wrapped); `--times` multiplies them. Several songs:

    python tools/relocate.py fixtures/Ghouls_n_Ghosts.sid tunes/ghouls/build/reloc.sid \
        --hvsc fixtures --songs all --check --report tunes/ghouls/build/reloc.json

The relocated PSID has no HVSC entry, so its lift is checked for a frame count per song:

    python tools/transliterate.py tunes/emomyst/build/reloc.sid tunes/emomyst/build/reloc \
        --header emomyst.h --list tunes/emomyst/build/reloc/units \
        --overruns tunes/emomyst/build/reloc/overruns.json
    python -c "import pathlib, transliterate as t; t.compile_prg([pathlib.Path(l.strip()) \
        for l in open('tunes/emomyst/build/reloc/units')], flags=('-Wall', '-Wextra', '-Werror'))"
    python tools/sidtrace.py tunes/emomyst/build/reloc.sid tunes/emomyst/build/reloc/gen.prg \
        --frames 11126 --overruns tunes/emomyst/build/reloc/overruns.json

Outputs stay under the tune's gitignored `build/`: they hold the tune's data.
