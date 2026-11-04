"""
UK House Price Index lookups for bringing past sale prices up to date.
"""

from typing import Iterable, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from .config import ENGLAND_CODE

INDEX_COLUMNS = ["idx_all", "idx_D", "idx_S", "idx_T", "idx_F"]


def month_start(dates) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(dates).to_period("M").to_timestamp()


class HousePriceIndex:
    """
    Index series by (area code, property type). Factors fall back from the
    local authority to the region to England, and from the property-type
    index to the all-types index, whichever first has data.
    """

    def __init__(self, df: pd.DataFrame):
        self._series = {}
        df = df.assign(month=month_start(df["month"]))
        for col in INDEX_COLUMNS:
            sub = df[["area", "month", col]].dropna()
            for area, g in sub.groupby("area"):
                s = pd.Series(g[col].to_numpy(float), index=pd.DatetimeIndex(g["month"]))
                self._series[(area, col)] = s.sort_index()
        england = self._series[(ENGLAND_CODE, "idx_all")]
        self.last_month = england.index.max()

    def valuation_month(self, as_of: Optional[pd.Timestamp] = None) -> pd.Timestamp:
        """Latest index month, or the last full month before ``as_of``."""
        if as_of is None:
            return self.last_month
        before = month_start([as_of])[0] - pd.DateOffset(months=1)
        return min(self.last_month, before)

    def _keys(self, areas: Iterable[str], ptype: Optional[str]):
        for area in [a for a in areas if a] + [ENGLAND_CODE]:
            if ptype:
                yield (area, f"idx_{ptype}")
            yield (area, "idx_all")

    def factors(
        self,
        areas: Sequence[str],
        ptype: Optional[str],
        sale_dates,
        to_month: pd.Timestamp,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Multipliers taking prices at ``sale_dates`` to ``to_month``, and a
        label naming the index series used for each.
        """
        from_months = month_start(sale_dates)
        from_months = from_months.where(from_months <= to_month, to_month)
        out = np.full(len(from_months), np.nan)
        labels = np.full(len(from_months), None, dtype=object)
        for key in self._keys(areas, ptype):
            s = self._series.get(key)
            if s is None or to_month not in s.index:
                continue
            base = s.reindex(from_months).to_numpy()
            fill = np.isnan(out) & ~np.isnan(base)
            out[fill] = s[to_month] / base[fill]
            labels[fill] = f"{key[0]}:{key[1]}"
            if not np.isnan(out).any():
                break
        return out, labels
