"""
Bulk downloads. This is the only module that uses the network, and it never
takes address input: every URL is a fixed constant from ``config``.
"""

import datetime as dt
import json
import logging
from pathlib import Path
import re
import shutil
from typing import Optional
import urllib.error
import urllib.request

from .config import (
    ARCGIS_ITEM_DATA_URL,
    EPC_BULK_URL,
    ONSPD_SEARCH_URL,
    PPD_URL,
    UKHPI_URL_TEMPLATE,
    Paths,
)

log = logging.getLogger(__name__)
USER_AGENT = "house-price-estimate/0.1 (bulk download)"
CHUNK = 1 << 20


def _open(url: str, headers: Optional[dict] = None):
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, **(headers or {})}
    )
    return urllib.request.urlopen(req, timeout=60)


def _remote_size(url: str) -> Optional[int]:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            size = r.headers.get("Content-Length")
            return int(size) if size else None
    except urllib.error.HTTPError:
        return None


def fetch(url: str, dest: Path, force: bool = False) -> Path:
    """Stream ``url`` to ``dest``, resuming a partial ``.part`` download."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    size = _remote_size(url)
    if dest.exists() and not force and (size is None or dest.stat().st_size == size):
        log.info("%s already downloaded", dest.name)
        return dest
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={have}-"} if have and size else {}
    with _open(url, headers) as r:
        mode = "ab" if headers and r.status == 206 else "wb"
        done = have if mode == "ab" else 0
        next_report = done + 200 * CHUNK
        with open(part, mode) as f:
            while chunk := r.read(CHUNK):
                f.write(chunk)
                done += len(chunk)
                if done >= next_report:
                    total = f" / {size / 1e6:,.0f}" if size else ""
                    log.info(f"{dest.name}: {done / 1e6:,.0f}{total} MB")
                    next_report += 200 * CHUNK
    if size is not None and part.stat().st_size != size:
        raise IOError(f"{dest.name}: expected {size} bytes, got {part.stat().st_size}")
    part.replace(dest)
    log.info("Saved %s", dest)
    return dest


def download_ppd(paths: Paths, force: bool = False) -> Path:
    return fetch(PPD_URL, paths.ppd_csv, force)


def download_hpi(paths: Paths, force: bool = False, months_back: int = 6) -> Path:
    """Download the most recent UK HPI full file (tries recent months)."""
    today = dt.date.today()
    for k in range(months_back + 1):
        y, m = divmod(today.year * 12 + today.month - 1 - k, 12)
        url = UKHPI_URL_TEMPLATE.format(year=y, month=m + 1)
        if _remote_size(url) is None:
            continue
        log.info("Using UK HPI file for %d-%02d", y, m + 1)
        fetch(url, paths.hpi_csv, force)
        (paths.raw / "ukhpi_source.txt").write_text(url + "\n")
        return paths.hpi_csv
    raise RuntimeError("No UK HPI full file found for recent months")


def latest_onspd_url() -> str:
    """Find the bulk zip for the latest ONSPD release in the ONS catalogue."""
    with _open(ONSPD_SEARCH_URL) as r:
        items = json.load(r).get("features", [])
    title_re = re.compile(r"^ONS Postcode Directory \(([A-Za-z]+) (\d{4})\)$")
    releases = []
    for item in items:
        props = item.get("properties", {})
        m = title_re.match(props.get("title", "").strip())
        if m and props.get("type") == "CSV Collection":
            month = dt.datetime.strptime(m.group(1)[:3], "%b").month
            releases.append(((int(m.group(2)), month), item["id"], props["title"]))
    if not releases:
        raise RuntimeError(
            "Could not find an ONSPD release; download the zip manually from "
            "https://geoportal.statistics.gov.uk and pass --onspd-url or put "
            "it in data/raw/onspd/"
        )
    _, item_id, title = max(releases)
    log.info("Latest ONSPD release: %s", title)
    return ARCGIS_ITEM_DATA_URL.format(item_id=item_id)


def download_onspd(
    paths: Paths, url: Optional[str] = None, force: bool = False
) -> Path:
    url = url or latest_onspd_url()
    return fetch(url, paths.onspd_dir / "onspd.zip", force)


def epc_instructions(paths: Paths) -> str:
    return (
        "EPC data (optional, improves accuracy) must be downloaded manually:\n"
        f"  1. Sign in at {EPC_BULK_URL} (free GOV.UK One Login account).\n"
        "  2. Download the domestic certificates bulk CSV/zip for England.\n"
        f"  3. Put the zip or CSV files in {paths.epc_dir}/ and re-run build.\n"
    )


def download_all(
    paths: Paths, onspd_url: Optional[str] = None, force: bool = False
) -> None:
    paths.raw.mkdir(parents=True, exist_ok=True)
    download_hpi(paths, force)
    download_onspd(paths, onspd_url, force)
    download_ppd(paths, force)
    paths.epc_dir.mkdir(parents=True, exist_ok=True)
    if not any(paths.epc_dir.iterdir()):
        print(epc_instructions(paths))
    free = shutil.disk_usage(paths.data_dir).free / 1e9
    log.info("Done. %.0f GB free in %s", free, paths.data_dir)
