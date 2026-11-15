# Data footprint

A hand translation holds exactly the original's data: `tools/footprint.py` checks it per byte
against jennings coverage of the original. Each tune's generator (`tunes/<tune>/gen_*.py`) maps
the hand C back to original addresses:

| Map | Content |
|---|---|
| `held(psid)` | `{array: original addresses}`: each C array holding original bytes, element *i* at the *i*-th address (also the map of the hand C's reads in `tests/test_eventlog.py`) |
| `named(psid)` | `{what the C has instead: original addresses}`: bytes the original reads as data that the C keeps as variables or translates into code (a switch for a jump table, a voice index for a patch table) |

Coverage runs init and play over a list of runs (PSID, play calls per song, songs):

| Set | Bytes |
|---|---|
| used | read or written as data (`READ`, `WRITE`), I/O and the stack excluded |
| footprint | used bytes, and the image's non-instruction bytes (jennings' listing of the runs) |
| operand state | instruction bytes the original writes |

`check(original, held, named)` returns the runs of each, all empty for a clean translation:

| Result | Bytes |
|---|---|
| extra | held bytes outside the footprint: instruction bytes the original does not read as data, RAM it never writes |
| missing | bytes read as data, outside zero page and operand state, neither held nor named |
| overlap | held bytes also named |

Each tune's `test_data_footprint` checks its hand C over the runs of its tests (songs to their
lengths, patched variants). The CLI checks every song for the construct map's frames:

    python3 tools/footprint.py commando incoherent automatas ghouls emomyst

Where the original's behaviour depends on addresses (absolute pointers in its data, reads past a
table's end), the hand C resolves an original address to the array holding it; instruction bytes
a read past a table's end takes in no run are not held and read 0.
