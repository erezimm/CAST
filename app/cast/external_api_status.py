import logging
import os
import sys
import threading
from datetime import datetime, timezone

import requests
from django.conf import settings
from django.core.cache import cache

from candidates.photometry_utils import ATLAS_BASEURL, LASAIR_ENDPOINT, get_lasair_api_token
from candidates.tns_utils import get_tns_config
from lasair.lasair import LasairError, lasair_client

logger = logging.getLogger(__name__)

EXTERNAL_API_STATUS_CACHE_KEY = "external_api_status:v1"
CHECK_TIMEOUT = 10

_startup_lock = threading.Lock()
_startup_started = False


def _tns_configured():
    tns_settings = settings.BROKERS.get("TNS", {})
    return all(tns_settings.get(key) for key in ("api_key", "bot_id", "bot_name"))


def _atlas_configured():
    atlas_settings = settings.BROKERS.get("ATLAS", {})
    return bool(atlas_settings.get("api_token"))


def _astro_colibri_configured():
    colibri_settings = settings.ASTRO_COLIBRI
    return all(colibri_settings.get(key) for key in ("api_url", "username", "password"))


def _utcnow_iso():
    return datetime.now(timezone.utc).isoformat()


def _base_status(name, configured, metadata=None):
    return {
        "name": name,
        "configured": configured,
        "auth_ok": None,
        "status_label": "not_configured" if not configured else "unknown",
        "message": "Service is not configured." if not configured else "Status not checked yet.",
        "checked_at": _utcnow_iso(),
        "metadata": metadata or {},
    }


def _mark_ok(status, message, metadata=None):
    status["auth_ok"] = True
    status["status_label"] = "ok"
    status["message"] = message
    if metadata:
        status["metadata"].update(metadata)
    return status


def _mark_failure(status, label, message, metadata=None):
    status["auth_ok"] = False
    status["status_label"] = label
    status["message"] = message
    if metadata:
        status["metadata"].update(metadata)
    return status


def check_tns_status():
    configured = _tns_configured()
    environment = "sandbox" if settings.TNS_TEST else "production"
    status = _base_status("TNS", configured, metadata={"environment": environment})
    if not configured:
        return status

    config = get_tns_config()
    endpoint = f"{config['url_api']}/get/search"
    payload = {
        "api_key": config["api_key"],
        "data": '{"ra":"0.0","dec":"0.0","radius":"1","units":"arcsec"}',
    }
    try:
        response = requests.post(
            endpoint,
            headers=config["headers"],
            data=payload,
            timeout=CHECK_TIMEOUT,
        )
        response.raise_for_status()
        response.json()
        return _mark_ok(status, "Authenticated successfully against TNS.")
    except requests.HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else None
        label = "auth_failed" if code in {400, 401, 403} else "network_error"
        return _mark_failure(status, label, f"TNS returned HTTP {code}.")
    except requests.RequestException as exc:
        return _mark_failure(status, "network_error", f"TNS request failed: {exc}")
    except ValueError as exc:
        return _mark_failure(status, "unknown", f"TNS returned invalid JSON: {exc}")


def check_lasair_status():
    token = get_lasair_api_token()
    configured = bool(token)
    status = _base_status("LASAIR", configured)
    if not configured:
        return status

    try:
        client = lasair_client(token, endpoint=LASAIR_ENDPOINT, timeout=CHECK_TIMEOUT)
        client.cone(ra=0.0, dec=0.0, radius=1.0, requestType="nearest")
        return _mark_ok(status, "Authenticated successfully against LASAIR.")
    except LasairError as exc:
        message = str(exc.message)
        lowered = message.lower()
        if "unauthorized" in lowered:
            label = "auth_failed"
        elif "limit" in lowered:
            label = "rate_limited"
        elif "timed out" in lowered:
            label = "network_error"
        else:
            label = "unknown"
        return _mark_failure(status, label, f"LASAIR check failed: {message}")
    except requests.RequestException as exc:
        return _mark_failure(status, "network_error", f"LASAIR request failed: {exc}")
    except Exception as exc:
        return _mark_failure(status, "unknown", f"LASAIR check failed: {exc}")


def check_atlas_status():
    atlas_settings = settings.BROKERS.get("ATLAS", {})
    configured = _atlas_configured()
    status = _base_status("ATLAS", configured)
    if not configured:
        return status

    token = atlas_settings["api_token"]
    headers = {"Authorization": f"Token {token}", "Accept": "application/json"}

    try:
        response = requests.get(f"{ATLAS_BASEURL}/queue/", headers=headers, timeout=CHECK_TIMEOUT)
        if response.status_code == 200:
            return _mark_ok(status, "Authenticated successfully against ATLAS.")
        if response.status_code in {401, 403}:
            return _mark_failure(status, "auth_failed", f"ATLAS returned HTTP {response.status_code}.")
        return _mark_failure(status, "network_error", f"ATLAS returned HTTP {response.status_code}.")
    except requests.RequestException as exc:
        return _mark_failure(status, "network_error", f"ATLAS request failed: {exc}")


def check_astro_colibri_status():
    colibri_settings = settings.ASTRO_COLIBRI
    configured = _astro_colibri_configured()
    status = _base_status("Astro-COLIBRI", configured, metadata={"check_mode": "best_effort"})
    if not configured:
        return status

    try:
        response = requests.get(
            colibri_settings["api_url"],
            auth=requests.auth.HTTPBasicAuth(
                colibri_settings["username"],
                colibri_settings["password"],
            ),
            timeout=CHECK_TIMEOUT,
        )
        if response.status_code < 400:
            return _mark_ok(
                status,
                "Authenticated or reached Astro-COLIBRI successfully (best effort check).",
            )
        if response.status_code in {401, 403}:
            return _mark_failure(status, "auth_failed", f"Astro-COLIBRI returned HTTP {response.status_code}.")
        if response.status_code < 500:
            return _mark_ok(
                status,
                f"Astro-COLIBRI responded with HTTP {response.status_code}; reachability confirmed (best effort check).",
            )
        return _mark_failure(
            status,
            "network_error",
            f"Astro-COLIBRI returned HTTP {response.status_code}.",
        )
    except requests.RequestException as exc:
        return _mark_failure(status, "network_error", f"Astro-COLIBRI request failed: {exc}")


def build_external_api_status_snapshot():
    services = [
        check_tns_status(),
        check_lasair_status(),
        check_atlas_status(),
        check_astro_colibri_status(),
    ]
    return {
        "refreshed_at": _utcnow_iso(),
        "services": services,
    }


def write_external_api_status_snapshot(snapshot):
    cache.set(EXTERNAL_API_STATUS_CACHE_KEY, snapshot, timeout=None)
    return snapshot


def refresh_external_api_status():
    snapshot = build_external_api_status_snapshot()
    return write_external_api_status_snapshot(snapshot)


def get_external_api_status_snapshot():
    return cache.get(EXTERNAL_API_STATUS_CACHE_KEY)


def get_external_api_service_status(service_name):
    snapshot = get_external_api_status_snapshot() or {}
    for service in snapshot.get("services", []):
        if service.get("name") == service_name:
            return service

    fallback_builders = {
        "TNS": lambda: _base_status(
            "TNS",
            _tns_configured(),
            metadata={"environment": "sandbox" if settings.TNS_TEST else "production"},
        ),
        "LASAIR": lambda: _base_status("LASAIR", bool(get_lasair_api_token())),
        "ATLAS": lambda: _base_status("ATLAS", _atlas_configured()),
        "Astro-COLIBRI": lambda: _base_status(
            "Astro-COLIBRI",
            _astro_colibri_configured(),
            metadata={"check_mode": "best_effort"},
        ),
    }
    builder = fallback_builders.get(service_name)
    return builder() if builder else None


def is_external_api_service_enabled(service_name):
    service = get_external_api_service_status(service_name)
    if not service:
        return False
    if not service.get("configured"):
        return False
    return service.get("auth_ok") is not False


def _run_startup_refresh():
    try:
        refresh_external_api_status()
    except Exception as exc:
        logger.warning("External API startup refresh failed: %s", exc)


def schedule_external_api_status_startup_refresh():
    global _startup_started
    if "test" in sys.argv or "migrate" in sys.argv or "collectstatic" in sys.argv:
        return
    if settings.DEBUG and os.environ.get("RUN_MAIN") not in {None, "true"}:
        return
    with _startup_lock:
        if _startup_started:
            return
        _startup_started = True
    thread = threading.Thread(
        target=_run_startup_refresh,
        name="external-api-status-startup",
        daemon=True,
    )
    thread.start()
