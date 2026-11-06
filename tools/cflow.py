"""Model of generated C from clang's JSON AST: integer evaluation, source spans, regions,
call graph, and a must-equal value flow over functions (tools/ablate.py rewrites with it).
"""

import itertools
import operator
import re
from collections import defaultdict

from costs import callee, walk

IO = range(0xD000, 0xE000)
LOOPS = ("ForStmt", "WhileStmt", "DoStmt")
CASTS = ("ImplicitCastExpr", "CStyleCastExpr")
ASSIGNS = ("=", "++", "--")
WIDTH = {
    "_Bool": (1, False),
    "char": (8, True),
    "signed char": (8, True),
    "unsigned char": (8, False),
    "short": (16, True),
    "int": (16, True),
    "unsigned short": (16, False),
    "unsigned int": (16, False),
    "long": (32, True),
    "unsigned long": (32, False),
}
BINARY = {
    "+": operator.add,
    "-": operator.sub,
    "*": operator.mul,
    "&": operator.and_,
    "|": operator.or_,
    "^": operator.xor,
    "<<": operator.lshift,
    ">>": operator.rshift,
    "==": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    ">": operator.gt,
    "<=": operator.le,
    ">=": operator.ge,
}
RET = "return"


class NotConstant(Exception):
    """An expression the evaluator cannot reduce to a value."""


def ctype(node):
    """(bits, signed) of an expression's integer type, or None."""
    t = node.get("type", {})
    return WIDTH.get(t.get("desugaredQualType", t.get("qualType", "")).strip())


def wrap(value, node):
    """`value` converted to the type of `node`."""
    w = ctype(node)
    if w is None:
        raise NotConstant(node.get("kind"))
    bits, signed = w
    value &= (1 << bits) - 1
    return value - (1 << bits) if signed and value >> (bits - 1) else value


def evaluate(node, env):  # pylint: disable=too-many-return-statements
    """The value of a side-effect free C integer expression, `env` {decl id: value}."""
    kind, inner = node.get("kind"), node.get("inner", ())
    if kind in ("IntegerLiteral", "CharacterLiteral"):
        return wrap(int(node["value"]), node)
    if kind in ("ParenExpr", "ConstantExpr"):
        return evaluate(inner[0], env)
    if kind in CASTS:
        v = evaluate(inner[0], env)
        cast = node.get("castKind")
        if cast == "IntegralToBoolean":
            return int(v != 0)
        return v if cast in ("LValueToRValue", "NoOp") else wrap(v, node)
    if kind == "DeclRefExpr" and node["referencedDecl"]["id"] in env:
        return env[node["referencedDecl"]["id"]]
    if kind == "UnaryOperator" and node["opcode"] in ("-", "~", "!", "+"):
        v = evaluate(inner[0], env)
        return wrap({"-": -v, "~": ~v, "!": int(not v), "+": v}[node["opcode"]], node)
    if kind == "ConditionalOperator":
        return evaluate(inner[1] if evaluate(inner[0], env) else inner[2], env)
    if kind == "BinaryOperator" and node["opcode"] in ("&&", "||"):
        a = bool(evaluate(inner[0], env))
        return (
            int(a)
            if (node["opcode"] == "&&") != a
            else int(bool(evaluate(inner[1], env)))
        )
    if kind == "BinaryOperator" and node["opcode"] in BINARY:
        a, b = evaluate(inner[0], env), evaluate(inner[1], env)
        return wrap(int(BINARY[node["opcode"]](a, b)), node)
    raise NotConstant(kind)


def constant(node, env=None):
    """evaluate(), or None when not constant."""
    try:
        return evaluate(node, env or {})
    except NotConstant:
        return None


def strip(node, kinds=("ParenExpr", *CASTS)):
    """`node` under parentheses and casts."""
    while node.get("kind") in kinds:
        node = node["inner"][0]
    return node


def target(node):
    """Declaration id an assignment, increment or decrement writes, or None."""
    if node.get("kind") == "CompoundAssignOperator" or node.get("opcode") in ASSIGNS:
        return (
            strip(node["inner"][0], ("ParenExpr",)).get("referencedDecl", {}).get("id")
        )
    return None


def is_byte(node):
    """Whether an expression's value is an unsigned byte before integer promotion."""
    while node.get("kind") in ("ParenExpr", "ImplicitCastExpr"):
        if ctype(node) == (8, False):
            return True
        node = node["inner"][0]
    v = constant(node)
    return ctype(node) == (8, False) or (v is not None and 0 <= v < 256)


def effects(node):
    """Whether an expression may have side effects."""
    return any(
        n.get("kind") in ("CallExpr", "CompoundAssignOperator")
        or n.get("opcode") in ASSIGNS
        for n, _ in walk(node)
    )


def rewrite(text, edits):
    """`text` with each (begin, end, replacement) applied; an edit overlapping a later one
    is dropped."""
    out, last = text, len(text) + 1
    for b, e, s in sorted(set(edits), key=lambda x: (x[0], x[1]), reverse=True):
        if e <= last:
            out, last = out[:b] + s + out[e:], b
    return out


def parse_header(text):
    """({name: value} of numeric #defines, {name: bytes} of the _INIT lists) of a header."""
    text = text.replace("\\\n", " ")
    defs = {
        k: int(v, 0)
        for k, v in re.findall(r"^#define (\w+) +(0x[0-9A-Fa-f]+|\d+)\s*$", text, re.M)
    }
    inits = {
        k: bytes(int(x, 0) for x in v.split(",") if x.strip())
        for k, v in re.findall(r"^#define (\w+)_INIT +(.*)$", text, re.M)
    }
    return defs, inits


def regions(defs):
    """{name: (origin, length)} of the header's regions."""
    return {
        k[:-4]: (v, defs[k[:-4] + "_LEN"])
        for k, v in defs.items()
        if k.endswith("_ORG") and k[:-4] + "_LEN" in defs
    }


class Unit:
    """A C file with its AST: functions, globals, call graph and source spans."""

    def __init__(self, text, tree, regs):
        self.text, self.regs = text, regs
        self.parent = {n["id"]: p for n, p in walk(tree) if "id" in n and p is not None}
        top = tree.get("inner", ())
        self.fns = {
            d["name"]: d
            for d in top
            if d.get("kind") == "FunctionDecl"
            and any(c.get("kind") == "CompoundStmt" for c in d.get("inner", ()))
        }
        self.globals = {d["id"]: d for d in top if d.get("kind") == "VarDecl"}
        self.arrays = {
            d["name"]
            for d in self.globals.values()
            if d["name"] in regs and "[" in d["type"]["qualType"]
        }
        self.calls = {
            f: {callee(n) for n, _ in walk(d) if n.get("kind") == "CallExpr"}
            for f, d in self.fns.items()
        }

    def span(self, node):
        """[begin, end) offsets of a node in the file, macro expansions included."""
        r = node["range"]
        b = r["begin"].get("expansionLoc", r["begin"])
        e = r["end"].get("expansionLoc", r["end"])
        return b["offset"], e["offset"] + e["tokLen"]

    def extent(self, node):
        """span() of a statement, with the semicolon closing it (a do-while's, or a loop's
        whose body is a simple statement)."""
        b, e = self.span(node)
        return b, self.text.index(";", e) + 1 if self.text[e - 1] not in ";}" else e

    def src(self, node):
        """Source text of a node."""
        b, e = self.span(node)
        return self.text[b:e]

    def body(self, name):
        """The CompoundStmt of a function."""
        return next(c for c in self.fns[name]["inner"] if c["kind"] == "CompoundStmt")

    def params(self, name):
        """ParmVarDecls of a function."""
        return [p for p in self.fns[name]["inner"] if p.get("kind") == "ParmVarDecl"]

    def reach(self, root):
        """Functions reachable from `root`, itself included."""
        seen, todo = set(), [root]
        while todo:
            f = todo.pop()
            if f in self.fns and f not in seen:
                seen.add(f)
                todo += self.calls[f]
        return seen

    def only(self, root, other):
        """Functions reachable from `root` and not from `other`."""
        return self.reach(root) - self.reach(other)

    def cell(self, node):
        """(region, index) of an array subscript with a constant index into a region."""
        node = strip(node, ("ParenExpr", "ImplicitCastExpr"))
        if node.get("kind") != "ArraySubscriptExpr":
            return None
        name = self.src(node["inner"][0]).strip("() ")
        i = constant(node["inner"][1])
        return (name, i) if name in self.regs and i is not None else None

    def address(self, node):
        """6502 address of a constant-index region subscript, or None."""
        c = self.cell(node)
        return None if c is None else self.regs[c[0]][0] + c[1]

    def rvalue(self, node):
        """Whether a node is read (under an lvalue-to-rvalue conversion)."""
        p = self.parent.get(node["id"], {})
        return p.get("castKind") == "LValueToRValue"

    def pointer(self, node):
        """(region, index) of a little-endian pointer read `R[i] | (uint16_t)R[i + 1] << 8`."""
        node = strip(node)
        if node.get("opcode") != "|":
            return None
        for lo, hi in (node["inner"], node["inner"][::-1]):
            hi = strip(hi)
            if hi.get("opcode") != "<<" or constant(hi["inner"][1]) != 8:
                continue
            a, b = self.cell(lo), self.cell(strip(hi["inner"][0]))
            if a and b and a[0] == b[0] and b[1] == a[1] + 1:
                return a
        return None

    def array_at(self, lo, hi):
        """The defined region array holding addresses [lo, hi], or None."""
        if lo in IO or hi in IO:
            return None
        return next(
            (
                a
                for a in sorted(self.arrays)
                if self.regs[a][0] <= lo and hi < sum(self.regs[a])
            ),
            None,
        )

    def consts(self, name):
        """{decl id: value} of a function's locals initialised to a constant, never assigned."""
        assigned = {target(n) for n, _ in walk(self.fns[name])}
        env = {}
        for n, _ in walk(self.fns[name]):
            if (
                n.get("kind") == "VarDecl"
                and n.get("inner")
                and n["id"] not in assigned
            ):
                v = constant(n["inner"][0], env)
                if v is not None:
                    env[n["id"]] = wrap(v, n)
        return env


class Flow:  # pylint: disable=too-many-instance-attributes
    """Must-equal value analysis of a function: which variables hold the same value.

    A state maps declaration ids and zero-page cells to value tokens (a parameter's entry
    value, a literal, a pointer of two cells' tokens, or fresh); None is unreachable."""

    def __init__(self, unit, summary, mod):
        self.u, self.summary, self.mod = unit, summary, mod
        self.tracked = {
            i
            for i, d in unit.globals.items()
            if ctype(d) and "volatile" not in d["type"]["qualType"]
        }
        self.count = itertools.count()
        self.brk, self.cont, self.gotos, self.exits = [], [], defaultdict(list), []
        self.sites, self.ptrs, self.back = {}, {}, set()

    def fresh(self):
        """A token equal to no other."""
        return ("fresh", next(self.count))

    def run(self, name):
        """[(exit state, returned token)] of a function; records global read sites."""
        decl = self.u.fns[name]
        self.brk, self.cont, self.gotos, self.exits = [], [], defaultdict(list), []
        labels = {n["declId"]: n for n, _ in walk(decl) if n.get("kind") == "LabelStmt"}
        self.back = {
            n["targetLabelDeclId"]
            for n, _ in walk(decl)
            if n.get("kind") == "GotoStmt"
            and self.u.span(n)[0] > self.u.span(labels[n["targetLabelDeclId"]])[0]
        }
        st = {p["id"]: ("param", j) for j, p in enumerate(self.u.params(name))}
        end = self.stmt(self.u.body(name), st)
        if end is not None:
            self.exits.append((end, None))
        return self.exits

    @staticmethod
    def join(*states):
        """Facts common to every reachable state."""
        live = [s for s in states if s is not None]
        if not live:
            return None
        return {k: v for k, v in live[0].items() if all(s.get(k) == v for s in live)}

    def zp(self, node):
        """State key of a constant-index zero-page cell, or None."""
        c = self.u.cell(node)
        return ("cell", *c) if c and self.u.regs[c[0]][0] + c[1] < 0x100 else None

    def val(self, node, st):
        """Token of an expression that copies a variable, is a literal, or reads a zero-page
        pointer, else None."""
        while node.get("kind") in ("ParenExpr", "ImplicitCastExpr"):
            cast = node.get("castKind")
            if cast == "IntegralCast":
                src, dst = ctype(node["inner"][0]), ctype(node)
                if not src or not dst or dst[0] < src[0]:
                    return None
            elif node["kind"] == "ImplicitCastExpr" and cast not in (
                "LValueToRValue",
                "NoOp",
            ):
                return None
            node = node["inner"][0]
        if node.get("kind") == "IntegerLiteral":
            return ("lit", int(node["value"]))
        if node.get("kind") == "DeclRefExpr":
            return st.get(node["referencedDecl"]["id"])
        c = self.u.pointer(node)
        lo = c and st.get(("cell", *c))
        hi = c and st.get(("cell", c[0], c[1] + 1))
        return ("ptr", *c, lo, hi) if lo and hi and self.zp_cell(c) else None

    def zp_cell(self, cell):
        """Whether a (region, index) cell is in zero page."""
        return self.u.regs[cell[0]][0] + cell[1] < 0x100

    def pointer_read(self, n, st):
        """Record whether rd() reads through a variable holding a zero-page pointer whose
        cells are unchanged, plus a byte."""
        arg = strip(n["inner"][1])
        parts = [(arg, None)]
        if arg.get("opcode") == "+":
            parts += [tuple(arg["inner"]), tuple(arg["inner"][::-1])]
        for ptr, y in parts:
            t = self.val(ptr, st)
            if t and t[0] == "ptr" and (y is None or is_byte(y)):
                cur = (st.get(("cell", t[1], t[2])), st.get(("cell", t[1], t[2] + 1)))
                ok = cur == t[3:]
                old = self.ptrs.get(n["id"], (None, None, None, True))
                self.ptrs[n["id"]] = (
                    n,
                    t[1:3],
                    y,
                    ok and old[3] and old[1] in (None, t[1:3]),
                )
                return
        self.ptrs[n["id"]] = (n, None, None, False)

    def stmt(
        self, n, st
    ):  # pylint: disable=too-many-return-statements,too-many-branches
        """State after a statement."""
        kind, inner = n.get("kind"), n.get("inner", ())
        if kind == "CompoundStmt":
            for c in inner:
                st = self.stmt(c, st)
            return st
        if kind == "DeclStmt":
            for v in inner:
                init = v.get("inner", [{}])[0]
                st = self.expr(init, st)
                if st is not None:
                    st = {**st, v["id"]: self.val(init, st) or self.fresh()}
            return st
        if kind == "IfStmt":
            st = self.expr(inner[0], st)
            other = self.stmt(inner[2], st) if len(inner) > 2 else st
            return self.join(self.stmt(inner[1], st), other)
        if kind in LOOPS:
            return self.loop(n, st)
        if kind == "SwitchStmt":
            return self.switch(n, st)
        if kind in ("BreakStmt", "ContinueStmt"):
            (self.brk if kind == "BreakStmt" else self.cont)[-1].append(st)
            return None
        if kind == "ReturnStmt":
            st = self.expr(inner[0], st) if inner else st
            if st is not None:
                self.exits.append((st, self.val(inner[0], st) if inner else None))
            return None
        if kind == "GotoStmt":
            self.gotos[n["targetLabelDeclId"]].append(st)
            return None
        if kind == "LabelStmt":
            back = n["declId"] in self.back
            return self.stmt(
                inner[0], {} if back else self.join(st, *self.gotos[n["declId"]])
            )
        return self.expr(n, st)

    def loop(self, n, st):
        """State after a loop: its head iterated to a fixed point."""
        kind, inner = n["kind"], n["inner"]
        if kind == "ForStmt":
            cond, inc, body = inner[2], inner[3], inner[4]
        else:
            cond, inc, body = inner[kind == "DoStmt"], {}, inner[kind == "WhileStmt"]
        pre = self.stmt(inner[0], st) if kind == "ForStmt" else st
        head = pre
        while True:
            self.brk.append([])
            self.cont.append([])
            s, out = head, None
            if kind != "DoStmt" and cond.get("kind"):
                s = out = self.expr(cond, s)
            s = self.expr(inc, self.join(self.stmt(body, s), *self.cont.pop()))
            if kind == "DoStmt":
                s = out = self.expr(cond, s)
            brk = self.brk.pop()
            new = self.join(pre, s)
            if new == head:
                return self.join(out, *brk)
            head = new

    def switch(self, n, st):
        """State after a switch statement."""
        entry = self.expr(n["inner"][0], st)
        self.brk.append([])
        cur, default = None, False
        for c in n["inner"][-1].get("inner", ()):
            while c.get("kind") in ("CaseStmt", "DefaultStmt"):
                default |= c["kind"] == "DefaultStmt"
                cur = self.join(cur, entry)
                c = c["inner"][-1]
            cur = self.stmt(c, cur)
        return self.join(cur, *self.brk.pop(), None if default else entry)

    def call(self, n, st, maybe):
        """State after a call: the callee's writable globals forgotten, those it returns a
        parameter in set to the argument, unreachable after a call that never returns.
        """
        for a in n["inner"][1:]:
            st = self.expr(a, st, maybe)
        if st is None:
            return None
        name = callee(n)
        if name == "rd" and not maybe:
            self.pointer_read(n, st)
        args = [self.val(a, st) for a in n["inner"][1:]]
        m = self.mod(name)
        out = {
            k: v
            for k, v in st.items()
            if k not in m and not (isinstance(k, tuple) and "cells" in m)
        }
        if maybe:
            return out
        if name in self.summary["noreturn"]:
            return None
        for g, j in self.summary["ident"].get(name, {}).items():
            if g != RET:
                out[g] = (args[j] if j < len(args) else None) or self.fresh()
        return out

    def expr(self, n, st, maybe=False):  # pylint: disable=R0911,R0912
        """State after evaluating an expression; `maybe`: it may not be evaluated."""
        if st is None or not n.get("kind"):
            return st
        kind, inner, op = n["kind"], n.get("inner", ()), n.get("opcode")
        if kind == "CallExpr":
            return self.call(n, st, maybe)
        if n.get("castKind") == "LValueToRValue":
            ref = strip(inner[0], ("ParenExpr",))
            if ref.get("referencedDecl", {}).get("id") in self.tracked:
                self.site(ref, st)
            key = self.zp(ref)
            if key and key not in st:
                st = {**st, key: self.fresh()}
        if kind in ("ConditionalOperator", "BinaryConditionalOperator") or op in (
            "&&",
            "||",
        ):
            st = self.expr(inner[0], st, maybe)
            for c in inner[1:]:
                st = self.expr(c, st, True)
            return st
        if op == "=":
            st = self.expr(inner[1], st, maybe)
            lhs = strip(inner[0], ("ParenExpr",))
            if lhs.get("kind") == "ArraySubscriptExpr":
                st = self.expr(inner[0], st, maybe)
                key = self.zp(lhs)
                if st is not None and key is None and zero_page_array(self.u, lhs):
                    return {k: v for k, v in st.items() if not isinstance(k, tuple)}
            elif lhs.get("kind") == "DeclRefExpr":
                key = lhs["referencedDecl"]["id"]
            else:
                return self.expr(inner[0], st, maybe)
            if st is None or key is None:
                return st
            tok = None if maybe else self.val(inner[1], st) or self.fresh()
            st = {k: v for k, v in st.items() if k != key}
            return st if tok is None else {**st, key: tok}
        for c in inner:
            st = self.expr(c, st, maybe)
        key = target(n)
        if kind == "CompoundAssignOperator" or op in ASSIGNS:
            key = key or self.zp(inner[0])
        return (
            st
            if key is None or st is None
            else {k: v for k, v in st.items() if k != key}
        )

    def site(self, ref, st):
        """Record which literal or variables hold a global's value where it is read."""
        t = st.get(ref["referencedDecl"]["id"])
        if t is None:
            cands = set()
        elif t[0] == "lit":
            cands = {t}
        else:
            cands = {k for k, v in st.items() if v == t and k not in self.tracked}
        old = self.sites.get(ref["id"])
        self.sites[ref["id"]] = (ref, cands if old is None else old[1] & cands)


def zero_page_array(unit, node):
    """Whether an array subscript indexes a region array starting in zero page."""
    node = strip(node, ("ParenExpr", "ImplicitCastExpr"))
    name = unit.src(node["inner"][0]).strip("() ")
    return name in unit.regs and unit.regs[name][0] < 0x100


def summarize(unit):
    """Flow summaries to a fixed point, {"ident": {fn: {global id or RET: parameter index}},
    "noreturn": set}, and mod(fn): the globals, and "cells" for zero page, a call may write.
    """

    def stores_zp(d):
        return any(
            (n.get("opcode") == "=" or n.get("kind") == "CompoundAssignOperator")
            and strip(n["inner"][0], ("ParenExpr",)).get("kind") == "ArraySubscriptExpr"
            and zero_page_array(unit, n["inner"][0])
            or n.get("kind") == "CallExpr"
            and callee(n) == "wr"
            for n, _ in walk(d)
        )

    direct = {
        f: {target(n) for n, _ in walk(d)} & set(unit.globals)
        | ({"cells"} if stores_zp(d) else set())
        for f, d in unit.fns.items()
    }
    public = {i for i, d in unit.globals.items() if d.get("storageClass") != "static"}
    public.add("cells")
    mods = {
        f: set().union(*(direct.get(g, public) for g in unit.reach(f) | unit.calls[f]))
        for f in unit.fns
    }

    def mod(name):
        return mods.get(name, public)

    summary = {"ident": {}, "noreturn": set()}
    while True:
        new = {"ident": {}, "noreturn": set()}
        for f in unit.fns:
            flow = Flow(unit, summary, mod)
            exits = flow.run(f)
            if not exits:
                new["noreturn"].add(f)
                continue
            ident = {}
            for g in flow.tracked:
                toks = {s.get(g) for s, _ in exits}
                if len(toks) == 1 and (t := toks.pop()) and t[0] == "param":
                    ident[g] = t[1]
            rets = {r for _, r in exits}
            if len(rets) == 1 and (t := rets.pop()) and t[0] == "param":
                ident[RET] = t[1]
            new["ident"][f] = ident
        if new == summary:
            return summary, mod
        summary = new


def scopes(unit, name):
    """{decl id: (name, begin, end)} of a function's parameters and locals: where each is
    declared and where its scope ends."""
    out = {p["id"]: (p["name"], *unit.span(unit.fns[name])) for p in unit.params(name)}
    for n, p in walk(unit.fns[name]):
        if n.get("kind") == "DeclStmt":
            for v in n.get("inner", ()):
                out[v["id"]] = (v["name"], unit.span(v)[0], unit.span(p)[1])
    return out


def loops(unit, fn):
    """The outermost loop statements of a function, in source order."""
    out = []

    def visit(node):
        for c in node.get("inner", ()):
            if c.get("kind") in LOOPS:
                out.append(c)
            else:
                visit(c)

    visit(unit.body(fn))
    return out


def removable(unit, fn, loop):
    """Whether removing a loop keeps control flow and locals: it is a statement of a block,
    does not return, jumps out only to the statement after it, is not jumped into, and no
    local it assigns is used outside it."""
    block = unit.parent[loop["id"]]
    if block.get("kind") != "CompoundStmt":
        return False
    inside = [n for n, _ in walk(loop)]
    labels = {n["declId"] for n in inside if n.get("kind") == "LabelStmt"}
    stmts = block["inner"]
    after = stmts[stmts.index(loop) + 1 :][:1]
    exits = labels | {n["declId"] for n in after if n.get("kind") == "LabelStmt"}
    inner = {n.get("id") for n in inside}
    if any(n.get("kind") == "ReturnStmt" for n in inside) or any(
        n.get("kind") == "GotoStmt"
        and (
            n["targetLabelDeclId"] not in exits
            if n["id"] in inner
            else n["targetLabelDeclId"] in labels
        )
        for n, _ in walk(unit.fns[fn])
    ):
        return False
    declared = {n["id"] for n in inside if n.get("kind") == "VarDecl"}
    assigned = {target(n) for n in inside} - declared - set(unit.globals) - {None}
    b, e = unit.extent(loop)
    return not any(
        n.get("referencedDecl", {}).get("id") in assigned
        and not b <= unit.span(n)[0] < e
        for n, _ in walk(unit.fns[fn])
        if n.get("kind") == "DeclRefExpr"
    )


def chain(node):
    """[(condition or None, body)] of an if / else if / else chain."""
    out = []
    while node.get("kind") == "IfStmt" and len(node["inner"]) == 3:
        out.append((node["inner"][0], node["inner"][1]))
        node = node["inner"][2]
    return out + [(None, node)] if out else []


def alike(a, b, ren, lits):
    """Whether two subtrees match up to integer literals (collected in `lits` as (literal
    in `a`, its value in `b`)) and locals they declare (`ren` maps b's ids to a's)."""
    keys = ("kind", "opcode", "castKind", "type")
    if any(a.get(k) != b.get(k) for k in keys) or len(a.get("inner", ())) != len(
        b.get("inner", ())
    ):
        return False
    if a.get("kind") == "IntegerLiteral":
        lits.append((a, int(b["value"])))
        return "spellingLoc" not in a["range"]["begin"]
    if a.get("kind") == "VarDecl":
        ren[b["id"]] = a["id"]
    if a.get("kind") == "DeclRefExpr":
        ida, idb = a["referencedDecl"]["id"], b["referencedDecl"]["id"]
        if ren.get(idb, idb) != ida:
            return False
    return all(
        alike(x, y, ren, lits) for x, y in zip(a.get("inner", ()), b.get("inner", ()))
    )
