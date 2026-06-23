from __future__ import annotations

import csv
import logging
import math
import shutil
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from django.conf import settings
from django.db.models import Q

from candidates.models import Candidate

logger = logging.getLogger(__name__)

_DOWNLOAD_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class DownloadSummary:
    url: str
    zip_path: str
    csv_path: str
    bytes_downloaded: int


@dataclass(frozen=True)
class MatchSummary:
    csv_path: str
    candidates_considered: int
    candidates_matched: int
    candidates_updated: int
    rows_scanned: int
    dry_run: bool


def _tns_public_config() -> dict[str, Any]:
    return getattr(settings, "TNS_PUBLIC_OBJECTS", {})


def _config_value(name: str, override: Any) -> Any:
    if override is not None:
        return override
    return _tns_public_config().get(name)


def download_tns_public_objects_catalog(
    *,
    url: str | None = None,
    user_agent: str | None = None,
    zip_path: str | None = None,
    csv_path: str | None = None,
    timeout_seconds: int = 300,
) -> DownloadSummary:
    configured_url = _config_value("url", url)
    configured_user_agent = _config_value("user_agent", user_agent)
    configured_zip_path = Path(_config_value("zip_path", zip_path))
    configured_csv_path = Path(_config_value("csv_path", csv_path))

    if not configured_url:
        raise ValueError("TNS public catalog URL is not configured.")
    if not configured_user_agent:
        raise ValueError("TNS public catalog User-Agent marker is not configured.")

    configured_zip_path.parent.mkdir(parents=True, exist_ok=True)
    configured_csv_path.parent.mkdir(parents=True, exist_ok=True)

    temp_zip_path = configured_zip_path.with_name(configured_zip_path.name + ".tmp")
    temp_csv_path = configured_csv_path.with_name(configured_csv_path.name + ".tmp")

    headers = {"User-Agent": configured_user_agent}
    bytes_downloaded = 0

    with requests.get(
        configured_url,
        headers=headers,
        stream=True,
        timeout=timeout_seconds,
    ) as response:
        response.raise_for_status()
        with temp_zip_path.open("wb") as out_file:
            for chunk in response.iter_content(chunk_size=_DOWNLOAD_CHUNK_SIZE):
                if not chunk:
                    continue
                out_file.write(chunk)
                bytes_downloaded += len(chunk)

    temp_zip_path.replace(configured_zip_path)

    with zipfile.ZipFile(configured_zip_path, "r") as archive:
        csv_members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if not csv_members:
            raise ValueError(
                f"No CSV file found in downloaded archive: {configured_zip_path}"
            )
        with archive.open(csv_members[0], "r") as source, temp_csv_path.open("wb") as destination:
            shutil.copyfileobj(source, destination, length=_DOWNLOAD_CHUNK_SIZE)

    temp_csv_path.replace(configured_csv_path)

    return DownloadSummary(
        url=configured_url,
        zip_path=str(configured_zip_path),
        csv_path=str(configured_csv_path),
        bytes_downloaded=bytes_downloaded,
    )


def _normalize_header(header: str) -> str:
    return "".join(ch for ch in header.lower() if ch.isalnum())


def _resolve_catalog_columns(fieldnames: list[str]) -> tuple[str, str, str, str | None]:
    normalized = {_normalize_header(name): name for name in fieldnames if name}

    def pick(*aliases: str) -> str | None:
        for alias in aliases:
            if alias in normalized:
                return normalized[alias]
        return None

    ra_col = pick("ra", "radeg", "radegree", "raj2000", "radegrees")
    dec_col = pick("dec", "declination", "decl", "decdeg", "decd", "decdegrees", "dej2000")
    objname_col = pick("objname", "name", "iauname", "tnsname")
    prefix_col = pick("nameprefix", "prefix", "objprefix")

    if not ra_col or not dec_col or not objname_col:
        raise ValueError(
            "Unable to identify required columns in TNS public CSV. "
            f"Fieldnames: {fieldnames}"
        )

    return ra_col, dec_col, objname_col, prefix_col


def _find_catalog_header_and_reader(
    csv_file,
) -> tuple[csv.DictReader, list[str], tuple[str, str, str, str | None]]:
    """
    Locate the actual catalog header row, skipping metadata/preamble lines.

    The TNS public CSV may start with one or more non-CSV-header lines (e.g. a
    timestamp). This function scans until it finds a row that contains the
    required coordinate/name columns and then returns a DictReader starting from
    the next line.
    """
    delimiters = [",", "\t", ";", "|"]
    header_line_index: int | None = None
    header_row: list[str] | None = None
    header_columns: tuple[str, str, str, str | None] | None = None
    header_delimiter: str | None = None

    for line_index, raw_line in enumerate(csv_file):
        if not raw_line.strip():
            continue

        for delimiter in delimiters:
            row = next(csv.reader([raw_line], delimiter=delimiter))
            if not row:
                continue
            try:
                columns = _resolve_catalog_columns(row)
            except ValueError:
                continue
            header_line_index = line_index
            header_row = row
            header_columns = columns
            header_delimiter = delimiter
            break

        if header_row is not None:
            break

    if header_row is None or header_columns is None or header_delimiter is None or header_line_index is None:
        raise ValueError("Unable to locate a valid catalog header row in the TNS public CSV.")

    csv_file.seek(0)
    for _ in range(header_line_index + 1):
        next(csv_file, None)

    dict_reader = csv.DictReader(csv_file, fieldnames=header_row, delimiter=header_delimiter)
    return dict_reader, header_row, header_columns



def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, float):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _clean_objname(value: str, prefix: str | None = None) -> str:
    objname = value.strip()
    if not objname:
        return ""
    if prefix:
        normalized_prefix = prefix.strip()
        if normalized_prefix and objname.upper().startswith((normalized_prefix + " ").upper()):
            objname = objname[len(normalized_prefix) + 1 :].strip()
    return objname


def _angular_separation_arcsec(ra1_deg: float, dec1_deg: float, ra2_deg: float, dec2_deg: float) -> float:
    ra1_rad = math.radians(ra1_deg)
    dec1_rad = math.radians(dec1_deg)
    ra2_rad = math.radians(ra2_deg)
    dec2_rad = math.radians(dec2_deg)

    delta_ra = ra2_rad - ra1_rad
    delta_dec = dec2_rad - dec1_rad

    sin_delta_dec = math.sin(delta_dec / 2.0)
    sin_delta_ra = math.sin(delta_ra / 2.0)
    a = sin_delta_dec**2 + math.cos(dec1_rad) * math.cos(dec2_rad) * sin_delta_ra**2
    c = 2.0 * math.asin(min(1.0, math.sqrt(a)))
    return math.degrees(c) * 3600.0


def _cell_key(
    ra_deg: float,
    dec_deg: float,
    cell_size_deg: float,
    ra_bins: int,
    dec_bins: int,
) -> tuple[int, int]:
    ra_norm = ra_deg % 360.0
    dec_clamped = max(-90.0, min(90.0, dec_deg))

    ra_bin = int(ra_norm / cell_size_deg) % ra_bins
    dec_bin = int((dec_clamped + 90.0) / cell_size_deg)
    if dec_bin >= dec_bins:
        dec_bin = dec_bins - 1
    return ra_bin, dec_bin


def match_candidates_to_tns_catalog(
    *,
    csv_path: str | None = None,
    radius_arcsec: float | None = None,
    overwrite_existing: bool = False,
    limit_candidates: int | None = None,
    dry_run: bool = False,
) -> MatchSummary:
    configured_csv_path = Path(_config_value("csv_path", csv_path))
    configured_radius_arcsec = float(
        _config_value("match_radius_arcsec", radius_arcsec) or 3.0
    )

    if configured_radius_arcsec <= 0:
        raise ValueError("Match radius must be positive.")
    if not configured_csv_path.exists():
        raise FileNotFoundError(f"TNS public CSV not found: {configured_csv_path}")

    queryset = Candidate.objects.exclude(ra__isnull=True).exclude(dec__isnull=True).order_by("id")
    if not overwrite_existing:
        queryset = queryset.filter(Q(tns_name__isnull=True) | Q(tns_name=""))
    if limit_candidates:
        queryset = queryset[:limit_candidates]

    candidates = list(queryset.only("id", "ra", "dec", "tns_name"))
    if not candidates:
        return MatchSummary(
            csv_path=str(configured_csv_path),
            candidates_considered=0,
            candidates_matched=0,
            candidates_updated=0,
            rows_scanned=0,
            dry_run=dry_run,
        )

    radius_deg = configured_radius_arcsec / 3600.0
    cell_size_deg = max(radius_deg * 5.0, 0.1)
    ra_bins = max(1, int(math.ceil(360.0 / cell_size_deg)))
    dec_bins = max(1, int(math.ceil(180.0 / cell_size_deg)))

    candidate_bins: dict[tuple[int, int], list[int]] = defaultdict(list)
    for idx, candidate in enumerate(candidates):
        candidate_bins[
            _cell_key(candidate.ra, candidate.dec, cell_size_deg, ra_bins, dec_bins)
        ].append(idx)

    best_match_by_candidate: dict[int, tuple[float, str]] = {}
    rows_scanned = 0

    with configured_csv_path.open("r", encoding="utf-8", errors="replace", newline="") as csv_file:
        reader, header_row, columns = _find_catalog_header_and_reader(csv_file)
        ra_col, dec_col, objname_col, prefix_col = columns

        for row in reader:
            rows_scanned += 1

            objname_raw = (row.get(objname_col) or "").strip()
            if not objname_raw:
                continue
            prefix_raw = (row.get(prefix_col) or "").strip() if prefix_col else None
            objname = _clean_objname(objname_raw, prefix=prefix_raw)
            if not objname:
                continue

            ra = _to_float(row.get(ra_col))
            dec = _to_float(row.get(dec_col))
            if ra is None or dec is None:
                continue

            row_ra_bin, row_dec_bin = _cell_key(ra, dec, cell_size_deg, ra_bins, dec_bins)

            for delta_ra_bin in (-1, 0, 1):
                scan_ra_bin = (row_ra_bin + delta_ra_bin) % ra_bins
                for delta_dec_bin in (-1, 0, 1):
                    scan_dec_bin = row_dec_bin + delta_dec_bin
                    if scan_dec_bin < 0 or scan_dec_bin >= dec_bins:
                        continue
                    for idx in candidate_bins.get((scan_ra_bin, scan_dec_bin), []):
                        candidate = candidates[idx]
                        separation = _angular_separation_arcsec(candidate.ra, candidate.dec, ra, dec)
                        if separation > configured_radius_arcsec:
                            continue
                        current_best = best_match_by_candidate.get(candidate.id)
                        if current_best is None or separation < current_best[0]:
                            best_match_by_candidate[candidate.id] = (separation, objname)

    logger.info(
        "TNS matcher header columns resolved from row: %s",
        header_row,
    )

    candidates_to_update: list[Candidate] = []
    for candidate in candidates:
        match = best_match_by_candidate.get(candidate.id)
        if not match:
            continue
        matched_objname = match[1]
        if candidate.tns_name == matched_objname:
            continue
        candidate.tns_name = matched_objname
        candidates_to_update.append(candidate)

    if candidates_to_update and not dry_run:
        Candidate.objects.bulk_update(candidates_to_update, ["tns_name"], batch_size=1000)

    return MatchSummary(
        csv_path=str(configured_csv_path),
        candidates_considered=len(candidates),
        candidates_matched=len(best_match_by_candidate),
        candidates_updated=len(candidates_to_update),
        rows_scanned=rows_scanned,
        dry_run=dry_run,
    )
