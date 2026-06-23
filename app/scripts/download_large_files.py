from __future__ import annotations

import argparse
import fnmatch
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

import requests
from requests.exceptions import RequestException

CHUNK_SIZE = 1024 * 1024
PROGRESS_UPDATE_INTERVAL_SECONDS = 1.0
DEFAULT_DOWNLOAD_RETRIES = 3
DEFAULT_DOWNLOAD_RETRY_DELAY_SECONDS = 5.0


def _expand(value: Any) -> Any:
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, list):
        return [_expand(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand(item) for key, item in value.items()}
    return value


def _load_manifest(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        print(f"Manifest not found at {path}; nothing to download.")
        return []

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Large files manifest must contain a JSON array.")

    expanded = _expand(data)
    for index, entry in enumerate(expanded, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"Manifest entry #{index} must be a JSON object.")
    return expanded


def _format_mb(num_bytes: int) -> str:
    return f"{num_bytes / (1024 * 1024):.1f} MB"


def _print_progress(name: str, downloaded: int, total: int | None) -> None:
    if total and total > 0:
        percent = downloaded / total * 100
        print(
            f"[progress] {name}: {_format_mb(downloaded)} / {_format_mb(total)} ({percent:.1f}%)",
            flush=True,
        )
        return

    print(f"[progress] {name}: {_format_mb(downloaded)} downloaded", flush=True)


def _download_file(
    name: str,
    url: str,
    destination: Path,
    headers: dict[str, str],
    timeout_seconds: int,
    retry_count: int,
    retry_delay_seconds: float,
) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = destination.with_name(destination.name + ".tmp")
    last_error: Exception | None = None

    for attempt in range(1, retry_count + 1):
        existing_bytes = temp_path.stat().st_size if temp_path.exists() else 0
        bytes_downloaded = existing_bytes
        request_headers = dict(headers)
        file_mode = "ab" if existing_bytes > 0 else "wb"

        if existing_bytes > 0:
            request_headers["Range"] = f"bytes={existing_bytes}-"
            print(
                f"[resume] {name}: resuming from {_format_mb(existing_bytes)}",
                flush=True,
            )

        try:
            with requests.get(url, headers=request_headers, stream=True, timeout=timeout_seconds) as response:
                response.raise_for_status()
                if existing_bytes > 0 and response.status_code == 200:
                    print(
                        f"[restart] {name}: server did not honor Range request, restarting from zero",
                        flush=True,
                    )
                    temp_path.unlink(missing_ok=True)
                    existing_bytes = 0
                    bytes_downloaded = 0
                    file_mode = "wb"

                content_length = int(response.headers.get("Content-Length", "0")) or 0
                total_bytes = (
                    existing_bytes + content_length
                    if response.status_code == 206 and content_length > 0
                    else content_length or None
                )
                last_progress_at = time.monotonic()
                with temp_path.open(file_mode) as output_file:
                    for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                        if not chunk:
                            continue
                        output_file.write(chunk)
                        bytes_downloaded += len(chunk)
                        now = time.monotonic()
                        if now - last_progress_at >= PROGRESS_UPDATE_INTERVAL_SECONDS:
                            _print_progress(name, bytes_downloaded, total_bytes)
                            last_progress_at = now

            _print_progress(name, bytes_downloaded, total_bytes)

            print(f"[finalizing] {temp_path} -> {destination}", flush=True)
            temp_path.replace(destination)
            print(f"[finalized] {destination}", flush=True)
            return bytes_downloaded
        except (RequestException, OSError) as exc:
            last_error = exc
            print(
                f"[retry] {name}: attempt {attempt}/{retry_count} failed: {exc}",
                flush=True,
            )
            if attempt == retry_count:
                break
            time.sleep(retry_delay_seconds)

    raise RuntimeError(f"Download failed after {retry_count} attempts: {last_error}")


def _pick_zip_member(archive: zipfile.ZipFile, pattern: str | None) -> str:
    members = [name for name in archive.namelist() if not name.endswith("/")]
    if not members:
        raise ValueError("ZIP archive is empty.")
    if pattern is None:
        if len(members) > 1:
            raise ValueError("ZIP archive contains multiple files; set extract.member_glob.")
        return members[0]

    matches = [name for name in members if fnmatch.fnmatch(name, pattern)]
    if not matches:
        raise ValueError(f"No ZIP member matched pattern {pattern!r}.")
    if len(matches) > 1:
        raise ValueError(f"ZIP member pattern {pattern!r} matched multiple files: {matches}")
    return matches[0]


def _extract_zip(download_path: Path, extract_config: dict[str, Any]) -> None:
    destination = Path(extract_config["path"])
    member_glob = extract_config.get("member_glob")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = destination.with_name(destination.name + ".tmp")

    if temp_path.exists():
        print(f"[cleanup] removing stale temp file {temp_path}", flush=True)
        temp_path.unlink()

    with zipfile.ZipFile(download_path, "r") as archive:
        member_name = _pick_zip_member(archive, member_glob)
        with archive.open(member_name, "r") as source, temp_path.open("wb") as output_file:
            shutil.copyfileobj(source, output_file, length=CHUNK_SIZE)

    print(f"[finalizing] {temp_path} -> {destination}", flush=True)
    temp_path.replace(destination)
    print(f"[finalized] {destination}", flush=True)


def _should_skip(entry: dict[str, Any], download_path: Path, extract_path: Path | None) -> bool:
    if not entry.get("skip_if_exists", True):
        return False
    if not download_path.exists():
        return False
    if extract_path is not None and not extract_path.exists():
        return False
    return True


def _coerce_paths(paths: Any) -> list[Path]:
    if paths is None:
        return []
    if isinstance(paths, str):
        return [Path(paths)]
    if isinstance(paths, list):
        return [Path(item) for item in paths]
    raise ValueError("'paths' must be a string or list of strings.")


def _should_skip_command(entry: dict[str, Any]) -> bool:
    if not entry.get("skip_if_exists", True):
        return False
    output_paths = _coerce_paths(entry.get("paths"))
    if not output_paths:
        return False
    return all(path.exists() for path in output_paths)


def _run_command(name: str, command: list[str]) -> None:
    print(f"[start] {name} -> {' '.join(command)}", flush=True)
    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"Command exited with status {completed.returncode}")
    print(f"[completed] {name}", flush=True)


def _validate_entry(entry: dict[str, Any], index: int) -> None:
    entry_type = entry.get("type", "download")

    if entry_type == "download":
        for field in ("url", "path"):
            if not entry.get(field):
                raise ValueError(f"Manifest entry #{index} is missing required field: {field}")

        extract = entry.get("extract")
        if extract is None:
            return
        if not isinstance(extract, dict):
            raise ValueError(f"Manifest entry #{index} field 'extract' must be an object.")
        if extract.get("format", "zip") != "zip":
            raise ValueError(f"Manifest entry #{index} has unsupported extract format: {extract.get('format')}")
        if not extract.get("path"):
            raise ValueError(f"Manifest entry #{index} extract.path is required.")
        return

    if entry_type == "command":
        command = entry.get("command")
        if not isinstance(command, list) or not command or not all(isinstance(item, str) for item in command):
            raise ValueError(f"Manifest entry #{index} field 'command' must be a non-empty list of strings.")
        _coerce_paths(entry.get("paths"))
        return

    raise ValueError(f"Manifest entry #{index} has unsupported type: {entry_type}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Download configured large files.")
    parser.add_argument(
        "--manifest",
        default=os.environ.get("LARGE_FILES_MANIFEST_PATH", "/app/large_files_manifest.json"),
        help="Path to the JSON manifest that describes large files to download.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=int(os.environ.get("LARGE_FILES_DOWNLOAD_TIMEOUT_SECONDS", "300")),
        help="HTTP timeout for each download request.",
    )
    parser.add_argument(
        "--retry-count",
        type=int,
        default=int(os.environ.get("LARGE_FILES_DOWNLOAD_RETRY_COUNT", str(DEFAULT_DOWNLOAD_RETRIES))),
        help="Number of times to retry a failed download.",
    )
    parser.add_argument(
        "--retry-delay-seconds",
        type=float,
        default=float(
            os.environ.get(
                "LARGE_FILES_DOWNLOAD_RETRY_DELAY_SECONDS",
                str(DEFAULT_DOWNLOAD_RETRY_DELAY_SECONDS),
            )
        ),
        help="Delay between failed download attempts.",
    )
    args = parser.parse_args()

    entries = _load_manifest(Path(args.manifest))
    if not entries:
        print("No large files configured; skipping download step.")
        return 0

    failures = 0
    for index, entry in enumerate(entries, start=1):
        try:
            _validate_entry(entry, index)
            entry_type = entry.get("type", "download")
            name = entry.get("name", entry.get("url", f"entry-{index}"))

            if entry_type == "download":
                download_path = Path(entry["path"])
                extract = entry.get("extract")
                extract_path = Path(extract["path"]) if extract else None

                if _should_skip(entry, download_path, extract_path):
                    print(f"[skip] {name}")
                    continue

                print(f"[start] {name} -> {download_path}", flush=True)
                bytes_downloaded = _download_file(
                    name=name,
                    url=entry["url"],
                    destination=download_path,
                    headers=entry.get("headers", {}),
                    timeout_seconds=args.timeout_seconds,
                    retry_count=args.retry_count,
                    retry_delay_seconds=args.retry_delay_seconds,
                )
                print(f"[downloaded] {name} -> {download_path} ({_format_mb(bytes_downloaded)})", flush=True)

                if extract:
                    print(f"[extracting] {download_path} -> {extract['path']}", flush=True)
                    _extract_zip(download_path, extract)
                    print(f"[extracted] {download_path} -> {extract['path']}", flush=True)
                continue

            if _should_skip_command(entry):
                print(f"[skip] {name}")
                continue

            _run_command(name, entry["command"])
        except Exception as exc:
            failures += 1
            print(f"[error] entry #{index}: {exc}", file=sys.stderr)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
