# Diagnostic tools

What to run to compare, trace and measure a translation, before writing a new tool.

| Question | Tool |
|---|---|
| Does a build issue the original's SID writes, in order, per call? | `tools/sidtrace.py PSID PRG (--frames N \| --hvsc ROOT \| --wrap LABELS LABEL) [--songs S] [--times T]` |
| What did the original do over its whole HVSC length? | the precomputed sidtrace captures under the HVSC root (`<Tune>.<song>.csv.zst`), read with `pysidtracker.survey.oracle_frames` and compared with `pysidtracker.oracle.aligned_match`; not re-run |
| Which code a tune executes, and which branch outcomes it never takes | `tools/survey.py [--hvsc ROOT]`, `tools/disasm.py` ([disassembly.md](disassembly.md)) |
| Which song data a hand translation reads | `tools/footprint.py` ([footprint.md](footprint.md)) |
| Every song data read of generated C, with its state and SID writes | `tools/eventlog.py` ([eventlog.md](eventlog.md)) |
| Value flow and state machines of generated C | `tools/flowgraph.py`, `tools/flowsegment.py`, `tools/condition.py` ([flowgraph.md](flowgraph.md)) |
| Where generated C keeps the 6502's machine model | `tools/residue.py` ([residue.md](residue.md)) |
| Readability defects against budgets | `make readability`, `tools/readability.py` ([readability.md](readability.md)), `tools/fcomplex.py` ([fcomplex.md](fcomplex.md)) |
| What costs code size | `tools/costs.py` ([costs.md](costs.md)), `tools/ablate.py` ([ablate.md](ablate.md)), `tools/sizes.py` ([sizes.md](sizes.md)) |
| Whether a test catches source edits | `tools/mutate.py` |
| Whether the lifter that generated the C changed | `tools/lifterstamp.py` |
