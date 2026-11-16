"""Identity of the installed lifter: the generated C's dependency on it (common.mk).

Each distribution `tools/transliterate.py` lifts with, its version and every native
module it installs (path, mtime, size). `lifterstamp.py STAMP` rewrites STAMP only when
the identity differs, so make regenerates after a pin or wheel change and not otherwise.
"""

import argparse
import importlib.metadata
import os
import pathlib

DISTS = ("deity-informant", "jennings", "vagrant-moses", "pysidtracker")
NATIVE = (".so", ".pyd")


def identity(dists=DISTS):
    """Lines naming each of `dists`' version and native modules."""
    lines = []
    for name in dists:
        dist = importlib.metadata.distribution(name)
        lines.append(f"{name} {dist.version}")
        for file in sorted(dist.files or (), key=str):
            if file.suffix in NATIVE:
                path = pathlib.Path(dist.locate_file(file)).resolve()
                st = path.stat()
                lines.append(f"  {path} {st.st_mtime_ns} {st.st_size}")
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
