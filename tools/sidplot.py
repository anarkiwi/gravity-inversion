"""Plot a tune's SID registers per frame from its HVSC sidtrace capture to an image file.

Each frame's value is the register's state at the end of the frame: voice frequencies (16 bit),
pulse widths (12 bit) and the filter cutoff (11 bit) as bold lines, the 8-bit registers thin,
on a symlog value axis from 0 to 65535."""

import argparse
import pathlib

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
from pysidtracker.survey import hvsc_capture, oracle_frames

matplotlib.use("Agg")

VOICE = ("#1f77b4", "#d62728", "#2ca02c")
FILTER = "#9467bd"


def series(grid):
    """``[(name, values, colour, wide)]`` of a per-frame register grid ``[frames, 25]``."""
    g = np.asarray(grid, np.int64)
    out = []
    for v in range(3):
        r = 7 * v
        out.append((f"freq {v + 1}", g[:, r] | g[:, r + 1] << 8, VOICE[v], True))
        out.append(
            (f"pulse {v + 1}", g[:, r + 2] | (g[:, r + 3] & 0x0F) << 8, VOICE[v], True)
        )
        for k, name in ((4, "control"), (5, "attack/decay"), (6, "sustain/release")):
            out.append((f"{name} {v + 1}", g[:, r + k], VOICE[v], False))
    out.append(("cutoff", g[:, 22] << 3 | g[:, 21] & 0x07, FILTER, True))
    out.append(("resonance/routing", g[:, 23], FILTER, False))
    out.append(("mode/volume", g[:, 24], FILTER, False))
    return out


def render(grid, path, title="", start=0):
    """Write the registers of ``grid`` (frames from ``start``) as an image at ``path``."""
    frames = np.arange(start, start + len(grid))
    fig, ax = plt.subplots(figsize=(14, 7), dpi=120)
    dashes = {"pulse": (0, (4, 2)), "cutoff": "solid"}
    for name, values, colour, wide in series(grid):
        style = next((s for k, s in dashes.items() if name.startswith(k)), "solid")
        ax.step(
            frames,
            values,
            where="post",
            color=colour,
            linestyle=style,
            linewidth=1.6 if wide else 0.6,
            alpha=1.0 if wide else 0.5,
            label=name if wide or name.startswith("control") else None,
        )
    ax.set_yscale("symlog", linthresh=1)
    ax.set_ylim(0, 65535)
    ax.set_xlim(frames[0], frames[-1])
    ax.set_xlabel("frame")
    ax.set_ylabel("register value")
    ax.set_title(title)
    ax.grid(True, which="major", alpha=0.3)
    ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0), fontsize=8)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def main(argv=None):
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("psid")
    ap.add_argument("out", help="image file (format from its suffix, e.g. .png)")
    ap.add_argument(
        "--hvsc", required=True, help="HVSC root (DOCUMENTS/Songlengths.md5)"
    )
    ap.add_argument("--captures", help="capture root (default: the HVSC root)")
    ap.add_argument("--song", type=int, default=1, help="1-based subtune")
    ap.add_argument("--start", type=int, default=0, help="first frame")
    ap.add_argument("--frames", type=int, default=500)
    args = ap.parse_args(argv)
    data = pathlib.Path(args.psid).read_bytes()
    capture = hvsc_capture(data, args.song - 1, args.hvsc, args.captures or args.hvsc)
    if capture is None:
        ap.error(f"no HVSC capture of song {args.song} of {args.psid}")
    _, grid = oracle_frames(capture, nframes=args.start + args.frames)
    title = (
        f"{pathlib.Path(args.psid).stem} song {args.song}, frames {args.start}-"
        f"{args.start + args.frames - 1}"
    )
    render(np.asarray(grid)[args.start :], args.out, title, args.start)


if __name__ == "__main__":
    main()
