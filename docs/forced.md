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
