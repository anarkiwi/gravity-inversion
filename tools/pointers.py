"""Zero-page pointers play reads through whose post-init words play never reads, pointed at
what they reach after a relocation (docs/relocate.md, `entries`)."""

from collections import defaultdict

from playtables import PAGE


def lands(word, offs, pads, taken):
    """The Δ, a page multiple, putting the bytes ascending `offs` from `word` in the first
    pad ((start, length)) of `pads` they fit, over bytes `taken` ({to: from}) holds free or
    the same; None if none."""
    for start, n in pads:
        base = start - offs[0]
        d = base + ((word - base) & 0xFF) - word
        if word + d + offs[-1] < start + n and all(
            taken.get(word + y + d, word + y) == word + y for y in offs
        ):
            return d
    return None


def _entry(word, offs, hosts, layout, taken):
    """(Δ or None, {(addr, Δ)} copies, why left or None) of one post-init `word` of
    :func:`entries`, its copies over `taken`."""
    span, delta, pads, fixed, _ = layout
    reach = [(word + y) & 0xFFFF for y in offs]
    held = set(span[reach].tolist())
    if -1 not in held and len({delta.get(i) for i in held}) == 1 and min(held) in delta:
        return delta[min(held)], set(), None
    if -1 not in held and not held & set(delta):
        return 0, set(), None
    if not fixed[reach].all():
        return None, set(), "reaches bytes a copy cannot hold"
    d = lands(word, offs, [pads[i] for i in sorted(hosts)], taken)
    if d is None:
        return None, set(), "no pad holds its reach"
    return d, {(word + y, d) for y in offs}, None


def _pointer(words, offs, hosts, layout):
    """(Δ or None, copies, why left or None) of a pointer's post-init `words`, one Δ for
    all (:func:`_entry`)."""
    got, more, why, taken = set(), set(), None, dict(layout[4])
    for word in words:
        d, c, w = _entry(word, offs, hosts, layout, taken)
        got.add(d)
        why = why or w
        more |= c
        taken.update((a + k, a) for a, k in c)
    if why or len(got) > 1:
        return None, set(), why or "songs disagree"
    return got.pop(), more, None


def entries(posts, run, tabs, layout, shift):
    """({pointer high byte: shift}, {(addr, Δ)} copies, {pointer: why} left) pointing each
    zero-page pointer of :attr:`Sources.pairs` of `run` whose post-init words (one per song of
    `posts`) play never reads (`shift` holds neither byte) at the bytes its offsets reach from
    them: by their block's Δ when they lie in one moved block, else, unless they lie in blocks
    that stay, at a copy of them in the pad of a moved block its instructions access (`tabs`
    {pc: {block index}}) when they are `fixed`, over the bytes `taken` holds free or the
    same; one Δ for every song. `layout`: (block index per byte, -1 outside them; {moved
    block index: Δ}; {moved block index: its pad's (start, length)}; `fixed`, the bool
    [65536] mask of the RAM bytes play neither writes nor reads as zero page, stack, ROM or
    I/O; `taken`, {to: from} of the bytes moved there, which the copies extend)."""
    ptrs, copies, left = {}, set(), {}
    for z, sites in sorted(run.pairs.items()):
        high = (z + 1) & 0xFF
        if z in shift or high in shift:
            continue
        d, more, why = _pointer(
            sorted({int(p.mem[z]) | int(p.mem[high]) << 8 for p in posts}),
            sorted({y for _, y in sites}),
            {i for p, _ in sites for i in tabs.get(p, ())} & set(layout[2]),
            layout,
        )
        if why:
            left[z] = why
        elif d:
            layout[4].update((a + k, a) for a, k in more)
            copies |= more
            ptrs[high] = d // PAGE & 0xFF
    return ptrs, copies, left


def widen(posts, run, tabs, left, pads):
    """{block index: pad length} holding, a whole-page move from each post-init word, the bytes
    each pointer `left` for want of a pad reaches, in a moved block its instructions access
    (`pads` {moved block index: (pad start, length)})."""
    out = defaultdict(int)
    for z, why in left.items():
        if why != "no pad holds its reach":
            continue
        offs = sorted({y for _, y in run.pairs[z]})
        words = {int(p.mem[z]) | int(p.mem[(z + 1) & 0xFF]) << 8 for p in posts}
        for i in {i for p, _ in run.pairs[z] for i in tabs.get(p, ()) if i in pads}:
            n = max((w + offs[0] - pads[i][0]) & 0xFF for w in words)
            out[i] = max(out[i], n + offs[-1] - offs[0] + 1)
    return out
