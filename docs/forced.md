# Forcing

`tools/forced.py PSID... [--json PATH]` finds the code of play that a song never runs.

Play's verified jennings certificate (`jennings.coverage.certificate`, per song state) gives its
conditional branches that go one way. For each, play is run to a call that reaches the branch, stopped
there, resumed at the successor never taken and run to the call's return. One-way branches in
the code that exposes are forced the same way within that call, to a fixed point. A branch is
forced from the first song state whose certificate has it one-way.

A forced run is dropped where the call does not return, or runs an instruction outside the PSID
image or at a byte of song data (loaded by play, never stored, no instruction's).

A forced run shows which instructions and edges the player has. Its data accesses are not facts
about any song.

| Report key | Meaning |
|---|---|
| `instructions`, `one_way` | counts from the certificates alone, then with the forced runs |
| `forced_edges` | `[pc, next]` edges the forced runs add to the certificates' |
| `strays` | `[branch, other]` of the forced branches whose run was dropped |
| `left` | branches still one-way |
| `runs`, `seconds` | forced runs kept and the time forcing took, the certificates excluded |

## Walking back

`tools/walkback.py PSID... [--json PATH] [--images DIR] [--jobs N]` turns each guard into a change
to song data and runs the real player, unforced, on the changed post-init state.

Guards are the one-way branches the unforced runs show, branches and jumps whose operand bytes
play stores, `JMP (ind)`, and returns to an address no `JSR` pushed. Each is walked back from its
first runs in the first call that runs it, through per-instruction data flow
(`jennings.dataflow.flow` over a native trace of the call): from the flag, operand, pointer or
pulled bytes deciding where it goes, to the instruction setting each, its inputs, the loads, and
the song-data byte loaded. A state cell play stores is followed to its last store, in an earlier
call where needed. Loaded values are followed before their addresses; for a computed transfer an
indexed table's index comes first, so the walk reaches the byte selecting the entry.

The values of that byte that send the guard elsewhere are read off the instructions: their P-Code
(deity-informant's lift) is evaluated over the byte's 256 values along the walk, other inputs as
seen. An indexed table is read only within the run of bytes around its seen entry that are
neither code nor stored. One value per new successor is kept: the one sending the fewest branches
between the load and the guard (those whose flag is computed from the byte) the other way, then
the nearest the byte's own.

Each change is written to the post-init image and play run, unforced, to `WINDOW` calls past the
guard's call. A run is rejected where a call does not return, runs song data or leaves the image,
or loads I/O. A run that adds edges is kept as a new base: its guards are walked in turn and its
changes compose with later ones, to the fixed point.

A guard the walk leaves open is reported with the hop it stopped at and why: a constant the player
stores, no song data feeding it (a register or the stack at play's entry, a cell only init sets,
player code), two song bytes at once, no value of the byte changing it, a rejected run, or kept
runs leaving it one-way.

| Report key | Meaning |
|---|---|
| `instructions` | counts from the certificates, with forcing, with the kept patched runs, and all |
| `patches` | per kept run, its guard, `[address, old, new]` changes, calls and new edges |
| `patched_edges` | `[pc, next]` edges the kept runs add to the certificates' |
| `executed` | the instructions any of them runs |
| `unresolved` | guards still open: the hop (`call`, `pc`, `loc`) and `reason` |
| `seconds` | the certificates, forcing, walking and the patched runs |

`--images DIR` writes each tune's kept patched images, 64 KiB each, to `DIR/<tune>.npz`.
