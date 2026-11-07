"""Instrumentation of a translated player's LLVM IR for tools/eventlog.c: every value
carries a tag, the newest sequence read it derives from, and every register write reports
the tag of its value (docs/eventlog.md)."""

import re

from irmodule import lines_of


class Tags:  # pylint: disable=too-few-public-methods
    """IR lines tracking, for every value, the newest sequence read it carries: a read
    takes a new tag (``cgen_tag(read)``), a load at a tagged address that address's tag,
    another load its bytes', other ops the greatest of their flowing operands'; a store
    keeps its value's tag per byte, a register write reports it (``cgen_sink``)."""

    def __init__(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self, m, fn, sources, entry=False, flows=None, sink="io_write"
    ):
        self.m, self.fn, self.sources, self.sink = m, fn, sources, sink
        self.flows = flows or {}
        self.args = set(m.funcs[fn][2]) if entry else set()

    def of(self, o):
        """The tag operand of an operand (an entry's arguments carry none)."""
        return f"%{o[1]}.t" if o[0] == "v" and o[1] not in self.args else "0"

    def entry(self):
        """Lines taking a callee's argument tags from the caller."""
        if self.args:
            return []
        return [
            f"  %{a}.t = load i32, ptr @cgen_targ{i}, align 4"
            for i, a in enumerate(self.m.funcs[self.fn][2])
        ]

    def before(self, op):
        """Tag lines before ``op``: a call hands its argument tags, a return its tag."""
        if op[0] == "call" and op[4] in self.m.funcs:
            return [
                f"  store i32 {self.of(o)}, ptr @cgen_targ{i}, align 4"
                for i, o in enumerate(op[3])
            ]
        if op[0] == "ret" and op[3]:
            return [f"  store i32 {self.of(op[3][0])}, ptr @cgen_tret, align 4"]
        if op[0] == "call" and op[4] == self.sink and len(op[3]) == 2:
            return [f"  call void @cgen_sink(i32 {self.of(op[3][1])})"]
        return []

    def umax(self, out, tags):
        """Lines setting ``out`` to the greatest of ``tags``."""
        tags = [x for x in tags if x != "0"] or ["0"]
        lines, acc = [], tags[0]
        for i, x in enumerate(tags[1:]):
            name = out if i == len(tags) - 2 else f"{out}.{i}"
            lines.append(f"  {name} = call i32 @llvm.umax.i32(i32 {acc}, i32 {x})")
            acc = name
        return lines or [f"  {out} = add i32 {acc}, 0"]

    @staticmethod
    def pointer(line, code):
        """The pointer operand text of a load or store line."""
        rx = (
            r"load [^,]+, ptr (.+?), align"
            if code == "load"
            else r"store .+?, ptr (.+?), align"
        )
        return re.search(rx, line).group(1)

    def lines(self, op, line):  # pylint: disable=too-many-return-statements
        """Tag lines for instruction ``op`` printed as ``line``."""
        code, name, _, args, extra = op
        if code == "store":
            return [
                f"  call void @cgen_tag_store(ptr {self.pointer(line, code)}, "
                f"i32 {self.of(args[0])})"
            ]
        if not name or code == "call" and not re.match(r"\s*%\S+ = ", line):
            return []
        out = f"%{name}.t"
        if code == "phi":
            inc = ", ".join(f"[ {self.of(o)}, %{b} ]" for o, b in zip(args, extra))
            return [f"  {out} = phi i32 {inc}"]
        if code == "select":
            c = args[0]
            cond = f"%{c[1]}" if c[0] == "v" else ("true" if c[1] else "false")
            return [
                f"  {out} = select i1 {cond}, i32 {self.of(args[1])}, i32 {self.of(args[2])}"
            ]
        if code == "icmp":
            return [f"  {out} = add i32 0, 0"]
        if code == "call":
            if (self.fn, name) in self.sources:
                return [
                    f"  {out} = call i32 @cgen_tag(i16 {self.sources[self.fn, name]})"
                ]
            if extra in self.m.funcs:
                return [f"  {out} = load i32, ptr @cgen_tret, align 4"]
            return [f"  {out} = add i32 0, 0"]
        if code == "load":
            if (self.fn, name) in self.sources:
                return [
                    f"  {out} = call i32 @cgen_tag(i16 {self.sources[self.fn, name]})"
                ]
            ptr = self.pointer(line, code)
            at = self.of(args[0])
            if at == "0":
                return [f"  {out} = call i32 @cgen_tag_load(ptr {ptr})"]
            return [
                f"  %{name}.c = call i32 @cgen_tag_load(ptr {ptr})",
                f"  %{name}.a = icmp ne i32 {at}, 0",
                f"  {out} = select i1 %{name}.a, i32 {at}, i32 %{name}.c",
            ]
        flow = self.flows.get((self.fn, name))
        return self.umax(
            out,
            [self.of(o) for o in args if flow is None or o[0] != "v" or o[1] in flow],
        )


def instrument(m, sources, entries=(), flows=None):
    """IR text of module ``m`` with the tags of :class:`Tags`; ``sources`` maps each
    sequence read ``(function, value)`` to its number, ``entries`` are the functions the
    host calls (their arguments carry no tag), ``flows`` the operands each value op takes
    its value from (all where absent)."""
    lines, where, _ = lines_of(m.text)
    after, before = {}, {}
    for fn, (order, blocks, _) in m.funcs.items():
        tags = Tags(m, fn, sources, fn in entries, flows)
        before.setdefault(where[fn, order[0], 0], []).extend(tags.entry())
        for b in order:
            for k, op in enumerate(blocks[b]):
                j = where[fn, b, k]
                after.setdefault(j, []).extend(tags.lines(op, lines[j]))
                before.setdefault(j, []).extend(tags.before(op))
    out = []
    for j, line in enumerate(lines):
        out += before.get(j, [])
        out.append(line)
        out += after.get(j, [])
    arity = max((len(a) for _, _, a in m.funcs.values()), default=0)
    decls = [f"@cgen_targ{i} = internal global i32 0" for i in range(arity)]
    decls += [
        "@cgen_tret = internal global i32 0",
        "declare void @cgen_sink(i32)",
        "declare i32 @cgen_tag(i16)",
        "declare i32 @cgen_tag_load(ptr)",
        "declare void @cgen_tag_store(ptr, i32)",
        "declare i32 @llvm.umax.i32(i32, i32)",
    ]
    return "\n".join(out + decls) + "\n"
