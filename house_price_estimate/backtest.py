"""
Accuracy check: value a random sample of past sales using only data from
before each sale, then compare with the price actually paid. The same
backtest calibrates the estimate ranges (``calibrate``).

The address string is rebuilt from the Land Registry fields, so address
matching is exercised too. HPI values are as currently published (later
revisions are a small look-ahead).
"""

import logging
from typing import Optional, Sequence

import numpy as np
import pandas as pd

from .estimate import Estimator, _s

log = logging.getLogger(__name__)


def _address(row) -> str:
    parts = [_s(row.saon), f"{_s(row.paon)} {_s(row.street)}".strip(), _s(row.town), row.postcode]
    return ", ".join(p for p in parts if p)


def sample_sales(est: Estimator, year: Optional[int], n: int, seed: int = 1,
                 start=None, end=None, counties: Sequence[str] = (),
                 england_only: bool = False) -> pd.DataFrame:
    """
    Random non-new-build sales in ``year``, or from ``start`` (inclusive) to
    ``end`` (exclusive), optionally only in the given ONSPD county codes
    or only in England.
    """
    where, args = ["NOT p.new_build"], []
    if year is not None:
        where.append("year(p.date) = ?")
        args.append(year)
    if start is not None:
        where.append("p.date >= ?::DATE")
        args.append(str(start))
    if end is not None:
        where.append("p.date < ?::DATE")
        args.append(str(end))
    join = "JOIN onspd o USING (postcode)" if counties or england_only else ""
    if england_only:
        where.append("o.rgn LIKE 'E12%'")
    if counties:
        where.append(f"o.cty IN ({', '.join('?' * len(counties))})")
        args.extend(counties)
    # DuckDB samples before WHERE, so filter in a subquery. Sample sizes
    # can't be bound as parameters.
    return est.conn.execute(
        f"""
        SELECT * FROM (
            SELECT p.price, p.date, p.postcode, p.ptype, p.paon, p.saon, p.street, p.town
            FROM ppd p {join} WHERE {' AND '.join(where)})
        USING SAMPLE reservoir({int(n)} ROWS) REPEATABLE ({int(seed)})
        """,
        args,
    ).df()


def run_backtest(est: Estimator, year: Optional[int], n: int, seed: int = 1,
                 sales: Optional[pd.DataFrame] = None, **sample_kw) -> pd.DataFrame:
    """Value sampled sales as of their sale date. ``sales`` reuses a sample."""
    if sales is None:
        sales = sample_sales(est, year, n, seed, **sample_kw)
    rows = []
    for i, row in enumerate(sales.itertuples(index=False), 1):
        v = est.estimate_address(_address(row), as_of=row.date)
        rows.append({"actual": row.price, "sale_date": row.date, "actual_type": row.ptype,
                     **v.as_dict()})
        if i % 250 == 0:
            log.info("backtest %d/%d", i, len(sales))
    out = pd.DataFrame(rows)
    out["error"] = out["estimate"] / out["actual"] - 1
    return out


def summarise(df: pd.DataFrame) -> pd.DataFrame:
    def stats(g):
        e = g["error"].dropna().abs()
        return pd.Series({
            "n": len(g),
            "estimated": len(e),
            "median_ape": e.median(),
            "within_10pct": (e <= 0.10).mean(),
            "within_20pct": (e <= 0.20).mean(),
            "median_signed_error": g["error"].median(),
            "in_range": ((g["actual"] >= g["low"]) & (g["actual"] <= g["high"])).mean(),
        })

    overall = stats(df).to_frame("all").T
    by_method = df.groupby("method").apply(stats, include_groups=False)
    by_conf = df.groupby("confidence").apply(stats, include_groups=False)
    by_conf.index = "confidence=" + by_conf.index.astype(str)
    by_match = df.groupby("ppd_match").apply(stats, include_groups=False)
    by_match.index = "ppd_match=" + by_match.index.astype(str)
    by_group = df.groupby("calibration_group").apply(stats, include_groups=False)
    by_group.index = "group=" + by_group.index.astype(str)
    return pd.concat([overall, by_method, by_conf, by_match, by_group])


def calibrate(est: Estimator, year: int, n: int, seed: int = 1, min_group: int = 50) -> dict:
    """
    10th/90th percentiles of actual / estimate per calibration group, from
    a backtest. Groups with fewer than ``min_group`` sales use 'all'.
    """
    df = run_backtest(est, year, n, seed).dropna(subset=["estimate"])
    df = df.assign(ratio=df["actual"] / df["estimate"])

    def quantiles(g):
        return {
            "n": int(len(g)),
            "q10": float(g["ratio"].quantile(0.10)),
            "q90": float(g["ratio"].quantile(0.90)),
            "median_ape": float((g["ratio"] - 1).abs().median()),
        }

    groups = {"all": quantiles(df)}
    for name, g in df.groupby("calibration_group"):
        if len(g) >= min_group:
            groups[name] = quantiles(g)
    return {
        "description": "10th/90th percentiles of actual price / estimate from a backtest",
        "year": year,
        "n": int(len(df)),
        "seed": seed,
        "use_epc": est.params.use_epc,
        "groups": groups,
    }
