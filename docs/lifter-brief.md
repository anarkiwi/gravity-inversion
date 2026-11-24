# Lifter design brief

Rules and mechanisms for lifting a player's play routine to C, collected by rewriting each tune by
hand to the target and recording what the lifter must do to produce it. Each tune's section lists
what its rewrite needed beyond the rules already here.

## Rules

1. **Lift after init.** Init runs on the machine. Play is lifted from the memory init leaves; init's
   code, relocation and patching do not constrain play's lift. With several songs, each song's
   post-init memory is its own input; init becomes its I/O writes and play's initial state per song
   (mechanism 14).
2. **The tune never stops.** A read past a table reads the table's last valid entry and a store
   outside its object does nothing. Output may differ from the original predictably; the reference
   is judged under the same rule (`sidtrace --overruns`). No audio judging.
3. **Every table is bounded to its own extent.** A table's extent runs from the operand base that
   indexes it to the next base or block. An index past it reads the last valid entry (the same
   field of the last record for a record table), including when the original reads live state
   there (commando's note 104 reads past the 96-entry frequency table into the voices' notes) or
   another table of the same data (automatas' slide speeds read on into the frequencies). The
   bases are those of all of play's instructions, run or not, in the original tune's addresses;
   an index with a floor the invariant proves (a command byte `>= $80` after `BPL`) moves its
   table's start to the operand plus the floor (a 1-based table whose operand is its start minus
   one, read after a zero test of the index). Bases a few bytes apart read at one index (`LDA
   $0CD0,Y` and `SBC $0CCF,Y`: the next entry minus this one) are one table read at the index plus
   the offset; the inner base does not end it. Split tables read at one index (frequency lo and
   hi) are one record table whose extent is the least of the parts'. Only an index that changes
   during play makes a base: a read whose index is constant data (a lookup at an instrument
   byte) reads fixed entries of whatever table holds them, is folded in the initializer and ends
   no table (emomyst: slowdown lookups at `$1850`, `$1880` inside the frequency tables).
   Tables are padded with their last valid entry, never with 0, and data is never pinned in place
   to preserve such a read. An index that is itself constant data is clamped in the initializer.
   One operand is one table: its start takes the least floor of the sites reading it (an
   instrument table read at `0..` and, after a zero test, at `1..`). A floor is the code's: the
   compare with an immediate deciding the path into the read (`CMP #$78`, `BPL`), or the static
   projection's least where that is one entry past the code's (a 1-based table); a higher least
   (notes from 4) moves no start. Of two bases read at one index value one entry apart, the one no
   other read uses alone is the inner one (emomyst `$185D` of `$185E`, incoherent `$0CD0` of
   `$0CCF`). A table no store writes is a `const` array padded to every index its reads can take
   (an 8-bit index: 256 entries, plus an inner base's offset), its last entry repeated: no
   runtime clamp remains and none is counted. A read indexed by the voice register offset reads
   the table at those fixed indexes; no extent or clamp is involved. A read through a pointer
   past a run of its object reads that run's last byte.
4. **Any unbounded pointer or access is a lift error.** Something was missed; diagnose and fix it.
   An access is bounded when its object or address set lies in one block, variable or table.
5. **Bounds come from the code and the read-only data.** List and table ends are stated by the
   code's terminator tests on bytes play never writes. They are recovered by an invariant over
   play's persistent state (see Mechanisms), never assumed unknowable.
6. **Decisions on constant data are made at compile time.** A test on bytes play never writes
   (instrument flags, pattern encoding) is resolved in a `static const` initializer or by decoding
   the data into typed records, so the compiler folds it and no runtime branch remains. The lift
   follows no orbit: a branch or computed jump is decided statically (deity-informant
   `decide.rs`, `info["decided"]`) by (a) read-only bytes at addresses the lift bounds (a pointer
   region's, a table's at its index values), (b) a cell constant where it is tested (init's values
   and every value play stores give one outcome), or (c) a counter or index whose static range
   excludes the arm. (d) An arm only the song's data excludes (no row sets it: emomyst's tie carry
   and glide test) is decided by decoding (mechanism 8) and stays a runtime branch until then.
7. **Only runtime decisions remain in code.** The floor for cognitive complexity is the number of
   decisions on values that change during play. Dispatch through types, vtables or handler tables
   chosen at runtime hides decisions rather than removing them and does not count as a reduction.
8. **Nothing merges until every access is bounded and play's cognitive complexity falls.**

## Relocation

`tools/relocate.py` (docs/relocate.md) prepares play-only PSIDs for the lift:

- player data moves off its code's pages by whole pages, so only pointer high bytes change;
- placement may use virtually all 64K: only code pages, zero page, the stack, I/O
  `$D000-$DFFF`, the vectors `$FFFA-$FFFF` and bytes play or init touch outside blocks are off
  limits; when play never executes or reads ROM the stub selects RAM with I/O (`$35`), and vacated
  pages are reusable;
- each moved table is padded with its last valid entry past its extent;
- init's zero-page pointer words are pointed at what they reach (copies into a pad when needed);
- operands of play instructions the trace never ran are rewritten like observed ones: each moves
  with the first block its static interval (operand plus the lift's index values) meets;
- accesses whose high bytes add one common base byte are one object, moved as one block by
  shifting the base, so table entries the trace never read stay valid;
- a protected run poisons and traps every vacated byte (jennings) to prove play no longer uses it.

## Mechanisms the lifter needs

1. **Pointer word sets.**
   - **A pointer pair is a temporary.** A zero-page pair (or a patched operand pair) is a global
     variable play reuses as an iterator; it is not one object. Split it into its def-use webs
     (the stores of each byte and the reads they reach, joined where one read is reached by
     several stores); each web is its own pointer variable, named and typed by the table its
     words come from. A table may have several webs (emomyst `$02/$03`: two over the pattern
     pointer table `$2477/$248C`, the row decode and the end-of-pattern test, and two over the
     instrument pointer table `$244E/$2463`). Webs need reaching definitions that keep a
     caller's definitions across a call of a procedure that never writes the location (its
     returns do not carry other callers' definitions back). A read reached by several webs is in
     shared code: clone the code per calling web (emomyst: the effect handlers under `$19AB`,
     reached with the instrument web and, while a voice has no instrument, `$1052 = 0`, with the
     pattern web left from earlier in the call); whether such a path runs is a question of the
     song data (mechanism 8), never a reason to merge the webs.
   - Input: a zero-page pair, or the operand pair of an absolute or absolute-indexed instruction
     play writes, loaded from two tables at one index (each plus a constant or an init-left base);
     `Program.words`/`ptr_sets` (zipped in `dispatch.rs`), `Program.regs` for Y.
   - Output: the set of zipped words, not their hull; the object is the union of
     `[w + y_min, w + y_max]`. When every word lies in bytes play never writes, each word starts
     its own `static const` array (to the next word or block), the pair is a `const uint8_t *`
     and its load is a pick from a `const uint8_t *const` table of those arrays built from the
     same zip. A word advanced by the bytes a decoder consumed is a cursor of its stream
     (mechanism 8) and stays in it.
   - Regions (deity-informant `pcells.rs`, `info["ptr_regions"]`): pointer cells are found from
     the code before the bounding passes, the orbit aside. A cell is a zero-page pair, the pairs
     `X` selects for `(zp,X)`, or an operand pair play stores; its bases come from the stores of
     its high byte along the definitions reaching its accesses: words zipped from tables at one
     index (each plus a base: a constant, an init-left byte or one play stores constants into),
     constants, copies of another cell (a stack of pointers indexed by a byte included), reads
     through another cell (what that cell points into); its own 16-bit add (`CLC; ADC lo; STA
     lo; BCC; INC hi`, `INC lo; BNE; INC hi`) keeps them. At entry it holds init's words and
     what any store left. Its region is the read-only block holding every base: an access reads
     the region at the pointer plus its index, past its end the last byte (rule 3), and a store
     there writes nothing, so the access is bounded by construction while the pointer is not.
     A base outside the held bytes is none (a table entry naming no data is never followed);
     bases in several blocks, or in code or variables, are reported (`ptr_region_notes`).
   - Exact pointers (`pcells.rs` `Regions::exact`, `Program.exact`): a pointer holds one of
     its bases on every path when each store of its high byte has its low byte's store in one
     straight run of code (each instruction flowing to the next alone, none entered another
     way), the two from one zipped table word, a constant or a copy of an exact pointer, and
     it never adds to itself, is read through another pointer, is null or names a word the
     region does not hold. Its read is then the region's `static const` array, padded with
     the region's last byte to its greatest base plus the index's greatest value,
     `region[(uint16_t)(p - lo) + y]`, with no clamp (mechanism 4). Not exact: a stream
     pointer stepped by its own add (ghouls, automatas), and emomyst's song pointer, whose
     pattern table words at index 0 (the 1-based table's floor is the orbit's) name no data.
   - Typed pointers (deity-informant `cgen/pointers.py`, `Program.ptr_cells`): a pair's webs
     are its reads joined to the stores their definitions name, the entry's value to the
     stores reaching the returns when a read sees it. A web read only through itself in one
     region, each store a zipped or constant word with its low byte's mate, never seeing a
     word init leaves, is one `const uint8_t *` in every function of the optimized module:
     `p = ptrs_<table>[i]` from a `static const uint8_t *const` table of `&region[off]` built
     from the zipped words (each clamped into the region, so a loose web's word naming no data
     stays in the array), `p = &region[k]`, and `p[y]` wherever the optimizer forwarded the
     word, a subroutine's read and an outlined piece's parameter included. Every word lies in
     the region, so the padded array bounds every read with no clamp. Typed: commando
     `$F0`/`$F2`, incoherent's two `$02` webs, emomyst's `$02` (exact and loose in one web).
     Left as bytes: a web with a self-add, copy or read through a pointer (ghouls, automatas),
     and pointers init's lift sets (emomyst's `$17CF` operands).
   - Check: no `cgen_out` object check remains on reads through the pair; liftcheck 0 unbounded;
     the orbit is a check: every access it observes lies in its region.
   - Cost: one pass over the zipped seeds; one walk of each pointer access's definitions.
   - Example: emomyst's song pointer `($02),Y` is an instrument or pattern pointer table entry
     plus the init-left base `$1C00`; ghouls' `$21/$22` is init's song-header word, stepped by
     `TYA; CLC; ADC $21; STA $21; BCC; INC $22`, copied into `$30/$31` and a return stack at
     `$6B1F,X`/`$6B22,X`, and reloaded through itself by a jump command: one region, the song
     data block.
2. **Play-entry state invariant.** Start persistent cells at init's values (per index for
   X-indexed per-voice cells), apply play's transfer function with read-only bytes as concrete
   constants and branch conditions refining values, join the exit state into the entry state and
   iterate to a fixpoint. It bounds list positions by their terminators, the bytes read there
   (pattern numbers), note values, voice indexes and call depths. A cell written only from
   constant bytes holds their union; a branch outcome no value in the set takes is dead code and is
   dropped, and a store only dead code makes leaves its cell constant data. A test whose outcome
   is the same in every reachable state at its site is decided at compile time even on a cell play
   writes (ghouls: whether a note reads a duration byte), and a cell single-valued at every read is
   that constant there (ghouls: a loop end's target). At decoded positions the site is (pc,
   record): the static projection of mechanism 8 gives each key cell's values per (pc, cursor
   partition), `sitevals`, and a test is decided per record from them; a key cell single-valued
   at a record is a row constant there (incoherent: the command and instrument in effect at each
   note row; emomyst: the instrument), and a key cell no other read uses goes. A cell no
   reachable read uses (none, or only reads whose value folds) is dropped with its stores.
   - **Orbit and components are checks.** With no input play is a deterministic map on its
     persistent cells (cells play writes that some call reads before writing, a cell read first
     in every call included, and the code bytes it writes), so its exact invariant is the orbit
     up to the first repeated entry state (`deity_informant.orbit`), and jennings' components
     (`jennings.coverage.components`: persistent cells, closures, feed cells, the calls covering
     every access) measure it. Neither is a source of bounds or decisions (rule 5; following play
     makes lifts slower by the calls it follows). Bounds, floors
     and site constants come from the code and the
     read-only data: the per-cell join, narrowed by the static projection of mechanism 8 where a
     cursor relates two cells (a pointer and its index). The join alone can put an index past its
     table where no state reaches (incoherent: last notes to 98 of 96 entries, 92 in the orbit), and
     a clamp for that is dead code. Every access and arm the orbit or the components observe must
     lie inside the static bounds (liftcheck's orbit check).
   - **No orbit in the lift.** Bounds are the code's (tables, pointer regions) and branches are
     decided statically (rule 6, deity-informant `decide.rs`): each path into a branch or
     computed jump, walked back to where it is entered another way, runs on a three-valued machine
     over the ops the transfer depends on, a value constant, unknown or a choice among (a) a
     pointer region's or a table's read-only bytes, (b) the values a kept cell holds (init's per
     song and every live store's) or (c) the analysis's value set of a register at the path's
     start; the arm no split of any path reaches is dead code, rounds repeating as dead stores
     leave cells. Cost: a bounded run per path (limits on paths, length and splits), the
     transfers in parallel. What the orbit decided beyond that is class (d), song data (listed
     below; per arm in the gitignored `out/branches/<tune>.txt`): those arms stay runtime
     branches until mechanism 8 decodes the song. liftcheck's orbit check stays: no walk takes an
     arm the lift dropped. The orbit's address sets also narrowed accesses (fewer last-entry
     sites, no variable test on region reads); that part of its complexity is the static
     invariant's to recover, not a branch's. Incoherent's cover lift with regions (cognitive
     2449) was the orbit's address set replacing a pattern pointer read's region with the 30-odd
     objects it visited, one test each; with no orbit in the lift the read keeps its region.
   - **Class (d), by tune** (branches and computed jumps the orbit takes fewer arms of than the
     lift keeps):
     commando (3) `$122B` `$12FB` `$1316`; automatas (8) `$122C` `$12D2` `$1386` `$1396` `$13C7`
     `$13EB` `$16FC` `$1709`; emomyst (36) `$11D9` `$11DB` `$125A` `$12F2` `$1327` `$1330` `$136D`
     `$13BD` `$1446` `$144D` `$1471` `$14A8` `$14B4` `$153A` `$1542` `$1565` `$15BD` `$161D`
     `$164C` `$1698` `$169D` `$16BE` `$16EA` `$1721` `$1789` `$17F2` `$17F7` `$180A` `$1811`
     `$1815` `$1951` `$1982` `$1999` `$19A6` `$19CF` `$1A13`; ghouls (27) `$6241` `$624F` `$628A`
     `$62FE` `$631E` `$6374` `$637F` `$6383` `$643C` `$645F` `$6477` `$64BB` `$650B` `$6561`
     `$656C` `$6570` `$65E0` `$661D` `$662B` `$664E` `$6666` `$66DA` `$66FA` `$6750` `$675B`
     `$6764` `$67CF`; incoherent (6) `$0874` `$087E` `$08A8` `$0A88` `$0AF3` `$0BDC`.
3. **Song data.** The bytes play reads, minus the bytes play may write, minus the bytes of the
   instructions play executes: one subtraction of three sets the lift already has (each load
   site's bytes, `stores`, `jennings.coverage.cfg.instruction_bytes` of `starts`). Tables in the
   code span are song data; executed bytes are not. Rules 5 and 6 rest on it. Each load site's
   bytes are its exact addresses (base plus the index register's value set, the pointer's word
   set plus `Y`), never its table's extent or its region's hull: a site whose bytes are a hull
   lacks its cursor's value set (mechanism 8), and that is the fact to compute.
4. **Objects per table.** Each table and each variable is its own object; play's variables never
   join a song block; a block's gap regions are their own arrays. Every load of bytes play never
   writes reads its object's `static const` array, never the block: a table site its table's
   (an inner base's reads at their offsets), a read at an index that is constant data the run
   of entries such reads cover (it ends no table, and is no read of the table holding the
   bytes: emomyst's slowdown lookups inside the frequencies), a base init leaves folded to its
   value (song tables at operands init writes). A pointer's region (mechanism 1) is its object:
   a read past it reads its last byte. A store's table is its own whatever its index (emomyst's
   `STA $2437,Y` at a cell init leaves): a store past it writes nothing.
   - How (deity-informant `tables.rs`, `printer.py` `zeroed`/`region_index`): a table is
     fixed when no store writes the byte of an entry its sites read (its start plus a multiple
     of the stride), a store with a table writing its own entries alone; so the columns of a
     record table no store writes are padded `static const` arrays beside the written ones
     (commando's instrument columns other than pulse width). The clamp helpers that remain
     are for tables play writes (commando's pulse width, ghouls' per-song blocks), pointers
     that are not exact, and region reads that may reach bytes a store writes (commando's
     pattern reads, at positions the code bounds only by 255, reach the instrument table at
     the region's end). A region array's subscript is still address arithmetic on the
     pointer's bytes (census raw-memory); the pointer as a `const uint8_t *` into it is
     mechanism 1's pick from a table of pointers.
5. **Structs.**
   - Input: per-site index register value sets (`Program.regs`), store and load address sets
     (`addr_sets`), table sites and strides (`tables.rs::sites`, `Program.owned`), per-copy
     families (`state.py classify`, `inst.rs`).
   - Rule: an index register whose address sets at a family of bases are `{b, b + s, b + 2s,
     ...}` makes the bases fields of one record array at stride `s`; bases at stride 1 with one
     entry per voice, or at the voice register offset, are `struct voice voices[VOICES]`;
     split tables read at one index are one record table. The register becomes a pointer to its
     record, assigned where play sets the register: a constant names its record, a copy or a
     multiple of the stride follows the source's pointer, a parameter's is passed by its
     callers, a cell's is stored beside it. It is never `recs[x / s]`; the register stays only
     for its other uses (a SID register offset). The pointer stays at every site, also where the
     orbit shows one record: site constants (mechanism 2) apply to cells, not to the register a
     routine is called with (a cell holding a voice index may fold). Init's accesses that reach
     a record otherwise (a clearing loop over every byte) leave it as bytes: init works on them
     and loads the records from them as it returns (rule 1). Pointer targets are one array per
     target with a table of them (mechanism 1).
   - Output: `struct` types and record arrays; record pointers in place of index arithmetic.
   - Check: census raw-memory 0 on state; sidtrace identity.
   - Cost: one pass over sites grouped by register and base family.
   - Examples: `struct instrument` ×8 and `struct freq {lo, hi}` (commando); emomyst's X = 14, 7,
     0 on 7-byte records is `struct voice`, and the filter-voice cell `LDX $15D2` folds to
     `voices[0]`.
   - How (deity-informant `printer.py` `Module.objects`, `CFunc.strided_access`,
     `state.classify`): with each variable its own object (`overrun="last"`) the candidates are
     also the data bytes play stores that an access reaches at one address per copy of at most
     `state.COPIES` copies, at any stride, never a byte of a table no store writes. Families of
     one shape are one struct (`state._merged`), so parallel per-voice tables at stride 1 are
     fields of one record array (commando's voice bytes from `$1461`, ghouls' `R1[k + v]`). A
     pointer-region load touches only its region, so it no longer blocks promotion as an
     unknown address, and bytes play stores are never a source of init's copies (`postinit`).
     Not yet: the voice struct's name where no voice register offset reaches it (`V0`,
     mechanism 15), and the record pointer for a parameter index (`V0[v5]`).
6. **Locals for transient state.**
   - Input: reaching definitions (`pdefs.rs`, `Def = (Loc, byte, writer pc or entry)`) extended
     to memory cells and rooted at each function's entry rather than play's; flag liveness
     (`opt.rs`, `wide.rs`).
   - Rule: a cell is local to function F when no read in F has F's entry definition among its
     reaching definitions, i.e. every read is defined earlier in the same invocation. A cell
     local to the voice routine needs no separate cross-voice check: one voice iteration is one
     invocation, so iteration k's write cannot reach iteration k+1's read. For unrolled
     per-voice copies F is each copy's region. A local of F that F's callers set right before
     the call is a parameter. A processor flag live from one block into another is a value
     returned and passed explicitly. A zero-page pointer whose every read is defined in the
     same invocation from a table entry plus an init-left base is a parameter referencing that
     entry's record; a read of it reached by the entry definition is a stale pointer and must be
     unreachable (mechanism 2), else it is a lift error.
   - Output: C locals and parameters; the cell leaves `struct voice`/state.
   - Check: liftcheck; sidtrace identity; the orbit check never sees a local's value carried
     from one call to the next.
   - Cost: one bitset dataflow per function.
   - Examples: the current voice's register offset and instrument; a carry left by a frequency
     add and consumed by a later `SBC`; emomyst's instrument mask, AD operand and detune operand.
   - Not yet done. Mechanisms 5 and 12 now make such cells scalars and fields (commando's
     `S_485`-`S_498`, the zero-page pointer bytes), so this pass takes them from state: per
     function, the promoted cells no read of which `pdefs` reaches from the function's entry
     (rooted there, not at play's), each declared at the function's entry and the
     scalar's stores and loads printed on it.
7. **Compile-time decisions** (rule 6): the residual of mechanism 8's records.
   - Input: mechanism 8's record graph and `sitevals` (key cell values per (pc, record)); the
     read-only bytes.
   - Rule: at each handler slot of a record, a branch is decided when its operands are
     read-only bytes, key cells single-valued at (pc, record), or key cells whose values all give
     one outcome; the slot's handler is the residual path. A computation entirely on static
     values (with its carries) is evaluated into a field. A read of a read-only table whose
     entries are equal at every index the site can take is that constant; the arithmetic the
     original does on the carries remains. A static value that only enters arithmetic with
     dynamic state is a field, not a handler. Each slot (call site) has its own handler; no
     handler array is indexed by a dynamic value. A path is cut into segments at static joins
     (post-dominators of decided branches); the record holds one `{handler, args}` slot per
     segment position, a skipped segment a no-op, so handlers number the sum of segment variants,
     not their product (a record of many optional fields is a list of `{setter, value}`
     segments). Records with equal content are one record.
   - Output: `static const` record initializers whose handler pointers and fields are constants;
     runtime calls are straight-line.
   - Check: every branch `out/zp/branches.py` classes as constant data or constant at its site
     has no C decision; sidtrace identity.
   - Cost: one walk of each slot's path per record.
   - Examples: commando's instrument fx bits, vibrato depth and pulse speed as an effect table;
     emomyst's ADSR through the slowdown tables, the all-zero vibrato speed table (`$1817`,
     `$1802`) leaving only the keyboard-tracking carries, the small-fx nibble merged over the AD
     byte of the instrument in effect, and hard restart as one start handler per tick call site
     (`start[2]`); a carry `3 + slide + C` from a compare on the row's fx is a field; automatas'
     sound records as `{setter, value}` lists with `ROUTE(x)`, `SWEEP(lo, hi)`; commando's
     `EVENT(info, arg, note)`.
8. **Const song data: static projection and records.** Everything play reads but never writes
   is `const`, as typed structures. Instruments, wave, pulse and filter programs, chords, order
   lists and patterns are one algorithm: partial evaluation of play once per value of each
   cursor into the song data (mechanism 3), with records keyed by those values.
   - **Split record fields by write set.** Input: a record table (base, stride, count) and play's
     store address sets (`addr_sets`, bounded by rule 4). Rule: reduce every store's address set
     inside the table modulo the stride; fields no store reaches form a `const struct`, written
     fields a mutable state array initialized from init's image; a field written for any record
     is state for all; an interval store over the table makes every field state. Output: reads of
     a field go to the array holding it. Cost: one pass over stores. Example: commando's
     `const struct instrument` of control, ADSR, vibrato, pulse speed and fx, and
     `struct pulse pulses[]` for the pulse width play modulates.
   - **Start from the song data, never from a classification of cells.** Song data is
     mechanism 3's set; nothing about it is discovered by searching play's states. A cell is a
     *cursor* because a load site into the song data takes its index or pointer from it (the
     reaching definitions of the site's index register or pointer bytes name it); never because
     of the shape of its stores, and a value that only accumulates and reaches I/O (a cutoff, a
     frequency, a pulse width) is never one, whatever it adds. A decoding that does not finish
     in seconds, or whose partitions outnumber its cursors' table lengths, has a wrong fact:
     find it; never raise a cap, reorder a queue or extend a timeout.
   - **Cursor value sets.** Input: mechanism 3's load sites, the reaching definitions of their
     index registers and pointer bytes (`pdefs`), play's stores, init's post-init values.
     Rule: a cursor's values are init's value and the values of every store to it; a store of an
     immediate gives that value, a store of a byte read from song data gives the bytes its site
     reads at its own cursor's values, an increment or add gives the values up to its table's
     last entry (rule 2), a copy gives the source's values. Iterate to a fixpoint over the
     cursors alone (one value set per cell and copy index, never a tuple of cells). Output: each
     cursor's value set and each load site's exact bytes. Example (emomyst): `$104E,X` indexes
     the order-list pointer words `$2477`/`$248C` stored to `$02/$03`; a byte read through them
     is stored to `$1052,X`, which indexes the pattern pointer words `$244E`/`$2463`; a pattern
     byte selects the instrument `$107C,X` indexing `$2448`; `$17CF`/`$17D3`/`$17D7` are the
     program operands. Cost: the sum of the cursors' table lengths.
   - **Static projection.** Input: the cursor value sets, init's post-init values (rule 1),
     play's IR. Rule: run play's transfer function once per cursor value with cells stored only
     from song data and immediates concrete, every other cell unknown and song data as
     constants; a branch on static values takes its one edge, a branch on an
     unknown takes both. A run is keyed by one cursor's value alone; every other cell is a value set, never a
     tuple of cells, and no run is keyed by another cursor's cells: a cursor's start values are
     its value set. Each cursor has a ghost
     key, its *record reference*: its value at the decode site (the row in effect), which keys
     sites that run after the cursor moves. Iterate to a fixpoint. Output: the record graph (cursor value
     -> path signature, fields, next values) and `sitevals` (key cell values per (pc, partition)).
     Cost: the sum over cursors of their values times the code reading through them;
     a cursor ranges over its bounded object and a value set grows to at most 256, so no
     whole-state enumeration and no emulation occurs. Check: every (pc, cursor, key value) the
     orbit visits is in `sitevals` (liftcheck's orbit check).
   - **Records.** Input: the record graph. Rule: a record is the bytes a decode path reads from
     its cursor value to the cursor's next store; its kind is the path signature (decided branch
     outcomes and static jump targets), its fields the static values flowing into dynamic stores
     or I/O writes (mechanism 7), its `next` the stored cursor values. Each program point that
     reads through the cursor or reads a key cell keyed by its record reference is a handler slot.
     A key cell stored from `ro[cursor + k]` is a cache: its reads are `ref->field` through the
     record reference (mechanisms 12, 13); a reference no slot reads after the cursor moves is
     dropped. A selector that never advances (an instrument number) is the zero-length case: its
     records are the residual of the code reading through it. A cursor through a record pointer
     (a program inside an instrument) keys by absolute address, so aliasing pointers share one
     decoding, and starts reached only from init's values are decoded with the closure of what
     they reach. Output: per stream a `static const` array of records in stream order, the cursor
     a record pointer, `pos += k` the next record, a jump, call or loop end a record holding its
     target reference, a return stack of references (depth bounded by its object, rule 4). A jump
     entry the decoder follows in the same call resolves into `next`; one that ends the call is a
     record of its own whose run moves to its `next`. Lift errors: a jump target inside another
     record's bytes; a key deciding a record's length multi-valued at a position (it needs the
     stream cloned per value). Check: sidtrace identity over the HVSC length and the orbit's
     closing frame under the lift's overrun sites; liftcheck 0 unbounded, 0 gotos. Cost: linear
     in records.
   - Examples:
     - commando: `struct event { start, info, arg, note }` in `struct pattern { events, count }`;
       tracks `struct track { steps, count, end }` with `end = stop_song` or `loop_track` from the
       `$FE`/`$FF` terminator chosen in the initializer (`TRACK_END(...)`).
     - automatas: program steps `{record pointer, wait}` with in-table jumps (pointer high byte 0)
       resolved into `next` and pattern starts renumbered to them.
     - incoherent: order entries `{pattern, transpose, next}` with `$FF` jumps resolved; a filter
       type entry and the cutoff after it are one record, its `next` past both; set records skip
       the time test because the time, a key counter reset at every program store and so in the
       program position's partition, is 0 at every set entry.
     - emomyst: wave, pulse and filter steps through the instrument pointer, 15 of the wave steps
       instrument 0's header reached from init's position 0 and 3, position 6 stored but never
       run still a record; a pulse `$FE` jump to a sweep entry spends its call and is a
       `pulse_jump` record; chords up to their `$7F` loop to their own start; rows read at tick 0,
       advanced at tick 1 and played at tick 2 are the row in effect `v->row` beside `v->pos`; a
       packed rest is a record of its own with its count, the row in effect while it counts.
     - ghouls: gotos are jump records, gosub/return push and pop references (depth at most 2),
       the fixed duration is a key deciding each note's length, the loop count is dynamic and a
       loop end's target is static; a record is a handler and two bytes or a reference (4 bytes).
9. **Flattening.** Nested blocks become small named functions with early returns; chained
   branches to one target become one `&&`/`||` condition; compare chains on one value become a
   `switch`. A function returning whether its caller goes on re-tests a branch already taken
   inside it, a C decision with no 6502 branch; it calls the code at the join instead (incoherent:
   tick 0 ends in the register writes or the per-tick work, the wave step in its effect).
10. **No gotos.** Shared tails become functions; multi-exit loops become `break`/`return`. Code
    that jumps between two phases (a sweep bouncing between its bounds) becomes a loop of the
    phases as conditions (`while (!up() && !down())`) entered after the first phase, not a loop
    testing a direction flag at its head: such a flag is a decision with no 6502 branch.
11. **Code as state.**
    - Input: code bytes play writes (`Graph.cells`, opcode variants `vars`, `dispatch.rs`
      computed transfers and their `origins`), mechanism 8's division and `sitevals`, mechanism
      6's locals.
    - Rule: an operand play writes is a variable. A data operand pair play writes is a pointer
      cell (mechanism 1): the instruction is an access through it, reading its region
      (automatas' row pointers in `LDA $FFFF,Y`, set from the zipped row tables, stepped by a
      16-bit add of `Y` and copied into the operands of three other loads). A code cell
      (opcode, jump operand, branch offset) whose stores are all key (mechanism 8) is static at
      its site: where `sitevals`
      gives it one value per record or per call, the instruction is that operation and the
      dispatch is the record's handler for that slot (transient patches: a `JMP` operand or a
      `CLC; BCC` offset loaded from a jump table by a command byte, an `RTS` stored around a
      `JSR`). Where it is several-valued and persistent it is a function pointer to the
      operation each stored value performs, assigned at its stores (on constant data, so in the
      initializer) and initialized from init's value; the site calls it with no decision.
      Opcodes stored together from one value are one pointer (mechanism 12). A data cell selects
      code when its value flows into a jump operand or a branch offset; a cell dispatched by
      compare branches stays data and its dispatch stays branches, however its other compares
      fold. A transient operand loaded from a record is a field of that record, and a flag the
      load leaves for the handler is a decided branch of the record (mechanism 7). Opcodes and
      operands only init writes are constants (rule 1); a per-copy operand a song never stores
      to is the same field in every copy of unrolled code.
    - Output: direct calls of record handlers, or operation pointers; no flag tested at the site.
    - Check: `out/zp/branches.py` counts no decision at a written opcode; sidtrace identity.
    - Cost: one pass over code cells after mechanism 8.
    - Examples: automatas' `ADC`/`SBC` pair of a 16-bit sweep is one `sweep` pointer to
      `sweep_up` or `sweep_down`, and its `RTS` over the tick entry is control flow;
      incoherent's patched `JSR` low byte (`$09B5`: `$0C4F,Y` at the command) is each command
      record's `run` and the effect `JMP` low byte (`$0C36`) the voice's `effect` pointer, with
      the vibrato depth `CMP #` and shift count `LDY #` speed fields and the `Z` of a speed load
      deciding effect 0; emomyst's `JMP $19CF` and `BCC` offsets at `$1951` and `$1A13` are
      row handlers; emomyst's slide type stays compare branches.
12. **One field per value.**
    - Input: store sites with value numbers (`opt.rs`), the store-value dependence graph
      (mechanism 8).
    - Rule: cells whose every store stores one value number, or a fixed shift or mask of it,
      at one block are one field; reads of a copy read the field. Cells stored from the
      parts of one wider value (a high byte and its low bits, with a carry kept across them) are
      one field of the combined width and code on the parts is arithmetic on that field. Cache
      cells stored together from one record are one record reference (mechanism 8).
    - Output: one C field per value.
    - Check: sidtrace identity; census counts no copies.
    - Cost: one pass over stores grouped by block.
    - Examples: operand copies of one pointer; `ASL` copies of one header kept as branch
      operands; emomyst's filter mode (`$1204 = a << 4`, `$120B = a & $70`) and the 11-bit
      cutoff (`$121F` and its low bits `$1247`, the sweep one signed add).
    - How, the first part (`printer.py` `Module.objects`): with each variable its own object a
      data byte play stores and reaches only at one address is its own `static` scalar, as a
      code cell is, not a byte of a region array (ghouls' `R1[k]` cells, the zero-page pointer
      bytes). Not yet: value-number groups (several cells of one value), wide fields, and the
      pointer bytes as one `const uint8_t *` (mechanism 1).
13. **Caches of constant data disappear.** A cell loaded from constant data at a position that
    cannot change before the cell is read is replaced by reading the data at the use; the branch
    that only loaded it goes with it.
14. **Several songs.** Each song's init is run on the machine; its I/O writes become a table
    (`{reg, value}` lists shared by songs with equal writes) replayed by init, and play's whole
    initial state becomes one `const struct` per song assigned at once (`tune = start[song]`).
    When init only stores the song and play's first call does the work (an init pass behind a
    flag init sets), that call is run on the machine instead: its writes are replayed and the
    start state assigned in play's first call, behind the flag's test.
    Constant data that differs between songs (blocks init copies) is decoded per song; songs whose
    decodings agree at every common position share one record array.
15. **Names from structure.**
    - Input: I/O write sites (`Program.machine`), forward def-use (`pdefs.rs`, value numbers),
      mechanism 8's division and records.
    - Rule: a cell's, field's or table's *feed* is the SID register role its values reach:
      `$D400/1` freq, `$D402/3` pulse, `$D404` control, `$D405` attack_decay, `$D406`
      sustain_release, `$D415/6` cutoff, `$D417` resonance_routing, `$D418` mode_volume. A field
      or table is named after its feed (`freqs`), an index after the table it indexes (`note` for
      the freq index), a counter compared and reset after its feed (`pulse_time`), a cursor
      `<kind>_pos` and its record reference `<kind>`. Record kinds follow structure: a selector
      whose record feeds attack_decay is `instrument`, a cursor over records holding a selector
      `row` in a `pattern`, a cursor over pattern selectors `order`, a program cursor through an
      instrument field `wave`, `pulse` or `filter` by its feed. A handler is `<kind>_<effect>`,
      the effect `set` (field stored), `sweep` (field added), `jump` (cursor only), `end` or
      `idle` (no effect). Collisions take the register byte (`_lo`, `_hi`) or the field's
      position.
    - Output: no address-tag names (`table_185e`, `fXX`, `S_<off>`, `f12`).
    - Check: census opaque names 0.
    - Cost: one forward pass per value to the I/O writes.

## Lifter build order

Each algorithm consumes only the facts of those above it.

1. **Song data** (mechanism 3): one exported byte set, read minus written minus executed, with
   each load site's exact bytes. Consumes the load sites' address sets, `stores`, `starts` and
   jennings' instruction bytes.
2. **Tables and own arrays** (rule 3, mechanism 4): exists (`tables.rs`, `Program.owned`,
   `printer.const_tables`, `pad_c`).
3. **Pointer word sets as const pointers** (mechanism 1): consumes `Program.words`/`ptr_sets`,
   1.
4. **Structs and record pointers** (mechanism 5): consumes `regs`, `addr_sets`, 2; exists in
   `state.py`/`records.py`.
5. **Function-rooted reaching definitions with memory cells**: extends `pdefs.rs`. Gives locals
   and parameters (mechanism 6).
6. **Store-value dependence graph and value-number groups**: from P-code def-use and `opt.rs`.
   Gives one field per value (mechanism 12).
7. **Cursor value sets** (mechanism 8): consumes 1, `pdefs`, `stores`; a fixpoint over the
   cursors alone.
8. **Static projection** (mechanism 8): consumes 7, init's post-init values and play's IR.
   Gives the record graph and `sitevals`; mechanism 2's decisions at decoded positions and its
   dead outcomes come from it. Replaces the cross-product of component tuples in
   `invariant.rs`.
9. **Write-set field split** (mechanism 8): consumes `addr_sets` and 4.
10. **Residualiser and record layout** (mechanisms 7, 8, 11, 13): consumes 8 and 9; emits
    `static const` records, handlers per slot and segment, cursors as record pointers, caches
    as record references, code cells as handlers or operation pointers. Extends
    `printer.Func.evaluate`/`kfold` to emit a function under known values with fields as
    `r->f`; const record structs in `records.py`.
11. **Flattening** (mechanisms 9, 10): exists (`structure.py`); runs on 10's output.
12. **Names** (mechanism 15): consumes `Program.machine`, 5, 6 and 10.

## Method per tune

1. Relocate and lift (or lift the unrelocated tune while relocation is pending); record checked
   sites, objects, raw-memory, opaque-name and cognitive complexity
   (`out/zp/tune.sh TUNE PSID OUT ARGS`). The tune's table records the current lift's numbers
   and its commit; the steps apply from whatever that lift already does (a test it already
   folds needs no step).
2. Rewrite play by hand in `out/proto/<tune>/` (gitignored: generated data holds the tune). Data
   tables come from the lift's image through a generator (`out/proto/commando_data.py` is the
   pattern).
   `tools/liftcheck.py PSID OUT --hand DIR` lifts a tune and prints its unbounded accesses,
   gotos, `-Werror`, sidtrace under each one's overrun sites, fcomplex and census counts beside
   the hand rewrite's.
3. Apply the steps in order, verifying each with `-Werror`, `sidtrace` over the full song(s)
   under the overrun rule, `tools/fcomplex.py --units` and `wayward-eagle census`:
   structs → flattening → switches → compile-time effect tables → decoded records → const song
   data. A step with nothing to apply (no compare chain on one value) is skipped. Where no rule-3
   clamp changes the output, a step is also judged against the HVSC sidtrace captures of the
   original (`out/proto/oracle.py`, `pysidtracker.oracle.aligned_match`); with a clamp, songs it
   affects differ from them and `sidtrace --overruns` with `last` sites is the reference. When the
   orbit (mechanism 2) closes past the HVSC length, sidtrace is also run to its closing frame, so
   every reachable state is compared.
4. Count the conditional branches in play's 6502 code (`out/zp/branches.py`, with `LO:HI:N:S`
   for stride-S jump tables and `+B:T:N` for a patched branch's offset table) and classify them
   (runtime decision, loop, carry, constant data, constant after init, constant at its site under
   mechanism 2, always taken); the final cognitive score is compared with the
   distinct runtime decisions among them. Code unrolled per voice or channel counts once per copy
   in the 6502 and once as a loop in C; a rule-3 clamp remains only on a table play writes, and
   has no 6502 branch. Init's own loops are scored with play: init stays in the play unit. An
   opcode play writes is never a decision at its site (mechanism 11).
5. Add what the tune needed beyond this brief to its section and to Mechanisms. A per-tune table
   of record layouts (header byte roles, row format, fx tables) saves listing lookups.

## Tunes

### Commando

Hubbard-style player: per-voice track lists of pattern numbers (`$FF` loop, `$FE` stop), patterns
of `info [extra] [note]` events, 32 instruments of 8 bytes, a 96-entry frequency table.

| Step | Cognitive | Raw-memory | Opaque | PRG |
|---|---|---|---|---|
| lifted (relocated) | 72 | 95 | 89 | 13246 |
| structs | 58 | 0 | 3 | — |
| flattened | 39 | 0 | 3 | 4411 |
| switches | 38 | 0 | 3 | 4888 |
| compile-time effect table | 34 | 0 | 3 | 5237 |
| decoded pattern events | 29 | 0 | 3 | 7050 |
| const song data (tracks, split instruments) | 28 | 0 | 3 | 7138 |

- Play has 41 conditional branches: about 37 decisions, 4 loops. The remaining 29 points are
  runtime state: song status, speed counter, track loop/stop, pattern end, note countdown and
  release, pulse delay and direction, vibrato phase, effect guards, and the frequency-table bound.
- Four pattern reads stayed checked in the lift because track positions were taken as arbitrary at
  play entry (mechanism 2).
- The frequency table and the voice state were one array in the lift; note 104 reads past the
  table (rule 3). The step table above was measured with the earlier zero rule (`freq()` returned
  `{0, 0}` past the table); under rule 3 it returns the last entry.
- Instrument fx bits, vibrato depth and pulse speed are constant (play writes only pulse width):
  mechanism 7's effect table.
- Event handlers by kind: rest, note, note with instrument, note with portamento.
- Census style findings in the prototype (effect-in-condition) come from assignments inside call
  arguments and are not lifter requirements.
- The census's `cognitive-complexity` count is the number of scored functions, so it rises when
  nesting is split into functions; judge by `fcomplex` totals.

### Automatas

CIA-timed player, every 8th call a tick: an order list of three pattern numbers per position
(bit 7 jumps to a loop position), patterns of `header [A] [B] [note]` rows (bit 7 ends the
pattern, low nibble waits), six program channels (two per voice) stepping through a 213-entry
table of `{record pointer, wait}` with in-table jumps (pointer high byte 0), sound records of two
flag bytes and up to 12 optional voice and filter fields, a filter sweep, and a frequency table
read from three bases. Nearly all state lives in operands of self-modifying code.

| Step | Cognitive | Raw-memory | Opaque | PRG |
|---|---|---|---|---|
| lifted (relocated) | 81 | 32 | 112 | 10326 |
| structs | 67 | 5 | 0 | 8159 |
| flattened | 45 | 0 | 0 | 8181 |
| switches | — | — | — | — |
| compile-time records and steps | 28 | 0 | 0 | 9684 |
| decoded pattern rows | 25 | 0 | 0 | 18278 |
| const song data (order, steps as references) | 25 | 0 | 0 | 19088 |
| opcode state as an operation (mechanism 11) | 24 | 0 | 0 | 19088 |

- The lift checked five sites: one object check on the record pointer and four reads through
  the row pointer, which play keeps in four self-modified `LDA abs` operands (mechanism 1). The
  pattern rows and the sound records were one 4885-byte object though relocation moved them as
  two blocks (mechanism 4).
- Play has 78 conditional branches: 31 runtime decisions (17 distinct; per-voice and
  per-channel code is unrolled ×3 and ×6), 3 carries of a 16-bit pointer add, 1 loop, 36 tests
  on constant data (field flags, header flags, in-table jumps, a mode value no record sets), 4
  constant after init (a SID-model clamp init patches to 0, an always-taken `BMI`), 3 tests that
  only fill a header cache (mechanism 13). The 24 points are the 17 runtime decisions (tick,
  order restart and loop, row pending, pattern end, program idle and wait, fixed or sliding
  pitch, slide direction, pulse off, direction, wrap and its two limits, cutoff and output
  clamps), the note bound (rule 3) and 6 loops. The sweep direction held in the `ADC`/`SBC`
  opcodes is a `sweep` operation assigned by the sweep setters, not a decision.
- Rule 3 changes the output for two tables, so the reference is judged with `last` sites
  (`sidtrace --overruns`): the note frequencies (120 entries, notes up to 168 reached by record
  offsets on pattern notes) and the slide speeds (6 two-byte-stride entries up to the base of an
  unrun arpeggio path, indexes up to 63 read on into the frequencies). The slide clamp folds into
  the record initializer; the note clamp stays a runtime bound.
- The arpeggio path (pitch mode 1-127) is dead: no record sets such a mode (mechanism 2). Its
  operands were left unrelocated in the input PSID; relocation now moves unrun operands.
- Init patches the clamp constant and an `ASL`/`NOP` for the SID model, and play patches an
  `RTS` over the tick entry for non-tick calls: constants and control flow (mechanism 11).
- No play table is written, so mechanism 8's split is empty: every song table is `const`. Rows
  and records are field lists (mechanism 7); program steps carry resolved `next` references and
  pattern starts are renumbered to them (mechanism 8).
- The decoded rows (1678) cost PRG size: a row is a field-list reference, count and wait.
- The header of a row is fetched a tick before its fields are applied; the fetch only caches
  constant bytes and disappears (mechanism 13), leaving a pending state as wait < 0.

### Ghouls

Tim Follin's player, 32 songs (11 music, 21 sound effects): per-voice byte streams of notes,
rests and commands `$80-$93` (dispatched through a split jump table into per-voice handler
copies by a self-modified `JMP`), with gotos, gosub/return on a 3-deep per-voice stack, counted
loops and an optional fixed duration; per-voice vibrato, trill, portamento, pulse sweep, hard
restart and gate-off, and a filter sweep. The voice code is unrolled ×3; music init copies the
song's blocks into place. Lifted unrelocated.

| Step | Cognitive | Raw-memory | Opaque | PRG |
|---|---|---|---|---|
| lifted (unrelocated) | 120 | 318 | 99 | 35806 |
| structs | 105 | 0 | 2 | 40574 |
| flattened | 66 | 0 | 2 | 40642 |
| switches | — | — | — | — |
| compile-time effect tables | — | — | — | — |
| decoded events | 58 | 0 | 0 | 40651 |
| const song data (one start state per song) | 57 | 0 | 0 | 40612 |

- The lift had 7 gotos: two joining vibrato's add exits (no carry, carry into a high byte that
  does not wrap) past the subtract, two skipping the note set (no portamento, note at target),
  two skipping the portamento clamp, one skipping the pulse write (direction positive). They
  became a 16-bit add with an overflow test, `pitch()` and `glide()` returns, and a
  `pulse_sweep()` return.
- Play's checks in the lift: the stream pointer's object (`$6CB7-$9E07`), the command range,
  two register-list reads and two gosub-stack stores (three objects). Decoding removes them all:
  positions, list ends and the call depth (at most 2) come from mechanism 2.
- Play writes no opcodes. Its self-modified operands are per-voice fields (vibrato and pulse
  direction, trill phase, pulse and cutoff start directions, fixed duration, untransposed-note
  count) and filter fields (direction, bounds as two `{lo, hi}` limits); the dispatch `JMP`
  operand is transient and becomes each event's handler (mechanism 11).
- Rule 3 changes the output of 4 songs through one table: the note frequencies (97 entries, lo
  table to the hi base; transposed and portamento notes reach 255), judged with 12 `last` sites.
  The lift matches the HVSC captures on all songs; every later step matches them on the 28
  songs no clamp reaches.
- Decoded events (mechanism 8, streams with control flow): 8026 records in 5 arrays (songs 0-6
  and the effects share one), 4 bytes each; a 9-byte record with explicit next and field lists
  overflowed memory by 20K. Gotos are jump records; a loop end's target is static (mechanism 2),
  so the loop pointer cell goes. No reachable note mixes fixed and read durations, so note and
  rest handlers come in timed and fixed forms chosen in the initializer.
- Command `$94` is unreachable in every song, so the default pulse words it stores are constant.
- From decoded events on, init replays its per-song SID writes and assigns the song's start
  state (mechanism 14); before, the lifted init and its 30K image stayed for the stream bytes.
- Play has 164 conditional branches: per voice copy 52 (40 runtime decisions, 4 carries of
  16-bit adds, 2 always taken, 5 on constant data, 1 constant at its site: the fixed-duration
  test) and 8 in the filter (7 runtime, 1 carry); 47 distinct runtime decisions. The 57 points
  are those decisions with nesting and `else` increments, 4 play loops (voices, the read loop,
  the pulse and cutoff bounces), init's write loop and the frequency bound (rule 3).
- Census effect-in-condition findings are calls in conditions (`expired()`, bounce phases),
  style, not lifter requirements.

### Incoherent

GoatTracker 2 export (Incoherent Nightmare tune 2), one song: per-voice order lists of pattern
numbers and transposes (`$FF` jumps), patterns of `[instrument] [command [data]] note|rest|gate`
rows and packed rest repeats (a 0 byte ends a pattern), 43 instruments of 9 columns, 1-based wave,
pulse and filter programs of `{left, right}` entries with `$FF` jumps, a 24-entry speed table of
fixed words or note-relative shifts, 16 commands and 5 effects dispatched through patched
`JSR`/`JMP` low bytes, a 96-entry frequency table. Init stores the song; play's first call is the
init pass. One X-indexed voice routine, called per voice. Lifted unrelocated.

| Step | Cognitive | Raw-memory | Opaque | PRG |
|---|---|---|---|---|
| lifted (unrelocated) | 165 | 133 | 133 | 9038 |
| structs | 133 | 0 | 0 | 9562 |
| flattened | 90 | 0 | 0 | 9889 |
| switches | — | — | — | — |
| compile-time records (instruments, speeds, program steps) | 56 | 0 | 0 | 13515 |
| decoded pattern events | 39 | 0 | 0 | 28349 |
| const song data (order lists, program positions as references) | 37 | 0 | 0 | 30526 |
| joins called, not returned as status (mechanism 9) | 36 | 0 | 0 | 31042 |

- The lift checked six pattern reads (`($02),Y` at `$0B84`, `$0B97`, `$0BA6`, `$0BB6`, `$0BBE`,
  `$0BEE`) and trapped the speed index at `$0AC7`; every song table, order list and pattern was
  one 5698-byte object (mechanism 4). Bounds (mechanism 2, orbit): the pattern number comes from
  the order lists at positions 0..189, each list looping by its `$FF` at 189 to 178 (166 entries),
  and is 0..121, inside the 122-entry pointer tables (lo base to hi base); a row position runs
  from 0 to the pattern's 0 byte (offsets to 141), inside the pattern's own extent; speed indexes
  are 0..23 of 24.
- The lift's 12 gotos: the wave delay reaching its count past the wave set, the pulse set entry
  past the sweep, two row bytes jumping to the note test (`$0B90`, `$0BAA`), four row exits to the
  epilogue (repeat end, command only, portamento note, rest), and four in the filter step (sweep
  entry, type entry without a cutoff, cutoff entry, sweep not done). Flattening made them returns
  and shared `next` steps; decoding removed the epilogue and the filter and pulse entry kinds.
- Self-modified bytes, no opcodes: the init flag, filter position, time, cutoff, routing and type
  are state; the volume operand only an unused entry writes is a constant; the vibrato depth, the
  shift count and the speed index (with the `Z` effect 0 tests) are speed record fields and its
  handler; the effect number operand is constant at its site under the dispatch; the patched `JSR`
  and `JMP` low bytes are the command record's `run` and the voice's `effect` operation, assigned
  where play stores the command and effect (mechanism 11).
- No table index passes its extent in the orbit (13380 frames, the loop closing at 11844 with
  period 1536): notes reach 92, note-relative speeds read entry 68, instruments 1..43. No rule-3
  clamp and no `last` site; the per-cell join would have needed a dead note clamp. There are no
  HVSC captures for the tune, so the reference is sidtrace of the original over the HVSC length
  and over the orbit.
- Mechanism 2 at decoded positions: every note row has one portamento outcome and one
  hard-restart outcome along the voices' trajectories, so note events come in three handlers; the
  pulse and filter times are 0 at every reached set entry, so set records skip the time test.
- Decoded: 4543 events of 4 bytes (a row end is an event holding the next row's index, or 0),
  100 command records, order entries `{pattern, transpose, next}` (one transpose per position).
  The instrument with first wave 0 never exists, so its handler is dropped.
- Play has 75 conditional branches: 29 runtime decisions, 2 loops (init clear, the speed shift on
  a constant count), 1 carry, 4 always taken, 35 on constant data, 4 constant at their site (the
  effect number compares under the dispatch, portamento and hard restart at note rows). The 36
  points are 28 conditions carrying the 29 decisions (the vibrato phase's `BCC`/`BEQ` pair is one
  comparison), the voice loop, the init pass's write loop, and 6 nesting and `else` increments.
- Census raw-memory counts one array subscripted at offsets of different objects (a 1-based table
  read at `y - 1` and at `y`); one accessor per table removes it.

- Open (the lifter's own extents, `overrun="last"` with the orbit): 11 table sites still
  take effect over the orbit, where this section says none should, so their extents are
  likely short: the filter program at `$093A`, `$095D`, `$096C` (bases `$117F`, `$1180`,
  `$1224`; reads of `$1224+Y` run past `$12C8`) from frame 11407, the wave program at
  `$0A79`, `$0A93`, `$0A9C`, `$0AA7`, `$0C28` from frame 11798, the pulse program at `$0B35`,
  `$0B49`, `$0B62` from frame 12245.

### Emomyst

SID-Wizard 1.x SWP player (Hermit), one song: per-voice order lists of pattern numbers (`$FF`
loops), patterns of `note [instrument|small fx [fx [param]]]` rows and packed rests `$70-$77`,
20 instruments of 16 header bytes with embedded wave, pulse and filter programs of 3-byte entries
(`$FE` jumps, `$FF` ends), chords, a tempo program, small fx (column or fx `>= $20`, by nibble)
dispatched through a patched `BCC` offset, big fx 1-`$1F` through a patched `JMP` operand, note
fx `$60-$7F` through another patched `BCC`, and a 96-entry frequency table. One X-indexed voice
routine (X = 14, 7, 0) on 7-byte-stride records; the song pointer `($02),Y` is a table entry
plus the init-left base `$1C00`. Lifted unrelocated.

| Step | Cognitive | Raw-memory | Opaque | PRG |
|---|---|---|---|---|
| lifted (unrelocated, 1ec1bf1) | 156 | 75 | 181 | 12870 |
| structs, locals, flattening, init in the play unit | 103 | 0 | 0 | 6691 |
| compile-time records (instruments, wave/pulse/filter programs, chords) | 67 | 0 | 0 | 8148 |
| decoded pattern rows (the instrument in effect a row constant) | 35 | 0 | 0 | 26143 |
| const song data (order lists as references, one field per value) | 34 | 0 | 0 | 26262 |

The hand target is 34/0/0 (`out/proto2/emomyst/s4_lists`, gitignored); every step has no
unbounded access, no goto, builds with `-Werror` and matches sidtrace over 11126 and 8084
frames under the lift's overrun sites.

- The lift's own arrays (mechanism 4): the frequency, vibrato, slowdown, pulse and routing tables
  and the jump words read in the code span, and the song tables at operands init writes, are
  `static const` arrays (raw-memory 75 to 60, cognitive 156, sidtrace identical over 11126 and
  8084 frames). The pattern, instrument and order reads through the song pointer `($02),Y` and the
  instrument pointer the voice keeps, and the pattern pointer tables (bytes init's stores may
  reach, constant after it), stay subscripts of the data block: their objects are the pointer word
  sets' targets (mechanism 1), each its own array chosen from a table of pointers, not yet lifted.
  Census counts `t[i]` beside `t[i + 1]` of a `uint8_t i` as two objects (the promotion of `i` in
  `i + 1` is in its key), so the vibrato pair and the routing pair still count 7.
- The lift checked 74 sites on two objects (51 `($02),Y` reads on one scattered object, 23
  table reads on `$1000-$24A1`) and had 12 gotos: the packed-rest test into the row decode,
  the wave delay entry past the wave set, four arpeggio joins (relative and absolute entries
  from the chord and wave paths), two order-list exits to the position store, the filter jump
  into a sweep, two instrument-init routing joins and the tempo-fx join. Flattening made them
  returns, a `for` over the order list and shared tails; records and decoding removed the
  entry kinds and the order loop. It built clean with
  `-Werror` here; its always-1 status returns (census status-call 4) went with flattening.
- Orbit (mechanism 2): 125 persistent cells, closing at frame 8084 with period 6120, past the
  HVSC length 5563; every step is run to 8084. Bounds: order positions 1..12 of 14, patterns
  1..21 of 21 (1-based, floor from the orbit), instruments 0..10 of 21 (entry 0 aliases 1),
  chords 1..4 of 5, chord steps 0..17 of 18, the tempo position constant 0.
- Rule 3 changes the output: the frequency record table is 96 entries (hi `$1869-$18D0`, lo
  `$18D1` up to the note-fx offset base `$18B9 + $78`) and arpeggio pitches reach 117; the
  vibrato table `$185E` ends at the hi base after 11 entries while vibrato depth, pulse and
  cutoff keyboard tracking read it to index 99 (`$185D` is its inner base). 18 `last` sites;
  the lift matches the HVSC captures, every later step matches sidtrace with them and not the
  captures.
- Self-modified bytes, no opcodes: filter routing, mode (type and resonance, written from one
  byte), keyboard track, an 11-bit cutoff (`$121F` and its low bits `$1247`, the sweep one
  signed add on it, carry included), filter voice, position and time are filter state; the
  instrument mask and flags,
  AD operand, portamento low byte, arpeggio operand, frequency and detune operands and the voice
  restore are transient locals (the detune is `x - x`, constant 0); the small-fx nibble and
  merge operand are handler arguments; the `JMP $19CF` operand and the `BCC` offsets at `$1951`
  and `$1A13` are transient dispatches, each row's handlers (mechanism 11).
- Constant after init or at its site: the song-control state `$1191` (`$FF`), the tempo request,
  the tempo program (one entry `$86`: the tick wraps at 6), the slowdown operands, the
  keep-pulse and keep-filter flags (no instrument sets them, so the instrument mask has no
  effect), the hard-restart test bit, the kept-table bytes `$0C-$0E` (all 0: note fx `$7E` is a
  gate off), the routing masks, the `$CB` vibrato clamp, the vibrato and portamento speed (0
  at every index the `$1817` and `$1802` lookups take, mechanism 7). Big fx 9-`$0B` and the tempo fx
  `$10-$15` are in no row. An instrument change always runs instrument init, so program
  positions never re-base; only voice 0 runs the filter, with its own instrument. Play skips
  its first call: the speed counter `$10A4` shifts from init's 0 and the clear carry returns.
- Records: 11 reachable instruments (0 aliases 1), 46 wave steps (15 of them instrument 0's
  header from init's position), 22 pulse, 7 filter and 14 chord steps (the `$7F` loops
  resolved), 843 rows, order entries `{rows, next}`. The row in effect is a reference beside the
  position (tick 0 reads, tick 1 advances, tick 2 plays); the instrument in effect is a row
  constant (3 rows idle), and hard restart is a start handler per row and tick call site.
- Play has 131 conditional branches and 23 distinct runtime decisions: the speed counter
  reload and first call (2), tempo wrap (1), tick 0/1/2 (3, one `switch`), rest at ticks 0 and
  1 (2), slide kind (3), vibrato delay (1), vibrato phase reload and direction (2), portamento
  direction and its two overshoots (3), filter voice (1), filter, pulse and wave times (3),
  pulse and cutoff keyboard track (2). Not runtime decisions: the tie carry (`$1446`) and
  glide test (`$1327`), dead since no row sets slide `$FF`; the instrument-0 tests (`$1406`,
  `$1523`) and the hard-restart bit, row constants; the slide-0 accumulate (`$153A`), the
  arpeggio sign (`$1789`) and the speed lookup's tests (`$180A`, `$1815`), constant at their
  sites; the frequency and vibrato clamps, padded (rule 3); the sweep end, one condition on
  the one cutoff field. The 34 points carry the 23 decisions, the voice loop and init's SID
  clear and voice start loops.

