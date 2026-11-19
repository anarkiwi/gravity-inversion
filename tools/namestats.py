"""How deity-informant names each tune's memory and values, from its `info["names"]`
(docs/readability.md, *Names*): atoms by kind, accesses by how they print, variables by how
they are named and the share left numbered.

    python tools/namestats.py [TUNE ...]  # one JSON line per tune
"""

import argparse
import json
from collections import Counter

from tqdm import tqdm

from constructs import ROOT, load
from residue import TUNES
from transliterate import transliterate


def summary(info):
    """``{"atoms", "labelled", "accesses", "straddling", "variables", "numbered"}`` of a
    translation's ``info``: atoms by kind, those named by a label, accesses by how they
    print, straddling address sets, variables by how named and the share left ``vN``."""
    names = info["names"]
    atoms = names["atoms"].values()
    kinds = names["variables"]
    total = sum(kinds.values()) - kinds.get("carry", 0)
    return {
        "atoms": dict(sorted(Counter(a["kind"] for a in atoms).items())),
        "labelled": sum(a["label"] is not None for a in atoms),
        "accesses": names["accesses"],
        "straddling": sum(len(v) for v in names["straddling"].values()),
        "variables": kinds,
        "numbered": round(kinds.get("numbered", 0) / total, 3) if total else 0.0,
    }


def main(argv=None):
    """CLI: print each tune's :func:`summary`."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("tunes", nargs="*", default=list(TUNES))
    args = ap.parse_args(argv)
    for tune in tqdm(args.tunes, desc="names", unit="tune"):
        psid = (ROOT / load(tune)["psid"]).read_bytes()
        _, _, info = transliterate(psid, "gen.h", split=True)
        tqdm.write(json.dumps({tune: summary(info)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
