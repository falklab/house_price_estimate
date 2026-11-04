"""Address parsing and normalisation. Pure functions, no I/O."""

import re
from dataclasses import dataclass

POSTCODE_RE = re.compile(
    r"(?<![A-Z0-9])([A-Z]{1,2}[0-9][A-Z0-9]?) ?([0-9][A-Z]{2})(?![A-Z0-9])"
)
NUMBER_RE = re.compile(r"[0-9]+[A-Z]?")

ABBREVIATIONS = {
    "RD": "ROAD",
    "AVE": "AVENUE",
    "AV": "AVENUE",
    "CL": "CLOSE",
    "CRES": "CRESCENT",
    "CRESC": "CRESCENT",
    "DR": "DRIVE",
    "GDNS": "GARDENS",
    "GRN": "GREEN",
    "GRO": "GROVE",
    "LN": "LANE",
    "PL": "PLACE",
    "SQ": "SQUARE",
    "TER": "TERRACE",
    "TERR": "TERRACE",
    "CT": "COURT",
    "PK": "PARK",
    "HSE": "HOUSE",
    "APT": "APARTMENT",
}
UNIT_WORDS = {"FLAT", "APARTMENT", "UNIT", "MAISONETTE", "STUDIO", "ROOM"}


def normalise_text(s: str | None) -> str:
    """Upper-case, strip punctuation, collapse spaces, expand abbreviations."""
    if not s:
        return ""
    s = s.upper().replace("&", " AND ")
    tokens = re.sub(r"[^A-Z0-9]+", " ", s).split()
    return " ".join(ABBREVIATIONS.get(t, t) for t in tokens)


def normalise_postcode(s: str | None) -> str | None:
    """Return the postcode in 'OUT IN' form, or None if not a valid shape."""
    if not s:
        return None
    m = POSTCODE_RE.fullmatch(re.sub(r"\s+", " ", s.strip().upper()))
    if m is None:
        m = POSTCODE_RE.fullmatch(re.sub(r"[^A-Z0-9]", "", s.upper()))
    return f"{m.group(1)} {m.group(2)}" if m else None


def extract_postcode(raw: str) -> tuple[str | None, str]:
    """Find the last postcode in an address; return it and the rest."""
    s = re.sub(r"\s+", " ", raw.upper())
    matches = list(POSTCODE_RE.finditer(s))
    if not matches:
        return None, s
    m = matches[-1]
    rest = s[: m.start()] + " " + s[m.end() :]
    return f"{m.group(1)} {m.group(2)}", rest


def identifiers(norm: str) -> tuple[str, ...]:
    """House/flat identifiers in order.

    Numbers (with an optional letter suffix) and single letters after unit
    words, e.g. "FLAT B 12 HIGH ROAD" gives ("B", "12"). "12 A" is merged to
    "12A".
    """
    tokens = norm.split()
    out = []
    for i, t in enumerate(tokens):
        prev = tokens[i - 1] if i else ""
        if NUMBER_RE.fullmatch(t):
            out.append(t)
        elif len(t) == 1 and t.isalpha():
            if prev in UNIT_WORDS:
                out.append(t)
            elif out and prev == out[-1] and prev.isdigit():
                out[-1] = prev + t
    return tuple(out)


@dataclass(frozen=True)
class ParsedAddress:
    postcode: str | None
    text: str
    """Normalised address without the postcode."""
    name: str
    """Normalised first comma-separated part, e.g. a house name."""
    idents: tuple[str, ...]


def parse_address(raw: str) -> ParsedAddress:
    postcode, rest = extract_postcode(raw or "")
    first = rest.split(",")[0]
    text = normalise_text(rest)
    return ParsedAddress(
        postcode=postcode,
        text=text,
        name=normalise_text(first),
        idents=identifiers(text),
    )
