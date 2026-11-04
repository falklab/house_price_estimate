# CLAUDE.md

Guidance for AI agents (and people) working on house_price_estimate. Workflow details for humans are in
[CONTRIBUTING.md](CONTRIBUTING.md).

## Project
house_price_estimate estimates the current market value of residential addresses in England, entirely on
the local machine. `download` fetches bulk open datasets (HM Land Registry Price Paid Data, the UK House
Price Index, the ONS Postcode Directory, and optionally EPC certificates placed by hand in `data/raw/epc/`);
`build` turns them into one DuckDB database (`data/processed/hpe.duckdb`); `estimate` values one address
or a CSV of addresses; `backtest` and `calibrate` measure accuracy on past sales and fit the estimate ranges.
The data lives in `data/` (tens of GB, gitignored), or wherever `--data-dir` / `HPE_DATA_DIR` points.

**Privacy is the core constraint** (see the README's Privacy section). Addresses must never leave the
machine:
- Only `download.py` uses the network, and only with the fixed URLs in `config.py`. It never takes address
  input. Don't add network access anywhere else.
- `estimate`, `backtest` and `calibrate` run inside `offline.no_network()`, and `Estimator` opens DuckDB
  read-only and calls `offline.lock_down_duckdb`. Keep both in place for any new code path that handles
  addresses.
- Never log addresses. Output keeps the fields in `cli.IDENTIFIABLE` out unless `--keep-identifiable` is
  passed, and gives the estimate as a price band.
- Never commit data: `data/`, `*.csv`, `*.tsv`, `*.parquet` and `*.duckdb` are gitignored and may hold
  personal addresses.

Domain terms used throughout the code:
- **PPD**: HM Land Registry Price Paid Data, the sale history; the `ppd` table. **PAON**/**SAON** are its
  primary/secondary addressable object names (house number or name, and flat).
- **HPI**: the UK House Price Index, used to bring past prices up to the valuation month (`hpi.py`).
- **ONSPD**: the ONS Postcode Directory: postcode to grid reference, local authority, region, LSOA.
- **EPC**: Energy Performance Certificates, the source of floor area and property type.
- **Repeat sale**: the property's own last sale, indexed with the HPI.
- **Comparables** (`comps_*`): nearby recent sales of the same type, weighted by distance and recency,
  and adjusted for floor area when EPC data is available.
- **Calibration** / **calibration group**: percentiles of actual price / estimate from a backtest, per
  group of similar evidence, that set the `low`/`high` range and the confidence.
- **Price band**: the banded estimate shown by default instead of the exact value (`cli.price_band`).

## Commands
```
uv sync                                   # install env from uv.lock
uv sync --group notebook                  # also the packages for notebooks/performance.ipynb
uv run pytest                             # tests
uv run pytest --cov                       # tests with coverage
uv run ruff check --fix . && uv run ruff format .
uv run house-price-estimate --help        # the CLI (same as python -m house_price_estimate)
uv add <pkg> / uv add --group dev <pkg>   # dependencies (never edit uv.lock by hand)
```
Before declaring a change done, run `uv run ruff check .`, `uv run ruff format --check .` and
`uv run pytest`. All must pass.

## Layout and architecture
- `house_price_estimate/cli.py`: argparse CLI (`download`, `build`, `estimate`, `backtest`, `calibrate`)
  and output shaping (price bands, dropping identifiable fields).
- `house_price_estimate/config.py`: `Paths` (data locations), download URLs, `ModelParams` (model tuning,
  a frozen dataclass) and the calibration file path.
- `house_price_estimate/download.py`: bulk downloads; the only module that uses the network.
- `house_price_estimate/build.py`: raw downloads to the DuckDB tables `onspd`, `hpi`, `epc` and `ppd`,
  mostly in SQL (`build_all` is the entry point).
- `house_price_estimate/address.py`: address and postcode parsing and normalisation; pure functions.
- `house_price_estimate/match.py`: matches a parsed address against the PPD/EPC records in its postcode.
- `house_price_estimate/hpi.py`: `HousePriceIndex`, index factors with area and property-type fallbacks.
- `house_price_estimate/estimate.py`: `Estimator` and `Valuation`; blends the repeat-sale and comparables
  estimates and applies the calibration.
- `house_price_estimate/backtest.py`: values past sales using only earlier data; `calibrate` fits the
  ranges from a backtest.
- `house_price_estimate/offline.py`: `no_network` and `lock_down_duckdb`.
- `notebooks/performance.ipynb`: accuracy report. Excluded from ruff; only change its code together with
  re-running it.

The CLI subcommands are thin: `cli.py` parses arguments, builds `Paths`/`ModelParams` and calls the
module functions (`build.build_all`, `Estimator.estimate_address`, `backtest.run_backtest`,
`backtest.calibrate`), running the offline commands inside `no_network()`. `estimate.py` is the best
example of the current conventions.

## Code style
ruff enforces formatting and linting (`pyproject.toml`). The formatter output is authoritative. Beyond what
ruff checks:

- **Layout**: 88 character lines, double quotes, 4-space indentation. Two blank lines between
  top-level definitions.
- **Imports**: inside the package, modules import each other relatively (`from .config import Paths`);
  tests and notebooks import `house_price_estimate.<module>`. Use `pathlib` for paths, and `Paths` for
  anything under the data directory.
- **Type hints** on function arguments and return values. Use built-in generics and `|` unions
  (`list[str]`, `Path | None`), not `typing.List` or `Optional`.
- **Docstrings**: Google convention as configured in ruff. The summary is one line ending with a period, on
  the same line as the opening `"""`, followed by prose; refer to code names in ``double backticks``.
  The exception is `cli.py`'s module docstring, which is also the `--help` text.
- **Naming**: `snake_case` functions and variables, `CapWords` classes, `UPPER_SNAKE` constants. Private
  module helpers use one leading underscore.
- **Logging**: use the module logger, `log = logging.getLogger(__name__)`, with lazy `%s` arguments, not
  f-strings. `print` is only for CLI output.
- **Errors**: raise a specific built-in (`FileNotFoundError`, `ValueError`, `RuntimeError`, ...) with a
  clear message that says what to do (e.g. "run the build step first"). Don't raise bare `Exception`.
- **Comments**: sparse, full-line, explaining why. Don't leave commented-out code.

## Tests
- pytest functions (no classes) in `tests/test_<area>.py`: `test_address.py`, `test_estimate.py`,
  `test_offline.py`. When a module's tests aren't in `tests/test_<module>.py`, keep `MODULE_TESTS` in
  `scripts/pytest_changed.py` up to date so the pre-commit hook runs them.
- The session fixture `paths` in `tests/conftest.py` builds a synthetic dataset in the real raw file
  formats and a database from it. Use it (and extend its data) instead of the real downloads.
- Fixtures use `@pytest.fixture`. Prefer small in-memory data. Write outputs to `tmp_path`.
- Never hard-code absolute or machine-specific paths. Use the `data_dir`, `expected_dir` and `local_data_dir`
  fixtures from `tests/conftest.py`.
- Mark tests needing large local data `@pytest.mark.local_data`, and taking more than a couple of seconds
  `@pytest.mark.slow`. Unmarked tests must run anywhere, quickly.
- Every test must assert something.

## Git and releases
- Commit messages and PR titles use Conventional Commits (`feat(scope): ...`, `fix: ...`, `test: ...`,
  `docs: ...`, `refactor: ...`, `build: ...`, `ci: ...`, `chore: ...`). Use `!` for breaking changes.
- Work on a branch, never directly on `main`.
- Don't change the version in `pyproject.toml`, `CHANGELOG.md` or `.release-please-manifest.json`;
  release-please manages them.
