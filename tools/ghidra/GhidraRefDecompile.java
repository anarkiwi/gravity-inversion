// Ghidra headless post-script of tools/ghidra_ref.py: decompile every function and
// write OUT.json (argument). Per function: name, entry, callees' entries, signature, the
// C as Ghidra prints it ("c") or the error, the same C with the token rewrites that make
// it parse ("body"), and [name, declaration, size, called] of each typing of a name it
// uses undeclared.
// Signatures are committed and all functions decompiled again until none changes.
//@category gravity-inversion
import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import ghidra.app.decompiler.ClangFieldToken;
import ghidra.app.decompiler.ClangFuncNameToken;
import ghidra.app.decompiler.ClangLabelToken;
import ghidra.app.decompiler.ClangLine;
import ghidra.app.decompiler.ClangNode;
import ghidra.app.decompiler.ClangSyntaxToken;
import ghidra.app.decompiler.ClangToken;
import ghidra.app.decompiler.ClangTypeToken;
import ghidra.app.decompiler.ClangVariableDecl;
import ghidra.app.decompiler.ClangVariableToken;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.decompiler.DecompiledFunction;
import ghidra.app.decompiler.PrettyPrinter;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.data.AbstractStringDataType;
import ghidra.program.model.data.Array;
import ghidra.program.model.data.ArrayDataType;
import ghidra.program.model.data.Composite;
import ghidra.program.model.data.DataType;
import ghidra.program.model.data.DataTypeWriter;
import ghidra.program.model.data.Enum;
import ghidra.program.model.data.Pointer;
import ghidra.program.model.data.TypeDef;
import ghidra.program.model.data.Undefined1DataType;
import ghidra.program.model.data.VoidDataType;
import ghidra.program.model.listing.Data;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.pcode.HighFunction;
import ghidra.program.model.pcode.HighFunctionDBUtil;
import ghidra.program.model.pcode.HighFunctionDBUtil.ReturnCommitOption;
import ghidra.program.model.pcode.HighSymbol;
import ghidra.program.model.pcode.HighVariable;
import ghidra.program.model.pcode.PcodeOp;
import ghidra.program.model.pcode.Varnode;
import ghidra.program.model.symbol.IllegalCharCppTransformer;
import ghidra.program.model.symbol.SourceType;
import java.io.StringWriter;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Iterator;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class GhidraRefDecompile extends GhidraScript {
    private static final int TIMEOUT = 120;
    private static final IllegalCharCppTransformer NAMES = new IllegalCharCppTransformer();
    private static final Pattern PIECE = Pattern.compile("_(\\d+)_(\\d+)_");
    private static final String DATA = "_data";
    private static final String CODE = "code";
    private final Set<DataType> composites = new LinkedHashSet<>();
    private final Set<String> functionNames = new HashSet<>();

    @Override
    public void run() throws Exception {
        DecompInterface di = new DecompInterface();
        di.toggleSyntaxTree(true);
        if (!di.openProgram(currentProgram)) {
            throw new IllegalStateException(di.getLastMessage());
        }
        List<Function> fs = new ArrayList<>();
        currentProgram.getFunctionManager().getFunctions(true).forEach(fs::add);
        fs.forEach(f -> functionNames.add(NAMES.simplify(f.getName())));
        Map<Function, DecompileResults> res = new HashMap<>();
        Map<Function, String> sig = new HashMap<>();
        boolean changed = true;
        for (int pass = 0; changed && pass <= fs.size(); pass++) {
            changed = false;
            for (Function f : fs) {
                DecompileResults r = di.decompileFunction(f, TIMEOUT, monitor);
                res.put(f, r);
                DecompiledFunction d = r.getDecompiledFunction();
                if (d != null && r.getHighFunction() != null
                        && !d.getSignature().equals(sig.put(f, d.getSignature()))) {
                    changed = true;
                    HighFunctionDBUtil.commitParamsToDatabase(r.getHighFunction(), true,
                            ReturnCommitOption.COMMIT, SourceType.ANALYSIS);
                }
            }
        }
        di.dispose();
        JsonArray functions = new JsonArray();
        for (Function f : fs) {
            functions.add(describe(f, res.get(f)));
        }
        JsonObject out = new JsonObject();
        out.add("functions", functions);
        StringWriter types = new StringWriter();
        new DataTypeWriter(currentProgram.getDataTypeManager(), types)
                .write(composites.stream().toList(), monitor);
        out.addProperty("types", composites.isEmpty() ? "" : types.toString());
        Files.writeString(Path.of(getScriptArgs()[0]), new Gson().toJson(out));
    }

    private JsonObject describe(Function f, DecompileResults r) throws Exception {
        JsonObject o = new JsonObject();
        o.addProperty("name", f.getName());
        o.addProperty("entry", f.getEntryPoint().getOffset());
        Set<Function> called = new HashSet<>(f.getCalledFunctions(monitor));
        if (f.isThunk()) {
            called.add(f.getThunkedFunction(true));
        }
        o.add("calls", new Gson().toJsonTree(called.stream()
                .map(g -> g.getEntryPoint().getOffset()).sorted().toList()));
        List<Long> pcs = new ArrayList<>();
        for (Instruction i : currentProgram.getListing().getInstructions(f.getBody(), true)) {
            pcs.add(i.getAddress().getOffset());
        }
        o.add("pcs", new Gson().toJsonTree(pcs));
        DecompiledFunction d = r.getDecompiledFunction();
        HighFunction h = r.getHighFunction();
        if (d == null || h == null) {
            o.addProperty("ok", false);
            o.addProperty("error", r.getErrorMessage().strip());
            o.addProperty("signature", f.getSignature().getPrototypeString() + ";");
            use(f.getReturnType());
            return o;
        }
        o.addProperty("ok", true);
        o.addProperty("error", "");
        o.addProperty("signature", d.getSignature());
        o.addProperty("c", d.getC());
        use(h.getFunctionPrototype().getReturnType());
        for (int i = 0; i < h.getFunctionPrototype().getNumParams(); i++) {
            use(h.getFunctionPrototype().getParam(i).getDataType());
        }
        List<ClangNode> tokens = new ArrayList<>();
        r.getCCodeMarkup().flatten(tokens);
        Set<String> declared = new HashSet<>();
        for (ClangNode n : tokens) {
            if (n.Parent() instanceof ClangVariableDecl && n instanceof ClangVariableToken v) {
                declared.add(NAMES.simplify(v.getText()));
            }
        }
        Map<ClangToken, String> text = new HashMap<>();
        Map<ClangToken, String> before = new HashMap<>();
        Set<List<Object>> undeclared = new LinkedHashSet<>();
        for (int i = 0; i < tokens.size(); i++) {
            if (!(tokens.get(i) instanceof ClangToken t)
                    || t.getSyntaxType() == ClangToken.CONST_COLOR) {
                continue;
            }
            String name = NAMES.simplify(t.getText());
            if (t instanceof ClangSyntaxToken && PIECE.matcher(name).matches()) {
                piece(tokens, i, text, before);
            } else if (t instanceof ClangTypeToken && name.equals(CODE)
                    && !(i + 1 < tokens.size() && tokens.get(i + 1) instanceof ClangToken n
                            && n.getText().strip().startsWith("*"))) {
                text.put(t, Undefined1DataType.dataType.getDisplayName());
            } else if (declared.contains(name)) {
                continue;
            } else if (t instanceof ClangFuncNameToken c) {
                if (!functionNames.contains(name) && c.getPcodeOp() != null) {
                    Varnode ret = c.getPcodeOp().getOutput();
                    DataType rt = ret == null || ret.getHigh() == null ? VoidDataType.dataType
                            : ret.getHigh().getDataType();
                    use(rt);
                    undeclared.add(List.of(name, rt.getDisplayName() + " " + name + "()", 0,
                            true));
                }
            } else if (t instanceof ClangVariableToken v) {
                if (functionNames.contains(name)) {
                    name += DATA;
                    text.put(t, name);
                }
                variable(v, h, name, undeclared);
            }
        }
        o.add("globals", new Gson().toJsonTree(undeclared));
        o.addProperty("body", render(f, r, text, before));
        return o;
    }

    private void variable(ClangVariableToken v, HighFunction h, String name,
            Set<List<Object>> undeclared) {
        HighVariable hv = v.getHighVariable();
        HighSymbol sym = v.getHighSymbol(h);
        Data listed = sym == null || sym.getStorage().getMinAddress() == null ? null
                : getDataAt(sym.getStorage().getMinAddress());
        DataType dt = hv == null && functionNames.contains(NAMES.simplify(v.getText()))
                ? new ArrayDataType(Undefined1DataType.dataType, 0, 1)
                : hv != null ? hv.getDataType()
                : listed != null && (listed.getDataType() instanceof Array
                        || listed.getDataType() instanceof AbstractStringDataType)
                                ? listed.getDataType()
                                : sym != null ? sym.getDataType() : Undefined1DataType.dataType;
        boolean called = hv != null && calledThrough(hv);
        if (!called && dt.getName().equals(CODE)) {
            dt = Undefined1DataType.dataType;
        }
        use(dt);
        int size = listed != null && dt == listed.getDataType() ? listed.getLength()
                : dt.getLength();
        undeclared.add(List.of(name, called ? CODE + " *" + name : declarator(dt, name), size,
                called));
    }

    private static void piece(List<ClangNode> tokens, int i, Map<ClangToken, String> text,
            Map<ClangToken, String> before) {
        List<ClangToken> prev = new ArrayList<>();
        for (int j = i - 1; j >= 0 && prev.size() < 2; j--) {
            if (tokens.get(j) instanceof ClangToken t && !t.getText().isBlank()) {
                prev.add(t);
            }
        }
        if (prev.size() < 2 || !prev.get(0).getText().strip().equals(".")
                || !(prev.get(1) instanceof ClangVariableToken)) {
            return;
        }
        Matcher m = PIECE.matcher(((ClangToken) tokens.get(i)).getText());
        m.matches();
        before.put(prev.get(1), "(*(undefined" + m.group(2) + " *)((undefined1 *)&");
        text.put(prev.get(0), " + " + m.group(1) + "))");
        text.put((ClangToken) tokens.get(i), "");
    }

    private static String render(Function f, DecompileResults r, Map<ClangToken, String> text,
            Map<ClangToken, String> before) {
        StringBuilder out = new StringBuilder();
        for (ClangLine line : new PrettyPrinter(f, r.getCCodeMarkup(), NAMES).getLines()) {
            out.append(line.getIndentString());
            for (ClangToken t : line.getAllTokens()) {
                out.append(before.getOrDefault(t, ""));
                boolean clean = (t instanceof ClangFuncNameToken
                        || t instanceof ClangVariableToken || t instanceof ClangTypeToken
                        || t instanceof ClangFieldToken || t instanceof ClangLabelToken)
                        && t.getSyntaxType() != ClangToken.CONST_COLOR;
                out.append(text.getOrDefault(t, clean ? NAMES.simplify(t.getText())
                        : t.getText()));
            }
            out.append('\n');
        }
        return out.toString();
    }

    private static boolean calledThrough(HighVariable hv) {
        for (Varnode vn : hv.getInstances()) {
            for (Iterator<PcodeOp> it = vn.getDescendants(); it.hasNext();) {
                PcodeOp op = it.next();
                if (op.getOpcode() == PcodeOp.CALLIND && op.getInput(0) == vn) {
                    return true;
                }
            }
        }
        return false;
    }

    private void use(DataType t) {
        while (t instanceof Pointer || t instanceof Array || t instanceof TypeDef) {
            t = t instanceof Pointer p ? p.getDataType()
                    : t instanceof Array a ? a.getDataType() : ((TypeDef) t).getBaseDataType();
        }
        if (t instanceof Composite || t instanceof Enum) {
            composites.add(t);
        }
    }

    static String declarator(DataType t, String name) {
        if (t instanceof AbstractStringDataType) {
            return "char " + name + "[]";
        }
        if (t instanceof Pointer p) {
            DataType to = p.getDataType();
            String s = to instanceof Array ? "(*" + name + ")" : "*" + name;
            return to == null ? "void " + s : declarator(to, s);
        }
        if (t instanceof Array a) {
            String n = a.getNumElements() == 0 ? "" : Integer.toString(a.getNumElements());
            return declarator(a.getDataType(), name + "[" + n + "]");
        }
        return t.getDisplayName() + " " + name;
    }
}
