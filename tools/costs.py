"""Detect code-size costs in generated C from clang's AST (docs/costs.md).

Each kind is a construct llvm-mos compiles to more code than the 6502 idiom it translates: K1
instance state through rd/wr, K2 multiplying by the instance index, K3 per-instance constants as
select-sums, K4 struct returns, K5 16-bit region indices, K6 discarded reads."""

import argparse
import json
import os
import pathlib
import re
import subprocess
from collections import Counter

RUNTIME = frozenset(
    "io_read io_write trap oob_rd oob_wr rd wr codechk tune_init tune_play".split()
)
#: cgen's bounds-checked readers of one held span (`rdC0`, ...), runtime like `rd`.
CHECKED = re.compile(r"rdC\d+$")
READS = frozenset(("rd", "io_read"))
KINDS = ("K1", "K2", "K3", "K4", "K5", "K6")
REGION = re.compile(r"(?:[RC]\d+|CODE)$")
CONDITIONAL = frozenset(
    (
        "IfStmt",
        "WhileStmt",
        "DoStmt",
        "ConditionalOperator",
        "BinaryConditionalOperator",
    )
)
WRAP = frozenset(("ParenExpr", "ImplicitCastExpr", "CStyleCastExpr"))


def ast(source, includes, cc=None):
    """clang's JSON AST of a C file."""
    cc = cc or os.environ.get("CC", "mos-c64-clang")
    args = [cc, "-Xclang", "-ast-dump=json", "-fsyntax-only"]
    args += [f"-I{i}" for i in includes] + [str(source)]
    return json.loads(subprocess.run(args, capture_output=True, check=True).stdout)


def walk(node, parent=None):
    """(node, parent) of every node of an AST subtree, preorder."""
    yield node, parent
    for child in node.get("inner", ()):
        yield from walk(child, node)


def refs(node):
    """Ids of the declarations a subtree refers to."""
    return {
        n["referencedDecl"]["id"]
        for n, _ in walk(node)
        if n.get("kind") == "DeclRefExpr"
    }


def callee(call):
    """Name of the function a CallExpr calls, or None."""
    for n, _ in walk(call["inner"][0]):
        if n.get("kind") == "DeclRefExpr":
            return n["referencedDecl"]["name"]
    return None


def literal(node):
    """Value of an integer literal under casts and parentheses, or None."""
    for n, _ in walk(node):
        if n.get("kind") == "IntegerLiteral":
            return int(n["value"])
        if n.get("kind") not in ("ImplicitCastExpr", "ParenExpr", "IntegerLiteral"):
            return None
    return None


def functions(tree):
    """Tune functions defined in the file: [(name, decl)]."""
    return [
        (d["name"], d)
        for d in tree.get("inner", ())
        if d.get("kind") == "FunctionDecl"
        and "inner" in d
        and d["name"] not in RUNTIME
        and not CHECKED.match(d["name"])
        and any(c.get("kind") == "CompoundStmt" for c in d["inner"])
    ]


def condition_tests(node):
    """Ids of the `==` comparisons used only as conditions (a branch, `&&`, `||`, `!`, through
    parentheses, casts and the value of a comma expression), not as values."""
    out = set()
    for n, _ in walk(node):
        kind, op = n.get("kind"), n.get("opcode")
        if kind in CONDITIONAL:
            subs = n["inner"][:1]
        elif op in ("&&", "||", "!"):
            subs = n["inner"]
        else:
            continue
        for sub in subs:
            while sub.get("kind") in WRAP or sub.get("opcode") == ",":
                sub = sub["inner"][-1]
            if sub.get("opcode") == "==":
                out.add(sub["id"])
    return out


def index_params(tree):
    """{function: parameter id} of instance functions: the parameter every call passes a literal,
    the literals being 0..m, m >= 1."""
    fns = dict(functions(tree))
    args = {}
    for n, _ in walk(tree):
        if n.get("kind") == "CallExpr" and (name := callee(n)) in fns:
            args.setdefault(name, []).append([literal(a) for a in n["inner"][1:]])
    out = {}
    for name, calls in args.items():
        params = [p for p in fns[name]["inner"] if p.get("kind") == "ParmVarDecl"]
        for i, p in enumerate(params):
            vals = [c[i] for c in calls if i < len(c) and c[i] is not None]
            if len(vals) == len(calls) and sorted(set(vals)) == list(
                range(max(vals) + 1)
            ) > [0]:
                out[name] = p["id"]
                break
    return out


def tainted(decl, seed):
    """Ids of the variables of a function whose values depend on declaration `seed`."""
    out, changed = {seed}, True
    while changed:
        changed = False
        for n, _ in walk(decl):
            if n.get("kind") == "VarDecl" and n.get("inner") and n["id"] not in out:
                if refs(n) & out:
                    out.add(n["id"])
                    changed = True
            elif n.get("kind") in (
                "BinaryOperator",
                "CompoundAssignOperator",
            ) and n.get("opcode", "").endswith("="):
                lhs, rhs = n["inner"][0], n["inner"][1]
                if lhs.get("kind") == "DeclRefExpr" and refs(rhs) & out:
                    target = lhs["referencedDecl"]["id"]
                    if target not in out:
                        out.add(target)
                        changed = True
    return out


def sites(tree):
    """(function, kind, node) of every costly construct of the tune functions in an AST."""
    index = index_params(tree)
    wide = {
        n["id"]
        for n, _ in walk(tree)
        if n.get("kind") in ("VarDecl", "ParmVarDecl")
        and n.get("type", {}).get("qualType") == "uint16_t"
    }
    for name, decl in functions(tree):
        k = index.get(name)
        dep = tainted(decl, k) if k else set()
        conds = condition_tests(decl) if k else set()
        if decl["type"]["qualType"].startswith("struct"):
            yield name, "K4", decl
        for n, parent in walk(decl):
            kind = n.get("kind")
            if kind == "CallExpr":
                called = callee(n)
                if k and called in ("rd", "wr") and refs(n["inner"][1]) & dep:
                    yield name, "K1", n
                if called in READS and parent and parent.get("kind") == "CompoundStmt":
                    yield name, "K6", n
            elif kind == "BinaryOperator" and k and refs(n) & dep:
                if n.get("opcode") == "*":
                    yield name, "K2", n
                if (
                    n.get("opcode") == "=="
                    and n["id"] not in conds
                    and any(literal(s) is not None for s in n["inner"])
                ):
                    yield name, "K3", n
            elif kind == "ArraySubscriptExpr":
                base = next(
                    (
                        r["referencedDecl"]["name"]
                        for r, _ in walk(n["inner"][0])
                        if r.get("kind") == "DeclRefExpr"
                    ),
                    "",
                )
                if REGION.match(base) and refs(n["inner"][1]) & wide:
                    yield name, "K5", n


def costs(tree):
    """{function: Counter of kinds} of the tune functions in an AST."""
    out = {name: Counter() for name, _ in functions(tree)}
    for name, kind, _ in sites(tree):
        out[name][kind] += 1
    return out


def offset(node):
    """Byte offset in the main file where a node begins (its expansion, inside a macro)."""
    begin = node.get("range", {}).get("begin", {})
    return begin.get("expansionLoc", begin).get("offset")


def totals(tree):
    """Counter of kinds over all tune functions."""
    return sum(costs(tree).values(), Counter())


def main(argv=None):
    """CLI entry point: print each function's costs and the totals."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", type=pathlib.Path)
    ap.add_argument("-I", dest="includes", action="append", default=[])
    ap.add_argument("--sites", action="store_true", help="list each construct's line")
    args = ap.parse_args(argv)
    tree = ast(args.source, args.includes)
    if args.sites:
        text = args.source.read_bytes()
        for name, kind, n in sites(tree):
            at = offset(n)
            line = text.count(b"\n", 0, at) + 1
            src = text[at:].split(b"\n", 1)[0].decode().strip()
            print(f"{line}: {kind} {name}: {src}")
    per = costs(tree)
    for name, c in sorted(per.items()):
        if c:
            print(name, " ".join(f"{k}={c[k]}" for k in KINDS if c[k]))
    t = sum(per.values(), Counter())
    print("total", " ".join(f"{k}={t[k]}" for k in KINDS))


if __name__ == "__main__":
    main()
