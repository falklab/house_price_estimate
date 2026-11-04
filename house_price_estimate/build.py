"""Convert raw bulk downloads into a single local DuckDB database.

Tables:
  onspd  postcode -> easting/northing, local authority, region, LSOA
  hpi    monthly UK HPI by area code and property type
  epc    latest EPC per address (optional)
  ppd    standard residential sales with location and linked floor area,
         clustered by grid cell so spatial queries scan few rows
"""

import csv
import logging
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

import duckdb

from .config import ENGLAND_CODE, GRID_SIZE_M, WALES_CODE, Paths

log = logging.getLogger(__name__)

PPD_COLUMNS = {
    "transaction_id": "VARCHAR",
    "price": "VARCHAR",
    "date": "VARCHAR",
    "postcode": "VARCHAR",
    "ptype": "VARCHAR",
    "new_build": "VARCHAR",
    "tenure": "VARCHAR",
    "paon": "VARCHAR",
    "saon": "VARCHAR",
    "street": "VARCHAR",
    "locality": "VARCHAR",
    "town": "VARCHAR",
    "district": "VARCHAR",
    "county": "VARCHAR",
    "category": "VARCHAR",
    "record_status": "VARCHAR",
}

# SQL equivalents of the address normalisation used for PPD <-> EPC linkage.
# The key is postcode plus the sorted house/flat numbers, or the normalised
# house name when there are no numbers.
MACROS = r"""
CREATE OR REPLACE MACRO norm_txt(s) AS
    trim(regexp_replace(upper(coalesce(s, '')), '[^A-Z0-9]+', ' ', 'g'));
CREATE OR REPLACE MACRO squash(s) AS
    regexp_replace(upper(coalesce(s, '')), '[^A-Z0-9]', '', 'g');
CREATE OR REPLACE MACRO norm_pc(s) AS
    CASE WHEN length(squash(s)) BETWEEN 5 AND 7
         THEN left(squash(s), length(squash(s)) - 3) || ' ' || right(squash(s), 3) END;
CREATE OR REPLACE MACRO num_key(s) AS
    array_to_string(
        list_sort(regexp_extract_all(norm_txt(s), '\b[0-9]+[A-Z]?\b')), ' ');
CREATE OR REPLACE MACRO addr_key(pc, num_src, name_src) AS
    CASE WHEN num_key(num_src) <> '' THEN pc || '|' || num_key(num_src)
         WHEN norm_txt(name_src) <> '' THEN pc || '|N|' || norm_txt(name_src) END;
"""


def _connect(db: Path) -> duckdb.DuckDBPyConnection:
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(db))
    conn.execute(f"SET temp_directory = '{db.parent / 'duckdb_tmp'}'")
    conn.execute("SET preserve_insertion_order = false")
    for stmt in MACROS.split(";"):
        if stmt.strip():
            conn.execute(stmt)
    return conn


def _sql_str(p: Path) -> str:
    return "'" + str(p).replace("'", "''") + "'"


def _csv_header(path: Path) -> list[str]:
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
        return next(csv.reader(f))


def _pick(header: list[str], *patterns: str) -> str | None:
    """First header column fully matching one of the regexes (case-insensitive)."""
    for pat in patterns:
        for col in header:
            if re.fullmatch(pat, col.strip(), flags=re.I):
                return col
    return None


def _quote(col: str) -> str:
    return '"' + col.replace('"', '""') + '"'


# --- ONSPD ------------------------------------------------------------------


def _onspd_csv(onspd_dir: Path, tmp: Path) -> Path:
    """Return the main ONSPD CSV, extracting it from the zip if needed."""
    csvs = sorted(onspd_dir.glob("*.csv"), key=lambda p: p.stat().st_size)
    if csvs:
        return csvs[-1]
    zips = sorted(onspd_dir.glob("*.zip"), key=lambda p: p.stat().st_mtime)
    if not zips:
        raise FileNotFoundError(f"No ONSPD zip or csv in {onspd_dir}")
    with zipfile.ZipFile(zips[-1]) as z:
        members = [
            m
            for m in z.infolist()
            if m.filename.lower().endswith(".csv")
            and "multi_csv" not in m.filename.lower()
        ]
        member = max(members, key=lambda m: m.file_size)
        log.info("Extracting %s", member.filename)
        out = tmp / Path(member.filename).name
        with z.open(member) as src, open(out, "wb") as dst:
            shutil.copyfileobj(src, dst, 1 << 20)
    return out


def build_onspd(conn, paths: Paths, tmp: Path) -> None:
    path = _onspd_csv(paths.onspd_dir, tmp)
    header = _csv_header(path)
    cols = {
        "postcode": _pick(header, "pcds", "pcd", "pcd7", "pcd8"),
        "east": _pick(header, "east1m", "oseast1m"),
        "north": _pick(header, "north1m", "osnrth1m"),
        "lad": _pick(header, "oslaua", r"lad\d\dcd"),
        "rgn": _pick(header, "rgn", r"rgn\d\dcd"),
        "ctry": _pick(header, "ctry", r"ctry\d\dcd"),
        "lsoa": _pick(header, r"lsoa21(cd)?", r"lsoa11(cd)?", "lsoa11"),
    }
    missing = [k for k, v in cols.items() if v is None]
    if missing:
        raise ValueError(f"ONSPD columns not found: {missing}")
    # Optional: county and 2021 Rural Urban Classification (for reporting).
    optional = {
        "cty": _pick(header, r"cty\d\dcd", "oscty"),
        "ruc21": _pick(header, "ruc21ind"),
    }
    q = {k: _quote(v) for k, v in cols.items()}
    extra = "".join(
        f", {_quote(v) if v else 'NULL::VARCHAR'} AS {k}" for k, v in optional.items()
    )
    log.info("Building onspd from %s", path.name)
    conn.execute(f"""
        CREATE OR REPLACE TABLE onspd AS
        SELECT norm_pc({q["postcode"]}) AS postcode,
               TRY_CAST({q["east"]} AS INTEGER) AS east,
               TRY_CAST({q["north"]} AS INTEGER) AS north,
               {q["lad"]} AS lad, {q["rgn"]} AS rgn, {q["lsoa"]} AS lsoa{extra}
        FROM read_csv({_sql_str(path)}, header = true, all_varchar = true)
        WHERE {q["ctry"]} IN ('{ENGLAND_CODE}', '{WALES_CODE}')
          AND TRY_CAST({q["east"]} AS INTEGER) > 0
          AND norm_pc({q["postcode"]}) IS NOT NULL
        ORDER BY postcode
    """)


# --- UK HPI -----------------------------------------------------------------


def build_hpi(conn, paths: Paths) -> None:
    header = _csv_header(paths.hpi_csv)
    want = {
        "idx_all": "Index",
        "idx_D": "DetachedIndex",
        "idx_S": "SemiDetachedIndex",
        "idx_T": "TerracedIndex",
        "idx_F": "FlatIndex",
    }
    select = []
    for alias, name in want.items():
        col = _pick(header, name)
        select.append(
            f"TRY_CAST({_quote(col)} AS DOUBLE) AS {alias}"
            if col
            else f"NULL::DOUBLE AS {alias}"
        )
    date_col, area_col = (
        _quote(_pick(header, "Date")),
        _quote(_pick(header, "AreaCode")),
    )
    log.info("Building hpi")
    conn.execute(f"""
        CREATE OR REPLACE TABLE hpi AS
        SELECT strptime({date_col}, '%d/%m/%Y')::DATE AS month,
               {area_col} AS area, {", ".join(select)}
        FROM read_csv({_sql_str(paths.hpi_csv)}, header = true, all_varchar = true)
        ORDER BY area, month
    """)


# --- EPC --------------------------------------------------------------------

EPC_FIELDS = {
    "address1": r"address1",
    "address2": r"address2",
    "address3": r"address3",
    "postcode": r"postcode",
    "property_type": r"property_type",
    "built_form": r"built_form",
    "floor_area": r"total_floor_area",
    "rooms": r"number_habitable_rooms",
    "lodgement": r"lodgement_date",
}


def _epc_sources(epc_dir: Path, tmp: Path):
    """Yield EPC certificate CSV paths, extracting zip members one at a time."""
    for path in sorted(epc_dir.rglob("*")):
        name = path.name.lower()
        if name.endswith(".csv") and "recommend" not in name:
            yield path
        elif name.endswith(".zip"):
            with zipfile.ZipFile(path) as z:
                for m in z.infolist():
                    n = m.filename.lower()
                    if n.endswith(".csv") and "recommend" not in n:
                        out = tmp / "epc_member.csv"
                        with z.open(m) as src, open(out, "wb") as dst:
                            shutil.copyfileobj(src, dst, 1 << 20)
                        yield out
                        out.unlink()


def build_epc(conn, paths: Paths, tmp: Path) -> bool:
    """Load EPC certificates if any were downloaded. Returns True if loaded."""
    conn.execute("DROP TABLE IF EXISTS epc_raw")
    loaded = 0
    if paths.epc_dir.exists():
        for src in _epc_sources(paths.epc_dir, tmp):
            header = _csv_header(src)
            cols = {k: _pick(header, pat) for k, pat in EPC_FIELDS.items()}
            if not cols["postcode"] or not cols["floor_area"]:
                continue
            q = {k: (_quote(v) if v else "NULL") for k, v in cols.items()}
            body = f"""
                SELECT norm_pc({q["postcode"]}) AS postcode,
                       {q["address1"]} AS address1, {q["address2"]} AS address2,
                       {q["address3"]} AS address3,
                       CASE
                         WHEN upper({q["property_type"]}) IN ('FLAT', 'MAISONETTE')
                           THEN 'F'
                         WHEN upper({q["property_type"]}) IN ('HOUSE', 'BUNGALOW') THEN
                           CASE
                             WHEN upper({q["built_form"]}) = 'DETACHED' THEN 'D'
                             WHEN upper({q["built_form"]}) = 'SEMI-DETACHED' THEN 'S'
                             WHEN upper({q["built_form"]}) LIKE '%TERRACE%' THEN 'T'
                           END
                       END AS ptype,
                       CASE
                         WHEN TRY_CAST({q["floor_area"]} AS DOUBLE)
                              BETWEEN 10 AND 1500
                           THEN TRY_CAST({q["floor_area"]} AS DOUBLE)
                       END AS floor_area,
                       TRY_CAST({q["rooms"]} AS INTEGER) AS rooms,
                       TRY_CAST(left({q["lodgement"]}, 10) AS DATE) AS lodgement_date
                FROM read_csv({_sql_str(src)}, header = true, all_varchar = true,
                              ignore_errors = true)
                WHERE norm_pc({q["postcode"]}) IS NOT NULL
            """
            if loaded == 0:
                conn.execute(f"CREATE TABLE epc_raw AS {body}")
            else:
                conn.execute(f"INSERT INTO epc_raw {body}")
            loaded += 1
    if not loaded:
        log.info(
            "No EPC data found in %s; floor areas will be unavailable", paths.epc_dir
        )
        conn.execute("DROP TABLE IF EXISTS epc")
        return False
    log.info("Loaded %d EPC file(s); keeping latest certificate per address", loaded)
    conn.execute("""
        CREATE OR REPLACE TABLE epc AS
        SELECT * EXCLUDE (rn) FROM (
            SELECT *, row_number() OVER (
                PARTITION BY postcode,
                             norm_txt(concat_ws(' ', address1, address2, address3))
                ORDER BY lodgement_date DESC NULLS LAST) AS rn
            FROM epc_raw)
        WHERE rn = 1
        ORDER BY postcode
    """)
    conn.execute("DROP TABLE epc_raw")
    return True


# --- PPD --------------------------------------------------------------------


def build_ppd(conn, paths: Paths, with_epc: bool) -> None:
    columns = ", ".join(f"'{k}': '{v}'" for k, v in PPD_COLUMNS.items())
    if with_epc:
        # Floor area per address key; dropped when the key is ambiguous
        # (several addresses with materially different floor areas).
        conn.execute("""
            CREATE OR REPLACE TEMP TABLE epc_link AS
            SELECT key, arg_max(floor_area, lodgement_date) AS floor_area
            FROM (SELECT addr_key(postcode,
                                  concat_ws(' ', address1, address2, address3),
                                  address1) AS key,
                         norm_txt(concat_ws(' ', address1, address2, address3)) AS addr,
                         floor_area, lodgement_date
                  FROM epc WHERE floor_area IS NOT NULL)
            WHERE key IS NOT NULL
            GROUP BY key
            HAVING count(DISTINCT addr) = 1 OR max(floor_area) <= 1.15 * min(floor_area)
        """)
    else:
        conn.execute(
            "CREATE OR REPLACE TEMP TABLE epc_link (key VARCHAR, floor_area DOUBLE)"
        )
    log.info("Building ppd (this takes a while)")
    conn.execute(f"""
        CREATE OR REPLACE TABLE ppd AS
        WITH p AS (
            SELECT TRY_CAST(price AS INTEGER) AS price,
                   TRY_CAST(left(date, 10) AS DATE) AS date,
                   norm_pc(postcode) AS postcode, ptype,
                   coalesce(new_build = 'Y', false) AS new_build,
                   tenure, paon, saon, street, locality, town,
                   addr_key(norm_pc(postcode), concat_ws(' ', saon, paon),
                            concat_ws(' ', saon, paon)) AS key
            FROM read_csv({_sql_str(paths.ppd_csv)}, header = false,
                          columns = {{{columns}}}, quote = '"')
            WHERE category = 'A' AND ptype IN ('D', 'S', 'T', 'F')
              AND TRY_CAST(price AS INTEGER) >= 10000
        ),
        unique_keys AS (
            SELECT key FROM p WHERE key IS NOT NULL GROUP BY key
            HAVING count(DISTINCT concat_ws('|', norm_txt(saon), norm_txt(paon),
                                            norm_txt(street))) = 1
        )
        SELECT p.* EXCLUDE (key), o.east, o.north, o.lad, o.rgn, o.lsoa,
               (o.east // {GRID_SIZE_M})::INTEGER AS gx,
               (o.north // {GRID_SIZE_M})::INTEGER AS gy,
               CASE WHEN u.key IS NOT NULL THEN l.floor_area END AS floor_area
        FROM p
        JOIN onspd o USING (postcode)
        LEFT JOIN unique_keys u ON u.key = p.key
        LEFT JOIN epc_link l ON l.key = p.key
        ORDER BY gx, gy, postcode, date
    """)


def build_all(paths: Paths, skip_epc: bool = False) -> None:
    for required in (paths.ppd_csv, paths.hpi_csv):
        if not required.exists():
            raise FileNotFoundError(f"{required} missing; run the download step first")
    tmp_db = paths.db.with_suffix(".building")
    tmp_db.unlink(missing_ok=True)
    conn = _connect(tmp_db)
    with tempfile.TemporaryDirectory(dir=paths.processed) as tmp:
        tmp = Path(tmp)
        build_onspd(conn, paths, tmp)
        build_hpi(conn, paths)
        with_epc = False if skip_epc else build_epc(conn, paths, tmp)
        build_ppd(conn, paths, with_epc)
    counts = {
        t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        for t in ["onspd", "hpi", "ppd"] + (["epc"] if with_epc else [])
    }
    linked = conn.execute("SELECT count(floor_area) FROM ppd").fetchone()[0]
    conn.close()
    tmp_db.replace(paths.db)
    shutil.rmtree(paths.processed / "duckdb_tmp", ignore_errors=True)
    for t, n in counts.items():
        log.info(f"{t}: {n:,} rows")
    log.info(f"ppd rows with EPC floor area: {linked:,}")
    log.info("Database written to %s", paths.db)
