# Disassembly and data flow

Disassembly, instruction decoding and per-instruction data flow come from jennings and
pysidtracker; a capability they lack is added there, not written in this repository.

| Need | Use |
|---|---|
| Listing of a PSID's init and play code, computed transfers resolved through constant tables | `tools/disasm.py PSID OUT [--frames N \| --hvsc ROOT] [--song S]` (jennings `coverage.listing.Listing` over a coverage run; writes `OUT` and its `.jcov` sidecar) |
| Rows of an address range of a SID image, init/play labelled | `pysidtracker.disasm.disassemble(image, start, end, entry_labels(image))` |
| The image after init (init may unpack or scatter data) | `pysidtracker.disasm.image_after_init(image, subtune=)` |
| One instruction's text and length | `jennings.disassembler.Disassembler(MPU(memory=mem), AddressParser()).instruction_at(pc)` |
| Opcode mnemonic and addressing mode | `jennings.opcodes.OPCODES[op]` |
| What each register, flag and byte an instruction sets depends on | `jennings.dataflow.flow(mem, pc, loads, stores, operands)`; `operands` maps self-modified operand bytes to the locations they come from |
| An instruction's P-Code | `deity_informant.lift(mem, pc)` |
| Successors of an instruction | `jennings.coverage.cfg.successors(mem, pc)` |
| Bytes of the instructions starting at a mask of addresses | `jennings.coverage.cfg.instruction_bytes(mem, starts)` |
| Operand bytes of the branches, jumps and calls starting at a mask of addresses | `jennings.coverage.cfg.control_operands(mem, starts)` |
| Opcodes that never continue at the next instruction (exits, JAMs, `JMP`) | `jennings.coverage.cfg.STOPS` |
| The words of a hand-written split jump table | `jennings.coverage.resolve.split_table(mem, lo, hi, n, stride=, high=)`; `Listing.disassemble(..., tables=)` decodes from them |
| Each data access of a call with its base address (an indexed access's operand, `(zp),Y`'s pointer) | `NativeMPU.run_to_rts_accesses(pc, acc, max_cycles, remap, bases=True)` |
| The ROMs a CPU port value maps | `vagrant_moses.memmap.rom_ranges(port)` |
| Branches a coverage run saw go one way | `jennings.coverage.one_way(cov, mem)` |
| A subroutine under coverage, `$D012` counting for raster polls | `jennings.coverage.call(mpu, addr, a, max_steps=, raster=)` |
| Cycles and page-cross penalty of an opcode | `jennings.opcodes.CYCLES[op]`, `jennings.opcodes.EXTRA[op]` |
| A PSID's header, image and 64 KiB memory | `pysidtracker.header.parse_sid_header(data)`, `pysidtracker.image.SidImage.from_sid(data)` (`.load`, `.image`, `.mem`); `tools/sidtrace.py` `psid_parts(data)` gives `(load, init, play, image)` |
| A PSID file from its fields | `pysidtracker.header.write_psid(load=, init=, play=, image=, ...)`; `SidHeader.replace(**fields).to_bytes()` |
| An ACME symbol list | `jennings.acme.read_symbol_list(text)` |
| Static facts of a lift: instruction starts, address sets per access, stores, computed jump targets, register value sets, reaching definitions | the deity-informant `Program` `cgen.lift` returns: `starts`, `addr_sets`, `stores`, `jumps`, `regs`, `pdefs` |
| Per-opcode roles in the lift | `tools/opclass.py` |
| Repeated code, JSR targets, fall-through entries | `tools/copies.py` |
| Procedure graphs and loop kinds | `tools/passes.py` ([passes.md](passes.md)) |
| A reference decompilation | `tools/ghidra_ref.py` ([ghidra_ref.md](ghidra_ref.md)) |
