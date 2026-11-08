"""Song data read sites of a translated player's generated C, from its LLVM IR.

clang lowers the C at -O0, so every C array read is one ``load`` and every write one
``store``, each with its source line. :func:`instrument` reports to tools/eventlog.c every load
that may read a song data byte (a site) and every store to memory other than a local
(docs/eventlog.md)."""

import os
import re
import subprocess

import llvmlite.binding as llvm

SIZE = {"i1": 1, "i8": 1, "i16": 2, "i32": 4, "i64": 8, "ptr": 8}
LOAD = re.compile(r"\s*(%[\w.]+) = load i8, ptr (.+?), align \d+")
STORE = re.compile(r"\s*store i8 (.+?), ptr (.+?), align \d+")
CALL = re.compile(r".*\bcall [^@]*@([\w.]+)\(")
DEFINE = re.compile(r"define [^@]*@([\w.]+)\(([^)]*)\)")
GEP = re.compile(
    r"\s*(%[\w.]+) = (alloca|getelementptr (?:inbounds |nuw |nusw )*[^,]+, ptr (.*))"
)
LOCAL = "local"
#: The generated C's read helpers: ``rdCj`` (a span's bounds-checked reader), ``rd``,
#: ``cgen_rd`` and ``oob_rd``.
HELPERS = r"(?:oob_|cgen_)?rd(?:C\d+)?"
DBG = re.compile(r"!dbg !(\d+)")
LOC = re.compile(r"!(\d+) = !DILocation\(line: (\d+), column: (\d+)")
HOOKS = """declare void @cgen_ld(i32, ptr, i8)
declare void @cgen_st(ptr, i8)
declare void @cgen_region(ptr, i32, i32)
@cgen_site = internal global i32 0
@cgen_copy = external global i8
"""


def _type(s):
    """``(type, rest)``: the IR type at the start of ``s`` (arrays and structs nested)."""
    s = s.lstrip()
    if s[0] not in "[{<":
        m = re.match(r"[\w.]+", s)
        return m.group(0), s[m.end() :]
    depth = 0
    for i, ch in enumerate(s):
        depth += {"[": 1, "{": 1, "]": -1, "}": -1}.get(ch, 0)
        if not depth and ch in "]}":
            end = i + 2 if s[0] == "<" else i + 1
            return s[:end], s[end:]
    raise ValueError(s)


def _fields(ty):
    """The field types of a (packed) struct type."""
    body, out, depth, start = (ty[2:-2] if ty[0] == "<" else ty[1:-1]), [], 0, 0
    for i, ch in enumerate(body + ","):
        depth += {"[": 1, "{": 1, "<": 1, "]": -1, "}": -1, ">": -1}.get(ch, 0)
        if ch == "," and not depth:
            out.append(_type(body[start:i])[0])
            start = i + 1
    return out


def size(ty):
    """Byte size of an IR type (structs packed)."""
    if ty[0] == "[":
        n, rest = ty[1:].split(" x ", 1)
        return int(n) * size(rest[:-1])
    if ty[0] in "{<":
        return sum(size(f) for f in _fields(ty))
    return SIZE[ty]


def offset(ty, idx):
    """Byte offset of GEP indices ``idx`` over source element type ``ty``."""
    off = idx[0] * size(ty)
    for i in idx[1:]:
        if ty[0] in "{<":
            fields = _fields(ty)
            off += sum(size(f) for f in fields[:i])
            ty = fields[i]
        else:
            ty = ty[1:-1].split(" x ", 1)[1]
            off += i * size(ty)
    return off


def pointer(text):
    """``(global, byte offset or None)`` of a pointer operand's text; None for a value."""
    text = text.strip()
    m = re.fullmatch(r"getelementptr (?:inbounds |nuw |nusw )*\((.*)\)", text)
    if not m:
        m = re.fullmatch(r"@([\w.]+)", text)
        return (m.group(1), 0) if m else None
    ty, rest = _type(m.group(1))
    rest = rest.split("ptr ", 1)[1]
    first = _operand(rest)
    base = pointer(first)
    idx = re.findall(r"i\d+ ([^,]+)", rest[len(first) :])
    if (
        base is None
        or base[1] is None
        or not all(re.fullmatch(r"-?\d+", i) for i in idx)
    ):
        return base and (base[0], None)
    return base[0], base[1] + offset(ty, [int(i) for i in idx])


def _operand(text):
    """The first operand of a comma-separated list (nested parentheses kept)."""
    depth = 0
    for end, ch in enumerate(text + ","):
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if ch == "," and not depth:
            return text[:end].strip()
    return text


def lower(c_path, includes=(), defines=(), passes=False):
    """The LLVM IR text of a C file at -O0 with line tables; with ``passes`` open to
    function passes, its values named."""
    flags = ["-O0", "-gline-tables-only", "-fno-builtin"]
    if passes:
        flags += ["-Xclang", "-disable-O0-optnone", "-fno-discard-value-names"]
    flags += ["-S", "-emit-llvm", "-o", "-"]
    flags += [f"-I{i}" for i in includes] + [f"-D{d}" for d in defines]
    return subprocess.run(
        [os.environ.get("CLANG", "clang"), *flags, str(c_path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def target():
    """The native target machine, position-independent."""
    llvm.initialize_native_target()
    llvm.initialize_native_asmprinter()
    return llvm.Target.from_default_triple().create_target_machine(reloc="pic")


def compile_object(text, path):
    """Compile IR text to a position-independent native object at ``path``."""
    mod = llvm.parse_assembly(text)
    mod.verify()
    with open(path, "wb") as f:
        f.write(target().emit_object(mod))


def globals_of(text):
    """``{name: byte size}`` of the module's defined global variables."""
    return {
        m.group(1): size(_type(m.group(2))[0])
        for m in re.finditer(
            r"^@([\w.]+) = (?:[\w()]+ )*?(?:global|constant) (.*)$", text, re.M
        )
    }


def instrument(
    text, regions, song, helpers=(), copies=()
):  # pylint: disable=too-many-locals
    """``(IR text, sites)``: ``text`` reporting each site's reads (``cgen_ld(site, pointer,
    value)``) and each store to a global or through a pointer (``cgen_st``), and
    ``cgen_regions()`` registering ``regions`` (``{global: origin}``) for tools/eventlog.c.

    A site is a load from a global of ``regions`` holding a byte of ``song`` (a mask of
    original addresses), at it when its offset is constant, or through a pointer value; a
    load in one of the read ``helpers`` reports the site of the call that entered it. Each
    function of ``copies`` takes its first argument, an instance index, as the copy its
    sites report. ``sites`` is ``[(function, line, column)]``."""
    lines, locs = text.split("\n"), {
        int(a): (int(b), int(c)) for a, b, c in LOC.findall(text)
    }
    sizes, sites, out = globals_of(text), [], []
    holds = {
        g
        for g, org in regions.items()
        if g in sizes and song[org : org + sizes[g]].any()
    }
    fn, bases, arg = None, {}, None

    def base(ptr):
        """``(global, offset or None)``, LOCAL, or None for a pointer from memory."""
        return bases.get(ptr) if ptr.startswith("%") else pointer(ptr)

    def may_read(ptr):
        b = base(ptr)
        if b is None or b == LOCAL or b[0] not in regions:
            return b is None
        g, off = b
        return g in holds and (off is None or bool(song[regions[g] + off]))

    def site(line):
        loc = DBG.search(line)
        sites.append((fn, *(locs[int(loc.group(1))] if loc else (0, 0))))
        return len(sites) - 1

    for line in lines:
        d = DEFINE.match(line)
        if d:
            fn, bases = d.group(1), {}
            arg = (re.findall(r"%[\w.]+", d.group(2)) or [None])[0]
        m = GEP.match(line)
        if m:
            b = LOCAL if m.group(2) == "alloca" else base(_operand(m.group(3)))
            bases[m.group(1)] = b if b in (None, LOCAL) else (b[0], None)
        ld, st, call = LOAD.match(line), STORE.match(line), CALL.match(line)
        if st and base(st.group(2)) != LOCAL:
            out.append(f"  call void @cgen_st(ptr {st.group(2)}, i8 {st.group(1)})")
        if call and call.group(1) in helpers and fn not in helpers:
            out.append(f"  store i32 {site(line)}, ptr @cgen_site, align 4")
        if fn in copies and re.match(r"\s*ret\b", line):
            out.append("  store i8 %cg.old, ptr @cgen_copy, align 1")
        out.append(line)
        if d and fn in copies:
            out += [
                "  %cg.old = load i8, ptr @cgen_copy, align 1",
                f"  %cg.copy = add i8 {arg}, 1",
                "  store i8 %cg.copy, ptr @cgen_copy, align 1",
            ]
        if ld and may_read(ld.group(2)):
            s = f"%cg.s{len(out)}"
            if fn in helpers:
                out.append(f"  {s} = load i32, ptr @cgen_site, align 4")
            else:
                s = site(line)
            out.append(
                f"  call void @cgen_ld(i32 {s}, ptr {ld.group(2)}, i8 {ld.group(1)})"
            )
    reg = [
        f"  call void @cgen_region(ptr @{g}, i32 {sizes[g]}, i32 {org})"
        for g, org in regions.items()
        if g in sizes
    ]
    out += [HOOKS, "define void @cgen_regions() {", *reg, "  ret void", "}"]
    return "\n".join(out) + "\n", sites
