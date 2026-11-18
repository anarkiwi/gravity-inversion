"""Declarations of the types and operations Ghidra's decompiler prints in its C, so that C
parses: its sized types and, as ``const`` functions, its ``CONCAT``, ``SUB``, ``ZEXT``,
``SEXT``, ``CARRY``, ``SCARRY`` and ``SBORROW`` (docs/ghidra_ref.md)."""

SIZES = range(1, 9)
NAMED = {
    "byte": "uint8_t",
    "sbyte": "int8_t",
    "word": "uint16_t",
    "sword": "int16_t",
    "dword": "uint32_t",
    "sdword": "int32_t",
    "qword": "uint64_t",
    "sqword": "int64_t",
    "uchar": "unsigned char",
    "ushort": "unsigned short",
    "uint": "unsigned int",
    "ulong": "unsigned long",
    "longlong": "long long",
    "ulonglong": "unsigned long long",
    "undefined": "uint8_t",
    "float10": "long double",
}
CONST = "__attribute__((const))"
#: clang errors on conversions Ghidra prints between integers and pointers.
IGNORED = ("-Wint-conversion",)


def bits(n):
    """The width of the least exact-width integer type of at least ``n`` bytes."""
    return next(b for b in (8, 16, 32, 64) if b >= 8 * n)


def _operations():
    for a in SIZES:
        u, s = f"uint{bits(a)}_t", f"int{bits(a)}_t"
        for b in SIZES:
            ub = f"uint{bits(b)}_t"
            if a + b <= 8:
                yield f"{CONST} uint{bits(a + b)}_t CONCAT{a}{b}({u}, {ub});"
            if a > b:
                yield f"{CONST} {ub} SUB{a}{b}({u}, int);"
            if a < b:
                yield f"{CONST} {ub} ZEXT{a}{b}({u});"
                yield f"{CONST} int{bits(b)}_t SEXT{a}{b}({s});"
    for n in (1, 2, 4, 8):
        u, s = f"uint{8 * n}_t", f"int{8 * n}_t"
        yield f"{CONST} bool CARRY{n}({u}, {u});"
        yield f"{CONST} bool SCARRY{n}({s}, {s});"
        yield f"{CONST} bool SBORROW{n}({s}, {s});"


def header():
    """The text of the header."""
    lines = ["#pragma once", "#include <stdbool.h>", "#include <stdint.h>"]
    lines += [f'#pragma clang diagnostic ignored "{w}"' for w in IGNORED]
    lines += [f"typedef {t} {name};" for name, t in NAMED.items()]
    for n in SIZES:
        b = bits(n)
        lines.append(f"typedef uint{b}_t undefined{n};")
        if n not in (1, 2, 4, 8):
            lines += [f"typedef uint{b}_t uint{n};", f"typedef int{b}_t int{n};"]
    lines.append("typedef void code();")
    lines += _operations()
    lines += [
        "__attribute__((noreturn)) void halt_baddata(void);",
        "__attribute__((noreturn)) void halt_unimplemented(void);",
    ]
    return "\n".join(lines) + "\n"
