"""E1 and E2 residue (docs/residue.md): instructions printed more than once and checks past
one per pointer definition, as deity-informant's `info["emission"]` gives them."""

from deity_informant.cgen import cost


def sites(info, site):
    """The E1 and E2 sites of a lift's `info`, each made by `site(kind, pc, detail)`."""
    return [site(*s) for s in cost.sites(info["emission"])]
