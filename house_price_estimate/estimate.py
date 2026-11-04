"""Estimate the current value of an address from the local database.

Two estimates are combined:
  repeat sale  the property's last sale, indexed with the UK HPI
  comparables  nearby recent sales of the same type, indexed to the same
               month, weighted by distance and recency and (when EPC floor
               areas are available) adjusted for floor area
The repeat-sale weight decays with the time since that sale.

All work is local. ``Estimator`` opens the database read-only and locks
DuckDB down so it cannot reach the network or other files.
"""

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from .address import ParsedAddress, identifiers, normalise_text, parse_address
from .config import GRID_SIZE_M, ModelParams, calibration_path
from .hpi import HousePriceIndex
from .match import Candidate, match_address
from .offline import lock_down_duckdb

PPD_COLS = (
    "price, date, postcode, ptype, new_build, tenure, paon, saon, street, "
    "east, north, lad, rgn, floor_area"
)


@dataclass
class Valuation:
    postcode: str | None = None
    status: str = ""
    """'ok', 'no_postcode' or 'postcode_not_found'."""
    ppd_match: str = "none"
    ppd_match_score: float = 0.0
    epc_match: str = "none"
    property_type: str | None = None
    property_type_source: str | None = None
    floor_area: float | None = None
    n_sales: int = 0
    last_sale_price: int | None = None
    last_sale_date: str | None = None
    last_sale_new_build: bool | None = None
    repeat_sale_est: float | None = None
    comps_n: int = 0
    comps_radius_m: int | None = None
    comps_floor_area_adjusted: bool = False
    comps_est: float | None = None
    repeat_weight: float = 0.0
    estimate: float | None = None
    low: float | None = None
    high: float | None = None
    method: str = ""
    calibration_group: str | None = None
    confidence: str = ""
    valuation_month: str | None = None
    hpi_series: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def weighted_quantile(values, weights, q):
    values, weights = np.asarray(values, float), np.asarray(weights, float)
    order = np.argsort(values)
    v, w = values[order], weights[order]
    cum = (np.cumsum(w) - 0.5 * w) / w.sum()
    return np.interp(q, cum, v)


def calibration_group(v: "Valuation") -> str | None:
    """Which empirical error distribution an estimate's range comes from."""
    if v.method == "repeat_sale+comparables":
        years = (
            pd.Timestamp(v.valuation_month) - pd.Timestamp(v.last_sale_date)
        ).days / 365.25
        return (
            "repeat_le5y"
            if years <= 5
            else "repeat_5_15y"
            if years <= 15
            else "repeat_gt15y"
        )
    if v.method == "comparables":
        return (
            "comps_floor_area" if v.comps_floor_area_adjusted else "comps_no_floor_area"
        )
    if v.method == "repeat_sale":
        return "repeat_only"
    return None


def _s(x) -> str:
    return "" if x is None or (isinstance(x, float) and math.isnan(x)) else str(x)


def ppd_candidates(df: pd.DataFrame) -> list[Candidate]:
    out = []
    for saon, paon, street in zip(df["saon"], df["paon"], df["street"], strict=True):
        unit = normalise_text(f"{_s(saon)} {_s(paon)}")
        street = normalise_text(_s(street))
        out.append(
            Candidate(f"{unit} {street}".strip(), unit, street, identifiers(unit))
        )
    return out


def epc_candidates(df: pd.DataFrame) -> list[Candidate]:
    out = []
    for a1, a2, a3 in zip(df["address1"], df["address2"], df["address3"], strict=True):
        text = normalise_text(" ".join(_s(a) for a in (a1, a2, a3)))
        out.append(Candidate(text, normalise_text(_s(a1)), "", identifiers(text)))
    return out


class Estimator:
    def __init__(
        self,
        db_path: Path,
        params: ModelParams = ModelParams(),
        calibration: Path | None = None,
        use_calibration: bool = True,
    ):
        """Open the database and load the calibration.

        ``calibration`` defaults to the file written by ``calibrate`` next to
        the database for these params (with or without EPC). Without one, the
        range falls back to the comparables' interquartile range.
        """
        if not Path(db_path).exists():
            raise FileNotFoundError(f"{db_path} not found; run the build step first")
        self.params = params
        self.conn = duckdb.connect(str(db_path), read_only=True)
        lock_down_duckdb(self.conn)
        tables = {r[0] for r in self.conn.execute("SHOW TABLES").fetchall()}
        self.has_epc = "epc" in tables and params.use_epc
        self.hpi = HousePriceIndex(self.conn.execute("SELECT * FROM hpi").df())
        self.calibration = None
        path = calibration or calibration_path(Path(db_path).parent, params.use_epc)
        if use_calibration and path.exists():
            self.calibration = json.loads(path.read_text())

    def close(self):
        self.conn.close()

    def _df(self, sql: str, args: list) -> pd.DataFrame:
        return self.conn.execute(sql, args).df()

    # -- lookups -------------------------------------------------------------

    def _location(self, postcode: str) -> dict | None:
        df = self._df(
            "SELECT east, north, lad, rgn FROM onspd WHERE postcode = ?", [postcode]
        )
        return None if df.empty else df.iloc[0].to_dict()

    def _postcode_sales(self, postcode: str, loc: dict, before) -> pd.DataFrame:
        return self._df(
            f"SELECT {PPD_COLS} FROM ppd WHERE gx = ? AND gy = ? AND postcode = ? "
            "AND date < ? ORDER BY date",
            [
                int(loc["east"]) // GRID_SIZE_M,
                int(loc["north"]) // GRID_SIZE_M,
                postcode,
                before,
            ],
        )

    def _postcode_epc(self, postcode: str) -> pd.DataFrame:
        return self._df(
            "SELECT address1, address2, address3, ptype, floor_area, lodgement_date "
            "FROM epc WHERE postcode = ?",
            [postcode],
        )

    def _comparables(self, loc, ptype, start, before, exclude) -> tuple:
        p = self.params
        e, n = float(loc["east"]), float(loc["north"])
        df = pd.DataFrame()
        for radius in p.comp_radii_m:
            df = self._df(
                f"SELECT {PPD_COLS} FROM ppd "
                "WHERE gx BETWEEN ? AND ? AND gy BETWEEN ? AND ? "
                "AND ptype = ? AND date >= ? AND date < ?",
                [
                    int(e - radius) // GRID_SIZE_M,
                    int(e + radius) // GRID_SIZE_M,
                    int(n - radius) // GRID_SIZE_M,
                    int(n + radius) // GRID_SIZE_M,
                    ptype,
                    start,
                    before,
                ],
            )
            df["dist"] = np.hypot(df["east"] - e, df["north"] - n)
            df = df[df["dist"] <= radius]
            if exclude is not None and len(df):
                own = (
                    (df["postcode"] == exclude[0])
                    & (df["paon"].fillna("") == exclude[1])
                    & (df["saon"].fillna("") == exclude[2])
                )
                df = df[~own]
            if (~df["new_build"]).sum() >= p.comp_min_n:
                df = df[~df["new_build"]]
            if len(df) >= p.comp_min_n:
                return df, radius
        return df, p.comp_radii_m[-1]

    # -- estimation ----------------------------------------------------------

    def estimate_address(self, address: str, as_of=None) -> Valuation:
        return self.estimate_parsed(parse_address(address), as_of)

    def estimate_parsed(self, parsed: ParsedAddress, as_of=None) -> Valuation:
        p = self.params
        v = Valuation(postcode=parsed.postcode)
        if not parsed.postcode:
            v.status = "no_postcode"
            return v
        loc = self._location(parsed.postcode)
        if loc is None:
            v.status = "postcode_not_found"
            return v
        v.status = "ok"
        as_of = pd.Timestamp(as_of) if as_of is not None else None
        to_month = self.hpi.valuation_month(as_of)
        v.valuation_month = to_month.strftime("%Y-%m")
        before = (as_of or pd.Timestamp.max.normalize()).date()

        # The property's own sales
        sales = self._postcode_sales(parsed.postcode, loc, before)
        m = match_address(parsed, ppd_candidates(sales), p.fuzzy_threshold)
        v.ppd_match, v.ppd_match_score = m.status, m.score
        own = (
            sales.iloc[m.indices].sort_values("date") if m.indices else sales.iloc[0:0]
        )

        epc = None
        if self.has_epc:
            epc_df = self._postcode_epc(parsed.postcode)
            em = match_address(parsed, epc_candidates(epc_df), p.fuzzy_threshold)
            v.epc_match = em.status
            if em.indices:
                epc = epc_df.iloc[em.indices].sort_values("lodgement_date").iloc[-1]

        # Property type and floor area
        if len(own):
            v.property_type, v.property_type_source = own["ptype"].iloc[-1], "ppd"
        elif epc is not None and isinstance(epc["ptype"], str):
            v.property_type, v.property_type_source = epc["ptype"], "epc"
        elif len(sales):
            v.property_type = sales["ptype"].mode().iloc[0]
            v.property_type_source = "postcode_mode"
        else:
            v.property_type, v.property_type_source = "T", "default"
        if p.use_epc:
            if epc is not None and pd.notna(epc["floor_area"]):
                v.floor_area = float(epc["floor_area"])
            elif len(own) and own["floor_area"].notna().any():
                v.floor_area = float(own["floor_area"].dropna().iloc[-1])

        areas = [loc["lad"], loc["rgn"]]

        # Repeat-sale estimate
        if len(own):
            last = own.iloc[-1]
            v.n_sales = len(own)
            v.last_sale_price = int(last["price"])
            v.last_sale_date = pd.Timestamp(last["date"]).strftime("%Y-%m-%d")
            v.last_sale_new_build = bool(last["new_build"])
            f, lab = self.hpi.factors(areas, v.property_type, [last["date"]], to_month)
            if not np.isnan(f[0]):
                v.repeat_sale_est = float(last["price"] * f[0])
                v.hpi_series = lab[0]

        # Comparables estimate
        start = (to_month - pd.DateOffset(months=round(12 * p.comp_years))).date()
        exclude = None
        if len(own):
            exclude = (
                parsed.postcode,
                _s(own["paon"].iloc[-1]),
                _s(own["saon"].iloc[-1]),
            )
        comps, radius = self._comparables(loc, v.property_type, start, before, exclude)
        spread = None
        if len(comps):
            factor = np.full(len(comps), np.nan)
            for (lad, rgn), pos in comps.groupby(["lad", "rgn"]).indices.items():
                factor[pos], _ = self.hpi.factors(
                    [lad, rgn], v.property_type, comps["date"].iloc[pos], to_month
                )
            comps = comps.assign(indexed=comps["price"].to_numpy() * factor).dropna(
                subset=["indexed"]
            )
            age = (to_month - pd.to_datetime(comps["date"])).dt.days / 365.25
            comps["w"] = (
                1 / (1 + (comps["dist"] / p.distance_scale_m) ** 2)
            ) * 0.5 ** (age.clip(lower=0) / p.age_half_life_years)
            comps = comps.nlargest(p.comp_max_n, "w")
        if len(comps):
            values = comps["indexed"].to_numpy()
            weights = comps["w"].to_numpy()
            with_area = comps["floor_area"].notna().to_numpy()
            if v.floor_area and with_area.sum() >= max(
                p.min_floor_area_comps, len(comps) // 2
            ):
                ratio = v.floor_area / comps["floor_area"].to_numpy()[with_area]
                values = values[with_area] * ratio**p.floor_area_elasticity
                weights = weights[with_area]
                v.comps_floor_area_adjusted = True
            v.comps_n, v.comps_radius_m = len(values), int(radius)
            q25, q50, q75 = weighted_quantile(values, weights, [0.25, 0.5, 0.75])
            v.comps_est = float(q50)
            spread = (q25 / q50, q75 / q50)

        # Blend
        if v.repeat_sale_est is not None and v.comps_est is not None:
            years = (to_month - pd.Timestamp(v.last_sale_date)).days / 365.25
            w = p.repeat_weight_max * math.exp(
                -max(years, 0) / p.repeat_weight_decay_years
            )
            if v.last_sale_new_build:
                w *= p.new_build_repeat_penalty
            v.repeat_weight = w
            v.estimate = w * v.repeat_sale_est + (1 - w) * v.comps_est
            v.method = "repeat_sale+comparables"
        elif v.repeat_sale_est is not None:
            v.repeat_weight = 1.0
            v.estimate = v.repeat_sale_est
            v.method = "repeat_sale"
        elif v.comps_est is not None:
            v.estimate = v.comps_est
            v.method = "comparables"
        else:
            v.method = "none"
            return v

        v.calibration_group = calibration_group(v)
        groups = {
            k: g
            for k, g in (self.calibration or {}).get("groups", {}).items()
            if k != "all"
        }
        if groups:
            # Empirical 10th/90th percentiles of actual / estimate. A group
            # too rare to calibrate gets the widest range and low confidence.
            q = groups.get(v.calibration_group)
            if q is None:
                q = max(groups.values(), key=lambda g: g["q90"] - g["q10"])
                v.confidence = "low"
            else:
                v.confidence = self._confidence_from_width(q["q90"] - q["q10"])
            spread = (q["q10"], q["q90"])
        else:
            if spread is None:
                years = (to_month - pd.Timestamp(v.last_sale_date)).days / 365.25
                half = 0.10 + 0.01 * max(years, 0)
                spread = (1 - half, 1 + half)
            v.confidence = self._confidence(v)
        v.low, v.high = v.estimate * spread[0], v.estimate * spread[1]
        v.estimate, v.low, v.high = (round(x, -3) for x in (v.estimate, v.low, v.high))
        return v

    def _confidence_from_width(self, width: float) -> str:
        p = self.params
        if width <= p.confidence_high_width:
            return "high"
        return "medium" if width <= p.confidence_medium_width else "low"

    def _confidence(self, v: Valuation) -> str:
        """Rule-based fallback used when there is no calibration file."""
        matched = v.ppd_match == "exact" or v.epc_match == "exact"
        recent = v.repeat_sale_est is not None and pd.Timestamp(
            v.valuation_month
        ) - pd.Timestamp(v.last_sale_date) <= pd.Timedelta(days=round(365.25 * 15))
        enough = v.comps_n >= self.params.comp_min_n
        if matched and enough and (recent or v.comps_floor_area_adjusted):
            return "high"
        if (matched or v.floor_area) and (enough or recent):
            return "medium"
        return "low"
