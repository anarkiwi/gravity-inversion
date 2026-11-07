"""The LLVM IR of a translated player's generated C, as compact Python structures.

clang lowers the C (inlining what it may), llvmlite parses it; :class:`Module` holds each
function's blocks as op tuples with their loops and liveness (docs/eventlog.md)."""

import os
import re
import subprocess

import llvmlite.binding as llvm

INLINE = 1 << 30

SIZE = {"i1": 1, "i8": 1, "i16": 2, "i32": 4, "i64": 8, "ptr": 8}
CASTS = ("zext", "sext", "trunc", "freeze")


def signed(a, w):
    """``a``, a ``w``-bit value, as a signed integer."""
    return a - (1 << w) if a >> (w - 1) & 1 else a


def width(ty):
    """The bit width of an integer type, else None."""
    m = re.fullmatch(r"i(\d+)", str(ty))
    return int(m.group(1)) if m else None


def _type(s):
    """``(type, rest)``: the IR type at the start of ``s`` (arrays nested)."""
    s = s.lstrip()
    if not s.startswith("["):
        m = re.match(r"[\w.]+", s)
        return m.group(0), s[m.end() :]
    depth = 0
    for i, ch in enumerate(s):
        depth += {"[": 1, "]": -1}.get(ch, 0)
        if not depth:
            return s[: i + 1], s[i + 1 :]
    raise ValueError(s)


def _size(ty):
    """Byte size of an IR type."""
    if ty.startswith("["):
        n, rest = ty[1:].split(" x ", 1)
        return int(n) * _size(rest[:-1])
    return SIZE[ty]


def _elem(ty):
    """The element type of an array type."""
    return ty[1:-1].split(" x ", 1)[1]


def offset(ty, idx):
    """Byte offset of GEP indices ``idx`` over source element type ``ty``."""
    off = idx[0] * _size(ty)
    for i in idx[1:]:
        ty = _elem(ty)
        off += i * _size(ty)
    return off


def _items(s):
    """The top-level comma-separated items of an aggregate initializer's body."""
    out, depth, start, quote = [], 0, 0, False
    for i, ch in enumerate(s):
        if ch == '"':
            quote = not quote
        elif not quote and ch in "[(":
            depth += 1
        elif not quote and ch in "])":
            depth -= 1
        elif not quote and ch == "," and not depth:
            out.append(s[start:i].strip())
            start = i + 1
    return out + [s[start:].strip()] if s.strip() else out


def _target():
    llvm.initialize_native_target()
    llvm.initialize_native_asmprinter()
    return llvm.Target.from_default_triple().create_target_machine(reloc="pic")


def lower(c_path, includes=(), defines=()):
    """The LLVM IR text of a C file: clang -O1 inlining every call it may (no loop
    unrolling, vectorization or library idioms), switches lowered to branches and every
    value named."""
    flags = [
        "-O1",
        "-mllvm",
        f"-inline-threshold={INLINE}",
        "-fno-builtin",
        "-fno-unroll-loops",
    ]
    flags += ["-fno-vectorize", "-fno-slp-vectorize", "-S", "-emit-llvm", "-o", "-"]
    flags += [f"-I{i}" for i in includes] + [f"-D{d}" for d in defines]
    text = subprocess.run(
        [os.environ.get("CLANG", "clang"), *flags, str(c_path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    tm = _target()
    mod = llvm.parse_assembly(text)
    mpm = llvm.create_new_module_pass_manager()
    mpm.add_lower_switch_pass()
    mpm.add_instruction_namer_pass()
    mpm.run(mod, llvm.create_pass_builder(tm, llvm.create_pipeline_tuning_options()))
    return str(mod)


def compile_object(text, path):
    """Compile IR text to a position-independent native object at ``path``."""
    tm = _target()
    mod = llvm.parse_assembly(text)
    mod.verify()
    with open(path, "wb") as f:
        f.write(tm.emit_object(mod))


def _escaped(s):
    """Bytes of the body of an IR ``c"..."`` string."""
    out, i = bytearray(), 0
    while i < len(s):
        if s[i : i + 2] == "\\\\":
            out.append(0x5C)
            i += 2
        elif s[i] == "\\":
            out.append(int(s[i + 1 : i + 3], 16))
            i += 3
        else:
            out.append(ord(s[i]))
            i += 1
    return list(out)


def _pointer(text):
    """``(global, byte offset)`` of a constant pointer's text, else None."""
    m = re.search(r"getelementptr (?:inbounds |nuw |nusw )*\((.*)\)\s*$", text.strip())
    if m:
        ty, rest = _type(m.group(1))
        g = re.match(r",\s*ptr @([\w.]+)", rest)
        idx = [int(x) for x in re.findall(r"i\d+ (-?\d+)", rest[g.end() :])]
        return g.group(1), offset(ty, idx)
    m = re.search(r"(?:^|ptr )@([\w.]+)$", text.strip())
    return (m.group(1), 0) if m else None


def _bytes(ty, init):
    """Bytes of a constant of type ``ty``; a pointer is its target then Nones."""
    init = init.strip()
    if init in ("zeroinitializer", "undef", "poison"):
        return [0] * _size(ty)
    if init.startswith('c"'):
        return _escaped(init[2:-1])
    if ty.startswith("["):
        out = []
        for item in _items(init[1:-1]):
            ety, rest = _type(item)
            out += _bytes(ety, rest)
        return out
    if ty == "ptr":
        return [_pointer(init)] + [None] * (SIZE["ptr"] - 1)
    return list((int(init) & ((1 << 8 * _size(ty)) - 1)).to_bytes(_size(ty), "little"))


def _initial(g):
    """Initial bytes of a global variable."""
    m = re.search(r"(?:global|constant) (.*)$", str(g).strip())
    ty, rest = _type(m.group(1))
    return _bytes(ty, re.sub(r", align \d+$", "", rest))


def _operand(o):  # pylint: disable=too-many-return-statements
    """``('c', int or None)``, ``('p', (global, offset))``, ``('v', name)``,
    ``('b', block)`` or ``('f', function)`` of an llvmlite operand."""
    if o.is_block or str(o.type) == "label":
        return ("b", o.name)
    if o.is_function:
        return ("f", o.name)
    if o.is_global:
        return ("p", (o.name, 0))
    if o.is_constant:
        w = width(o.type)
        if w is None:
            return ("p", (o.name, 0) if o.name else _pointer(str(o)))
        try:
            return ("c", o.get_constant_value() & ((1 << w) - 1))
        except (ValueError, TypeError, AttributeError):
            return ("c", None)
    return ("v", o.name)


def _op(ins, text):
    """``(opcode, result, width, operands, extra)`` of an instruction printed as ``text``."""
    code, text, ops = ins.opcode, text.strip(), list(ins.operands)
    extra = None
    if code == "icmp":
        extra = (
            re.search(r"icmp (?:samesign )?(\w+)", text).group(1),
            width(ops[0].type),
        )
    elif code == "getelementptr":
        extra = _type(
            re.search(r"getelementptr (?:inbounds |nuw |nusw )*(.*)", text).group(1)
        )[0]
    elif code == "phi":
        extra = [b.name for b in ins.incoming_blocks]
    elif code in CASTS:
        extra = width(ops[0].type)
    elif code == "store":
        extra = width(ops[0].type) or 64
    elif code == "call":
        extra, ops = ops[-1].name, ops[:-1]
    return (code, ins.name or None, width(ins.type), [_operand(o) for o in ops], extra)


def successors(ops):
    """The successor blocks of a block's terminator, the taken edge first."""
    term = ops[-1]
    return [o[1] for o in term[3] if o[0] == "b"][::-1] if term[0] == "br" else []


class Module:  # pylint: disable=too-few-public-methods,too-many-instance-attributes
    """An IR module's defined functions ``{name: (block order, {block: ops}, args)}``,
    global variables' initial bytes, loops and liveness."""

    def __init__(self, text):
        mod = llvm.parse_assembly(text)
        self.text = text
        lines, where, _ = lines_of(text)
        self.globals = {
            g.name: _initial(g) for g in mod.global_variables if not g.is_declaration
        }
        self.funcs = {}
        for f in mod.functions:
            if not f.is_declaration:
                blocks = {
                    b.name: [
                        _op(x, lines[where[f.name, b.name, k]])
                        for k, x in enumerate(b.instructions)
                    ]
                    for b in f.blocks
                }
                order = [b.name for b in f.blocks]
                self.funcs[f.name] = (order, blocks, [a.name for a in f.arguments])
        self.pure = self._pure()
        self.loops, self.headers, self.latches, self.rpo, self.live_out = (
            {},
            {},
            {},
            {},
            {},
        )
        for fn in self.funcs:
            self.loops[fn], self.headers[fn], self.latches[fn], self.rpo[fn] = (
                self._loops(fn)
            )
            self.live_out[fn] = self._liveness(fn)
        self.ipdom = {fn: self._postdominators(fn) for fn in self.funcs}
        self._live = {}

    def _pure(self):
        """The functions that store nothing and call only functions that do not."""
        bad = {
            f
            for f, (_, blocks, _) in self.funcs.items()
            if any(
                op[0] == "store" or op[0] == "call" and op[4] not in self.funcs
                for b in blocks.values()
                for op in b
            )
        }
        changed = True
        while changed:
            changed = False
            for f, (_, blocks, _) in self.funcs.items():
                if f not in bad and any(
                    op[0] == "call" and op[4] in bad
                    for b in blocks.values()
                    for op in b
                ):
                    bad.add(f)
                    changed = True
        return set(self.funcs) - bad

    def _loops(self, fn):  # pylint: disable=too-many-locals
        """``({block: header phis of the natural loops holding it}, loop headers, latches,
        {block: reverse postorder index})``."""
        order, blocks, _ = self.funcs[fn]
        succ = {b: successors(blocks[b]) for b in order}
        pred = {b: [] for b in order}
        for b, ss in succ.items():
            for s in ss:
                pred[s].append(b)
        seen, onstack, post = {order[0]}, {order[0]}, []
        stack, back = [(order[0], iter(succ[order[0]]))], []
        while stack:
            b, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                stack.pop()
                onstack.discard(b)
                post.append(b)
            elif nxt in onstack:
                back.append((b, nxt))
            elif nxt not in seen:
                seen.add(nxt)
                onstack.add(nxt)
                stack.append((nxt, iter(succ[nxt])))
        out = {b: [] for b in order}
        for tail, head in back:
            body, work = {head, tail}, [tail]
            while work:
                for q in pred[work.pop()]:
                    if q not in body:
                        body.add(q)
                        work.append(q)
            phis = [op[1] for op in blocks[head] if op[0] == "phi"]
            for b in body:
                out[b] += [p for p in phis if p not in out[b]]
        rpo = post[::-1]
        return (
            out,
            {h for _, h in back},
            {t for t, _ in back},
            {b: i for i, b in enumerate(rpo)},
        )

    def _postdominators(self, fn):  # pylint: disable=too-many-locals
        """``{block: immediate post-dominator}`` (None: the exit), by Cooper, Harvey and
        Kennedy's iteration over the reversed CFG."""
        order, blocks, _ = self.funcs[fn]
        succ = {b: successors(blocks[b]) for b in order}
        exit_ = None
        rsucc = {exit_: [b for b in order if not succ[b]]}
        for b in order:
            rsucc.setdefault(b, [])
            for s in succ[b]:
                rsucc.setdefault(s, []).append(b)
        post, seen, stack = [], {exit_}, [(exit_, iter(rsucc[exit_]))]
        while stack:
            b, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                stack.pop()
                post.append(b)
            elif nxt not in seen:
                seen.add(nxt)
                stack.append((nxt, iter(rsucc[nxt])))
        index = {b: i for i, b in enumerate(post)}
        idom = {exit_: exit_}

        def common(a, b):
            while a != b:
                while index[a] < index[b]:
                    a = idom[a]
                while index[b] < index[a]:
                    b = idom[b]
            return a

        changed = True
        while changed:
            changed = False
            for b in reversed(post[:-1]):
                preds = [s for s in succ[b] if s in idom] or (
                    [exit_] if not succ[b] else []
                )
                if not preds:
                    continue
                new = preds[0]
                for p in preds[1:]:
                    new = common(p, new)
                if idom.get(b) != new:
                    idom[b], changed = new, True
        return {b: idom.get(b) for b in order}

    def _liveness(self, fn):  # pylint: disable=too-many-locals
        """``{block: values live at its end}`` (a phi's operand lives on its edge)."""
        order, blocks, _ = self.funcs[fn]
        defs, uses, phidefs, phiuses = {}, {}, {}, {}
        for b in order:
            d, u = set(), set()
            phidefs[b] = {op[1] for op in blocks[b] if op[0] == "phi"}
            for op in blocks[b]:
                if op[0] == "phi":
                    for (kind, name), src in zip(op[3], op[4]):
                        if kind == "v":
                            phiuses.setdefault((b, src), set()).add(name)
                    continue
                u |= {o[1] for o in op[3] if o[0] == "v" and o[1] not in d}
                if op[1]:
                    d.add(op[1])
            defs[b], uses[b] = d, u
        succ = {b: successors(blocks[b]) for b in order}
        live_in, live_out = {b: set() for b in order}, {b: set() for b in order}
        changed = True
        while changed:
            changed = False
            for b in reversed(order):
                out = set()
                for s in succ[b]:
                    out |= (live_in[s] - phidefs[s]) | phiuses.get((s, b), set())
                inn = uses[b] | (out - defs[b])
                if out != live_out[b] or inn != live_in[b]:
                    live_out[b], live_in[b], changed = out, inn, True
        return live_out

    def live(self, fn, blk, i):
        """The values live before op ``i`` of a block (its phis' results from op 0)."""
        k = (fn, blk, i)
        if k not in self._live:
            ops = self.funcs[fn][1][blk]
            live = set(self.live_out[fn][blk])
            for op in reversed(ops[i:]):
                if op[0] == "phi":
                    break
                live.discard(op[1])
                live |= {o[1] for o in op[3] if o[0] == "v"}
            self._live[k] = frozenset(live)
        return self._live[k]


def lines_of(text):
    """``(lines, {(function, block, op index): line index}, {function: closing line})``."""
    where, ends, fn, blk, k = {}, {}, None, None, 0
    lines = text.split("\n")
    for j, line in enumerate(lines):
        mdef = re.match(r"define .*@([\w.]+)\(", line)
        if mdef:
            fn, blk, k = mdef.group(1), None, 0
        elif fn and re.match(r"[\w.]+:", line):
            blk, k = line.split(":")[0], 0
        elif fn and line.startswith("}"):
            ends[fn], fn = j, None
        elif fn and line.startswith("  ") and line.strip():
            where[fn, blk, k] = j
            k += 1
    return lines, where, ends
