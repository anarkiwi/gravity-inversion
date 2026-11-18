"""duplicate-code and duplicate-check residue (docs/residue.md): instructions printed more
than once and checks past one per pointer definition, as deity-informant's
`info["emission"]` gives them."""

from deity_informant.cgen import cost

#: The residue kind of each of deity-informant's cost site kinds.
KIND = {"E1": "duplicate-code", "E2": "duplicate-check"}


def sites(info, site):
    """The duplicate-code and duplicate-check sites of a lift's `info`, each made by
    `site(kind, pc, detail)`."""
    return [site(KIND[k], pc, d) for k, pc, d in cost.sites(info["emission"])]
