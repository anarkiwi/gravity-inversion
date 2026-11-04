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


def _walk(node, parent=None):
    yield node, parent
    for child in node.get("inner", ()):
        yield from _walk(child, node)


def _refs(node):
    """Ids of the declarations a subtree refers to."""
    return {
        n["referencedDecl"]["id"]
        for n, _ in _walk(node)
        if n.get("kind") == "DeclRefExpr"
    }


def _callee(call):
    for n, _ in _walk(call["inner"][0]):
        if n.get("kind") == "DeclRefExpr":
            return n["referencedDecl"]["name"]
    return None


def _literal(node):
    for n, _ in _walk(node):
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
        and any(c.get("kind") == "CompoundStmt" for c in d["inner"])
    ]


def condition_tests(node):
    """Ids of the `==` comparisons used only as conditions (a branch, `&&`, `||`, `!`, through
    parentheses and casts), not as values."""
    out = set()
    for n, _ in _walk(node):
        kind, op = n.get("kind"), n.get("opcode")
        if kind in CONDITIONAL:
            subs = n["inner"][:1]
        elif op in ("&&", "||", "!"):
            subs = n["inner"]
        else:
            continue
        for sub in subs:
            while sub.get("kind") in WRAP:
                sub = sub["inner"][0]
            if sub.get("opcode") == "==":
                out.add(sub["id"])
    return out


def index_params(tree):
    """{function: parameter id} of instance functions: the parameter every call passes a literal,
    the literals being 0..m, m >= 1."""
    fns = dict(functions(tree))
    args = {}
    for n, _ in _walk(tree):
        if n.get("kind") == "CallExpr" and (name := _callee(n)) in fns:
            args.setdefault(name, []).append([_literal(a) for a in n["inner"][1:]])
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
        for n, _ in _walk(decl):
            if n.get("kind") == "VarDecl" and n.get("inner") and n["id"] not in out:
                if _refs(n) & out:
                    out.add(n["id"])
                    changed = True
            elif n.get("kind") in (
                "BinaryOperator",
                "CompoundAssignOperator",
            ) and n.get("opcode", "").endswith("="):
                lhs, rhs = n["inner"][0], n["inner"][1]
                if lhs.get("kind") == "DeclRefExpr" and _refs(rhs) & out:
                    target = lhs["referencedDecl"]["id"]
                    if target not in out:
                        out.add(target)
                        changed = True
    return out


def costs(tree):
    """{function: Counter of kinds} of the tune functions in an AST."""
    index = index_params(tree)
    wide = {
        n["id"]
        for n, _ in _walk(tree)
        if n.get("kind") in ("VarDecl", "ParmVarDecl")
        and n.get("type", {}).get("qualType") == "uint16_t"
    }
    out = {}
    for name, decl in functions(tree):
        k, c = index.get(name), Counter()
        dep = tainted(decl, k) if k else set()
        conds = condition_tests(decl) if k else set()
        if decl["type"]["qualType"].startswith("struct"):
            c["K4"] += 1
        for n, parent in _walk(decl):
            kind = n.get("kind")
            if kind == "CallExpr":
                callee = _callee(n)
                if k and callee in ("rd", "wr") and _refs(n["inner"][1]) & dep:
                    c["K1"] += 1
                if callee in READS and parent and parent.get("kind") == "CompoundStmt":
                    c["K6"] += 1
            elif kind == "BinaryOperator" and k and _refs(n) & dep:
                c["K2"] += n.get("opcode") == "*"
                if (
                    n.get("opcode") == "=="
                    and n["id"] not in conds
                    and any(_literal(s) is not None for s in n["inner"])
                ):
                    c["K3"] += 1
            elif kind == "ArraySubscriptExpr":
                base = next(
                    (
                        r["referencedDecl"]["name"]
                        for r, _ in _walk(n["inner"][0])
                        if r.get("kind") == "DeclRefExpr"
                    ),
                    "",
                )
                if REGION.match(base) and _refs(n["inner"][1]) & wide:
                    c["K5"] += 1
        out[name] = c
    return out


def totals(tree):
    """Counter of kinds over all tune functions."""
    return sum(costs(tree).values(), Counter())


def main(argv=None):
    """CLI entry point: print each function's costs and the totals."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", type=pathlib.Path)
    ap.add_argument("-I", dest="includes", action="append", default=[])
    args = ap.parse_args(argv)
    per = costs(ast(args.source, args.includes))
    for name, c in sorted(per.items()):
        if c:
            print(name, " ".join(f"{k}={c[k]}" for k in KINDS if c[k]))
    t = sum(per.values(), Counter())
    print("total", " ".join(f"{k}={t[k]}" for k in KINDS))


if __name__ == "__main__":
    main()
