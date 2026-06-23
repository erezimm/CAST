import json
import os
from dataclasses import dataclass

from astropy.time import Time
from django.utils import timezone
from datetime import timezone as dt_timezone, datetime

from django.utils.dateparse import parse_datetime


def ensure_aware_utc(dt):
    # Handle None
    if dt is None:
        return None

    # Handle astropy Time
    if isinstance(dt, Time):
        dt = dt.to_datetime(timezone=dt_timezone.utc)

    # Handle string
    if isinstance(dt, str):
        dt = dt.strip()
        # AT-report datetimes carry a trailing "UTC" label (e.g. "2024-09-01 12:34:56 UTC")
        # that parse_datetime cannot handle (it expects an ISO offset like +00:00 or Z).
        # Strip it and let the naive -> UTC step below attach the timezone.
        if dt.upper().endswith("UTC"):
            dt = dt[:-3].rstrip()
        dt = parse_datetime(dt)
        if dt is None:
            return None

    # Ensure it's a datetime
    if not isinstance(dt, datetime):
        raise TypeError(f"Unsupported datetime type: {type(dt)}")

    # Make aware if naive
    if timezone.is_naive(dt):
        dt = dt.replace(tzinfo=dt_timezone.utc)

    # Normalize to UTC
    return dt.astimezone(dt_timezone.utc)

@dataclass
class ParsedAlertPayload:
    ra: float
    dec: float
    discovery_datetime: datetime
    at_report: dict
    last_report: dict
    filename: str

def parse_json_file(file) -> ParsedAlertPayload:
    """
    Read, decode, and validate an alert JSON file.
    Returns a ParsedAlertPayload or raises ValueError.
    """
    try:
        file_content = file.read().decode("utf-8")
        data = json.loads(file_content)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON format: {e}")
    except Exception as e:
        raise ValueError(f"Error reading file: {e}")
    finally:
        file.seek(0)

    at_report = data.get("at_report", {})
    last_report = data.get("last_report", {})

    try:
        ra = float(at_report["RA"]["value"])
        dec = float(at_report["Dec"]["value"])
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"Missing or invalid RA/Dec in file {file.name}")

    try:
        discovery_datetime_raw = at_report["discovery_datetime"][0]
    except (KeyError, IndexError, TypeError):
        raise ValueError(f"Missing or invalid discovery_datetime in file {file.name}")
    discovery_datetime = ensure_aware_utc(discovery_datetime_raw)


    return ParsedAlertPayload(
        ra=ra,
        dec=dec,
        discovery_datetime=discovery_datetime,
        at_report=at_report,
        last_report=last_report,
        filename=os.path.basename(file.name),
    )