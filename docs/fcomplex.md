# Function complexity

`tools/fcomplex.py C...` scores generated C by clang-tidy's
`readability-function-cognitive-complexity` (SonarSource's cognitive complexity, threshold 0):
per file its total, nesting penalty, gotos and number of functions scoring above 0, and its most complex functions.

    python tools/fcomplex.py tunes/*/build/gen/*.c --top 5

`fcomplex.score(path)` gives the same per function: points, nesting penalty, gotos and points
by construct (`if`, `else`, loop, `switch`, `goto`, `&&`, `||`, `?`).

C is parsed for the C64 with the file's directory, `c64/` and the llvm-mos SDK's headers,
copied once from the SDK image into `out/mos-sdk`:

```
mkdir -p out/mos-sdk && docker run --rm -u $(id -u):$(id -g) -v /scratch:/scratch -w "$PWD" ghcr.io/anarkiwi/docker-mos-llvm-sdk:v23.4.0 \
  sh -c 'for d in c64 commodore common; do cp -r /usr/local/llvm-mos/mos-platform/$d/include out/mos-sdk/$d; done'
```

`tools/flowcmp.py` checks the rewrites that lower the score keep behavior's data flow: the
value-flow graphs and state machines (docs/flowgraph.md) of each tune's C with and without
wayward-eagle's rewrites, each differing edge listed.

    python tools/flowcmp.py OUT TUNE ... [--edges]
