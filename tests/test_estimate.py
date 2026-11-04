import duckdb
import pytest

from house_price_estimate.backtest import run_backtest, summarise
from house_price_estimate.cli import IDENTIFIABLE, main
from house_price_estimate.config import ModelParams
from house_price_estimate.estimate import Estimator
from house_price_estimate.offline import no_network


@pytest.fixture
def est(paths):
    e = Estimator(paths.db)
    yield e
    e.close()


def test_build_filters_and_links(paths):
    conn = duckdb.connect(str(paths.db), read_only=True)
    assert (
        conn.execute("SELECT count(*) FROM onspd").fetchone()[0] == 3
    )  # Scotland dropped
    ppd = conn.execute("SELECT * FROM ppd").df()
    assert len(ppd) == 15  # category B and type O dropped
    assert set(ppd["ptype"]) == {"T", "F"}
    assert ppd.loc[ppd["street"] == "COMP STREET", "floor_area"].eq(80).all()
    assert ppd.loc[ppd["paon"] == "10", "floor_area"].item() == 100
    assert conn.execute("SELECT count(*) FROM epc").fetchone()[0] == 14
    conn.close()


def test_repeat_sale_and_comparables(est):
    with no_network():
        v = est.estimate_address("10 Test Rd, Norwich, NR4 7AA")
    assert v.status == "ok" and v.ppd_match == "exact" and v.epc_match == "exact"
    assert v.property_type == "T" and v.floor_area == 100
    assert v.repeat_sale_est == pytest.approx(300000)  # 200k x 150/100
    assert v.comps_floor_area_adjusted and v.comps_n == 12
    # Comps ~250k at 80 m2, scaled to 100 m2 with elasticity 0.75
    assert 280000 < v.comps_est < 310000
    assert v.method == "repeat_sale+comparables"
    assert v.low < v.estimate < v.high
    assert v.valuation_month == "2026-07"


def test_flat_matches_its_own_sale(est):
    v = est.estimate_address("Flat 2, 12 Test Road, NR4 7AA")
    assert v.ppd_match == "exact" and v.last_sale_price == 150000
    assert v.property_type == "F"
    assert v.method == "repeat_sale"  # no flat comparables nearby
    assert v.estimate == pytest.approx(225000)


def test_epc_only_property(est):
    v = est.estimate_address("Rose Cottage, Test Road, Norwich NR4 7AA")
    assert v.ppd_match == "none" and v.epc_match == "exact"
    assert v.property_type == "D" and v.property_type_source == "epc"


def test_unknown_house_uses_postcode_type(est):
    v = est.estimate_address("99 Test Road, NR4 7AB")
    assert v.ppd_match == "none"
    assert v.property_type_source == "postcode_mode" and v.method == "comparables"
    assert v.confidence == "low"


def test_without_epc(paths):
    e = Estimator(paths.db, ModelParams(use_epc=False))
    v = e.estimate_address("10 Test Road, NR4 7AA")
    assert v.floor_area is None and not v.comps_floor_area_adjusted
    e.close()


@pytest.mark.parametrize(
    "address, status",
    [
        ("10 Test Road, Norwich", "no_postcode"),
        ("10 Test Road, ZZ9 9ZZ", "postcode_not_found"),
    ],
)
def test_bad_postcodes(est, address, status):
    assert est.estimate_address(address).status == status


def test_as_of_hides_later_sales(est):
    v = est.estimate_address("10 Test Road, NR4 7AA", as_of="2015-01-01")
    assert v.last_sale_price is None and v.valuation_month == "2014-12"


def test_backtest_runs(est):
    df = run_backtest(est, 2025, 10)
    assert len(df) == 4  # comps sold in 2025
    assert "median_ape" in summarise(df).columns


def test_backtest_date_range_and_county(est):
    from house_price_estimate.backtest import sample_sales

    by_year = sample_sales(est, 2025, 100)
    by_range = sample_sales(est, None, 100, start="2025-01-01", end="2026-01-01")
    assert len(by_range) == len(by_year) == 4
    assert (
        len(sample_sales(est, None, 100, start="2025-01-01", counties=["E10000020"]))
        >= 4
    )
    assert len(sample_sales(est, 2025, 100, counties=["E10000029"])) == 0
    assert len(sample_sales(est, 2025, 100, england_only=True)) == 4
    conn = est.conn
    assert conn.execute("SELECT DISTINCT ruc21 FROM onspd").fetchall() == [("UN1",)]


def test_cli_batch(paths, tmp_path):
    src = tmp_path / "in.csv"
    src.write_text('id,address\n1,"10 Test Road, NR4 7AA"\n2,"nowhere"\n')
    out = tmp_path / "out.csv"
    main(
        [
            "--data-dir",
            str(paths.data_dir),
            "estimate",
            "--input",
            str(src),
            "--id-col",
            "id",
            "--out",
            str(out),
        ]
    )
    import pandas as pd

    res = pd.read_csv(out)
    assert list(res["id"]) == [1, 2]
    assert "address" not in res.columns
    assert list(res["status"]) == ["ok", "no_postcode"]
    assert not set(IDENTIFIABLE) & set(res.columns)
    assert {
        "price_band",
        "price_band_low",
        "price_band_high",
        "low",
        "high",
        "confidence",
    } <= set(res.columns)
    ok = res.loc[0]
    assert ok["low"] <= ok["price_band_low"] < ok["price_band_high"] <= ok["high"]
    assert ok["price_band_mid"] == (ok["price_band_low"] + ok["price_band_high"]) / 2
    assert ok["low"] % 25000 == 0 and ok["high"] % 25000 == 0
    assert res.loc[1, ["price_band", "price_band_mid", "low", "high"]].isna().all()
    main(
        [
            "--data-dir",
            str(paths.data_dir),
            "estimate",
            "--input",
            str(src),
            "--id-col",
            "id",
            "--out",
            str(out),
            "--keep-identifiable",
        ]
    )
    res = pd.read_csv(out)
    assert set(IDENTIFIABLE) <= set(res.columns)
    exact = res.loc[0]
    assert exact["price_band_low"] <= exact["estimate"] < exact["price_band_high"]
    assert exact["low"] % 1000 == 0 and exact["low"] >= ok["low"]
    assert res.loc[0, "last_sale_price"] == 200000


def test_cli_single_address_hides_identifiable(paths, capsys):
    main(
        [
            "--data-dir",
            str(paths.data_dir),
            "estimate",
            "--address",
            "10 Test Road, NR4 7AA",
        ]
    )
    fields = {line.split()[0] for line in capsys.readouterr().out.splitlines()}
    assert "price_band" in fields and not set(IDENTIFIABLE) & fields


@pytest.mark.parametrize(
    "x, band, label",
    [
        (0, (0, 25000), "£0k–£25k"),
        (249_000, (225_000, 250_000), "£225k–£250k"),
        (250_000, (250_000, 275_000), "£250k–£275k"),
        (512_000, (500_000, 550_000), "£500k–£550k"),
        (1_100_000, (1_000_000, 1_250_000), "£1m–£1.25m"),
        (4_999_000, (4_000_000, 5_000_000), "£4m–£5m"),
        (7_000_000, (5_000_000, None), "£5m+"),
    ],
)
def test_price_band(x, band, label):
    from house_price_estimate.cli import band_label, price_band

    assert price_band(x) == band
    assert band_label(*band) == label


def test_calibration_sets_range_and_confidence(paths, tmp_path):
    import json

    cal = tmp_path / "cal.json"
    cal.write_text(
        json.dumps(
            {
                "groups": {
                    "all": {"q10": 0.5, "q90": 1.5},
                    "repeat_gt15y": {"q10": 0.9, "q90": 1.2},
                    "repeat_le5y": {"q10": 0.95, "q90": 1.1},
                }
            }
        )
    )
    e = Estimator(paths.db, calibration=cal)
    v = e.estimate_address("10 Test Road, NR4 7AA")  # last sold 2015
    assert v.calibration_group == "repeat_5_15y"  # not calibrated: widest, low
    assert v.confidence == "low"
    assert (v.low, v.high) == (round(v.estimate * 0.9, -3), round(v.estimate * 1.2, -3))
    v = e.estimate_address("Flat 2, 12 Test Road, NR4 7AA")
    assert v.calibration_group == "repeat_only" and v.confidence == "low"
    e.close()
    cal.write_text(json.dumps({"groups": {"repeat_5_15y": {"q10": 0.9, "q90": 1.2}}}))
    e = Estimator(paths.db, calibration=cal)
    assert e.estimate_address("10 Test Road, NR4 7AA").confidence == "high"
    e.close()


def test_cli_calibrate(paths, tmp_path):
    import json
    import shutil

    (tmp_path / "processed").mkdir()
    shutil.copy(paths.db, tmp_path / "processed" / "hpe.duckdb")
    main(["--data-dir", str(tmp_path), "calibrate", "--year", "2024", "--n", "50"])
    cal = json.loads((tmp_path / "processed" / "calibration.json").read_text())
    assert cal["year"] == 2024 and cal["use_epc"] is True
    assert cal["groups"]["all"]["q10"] <= cal["groups"]["all"]["q90"]
    # The estimator picks it up automatically from next to the database.
    e = Estimator(tmp_path / "processed" / "hpe.duckdb")
    assert e.calibration == cal
    e.close()
