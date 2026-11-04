"""Paths, bulk-download URLs and model parameters.

The URLs here are the only remote locations the package ever contacts, and
only from the ``download`` command. None of them carry address data.
"""

import os
from dataclasses import dataclass
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = Path(os.environ.get("HPE_DATA_DIR", PACKAGE_ROOT / "data"))

# HM Land Registry Price Paid Data, complete file (all sales since 1995).
PPD_URL = (
    "http://prod.publicdata.landregistry.gov.uk.s3-website-eu-west-1"
    ".amazonaws.com/pp-complete.csv"
)
# UK House Price Index full file. Published monthly with a ~2 month lag.
UKHPI_URL_TEMPLATE = (
    "https://publicdata.landregistry.gov.uk/market-trend-data/"
    "house-price-index-data/UK-HPI-full-file-{year}-{month:02d}.csv"
)
# ONS Open Geography Portal catalogue search, used to find the latest ONSPD
# bulk zip. The query is a fixed string, never address data.
ONSPD_SEARCH_URL = (
    "https://geoportal.statistics.gov.uk/api/search/v1/collections/dataset/"
    "items?q=ONS%20Postcode%20Directory&limit=50"
)
ARCGIS_ITEM_DATA_URL = (
    "https://www.arcgis.com/sharing/rest/content/items/{item_id}/data"
)
EPC_BULK_URL = "https://get-energy-performance-data.communities.gov.uk/"

ENGLAND_CODE = "E92000001"
WALES_CODE = "W92000004"

# Side of the square grid cells (metres) used to cluster sales on disk so
# spatial queries only scan nearby rows.
GRID_SIZE_M = 2000


@dataclass(frozen=True)
class Paths:
    """Locations of raw downloads and the processed database."""

    data_dir: Path = DEFAULT_DATA_DIR

    @property
    def raw(self) -> Path:
        return self.data_dir / "raw"

    @property
    def ppd_csv(self) -> Path:
        return self.raw / "pp-complete.csv"

    @property
    def hpi_csv(self) -> Path:
        return self.raw / "ukhpi.csv"

    @property
    def onspd_dir(self) -> Path:
        return self.raw / "onspd"

    @property
    def epc_dir(self) -> Path:
        return self.raw / "epc"

    @property
    def processed(self) -> Path:
        return self.data_dir / "processed"

    @property
    def db(self) -> Path:
        return self.processed / "hpe.duckdb"


@dataclass(frozen=True)
class ModelParams:
    """Tuning parameters for the valuation model."""

    comp_years: float = 5.0
    """Only use comparable sales from this many years before valuation."""
    comp_radii_m: tuple = (500, 1000, 2000, 5000)
    """Search radii, tried in order until enough comparables are found."""
    comp_min_n: int = 10
    comp_max_n: int = 50
    """Keep at most this many comparables (highest weight first)."""
    distance_scale_m: float = 400.0
    """Comparable weight is 1 / (1 + (distance / scale)^2)."""
    age_half_life_years: float = 2.0
    """Comparable weight halves for every this many years since sale."""
    floor_area_elasticity: float = 0.75
    """Price scales as floor_area ** elasticity between comparables."""
    min_floor_area_comps: int = 5
    repeat_weight_max: float = 0.9
    """Weight on the indexed previous sale when it happened this month."""
    repeat_weight_decay_years: float = 40.0
    """Set from a backtest of 2025 sales: the indexed previous sale beats
    comparables until it is ~20 years old."""
    new_build_repeat_penalty: float = 0.5
    """Multiplier on repeat-sale weight when the last sale was a new build."""
    fuzzy_threshold: int = 85
    use_epc: bool = True
    confidence_high_width: float = 0.40
    """Calibrated 80% interval width (as a share of the estimate) at or
    below which an estimate is 'high' confidence. With EPC data this makes
    'high' a repeat sale within 15 years, and 'medium' an older repeat sale or
    floor-area adjusted comparables."""
    confidence_medium_width: float = 0.50


def calibration_path(processed_dir: Path, use_epc: bool) -> Path:
    """Calibration file for a model configuration. Contains no addresses."""
    return Path(processed_dir) / (
        "calibration.json" if use_epc else "calibration_no_epc.json"
    )
