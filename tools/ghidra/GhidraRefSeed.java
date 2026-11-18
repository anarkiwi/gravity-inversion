// Ghidra headless pre-script of tools/ghidra_ref.py: every gap of the address space
// becomes uninitialized RAM, then a function is created at each argument address
// (hexadecimal) that lies in initialized memory, before auto-analysis; the analysis
// commits the decompiler's parameters to each function's signature.
//@category gravity-inversion
import ghidra.app.cmd.disassemble.DisassembleCommand;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressRange;
import ghidra.program.model.address.AddressSet;
import ghidra.program.model.address.AddressSpace;
import ghidra.program.model.mem.Memory;

public class GhidraRefSeed extends GhidraScript {
    @Override
    public void run() throws Exception {
        setAnalysisOption(currentProgram, "Decompiler Parameter ID", "true");
        Memory mem = currentProgram.getMemory();
        AddressSpace sp = currentProgram.getAddressFactory().getDefaultAddressSpace();
        AddressSet gaps = new AddressSet(sp.getMinAddress(), sp.getMaxAddress()).subtract(mem);
        for (AddressRange r : gaps) {
            mem.createUninitializedBlock(String.format("ram_%04X", r.getMinAddress().getOffset()),
                    r.getMinAddress(), r.getLength(), false);
        }
        for (String a : getScriptArgs()) {
            Address at = sp.getAddress(Long.parseLong(a, 16));
            if (!mem.getAllInitializedAddressSet().contains(at)) {
                printerr("SEED " + at + " not in initialized memory");
                continue;
            }
            currentProgram.getSymbolTable().addExternalEntryPoint(at);
            new DisassembleCommand(at, null, true).applyTo(currentProgram, monitor);
            if (getFunctionAt(at) == null && createFunction(at, null) == null) {
                printerr("SEED " + at + " no function");
            }
        }
    }
}
