import pytest

from house_price_estimate.address import (
    extract_postcode,
    identifiers,
    normalise_postcode,
    normalise_text,
    parse_address,
)
from house_price_estimate.match import Candidate, match_address


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("nr4 7tj", "NR4 7TJ"),
        ("NR47TJ", "NR4 7TJ"),
        (" sw1a  1aa ", "SW1A 1AA"),
        ("EC1A1BB", "EC1A 1BB"),
        ("not a postcode", None),
        ("", None),
    ],
)
def test_normalise_postcode(raw, expected):
    assert normalise_postcode(raw) == expected


def test_extract_postcode_takes_last_and_strips_it():
    pc, rest = extract_postcode("Flat 2, 10 High St, Norwich NR47TJ")
    assert pc == "NR4 7TJ"
    assert "NR4" not in rest


@pytest.mark.parametrize(
    "text, expected",
    [
        ("FLAT 2 10 HIGH ROAD", ("2", "10")),
        ("FLAT B 12 HIGH ROAD", ("B", "12")),
        ("12 A HIGH ROAD", ("12A",)),
        ("12A HIGH ROAD", ("12A",)),
        ("12 14 HIGH ROAD", ("12", "14")),
        ("1ST FLOOR FLAT 3 THIRD AVENUE", ("3",)),
        ("ROSE COTTAGE MILL LANE", ()),
    ],
)
def test_identifiers(text, expected):
    assert identifiers(text) == expected


def test_normalise_text_expands_abbreviations():
    assert normalise_text("10, High Rd.") == "10 HIGH ROAD"
    assert normalise_text("Apt 3 & Co") == "APARTMENT 3 AND CO"


def test_parse_address():
    p = parse_address("Rose Cottage, Mill Ln, Norwich, NR4 7TJ")
    assert p.postcode == "NR4 7TJ"
    assert p.name == "ROSE COTTAGE"
    assert p.idents == ()


def _cand(unit, street):
    return Candidate(f"{unit} {street}", unit, street, identifiers(unit))


def test_match_numbered_prefers_same_street():
    cands = [
        _cand("10", "MILL CLOSE"),
        _cand("10", "MILL LANE"),
        _cand("FLAT 1 10", "MILL LANE"),
    ]
    m = match_address(parse_address("10 Mill Lane, NR4 7TJ"), cands)
    assert (m.status, m.indices) == ("exact", [1])


def test_match_flat_order_matters():
    cands = [_cand("FLAT 1 2", "PARK ROAD"), _cand("FLAT 2 1", "PARK ROAD")]
    m = match_address(parse_address("Flat 2, 1 Park Road, NR4 7TJ"), cands)
    assert (m.status, m.indices) == ("exact", [1])


def test_match_house_without_flat_is_not_a_flat():
    m = match_address(
        parse_address("Flat 2, 10 X Road, NR4 7TJ"), [_cand("10", "X ROAD")]
    )
    assert m.status == "none"


def test_match_named_house():
    cands = [_cand("ROSE COTTAGE", "MILL LANE"), _cand("MILL HOUSE", "MILL LANE")]
    m = match_address(parse_address("Rose Cottage, Mill Lane, Norwich NR4 7TJ"), cands)
    assert (m.status, m.indices) == ("exact", [0])
    m = match_address(parse_address("The Old Barn, Mill Lane, NR4 7TJ"), cands)
    assert m.status == "none"
