"""Command line interface.

download   fetch bulk datasets (the only command that uses the network)
build      build the local database from the downloads
estimate   value one address or a CSV of addresses, fully offline
backtest   measure accuracy on past sales, fully offline
calibrate  fit estimate ranges from a backtest, fully offline
"""

import argparse
import datetime as dt
import json
import logging
import sys
from pathlib import Path

from .config import DEFAULT_DATA_DIR, ModelParams, Paths, calibration_path

log = logging.getLogger("house_price_estimate")

# Output fields that could help identify a property: the method is
# deterministic on public data, so precise values can be matched against
# estimates for every address in an area. Dropped unless --keep-identifiable.
IDENTIFIABLE = (
    "postcode",
    "property_type",
    "floor_area",
    "n_sales",
    "last_sale_price",
    "last_sale_date",
    "last_sale_new_build",
    "comps_floor_area_adjusted",
    "hpi_series",
    "repeat_sale_est",
    "repeat_weight",
    "comps_est",
    "comps_n",
    "comps_radius_m",
    "ppd_match_score",
    "estimate",
)

# (upper limit, band width): £25k bands below £500k, £50k to £1m, £250k to
# £2m, £1m to £5m, then one open band.
BAND_STEPS = (
    (500_000, 25_000),
    (1_000_000, 50_000),
    (2_000_000, 250_000),
    (5_000_000, 1_000_000),
)


def price_band(x: float) -> tuple:
    """(lower, upper) edges of the band containing ``x``; upper is None above £5m."""
    start = 0
    for upper, step in BAND_STEPS:
        if x < upper:
            lower = start + (x - start) // step * step
            return int(lower), int(lower + step)
        start = upper
    return start, None


def _money(x) -> str:
    return f"£{x / 1e6:g}m" if x >= 1e6 else f"£{x / 1e3:g}k"


def band_label(lower: int, upper) -> str:
    return f"{_money(lower)}+" if upper is None else f"{_money(lower)}–{_money(upper)}"


def _output(v, keep_identifiable: bool) -> dict:
    """Output fields for one valuation.

    By default the estimate is replaced by its price band, and low/high are
    widened outwards to band edges.
    """
    d = v.as_dict()
    out = {}
    for k, x in d.items():
        if k == "estimate":
            lower, upper = price_band(x) if x is not None else (None, None)
            out["price_band"] = band_label(lower, upper) if x is not None else None
            out["price_band_low"], out["price_band_high"] = lower, upper
            # Midpoint as a single value for analysis; none for the open top band.
            out["price_band_mid"] = (lower + upper) // 2 if upper is not None else None
        if keep_identifiable or k not in IDENTIFIABLE:
            out[k] = x
    if not keep_identifiable and d["low"] is not None:
        out["low"] = price_band(d["low"])[0]
        lower, upper = price_band(d["high"])
        out["high"] = d["high"] if d["high"] == lower else upper
    return out


def _estimate(args, paths: Paths) -> None:
    import pandas as pd

    from .estimate import Estimator

    params = ModelParams(use_epc=not args.no_epc)
    est = Estimator(paths.db, params)
    if args.address:
        v = est.estimate_address(args.address)
        for k, val in _output(v, args.keep_identifiable).items():
            print(f"{k:28s} {val}")
        return

    df = pd.read_csv(args.input, dtype=str, keep_default_na=False)
    for col in [args.address_col, args.id_col, args.postcode_col]:
        if col and col not in df.columns:
            sys.exit(f"Column {col!r} not in {args.input}")
    rows = []
    for i, rec in enumerate(df.to_dict("records"), 1):
        address = rec[args.address_col]
        if args.postcode_col:
            address = f"{address}, {rec[args.postcode_col]}"
        v = est.estimate_address(address)
        out = {args.id_col: rec[args.id_col]} if args.id_col else {}
        if args.keep_address:
            out["address"] = address
        rows.append({**out, **_output(v, args.keep_identifiable)})
        if i % 100 == 0:
            log.info("estimated %d/%d", i, len(df))
    result = pd.DataFrame(rows)
    result.to_csv(args.out, index=False)
    counts = result["method"].value_counts().to_dict()
    log.info("Wrote %d estimates to %s (%s)", len(result), args.out, counts)


def _backtest(args, paths: Paths) -> None:
    import pandas as pd

    from .backtest import run_backtest, summarise
    from .estimate import Estimator

    est = Estimator(paths.db, ModelParams(use_epc=not args.no_epc))
    df = run_backtest(est, args.year, args.n, args.seed)
    if args.out:
        df.to_csv(args.out, index=False)
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        print(summarise(df).round(3).to_string())


def _calibrate(args, paths: Paths) -> None:
    from .backtest import calibrate
    from .estimate import Estimator

    params = ModelParams(use_epc=not args.no_epc)
    est = Estimator(paths.db, params, use_calibration=False)
    result = calibrate(est, args.year, args.n, args.seed)
    out = calibration_path(paths.processed, params.use_epc)
    out.write_text(json.dumps(result, indent=2) + "\n")
    for name, g in result["groups"].items():
        log.info(
            f"{name:22s} n={g['n']:5d}  80% range x{g['q10']:.2f}-x{g['q90']:.2f}  "
            f"median error {g['median_ape']:.1%}"
        )
    log.info("Wrote %s", out)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        prog="house_price_estimate",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("download", help="download bulk datasets")
    p.add_argument("--onspd-url", help="override the ONSPD zip URL")
    p.add_argument("--force", action="store_true", help="re-download existing files")

    p = sub.add_parser("build", help="build the local database")
    p.add_argument("--skip-epc", action="store_true")

    p = sub.add_parser("estimate", help="estimate current values (offline)")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--address", help="a single address including postcode")
    src.add_argument("--input", type=Path, help="CSV of addresses")
    p.add_argument("--address-col", default="address")
    p.add_argument("--postcode-col", help="optional separate postcode column")
    p.add_argument("--id-col", help="identifier column copied to the output")
    p.add_argument(
        "--keep-address",
        action="store_true",
        help="copy the address into the output (off by default)",
    )
    p.add_argument(
        "--keep-identifiable",
        action="store_true",
        help="include the exact estimate and range, and fields that could "
        "identify the property: "
        + ", ".join(IDENTIFIABLE)
        + " (off by default: the estimate is given as a price band)",
    )
    p.add_argument("--out", type=Path, help="output CSV (required with --input)")
    p.add_argument("--no-epc", action="store_true", help="ignore EPC floor areas")

    p = sub.add_parser("backtest", help="accuracy on held-out past sales (offline)")
    p.add_argument("--year", type=int, default=2025)
    p.add_argument("--n", type=int, default=2000)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--out", type=Path, help="optional per-sale results CSV")
    p.add_argument("--no-epc", action="store_true")

    p = sub.add_parser(
        "calibrate", help="fit estimate ranges from a backtest (offline)"
    )
    p.add_argument(
        "--year",
        type=int,
        default=dt.date.today().year - 2,
        help="year of sales to calibrate on (default: two years ago)",
    )
    p.add_argument("--n", type=int, default=10000)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--no-epc", action="store_true")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    paths = Paths(args.data_dir)

    if args.command == "download":
        from .download import download_all

        download_all(paths, args.onspd_url, args.force)
    elif args.command == "build":
        from .build import build_all

        build_all(paths, args.skip_epc)
    else:
        if args.command == "estimate" and args.input and not args.out:
            parser.error("--out is required with --input")
        from .offline import no_network

        run = {"estimate": _estimate, "backtest": _backtest, "calibrate": _calibrate}
        with no_network():
            run[args.command](args, paths)


if __name__ == "__main__":
    main()
