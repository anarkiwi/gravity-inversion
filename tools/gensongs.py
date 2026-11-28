"""Seeded GoatTracker 2 songs packed with the real playroutine, as PSIDs with their layout.

gensongs.py generate|check-a|check-b|certify|summary|smoke DIR [options]: generate songs
into DIR, run check A, check B or the certify timing on them, print the summary, or all of
it on a few tiny songs (docs/gensongs.md).
"""

import argparse
import hashlib
import json
import math
import os
import pathlib
from dataclasses import dataclass, replace

import numpy as np
from pygoattracker import typed as T
from pygoattracker.model import Orderlist, PlayPattern, Repeat, Subtune, Transpose
from tqdm import tqdm

import gtpack
import songchecks

#: {feature: (the defines it clears, the features it needs)}, in sweep order.
FEATURES = {
    "gate": (("NOGATE",), ()),
    "transpose": (("NOTRANS",), ()),
    "repeat": (("NOREPEAT",), ()),
    "nohr": ((), ()),
    "legato": ((), ()),
    "firstwavecmd": (("NOFIRSTWAVECMD",), ()),
    "wavedelay": (("NOWAVEDELAY",), ()),
    "wavecmd": (("NOWAVECMD",), ()),
    "pulse": (("NOPULSE",), ()),
    "pulsemod": (("NOPULSEMOD",), ("pulse",)),
    "filter": (("NOFILTER",), ()),
    "filtermod": (("NOFILTERMOD",), ("filter",)),
    "instrvib": (("NOINSTRVIB", "NOVIB"), ()),
    "portamento": (("NOPORTAMENTO",), ()),
    "calcspeed": (("NOCALCULATEDSPEED",), ("portamento",)),
    "toneporta": (("NOTONEPORTA",), ()),
    "zerospeed": (("NOZEROSPEED",), ("toneporta",)),
    "vibrato": (("NOVIB",), ()),
    "setad": (("NOSETAD",), ()),
    "setsr": (("NOSETSR",), ()),
    "setwave": (("NOSETWAVE",), ()),
    "setwaveptr": (("NOSETWAVEPTR",), ()),
    "setpulseptr": (("NOSETPULSEPTR", "NOPULSE"), ("pulse",)),
    "setfiltptr": (("NOSETFILTPTR", "NOFILTER"), ("filter",)),
    "setfiltctrl": (("NOSETFILTCTRL", "NOFILTER"), ("filter",)),
    "setcutoff": (("NOSETFILTCUTOFF", "NOFILTER"), ("filter",)),
    "setvolume": (("NOSETMASTERVOL",), ()),
    "timingmark": (("NOSETMASTERVOL",), ()),
    "tempo": (("NOGLOBALTEMPO",), ()),
    "chantempo": (("NOCHANNELTEMPO",), ()),
    "funktempo": (("NOFUNKTEMPO", "NOGLOBALTEMPO"), ()),
    "multisong": ((), ()),
}
#: The defines a song's features clear, and NONORMALSPEED its speed rows decide.
SWITCHES = sorted({d for ds, _ in FEATURES.values() for d in ds} | {"NONORMALSPEED"})
#: The command feature of each command class.
COMMANDS = {
    T.PortamentoUp: "portamento",
    T.PortamentoDown: "portamento",
    T.TonePortamento: "toneporta",
    T.Vibrato: "vibrato",
    T.SetAD: "setad",
    T.SetSR: "setsr",
    T.SetWave: "setwave",
    T.SetWavePtr: "setwaveptr",
    T.SetPulsePtr: "setpulseptr",
    T.SetFilterPtr: "setfiltptr",
    T.SetFilterCtrl: "setfiltctrl",
    T.SetCutoff: "setcutoff",
    T.SetMasterVolume: "setvolume",
    T.TimingMark: "timingmark",
    T.FunkTempo: "funktempo",
}
#: The command features a wavetable row may run.
WAVE_COMMANDS = ("portamento", "vibrato", "setad", "setsr", "setwave", "setcutoff")
#: Per size class: the ranges of patterns, pattern rows, orderlist entries and table rows.
SIZES = {
    "tiny": {"patterns": (1, 1), "rows": (1, 4), "orders": (1, 1), "table": (1, 2)},
    "small": {"patterns": (1, 3), "rows": (4, 12), "orders": (1, 3), "table": (1, 3)},
    "medium": {"patterns": (2, 6), "rows": (8, 32), "orders": (2, 6), "table": (2, 4)},
    "large": {
        "patterns": (4, 12),
        "rows": (16, 64),
        "orders": (4, 16),
        "table": (2, 6),
    },
}
WAVES = (0x41, 0x21, 0x11, 0x81, 0x51)
#: The instrument classes: hard restart, none, legato.
HR, NOHR, LEGATO = "hr", "nohr", "legato"


def closure(features):
    """`features` with the features each needs, in sweep order."""
    out, todo = set(), list(features)
    while todo:
        f = todo.pop()
        if f not in FEATURES:
            raise ValueError(f"unknown feature {f}")
        if f not in out:
            out.add(f)
            todo += FEATURES[f][1]
    return tuple(f for f in FEATURES if f in out)


def sweep():
    """The feature sets of the sweep: none, then one feature more at a time."""
    names = list(FEATURES)
    return [closure(names[:k]) for k in range(len(names) + 1)]


class _Gen:  # pylint: disable=too-many-instance-attributes
    """One song's tables, instruments and patterns from `rng`, using exactly `features`;
    `full` keeps an instrument of every class."""

    def __init__(self, rng, size, features, full):
        self.rng, self.size, self.tiny = rng, SIZES[size], size == "tiny"
        self.features = frozenset(features)
        self.wave, self.pulse, self.filter, self.speed = [], [], [], []
        self.kinds = {}
        classes = [HR, HR]
        classes += [NOHR] if full or NOHR in self.features else []
        classes += [LEGATO] if full or LEGATO in self.features else []
        self.instruments = [self.instrument(k, c) for k, c in enumerate(classes)]
        self.playable = [
            k + 1 for k, c in enumerate(classes) if c == HR or c in self.features
        ]

    def has(self, feature):
        """Whether the song uses `feature`."""
        return feature in self.features

    def _int(self, lo, hi):
        """A uniform integer in [lo, hi]."""
        return int(self.rng.integers(lo, hi + 1))

    def _pick(self, seq):
        """A uniform element of `seq`."""
        return seq[int(self.rng.integers(len(seq)))]

    def _span(self, key):
        """A uniform count in the size class's `key` range."""
        return self._int(*self.size[key])

    def loop(self, table, rows, start, rest=1):
        """Append `rows` and an end to `table`: a jump to one of the program's first `rest`
        rows or, outside tiny songs, sometimes a stop; the program's 1-based start."""
        table += rows
        if not self.tiny and self.rng.random() < 0.3:
            table.append(T.Stop())
        else:
            table.append(T.Jump(start + self._int(0, max(0, rest - 1))))
        return start

    def wave_program(self, first):
        """A wavetable program; the `first` one carries every wave feature."""
        start = len(self.wave) + 1
        rows = [
            T.WaveStep(
                T.SetWave(self._pick(WAVES)), T.WaveNote(self._pick((0, 12, 7)), True)
            )
            for _ in range(self._span("table"))
        ]
        if self.has("wavedelay") and (first or self.rng.random() < 0.5):
            rows.insert(1, T.WaveStep(T.Delay(self._int(1, 4)), None))
        if self.has("wavecmd") and (first or self.rng.random() < 0.5):
            rows.insert(1, T.WaveCommand(self.wave_command()))
        if self.rng.random() < 0.3:
            rows.append(T.WaveStep(None, T.WaveNote(self._int(24, 71), False)))
        return self.loop(self.wave, rows, start, len(rows))

    def wave_command(self):
        """A command a wavetable row runs: one of the song's wave command features."""
        names = [f for f in WAVE_COMMANDS if self.has(f)]
        return self.command(self._pick(names)) if names else T.DoNothing(0)

    def pulse_program(self):
        """A pulsetable program: a width, then modulation where the song has pulsemod."""
        start = len(self.pulse) + 1
        rows = [T.PulseSet(self._int(0x100, 0xE00))]
        if self.has("pulsemod"):
            hi = 8 if self.tiny else 0x7F
            rows += [
                T.PulseMod(self._int(1, hi), self._pick((-1, 1)) * self._int(1, 0x40))
                for _ in range(self._span("table"))
            ]
        return self.loop(self.pulse, rows, start, len(rows))

    def filter_program(self):
        """A filtertable program: mode, cutoff, then modulation where it has filtermod."""
        start = len(self.filter) + 1
        rows = [
            T.FilterSet(
                self._pick((0x10, 0x20, 0x40)), self._int(0, 15), self._int(1, 7)
            ),
            T.CutoffSet(self._int(0, 0xFF)),
        ]
        if self.has("filtermod"):
            hi = 8 if self.tiny else 0x7F
            rows += [
                T.FilterMod(self._int(1, hi), self._pick((-1, 1)) * self._int(1, 0x20))
                for _ in range(self._span("table"))
            ]
        return self.loop(self.filter, rows, start, 1 if self.tiny else len(rows))

    def speed_row(self, kind):
        """The 1-based speedtable row of a `kind` row (slide, vibrato, funk or calc): one
        made before half the time, or once the table holds 64 rows."""
        made = self.kinds.setdefault(kind, [])
        if made and (len(self.speed) >= 64 or self.rng.random() < 0.5):
            return self._pick(made)
        if kind == "calc":
            row = T.CalculatedSpeed(self._int(0, 3), self._int(0, 4))
        elif kind == "vibrato":
            row = T.FixedSpeed(self._int(1, 8) << 8 | self._int(1, 0x40))
        elif kind == "funk":
            row = T.FixedSpeed(self._int(3, 8) << 8 | self._int(3, 8))
        else:
            row = T.FixedSpeed(self._int(1, 0x200))
        self.speed.append(row)
        made.append(len(self.speed))
        return len(self.speed)

    def slide(self, calc=False):
        """A speed row a slide names: calculated with `calc`, or at random with calcspeed."""
        calc = calc or (self.has("calcspeed") and self.rng.random() < 0.4)
        return self.speed_row("calc" if calc else "slide")

    def instrument(self, k, cls):
        """Instrument `k` of class `cls`; the first carries every instrument feature."""
        first = k == 0
        pulse = self.has("pulse") and (first or self.rng.random() < 0.5)
        filt = self.has("filter") and (first or self.rng.random() < 0.3)
        vib = self.has("instrvib") and (first or self.rng.random() < 0.5)
        firstwave = T.SetWave(0x09)
        if self.has("firstwavecmd") and (first or self.rng.random() < 0.3):
            firstwave = self._pick((None, T.GateOff(), T.GateOn()))
        return T.Instrument(
            attack_decay=self._int(0, 0xFF),
            sustain_release=self._int(0, 0xFF),
            wave_ptr=self.wave_program(first),
            pulse_ptr=self.pulse_program() if pulse else 0,
            filter_ptr=self.filter_program() if filt else 0,
            vibrato_speed=self.speed_row("vibrato") if vib else 0,
            vibrato_delay=self._int(1, 8) if vib else 0,
            gate_timer=self._int(1, 2),
            hard_restart=cls == HR,
            legato=cls == LEGATO,
            first_wave=firstwave,
        )

    def command(self, name):
        """A command of feature `name` with random parameters."""
        if name == "portamento":
            return self._pick((T.PortamentoUp, T.PortamentoDown))(self.slide())
        if name == "toneporta":
            zero = self.has("zerospeed") and self.rng.random() < 0.3
            return T.TonePortamento(0 if zero else self.slide())
        if name in ("tempo", "chantempo"):
            return T.SetTempo(self._int(3, 8), name == "chantempo")
        made = {
            "vibrato": lambda: T.Vibrato(self.speed_row("vibrato")),
            "funktempo": lambda: T.FunkTempo(self.speed_row("funk")),
            "setad": lambda: T.SetAD(self._int(0, 0xFF)),
            "setsr": lambda: T.SetSR(self._int(0, 0xFF)),
            "setwave": lambda: T.SetWave(self._pick(WAVES)),
            "setwaveptr": lambda: T.SetWavePtr(self._pick(_starts(self.wave))),
            "setpulseptr": lambda: T.SetPulsePtr(self._pick(_starts(self.pulse))),
            "setfiltptr": lambda: T.SetFilterPtr(self._pick(_starts(self.filter))),
            "setfiltctrl": lambda: T.SetFilterCtrl(self._int(0, 15), self._int(0, 7)),
            "setcutoff": lambda: T.SetCutoff(self._int(0, 0xFF)),
            "setvolume": lambda: T.SetMasterVolume(self._int(8, 15)),
            "timingmark": lambda: T.TimingMark(self._int(0x10, 0xFF)),
        }
        return made[name]()

    def commands(self):
        """The command features of the song."""
        names = dict.fromkeys((*COMMANDS.values(), "tempo", "chantempo"))
        return [f for f in names if self.has(f)]

    def required(self):
        """The events every song of these features carries at least once."""
        out = [("cmd", f) for f in self.commands()]
        out += [("calc", "portamento")] if self.has("calcspeed") else []
        out += [("zero", "toneporta")] if self.has("zerospeed") else []
        out += [("gate", f) for f in ("keyoff", "keyon") if self.has("gate")]
        out += [("instrument", k) for k in self.playable]
        return out

    def note_row(self, instrument=None):
        """A row keying a note with `instrument` or a playable one."""
        return T.Row(T.Note(self._int(24, 71)), instrument or self._pick(self.playable))

    def event(self, kind, what):
        """The row carrying one required event."""
        if kind == "gate":
            return T.Row(T.KeyOff() if what == "keyoff" else T.KeyOn())
        if kind == "instrument":
            return self.note_row(what)
        if kind == "calc":
            return replace(self.note_row(), command=T.PortamentoUp(self.slide(True)))
        if kind == "zero":
            return replace(self.note_row(), command=T.TonePortamento(0))
        row = self.note_row() if what == "toneporta" else T.Row()
        return replace(row, command=self.command(what))

    def random_row(self):
        """A row of random content within the song's features."""
        r = self.rng.random()
        if r < 0.45:
            row = self.note_row()
        elif self.has("gate") and r < 0.55:
            row = T.Row(self._pick((T.KeyOff(), T.KeyOn())))
        else:
            row = T.Row(length=1 if self.tiny else self._int(1, 4))
        names = self.commands()
        if names and self.rng.random() < 0.25:
            row = replace(row, command=self.command(self._pick(names)))
        return row

    def patterns(self):
        """The patterns: the required events spread over them, the rest random."""
        events = self.required()
        n = self._span("patterns") * (1 if self.tiny else 3)
        rows = max(self._span("rows"), math.ceil(len(events) / n))
        out = [[self.random_row() for _ in range(rows)] for _ in range(n)]
        for k, (kind, what) in enumerate(events):
            out[k % n][k // n] = self.event(kind, what)
        return tuple(tuple(p) for p in out)

    def orderlist(self, patterns, shape, voice):
        """One voice's orderlist over `patterns` patterns: in a tiny song the one pattern
        after `shape` (transpose, repeat), alike on every voice; else every third pattern
        from `voice` (None: none) once, then random ones."""
        if self.tiny:
            entries = [] if shape[0] is None else [Transpose(shape[0])]
            entries += [] if shape[1] is None else [Repeat(shape[1])]
            return Orderlist([*entries, PlayPattern(0)], 0)
        mine = [] if voice is None else list(range(voice, patterns, 3))
        entries = []
        for k in range(max(self._span("orders"), len(mine))):
            if self.has("transpose") and (k == 0 or self.rng.random() < 0.3):
                entries.append(Transpose(self._int(-12, 12)))
            if self.has("repeat") and (k == 0 or self.rng.random() < 0.3):
                entries.append(Repeat(self._int(0, 3)))
            at = mine[k] if k < len(mine) else self._int(0, patterns - 1)
            entries.append(PlayPattern(at))
        return Orderlist(entries, self._int(0, len(entries) - 1))

    def song(self):
        """The TypedSong."""
        patterns = self.patterns()
        subtunes = []
        for sub in range(self._int(2, 3) if self.has("multisong") else 1):
            shape = (
                self._int(-12, 12) if self.has("transpose") else None,
                self._int(0, 3) if self.has("repeat") else None,
            )
            voices = [
                self.orderlist(len(patterns), shape, None if sub else c)
                for c in range(3)
            ]
            subtunes.append(Subtune(voices))
        song = T.TypedSong(
            subtunes=tuple(subtunes),
            patterns=patterns,
            instruments=tuple(self.instruments),
            wavetable=tuple(self.wave),
            pulsetable=tuple(self.pulse),
            filtertable=tuple(self.filter),
            speedtable=tuple(self.speed),
        )
        song.check()
        return song


def _starts(table):
    """The 1-based rows of `table` that start a program."""
    ends = [k + 1 for k, r in enumerate(table) if isinstance(r, (T.Jump, T.Stop))]
    return [1, *(e + 1 for e in ends[:-1])]


def _commands(song):
    """Every command the song's patterns and wavetable run."""
    out = [r.command for p in song.patterns for r in p if r.command is not None]
    return out + [r.command for r in song.wavetable if isinstance(r, T.WaveCommand)]


def _speeds(song):
    """The speed rows (0 for none) the song's slides, vibratos and instruments name."""
    slides = (T.PortamentoUp, T.PortamentoDown, T.TonePortamento, T.Vibrato)
    refs = [c.speed for c in _commands(song) if isinstance(c, slides)]
    return refs + [i.vibrato_speed for i in song.instruments if i.vibrato_delay]


def _instrument_uses(song):
    """The features the instruments `song` plays use (the first is always played)."""
    out = set()
    played = {1} | {r.instrument for p in song.patterns for r in p if r.instrument}
    for i in (song.instrument(k) for k in sorted(played)):
        out |= {"legato"} if i.legato else {"nohr"} if not i.hard_restart else set()
        out |= set() if isinstance(i.first_wave, T.SetWave) else {"firstwavecmd"}
        out |= {"pulse"} if i.pulse_ptr else set()
        out |= {"filter"} if i.filter_ptr else set()
        out |= {"instrvib"} if i.vibrato_delay else set()
    return out


def _table_uses(song):
    """The features `song`'s wave, pulse, filter and speed tables use."""
    rows = {
        type(r) for t in (song.wavetable, song.pulsetable, song.filtertable) for r in t
    }
    rows |= {type(r.wave) for r in song.wavetable if isinstance(r, T.WaveStep)}
    kinds = {
        T.WaveCommand: "wavecmd",
        T.Delay: "wavedelay",
        T.PulseMod: "pulsemod",
        T.FilterMod: "filtermod",
    }
    out = {f for k, f in kinds.items() if k in rows}
    for ref in _speeds(song):
        if ref == 0:
            out.add("zerospeed")
        elif isinstance(song.speedtable[ref - 1], T.CalculatedSpeed):
            out.add("calcspeed")
    return out


def uses(song):
    """The features `song` (a TypedSong) uses, read off its data as greloc reads it."""
    out = {COMMANDS[type(c)] for c in _commands(song) if type(c) in COMMANDS}
    out |= {
        "chantempo" if c.channel else "tempo"
        for c in _commands(song)
        if isinstance(c, T.SetTempo)
    }
    notes = {type(r.note) for p in song.patterns for r in p}
    out |= {"gate"} if notes & {T.KeyOff, T.KeyOn} else set()
    entries = {type(e) for s in song.subtunes for o in s.channels for e in o.entries}
    out |= {"transpose"} if Transpose in entries else set()
    out |= {"repeat"} if Repeat in entries else set()
    out |= {"multisong"} if len(song.subtunes) > 1 else set()
    return closure(out | _instrument_uses(song) | _table_uses(song))


def tailored(song):
    """The defines greloc builds `song`'s playroutine with, each feature it does not use
    removed, but for NOEFFECTS, left 0 (docs/gensongs.md)."""
    out = dict.fromkeys(SWITCHES, 1)
    for f in uses(song):
        out.update(dict.fromkeys(FEATURES[f][0], 0))
    table = song.speedtable
    normal = any(r and isinstance(table[r - 1], T.FixedSpeed) for r in _speeds(song))
    out["NONORMALSPEED"] = int(not normal)
    out["NUMSONGS"] = len(song.subtunes)
    return out


#: The full-featured build: every feature's code, whatever a song uses.
FULL = dict.fromkeys(SWITCHES, 0) | {"NUMSONGS": 3}


@dataclass(frozen=True)
class Spec:
    """One generated song: its `size` class, the `features` it uses, its `build` (tailored
    or full), and the `seed` and `index` its random stream is drawn from."""

    size: str
    features: tuple
    build: str
    seed: int
    index: int

    @property
    def name(self):
        """The song's file stem."""
        return f"{self.size}-{self.build}-{self.seed}-{self.index:04d}"

    def song(self):
        """The TypedSong."""
        rng = np.random.default_rng([self.seed, self.index])
        return _Gen(rng, self.size, self.features, self.build == "full").song()

    def pack(self):
        """(TypedSong, gtpack.Packed)."""
        song = self.song()
        defines = FULL if self.build == "full" else tailored(song)
        return song, gtpack.pack(song, defines)


def specs(seed, count, size, mode, *, features=None, build="tailored"):
    """The songs of one run: `count` of `features` (fixed; default all), or `count` per
    sweep step."""
    sets = (
        sweep()
        if mode == "sweep"
        else [closure(FEATURES if features is None else features)]
    )
    return [
        Spec(size, fs, build, seed, k * count + j)
        for k, fs in enumerate(sets)
        for j in range(count)
    ]


def write(spec, out):
    """Write `spec`'s PSID and layout to directory `out`; its manifest record."""
    song, packed = spec.pack()
    (out / f"{spec.name}.sid").write_bytes(packed.psid(spec.name))
    lay = gtpack.layout(packed)
    (out / f"{spec.name}.json").write_text(json.dumps(lay, indent=1), "utf-8")
    return {
        "name": spec.name,
        "size": spec.size,
        "build": spec.build,
        "seed": spec.seed,
        "index": spec.index,
        "features": list(spec.features),
        "uses": list(uses(song)),
        "playroutine": hashlib.sha256(packed.playroutine()).hexdigest(),
    }


def generate(out, songs, mode):
    """Write `songs` (Specs) of `mode` to directory `out` with `manifest.json`; the
    manifest."""
    out.mkdir(parents=True, exist_ok=True)
    records = [write(s, out) for s in tqdm(songs, desc="generate", unit="song")]
    players = sorted({r["playroutine"] for r in records})
    made = {"mode": mode, "songs": records, "playroutines": players}
    if mode == "fixed" and len(players) != 1:
        raise AssertionError(f"fixed features made {len(players)} playroutines")
    (out / "manifest.json").write_text(json.dumps(made, indent=1), "utf-8")
    return made


def manifest(directory):
    """The manifest of the songs in `directory`."""
    return json.loads((directory / "manifest.json").read_text("utf-8"))


def _spec(record):
    """The Spec of manifest `record`."""
    keys = ("size", "features", "build", "seed", "index")
    return Spec(*(tuple(record[k]) if k == "features" else record[k] for k in keys))


def run_a(directory, jobs):
    """Check A over the songs in `directory`; findings to `directory/findings`."""
    songs, before = manifest(directory)["songs"], songchecks.load()
    results = songchecks.run(songchecks.check_a, songs, directory, jobs)
    seeds = {s["name"]: s for s in songs}
    for name, recs in results.items():
        for r in recs:
            if r.get("equal") is False:
                found = {"song": seeds[name], "state": r["state"], "diff": r["diff"]}
                path = directory / "findings" / f"{name}.{r['state']}.json"
                songchecks.write_json(path, found)
    found = songchecks.summary_a(results, songs)
    return {
        "summary": found,
        "timing": songchecks.timing(results, songs),
        "load": [before, songchecks.load()],
        "songs": results,
    }


def run_b(directory, jobs):
    """Check B over the songs in `directory`, per playroutine they share."""
    songs, before, out = manifest(directory)["songs"], songchecks.load(), []
    for player in dict.fromkeys(s["playroutine"] for s in songs):
        group = [s for s in songs if s["playroutine"] == player]
        packed = _spec(group[0]).pack()[1]
        out.append(songchecks.check_b(directory, group, jobs, packed))
    return {"variations": out, "load": [before, songchecks.load()]}


def run_certify(directories, jobs):
    """The certify timing over the songs in `directories`."""
    before, results, songs = songchecks.load(), {}, []
    for d in directories:
        mine = manifest(d)["songs"]
        results |= songchecks.run(songchecks.certify_only, mine, d, jobs)
        songs += mine
    table = songchecks.timing(results, songs)
    return {"timing": table, "load": [before, songchecks.load()], "songs": results}


def _print_a(res):
    s = res["summary"]
    print(
        f"check A: {s['songs']} songs, {s['states']} states, {s['certified']} certified, "
        f"{s['equal']} equal, {len(s['unequal'])} unequal; forms {s['forms']}; "
        f"orbits {s['orbits']}; load {res['load']}"
    )
    for u in s["unequal"]:
        print(f"  UNEQUAL {u['song']} seed {u['seed']}")
    for u in s["not_certified"]:
        print(
            f"  not certified within {u['limit'][0]} calls / {u['limit'][1]} s: "
            f"{u['song']} state {u['state']} seed {u['seed']} ({u['form']})"
        )
    _print_certify(res)


def _print_b(res):
    for v in res["variations"]:
        n = v["instructions"]
        print(
            f"check B from {v['plain']} over {v['songs']} songs ({v['closed']} closed): "
            f"certified {n['certified']}, forced {n['forced']}, walked {n['walked']}, "
            f"union of songs {n['union']}; load {res['load']}"
        )
        for key in ("tools_only", "songs_only"):
            shown = " ".join(f"${a:04X}({name})" for a, name in v[key])
            print(f"  {key} {len(v[key])}: {shown}")


def _print_certify(res):
    print(f"certify, load {res['load']}")
    print("size states forms calls(median,max) seconds(median,max) limit(calls,cpu s)")
    for cls, t in res["timing"].items():
        print(
            f"{cls:7} {t['states']:6}  {t['forms']}  {t['calls']}  {t['seconds']}  "
            f"{t['limit']}; {len(t['not_certified'])} not certified"
        )


CHECKS = {
    "check-a": ("check_a.json", run_a, _print_a),
    "check-b": ("check_b.json", run_b, _print_b),
}


def summary(directory):
    """Print the results saved in `directory`."""
    for name, printer in (
        ("check_a.json", _print_a),
        ("check_b.json", _print_b),
        ("certify.json", _print_certify),
    ):
        path = directory / name
        if path.exists():
            res = json.loads(path.read_text("utf-8"))
            songs = manifest(directory)["songs"]
            if name != "check_b.json":
                res["timing"] = songchecks.timing(res["songs"], songs)
            printer(res)


def smoke(out, jobs):
    """Generate a few tiny songs of fixed features and a tiny full-build sweep into `out`
    and run checks A and B on them."""
    fixed, full = out / "fixed", out / "sweep"
    generate(
        fixed,
        specs(0, 2, "tiny", "fixed", features=("gate", "pulsemod", "vibrato")),
        "fixed",
    )
    generate(full, specs(0, 1, "tiny", "sweep", build="full"), "sweep")
    res = {"check_a": run_a(fixed, jobs), "check_b": run_b(full, jobs)}
    songchecks.write_json(fixed / "check_a.json", res["check_a"])
    songchecks.write_json(full / "check_b.json", res["check_b"])
    return res


def _parser():
    ap = argparse.ArgumentParser(description=__doc__.split("\n", maxsplit=1)[0])
    sub = ap.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate", help="write songs, layouts and a manifest")
    gen.add_argument("dir", type=pathlib.Path)
    gen.add_argument("--seed", type=int, default=0)
    gen.add_argument("--count", type=int, default=1, help="songs, per step in a sweep")
    gen.add_argument("--size", choices=SIZES, nargs="+", default=["tiny"])
    gen.add_argument("--mode", choices=("fixed", "sweep"), default="fixed")
    gen.add_argument("--features", help="comma-separated, fixed mode; default all")
    gen.add_argument("--build", choices=("tailored", "full"), default="tailored")
    for name, helps in (
        ("check-a", "certificates against the plain orbit"),
        ("check-b", "forcing and walking back against the union of songs"),
        ("certify", "certify calls and seconds by size class"),
        ("smoke", "a few tiny songs through checks A and B"),
    ):
        p = sub.add_parser(name, help=helps)
        p.add_argument(
            "dir", type=pathlib.Path, nargs="+" if name == "certify" else None
        )
        p.add_argument("--jobs", type=int, default=len(os.sched_getaffinity(0)))
    sub.add_parser("summary", help="print the saved results").add_argument(
        "dir", type=pathlib.Path
    )
    for p in sub.choices.values():
        p.add_argument("--json", type=pathlib.Path, help="also write the result here")
    return ap


def main(argv=None):
    """Command line entry."""
    args = _parser().parse_args(argv)
    os.environ.update(songchecks.THREADS)
    if args.command == "generate":
        features = None
        if args.features is not None:
            features = [f for f in args.features.split(",") if f]
        songs = [
            s
            for size in args.size
            for s in specs(
                args.seed,
                args.count,
                size,
                args.mode,
                features=features,
                build=args.build,
            )
        ]
        res = generate(args.dir, songs, args.mode)
        print(
            f"{len(songs)} songs in {args.dir}, {len(res['playroutines'])} playroutine(s)"
        )
    elif args.command == "summary":
        summary(args.dir)
        return 0
    elif args.command == "smoke":
        res = smoke(args.dir, args.jobs)
        _print_a(res["check_a"])
        _print_b(res["check_b"])
    elif args.command == "certify":
        res = run_certify(args.dir, args.jobs)
        songchecks.write_json(args.dir[0] / "certify.json", res)
        _print_certify(res)
    else:
        name, runner, printer = CHECKS[args.command]
        res = runner(args.dir, args.jobs)
        songchecks.write_json(args.dir / name, res)
        printer(res)
    if args.json:
        songchecks.write_json(args.json, res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
