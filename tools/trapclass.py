"""Trap classes of the residue census from cgen's trap_info (docs/residue.md)."""

#: The informational kind: traps guarding invalid input, listed but no residue.
GUARD = "invalid-input-guard"
#: trap_info guard kinds that trap input the original's meaning does not define.
GUARD_KINDS = frozenset(("entry", "objchk", "window", "jam"))
#: Extents of an `index` guard's table that end the data it indexes.
DATA_ENDS = frozenset(("loaded", "object", "code", "column"))


def _guards_input(g):
    """Whether a trap_info guard traps input the original does not define: an entry
    register outside its declared values, a pointer outside its object or window, a JAM, an
    index past the end of the data its table is (docs/residue.md)."""
    if g["kind"] in GUARD_KINDS:
        return True
    return (
        g["kind"] == "index"
        and bool(g["tables"])
        and all(t["by"] in DATA_ENDS for t in g["tables"])
    )


def trap_class(meta):
    """(kind, detail) of a trap from its trap_info: invalid-input-guard implied by the entry
    guard alone or every guard on invalid input, implied-guard otherwise implied or proven,
    else deopt."""
    if meta is None:
        return "deopt", "no trap_info"
    kind = meta["kind"]
    if meta["implied_by"] == ["entry"]:
        return GUARD, f"{kind} implied by the entry guard"
    if meta["implied_by"] is not None:
        by = ", ".join(f"trap({k})" for k in meta["implied_by"]) or "constants"
        return "implied-guard", f"{kind} implied by {by}"
    if meta["proven"]:
        return "implied-guard", f"{kind} proven"
    if meta["guards"] and all(_guards_input(g) for g in meta["guards"]):
        return GUARD, kind
    return "deopt", kind
