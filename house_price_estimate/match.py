"""Match a parsed input address against candidate records in the same postcode."""

from collections.abc import Sequence
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from .address import ParsedAddress


@dataclass
class Match:
    status: str
    """'exact', 'fuzzy' or 'none'."""
    score: float = 0.0
    indices: list[int] = field(default_factory=list)
    """Positions of all candidates judged to be the same property."""


@dataclass(frozen=True)
class Candidate:
    text: str
    """Normalised full address (unit, building, street)."""
    name: str
    """Normalised unit + building part, without street."""
    street: str
    idents: tuple[str, ...]


def match_address(
    parsed: ParsedAddress, candidates: Sequence[Candidate], fuzzy_threshold: int = 85
) -> Match:
    """Match a parsed address against the candidates in its postcode.

    Numbered addresses must have the same identifiers (same order means
    'exact', same set means 'fuzzy'); ties are broken on the fuzzy similarity
    of the full text, which separates streets sharing a postcode. Unnumbered
    addresses are matched on the house name.
    """
    if not candidates:
        return Match("none")
    if parsed.idents:
        ordered = [i for i, c in enumerate(candidates) if c.idents == parsed.idents]
        status = "exact"
        if not ordered:
            target = sorted(parsed.idents)
            ordered = [
                i for i, c in enumerate(candidates) if sorted(c.idents) == target
            ]
            status = "fuzzy"
        if not ordered:
            return Match("none")
        scores = {
            i: fuzz.token_set_ratio(parsed.text, candidates[i].text) for i in ordered
        }
        best = max(ordered, key=lambda i: scores[i])
        if scores[best] < 50:
            status = "fuzzy"
        same = [
            i
            for i in ordered
            if candidates[i].street == candidates[best].street
            and candidates[i].idents == candidates[best].idents
        ]
        return Match(status, float(scores[best]), same)

    pool = [i for i, c in enumerate(candidates) if not c.idents and c.name]
    if not pool:
        return Match("none")

    def key(i):
        name = candidates[i].name
        return (fuzz.token_set_ratio(parsed.name, name), fuzz.ratio(parsed.name, name))

    best = max(pool, key=key)
    set_score, plain_score = key(best)
    if set_score < fuzzy_threshold:
        return Match("none", float(set_score))
    status = "exact" if plain_score == 100 else "fuzzy"
    same = [i for i in pool if candidates[i].name == candidates[best].name]
    return Match(status, float(set_score), same)
