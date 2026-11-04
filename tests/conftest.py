"""
Synthetic raw datasets in the real file formats, built into a database once
per test session.
"""

import csv
import sys
import zipfile
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from house_price_estimate.build import build_all  # noqa: E402
from house_price_estimate.config import Paths  # noqa: E402

LAD, RGN = "E07000148", "E12000006"

POSTCODES = [
    # pcds, east, north, country
    ("NR4 7AA", 620000, 306000, "E92000001"),
    ("NR4 7AB", 620100, 306000, "E92000001"),
    ("NR4 7AD", 620300, 306200, "E92000001"),
    ("AB1 0AA", 385386, 801193, "S92000003"),
]

# price, date, postcode, type, new, tenure, paon, saon, street, category
SALES = [
    (200000, "2015-06-15", "NR4 7AA", "T", "N", "F", "10", "", "TEST ROAD", "A"),
    (150000, "2018-03-01", "NR4 7AA", "F", "N", "L", "12", "FLAT 2", "TEST ROAD", "A"),
    (120000, "2018-03-01", "NR4 7AA", "F", "N", "L", "12", "FLAT 1", "TEST ROAD", "A"),
    (999999, "2024-01-01", "NR4 7AA", "T", "N", "F", "14", "", "TEST ROAD", "B"),
    (999999, "2024-01-01", "NR4 7AA", "O", "N", "F", "16", "", "TEST ROAD", "A"),
] + [
    (
        240000 + 2000 * i,
        f"202{3 + i % 3}-0{1 + i % 9}-10",
        pc,
        "T",
        "N",
        "F",
        str(n),
        "",
        "COMP STREET",
        "A",
    )
    for i, (pc, n) in enumerate(
        [("NR4 7AB", k) for k in range(1, 7)] + [("NR4 7AD", k) for k in range(1, 7)]
    )
]

EPC = [
    # address1, address2, postcode, property_type, built_form, floor area
    ("10 Test Road", "", "NR4 7AA", "House", "Mid-Terrace", 100),
    ("Rose Cottage", "Test Road", "NR4 7AA", "House", "Detached", 150),
] + [
    (f"{k} Comp Street", "", pc, "House", "Mid-Terrace", 80)
    for pc in ("NR4 7AB", "NR4 7AD")
    for k in range(1, 7)
]


def _hpi_rows():
    for month in pd.date_range("2010-01-01", "2026-07-01", freq="MS"):
        value = 100.0 if month < pd.Timestamp("2020-01-01") else 150.0
        for area in (LAD, RGN, "E92000001"):
            yield [
                month.strftime("%d/%m/%Y"),
                "x",
                area,
                1,
                value,
                "",
                "",
                "",
                "",
                "",
                1,
                value,
                1,
                value,
                1,
                value,
                1,
                value,
            ]


def write_raw(raw: Path) -> None:
    raw.mkdir(parents=True)
    with open(raw / "pp-complete.csv", "w", newline="") as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL)
        for i, (price, date, pc, t, new, ten, paon, saon, street, cat) in enumerate(
            SALES
        ):
            w.writerow(
                [
                    f"{{{i}}}",
                    price,
                    f"{date} 00:00",
                    pc,
                    t,
                    new,
                    ten,
                    paon,
                    saon,
                    street,
                    "",
                    "NORWICH",
                    "NORWICH",
                    "NORFOLK",
                    cat,
                    "A",
                ]
            )
    with open(raw / "ukhpi.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "Date",
                "RegionName",
                "AreaCode",
                "AveragePrice",
                "Index",
                "IndexSA",
                "1m%Change",
                "12m%Change",
                "AveragePriceSA",
                "SalesVolume",
                "DetachedPrice",
                "DetachedIndex",
                "SemiDetachedPrice",
                "SemiDetachedIndex",
                "TerracedPrice",
                "TerracedIndex",
                "FlatPrice",
                "FlatIndex",
            ]
        )
        w.writerows(_hpi_rows())
    (raw / "onspd").mkdir()
    header = (
        "pcd7,pcds,doterm,lad26cd,east1m,north1m,ctry26cd,rgn26cd,lsoa21cd,"
        "cty26cd,ruc21ind\n"
    )
    body = "".join(
        f'"{pc}","{pc}","","{LAD}","{e}","{n}","{c}","{RGN}","E01000001","E10000020","UN1"\n'
        for pc, e, n, c in POSTCODES
    )
    with zipfile.ZipFile(raw / "onspd" / "onspd.zip", "w") as z:
        z.writestr("Data/ONSPD_TEST_UK.csv", header + body)
        z.writestr("Data/multi_csv/ONSPD_TEST_UK_NR.csv", header + body * 50)
    (raw / "epc").mkdir()
    with zipfile.ZipFile(raw / "epc" / "domestic.zip", "w") as z:
        rows = [
            "CERTIFICATE_NUMBER,ADDRESS1,ADDRESS2,ADDRESS3,POSTCODE,PROPERTY_TYPE,"
            "BUILT_FORM,LODGEMENT_DATE,TOTAL_FLOOR_AREA,NUMBER_HABITABLE_ROOMS"
        ]
        rows += [
            f"{i},{a1},{a2},Norwich,{pc},{pt},{bf},2020-01-01,{fa},4"
            for i, (a1, a2, pc, pt, bf, fa) in enumerate(EPC)
        ]
        z.writestr("certificates.csv", "\n".join(rows) + "\n")
        z.writestr("recommendations.csv", "LMK,TEXT\n1,x\n")


@pytest.fixture(scope="session")
def paths(tmp_path_factory) -> Paths:
    paths = Paths(tmp_path_factory.mktemp("hpe"))
    write_raw(paths.raw)
    build_all(paths)
    return paths
