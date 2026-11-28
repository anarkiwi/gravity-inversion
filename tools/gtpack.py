"""GoatTracker 2 songs packed with the real 6502 playroutine, and the packer's layout.

The song data is laid out as pygoattracker's `songimage.song_image` lays it (greloc's order,
fixed table slots) and GoatTracker 2.76's `player.s` is assembled after it with the defines
given (docs/gensongs.md).
"""

import functools
import io
import zipfile
from dataclasses import dataclass, replace

from jennings.opcodes import OPCODES
from pygoattracker import gt2sources, songimage
from pygoattracker.gt2match import _Env
from pygoattracker.model import Pattern
from pygoattracker.packed import (
    WAVE_NONE,
    Build,
    _wave_packed,
    orderlist_bytes,
    pattern_bytes,
)
from pygoattracker.songimage import BASE, DEFINES, ZP, song_image
from pygoattracker.typed import TypedSong, packed_rows, raw_rows, rows_of, wave_bytes
from pysidtracker.header import write_psid
from pysidtracker.testing import fetch_verified

from constructs import ROOT

#: The release archive and member of the playroutine source.
SOURCE = ("gt2_276.zip", "src/player.s")
#: Where the playroutine is assembled.
CODE = 0x8000
#: The defines greloc gives that the song image does not.
GRELOC = dict.fromkeys(
    (
        "SOUNDSUPPORT",
        "VOLSUPPORT",
        "NOSETAD",
        "NOSETSR",
        "NOSETWAVE",
        "NOSETWAVEPTR",
        "NOSETPULSEPTR",
        "NOSETFILTPTR",
        "NOSETFILTCTRL",
        "NOSETFILTCUTOFF",
        "NOSETMASTERVOL",
        "FIRSTWAVEPARAM",
        "GATETIMERPARAM",
    ),
    0,
)
#: The instrument columns, the table columns and the pointer tables play reads.
INSTRUMENT = (
    "mt_insad",
    "mt_inssr",
    "mt_inswaveptr",
    "mt_inspulseptr",
    "mt_insfiltptr",
    "mt_insvibparam",
    "mt_insvibdelay",
    "mt_insgatetimer",
    "mt_insfirstwave",
)
TABLES = {
    "wavetable": ("mt_wavetbl", "mt_notetbl"),
    "pulsetable": ("mt_pulsetimetbl", "mt_pulsespdtbl"),
    "filtertable": ("mt_filttimetbl", "mt_filtspdtbl"),
    "speedtable": ("mt_speedlefttbl", "mt_speedrighttbl"),
}
POINTERS = (
    "mt_freqtbllo",
    "mt_freqtblhi",
    "mt_songtbllo",
    "mt_songtblhi",
    "mt_patttbllo",
    "mt_patttblhi",
)
DATA = (*INSTRUMENT, *(n for pair in TABLES.values() for n in pair), *POINTERS)
_FORMS = {"mem": ("zp", "abs"), "memx": ("zpx", "absx"), "memy": ("zpy", "absy")}
_ZP_FORMS = ("zp", "zpx", "zpy")
_ONE_BYTE = (*_ZP_FORMS, "imm", "rel", "indx", "indy")
_ENCODE = {v: op for op, v in sorted(OPCODES.items(), reverse=True)}
_PASSES = 8


class AssemblyError(ValueError):
    """The playroutine does not assemble with the symbols given."""


@functools.cache
def source():
    """The node tree (`pygoattracker.gt2sources.parse`) of the playroutine source, from the
    pinned release archive (`make fixture` fetches it into fixtures/; SHA-256 checked).
    """
    archive, member = SOURCE
    path, sha256 = gt2sources.ARCHIVES[archive]
    url = f"https://csdb.dk/getinternalfile.php/{path}"
    data = fetch_verified(url, sha256, cache_dir=ROOT / "fixtures", name=archive)
    with zipfile.ZipFile(io.BytesIO(data.read_bytes())) as z:
        return gt2sources.parse(z.read(member).decode("latin-1"))[0]


def _encode(mnem, mode, value, final):
    """(opcode, operand size) of one instruction, zero page where the operand fits."""
    for form in _FORMS.get(mode, (mode,)):
        op = _ENCODE.get((mnem, form))
        if op is None or (form in _ZP_FORMS and (value is None or value > 0xFF)):
            continue
        if form in ("impl", "acc"):
            return op, 0
        return op, 1 if form in _ONE_BYTE else 2
    if final or mode not in _FORMS:
        raise AssemblyError(f"no encoding of {mnem} {mode} {value}")
    return _ENCODE[(mnem, _FORMS[mode][1])], 2


def _operand(op, size, value, pc):
    """The operand bytes of `op` at `pc`."""
    if OPCODES[op][1] == "rel":
        offset = value - (pc + 2)
        if not -128 <= offset < 128:
            raise AssemblyError(f"branch at ${pc:04X} out of range")
        return bytes((offset & 0xFF,))
    return (value & (0xFF if size == 1 else 0xFFFF)).to_bytes(size, "little")


class _Pass:  # pylint: disable=too-few-public-methods
    """One pass over the tree: memory, labels defined, equates, instruction addresses."""

    def __init__(self, symbols, labels, equ, final):
        self.env = _Env(dict(symbols) | labels, dict(equ))
        self.env.passed = set(symbols)
        self.final, self.mem, self.pc, self.labels, self.ops = final, {}, 0, {}, set()

    def value(self, expr):
        """The value of `expr`, None while unknown before the final pass."""
        have = self.env.value(expr)
        if have is None and self.final:
            raise AssemblyError(f"unknown {self.env.unknowns(expr)} in {expr}")
        return have

    def emit(self, data):
        """Lay `data` at the program counter."""
        for byte in data:
            self.mem[self.pc] = byte
            self.pc += 1

    def node(self, node):
        """Assemble one node; the nodes of a taken conditional's branch, else ()."""
        kind = node[0]
        if kind == "?":
            taken = self.env.value(node[1])
            if taken is None:
                raise AssemblyError(f"undecided condition {node[1]}")
            return node[2] if taken else node[3]
        if kind == "L":
            self.labels[node[1]] = self.env.sym[node[1]] = self.pc
            self.env.passed.add(node[1])
        elif kind == "E":
            self.env.equ[node[1]] = node[2]
            self.env.sym.pop(node[1], None)
            self.env.passed.add(node[1])
        elif kind == "O":
            self.pc = self.value(node[1])
        elif kind == "B":
            self.emit((self.value(item) or 0) & 0xFF for item in node[1])
        elif kind == "F":
            self.emit(bytes(self.value(node[1])))
        else:
            mnem, mode, expr = node[1:]
            have = None if expr is None else self.value(expr)
            op, size = _encode(mnem, mode, have, self.final)
            arg = _operand(op, size, have, self.pc) if self.final and size else None
            self.ops.add(self.pc)
            self.emit(bytes((op,)) + (arg or bytes(size)))
        return ()

    def run(self, tree):
        """Assemble `tree`; self."""
        stack = [iter(tree)]
        while stack:
            node = next(stack[-1], None)
            if node is None:
                stack.pop()
            elif branch := self.node(node):
                stack.append(iter(branch))
        return self


def assemble(symbols):
    """(code, symbols, end) of the playroutine assembled at `symbols["base"]` with
    `symbols` (defines, `zpbase` and the data labels): labels and equates resolved."""
    symbols = GRELOC | dict(symbols)
    labels, equ = {}, {}
    for _ in range(_PASSES):
        p = _Pass(symbols, labels, equ, False).run(source())
        equ = p.env.equ
        if p.labels == labels:
            break
        labels = p.labels
    else:
        raise AssemblyError("labels do not settle")
    p = _Pass(symbols, labels, equ, True).run(source())
    start = symbols["base"]
    code = bytes(p.mem.get(a, 0) for a in range(start, p.pc))
    found = {k: p.env.value(v) for k, v in p.env.equ.items()}
    found = {k: v for k, v in found.items() if v is not None} | p.env.sym
    return code, found, p.pc


@dataclass(frozen=True)
class Packed:
    """A song packed with the playroutine: `image` loads at `BASE`; `symbols` the
    assembler's; `code` the playroutine's [start, end); `defines` those it was built with;
    `song` the TypedSong as laid out (instruments in greloc's class order)."""

    image: bytes
    init: int
    play: int
    symbols: dict
    code: tuple
    defines: dict
    song: TypedSong

    def psid(self, name=""):
        """The packed song as a PSID with one song per subtune."""
        return write_psid(
            load=BASE,
            init=self.init,
            play=self.play,
            image=self.image,
            name=name,
            songs=max(1, len(self.song.subtunes)),
        )

    def playroutine(self):
        """The playroutine's bytes."""
        lo, hi = self.code
        return self.image[lo - BASE : hi - BASE]


def pack(song, defines=None):
    """`song` (a TypedSong) packed with the playroutine built with `defines` over the
    full-featured build's (`NOEFFECTS` packing no commands, `NOWAVEDELAY` waveforms as
    greloc packs them), the image's bytes before its first table zeroed."""
    defines = dict(defines or {})
    img = song_image(song, Build(live_vibrato=bool(defines.get("NOEFFECTS"))))
    defines = DEFINES | GRELOC | img.found.symbols | defines
    names = [k for k in defines if k.isupper()]
    symbols = {k: defines[k] for k in (*names, *DATA)}
    if BASE + len(img.data) > CODE:
        raise ValueError(f"song data runs past the playroutine at ${CODE:04X}")
    code, found, end = assemble(symbols | {"base": CODE, "zpbase": ZP})
    data = bytearray(img.data)
    first = min(symbols[k] for k in DATA)
    data[: first - BASE] = bytes(first - BASE)
    typed = _ordered(song)
    if defines.get("NOWAVEDELAY"):
        at = symbols["mt_wavetbl"] - BASE
        for k, row in enumerate(typed.wavetable):
            data[at + k] = _wave_packed(*wave_bytes(row), WAVE_NONE)[0]
    image = bytes(data) + bytes(CODE - BASE - len(data)) + code
    return Packed(
        image,
        found["mt_init"],
        found["mt_play"],
        found,
        (CODE, end),
        {k: symbols[k] for k in names},
        typed,
    )


def _ordered(song):
    """`song` with its instruments in greloc's class order, as `song_image` lays it."""
    return songimage._ordered(song)[0]  # pylint: disable=protected-access


def _span(name, address, length, **more):
    return {"name": name, "address": address, "length": length, **more}


def layout(packed):
    """JSON-ready layout of `packed`: each table play reads with its address and length,
    the orderlists and patterns, the playroutine's code range and entries."""
    song, sym = packed.song, packed.symbols
    tables = [_span(n, sym[n], len(song.instruments)) for n in INSTRUMENT]
    for key, (left, right) in TABLES.items():
        n = len(getattr(song, key))
        tables += [_span(left, sym[left], n), _span(right, sym[right], n)]
    order = [o for s in song.subtunes for o in s.channels]
    for lo in ("mt_songtbl", "mt_patttbl"):
        n = len(order) if lo == "mt_songtbl" else len(song.patterns)
        tables += [_span(lo + half, sym[lo + half], n) for half in ("lo", "hi")]
    tables += [_span(n, sym[n], 128) for n in ("mt_freqtbllo", "mt_freqtblhi")]
    channels = len(song.subtunes[0].channels)
    lo, hi = packed.code
    fx = not packed.defines.get("NOEFFECTS")
    return {
        "load": BASE,
        "init": packed.init,
        "play": packed.play,
        "code": [lo, hi],
        "defines": packed.defines,
        "tables": tables,
        "orderlists": [
            _span(
                f"orderlist {k // channels}.{k % channels}", a, len(orderlist_bytes(o))
            )
            | {"subtune": k // channels, "channel": k % channels}
            for k, (a, o) in enumerate(zip(_pointers(packed, "mt_songtbl"), order))
        ],
        "patterns": [
            _span(f"pattern {k}", a, len(pattern_bytes(packed_pattern(p, fx))))
            for k, (a, p) in enumerate(
                zip(_pointers(packed, "mt_patttbl"), song.patterns)
            )
        ],
    }


def packed_pattern(rows, commands=True):
    """The packed rows of typed pattern `rows` as greloc packs them, without their
    commands unless `commands`."""
    rows = packed_rows(rows_of(Pattern(raw_rows(rows))))
    return rows if commands else tuple(replace(r, command=None) for r in rows)


def _pointers(packed, name):
    """The addresses the `name` lo/hi pointer table holds."""
    img, sym = packed.image, packed.symbols
    lo, hi = sym[name + "lo"] - BASE, sym[name + "hi"] - BASE
    return [img[lo + k] | img[hi + k] << 8 for k in range(0x100)]
