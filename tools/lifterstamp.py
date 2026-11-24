"""Identity of the lifter: the generated C's dependency on it (common.mk).

Each package `tools/transliterate.py` lifts with, as imported (the path PYTHONPATH gives it
first), its version and a digest of its Python sources and native modules.
`lifterstamp.py STAMP` rewrites STAMP only when the identity differs, so make regenerates
after a pin, wheel or source change and not otherwise.
"""

import argparse
import hashlib
import importlib.metadata
import importlib.util
import os
import pathlib

#: The packages the lift runs on: ``(distribution, module)``.
PACKAGES = (
    ("deity-informant", "deity_informant"),
    ("wayward-eagle", "wayward_eagle"),
    ("jennings", "jennings"),
    ("vagrant-moses", "vagrant_moses"),
    ("pysidtracker", "pysidtracker"),
)
#: The files of a package its identity covers.
SOURCES = (".py", ".so", ".pyd")


def digest(module):
    """``(directory, sha256)`` of the Python sources and native modules of the package
    ``module`` resolves to."""
    spec = importlib.util.find_spec(module)
    root = pathlib.Path(next(iter(spec.submodule_search_locations))).resolve()
    h = hashlib.sha256()
    for path in sorted(
        p for p in root.rglob("*") if p.suffix in SOURCES and p.is_file()
    ):
        h.update(str(path.relative_to(root)).encode() + b"\0" + path.read_bytes())
    return root, h.hexdigest()


def identity(packages=PACKAGES):
    """Lines naming each of ``packages``' version, where it is imported from and the digest
    of its sources."""
    lines = []
    for dist, module in packages:
        root, sha = digest(module)
        lines.append(f"{dist} {importlib.metadata.version(dist)} {root} {sha}")
    return "".join(f"{line}\n" for line in lines)


def write(stamp, text):
    """Write `text` to `stamp` unless it already holds it; True if written."""
    stamp = pathlib.Path(stamp)
    if stamp.exists() and stamp.read_text(encoding="utf-8") == text:
        return False
    stamp.parent.mkdir(parents=True, exist_ok=True)
    tmp = stamp.with_name(stamp.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, stamp)
    return True


def main(argv=None):
    """CLI: write the lifter identity stamp."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stamp", help="stamp file, rewritten only on change")
    args = parser.parse_args(argv)
    write(args.stamp, identity())


if __name__ == "__main__":
    main()
