#!/usr/bin/env python3
"""Archive NSE equity corporate announcements in sequential monthly windows.

These records are supporting evidence only. They are not treated as a complete
or authoritative corporate-action adjustment ledger.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from jugaad_data.nse.live import NSELive  # noqa: E402


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def month_windows(start: date, end: date) -> list[tuple[date, date]]:
    windows = []
    cursor = start
    while cursor <= end:
        if cursor.month == 12:
            next_month = date(cursor.year + 1, 1, 1)
        else:
            next_month = date(cursor.year, cursor.month + 1, 1)
        window_end = min(end, next_month - timedelta(days=1))
        windows.append((cursor, window_end))
        cursor = window_end + timedelta(days=1)
    return windows


def validate_payload(raw: bytes) -> tuple[list[dict], int]:
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("response is not JSON") from error
    if not isinstance(payload, list):
        raise ValueError(f"expected announcement list, got {type(payload).__name__}")
    if any(not isinstance(item, dict) for item in payload):
        raise ValueError("announcement list contains a non-object item")
    return payload, len(payload)


def atomic_bytes(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def existing_result(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        raw = path.read_bytes()
        _, rows = validate_payload(raw)
    except (OSError, ValueError):
        return None
    return {
        "status": "already_downloaded",
        "rows": rows,
        "possible_truncation": rows >= 2000,
        "bytes": len(raw),
        "sha256": sha256_bytes(raw),
        "path": str(path),
    }


def fetch_window(
    client: NSELive,
    start: date,
    end: date,
    timeout: float,
) -> bytes:
    url = client.base_url + client._routes["corporate_announcements"]
    response = client.s.get(
        url,
        params={
            "index": "equities",
            "from_date": start.strftime("%d-%m-%Y"),
            "to_date": end.strftime("%d-%m-%Y"),
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-date", type=date.fromisoformat, default=date(2019, 1, 1))
    parser.add_argument("--to-date", type=date.fromisoformat, default=date.today())
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "phase6_corporate_announcements",
    )
    parser.add_argument("--request-delay", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--max-windows", type=int)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--list", action="store_true", dest="list_only")
    args = parser.parse_args()
    if args.from_date > args.to_date:
        parser.error("--from-date cannot be after --to-date")
    if args.request_delay < 0 or args.timeout <= 0 or args.retries < 1:
        parser.error("invalid delay, timeout or retry count")
    if args.max_windows is not None and args.max_windows < 1:
        parser.error("--max-windows must be >= 1")

    windows = month_windows(args.from_date, args.to_date)
    if args.max_windows is not None:
        windows = windows[: args.max_windows]
    if args.list_only:
        print(
            f"{len(windows)} windows: {windows[0][0].isoformat()}.."
            f"{windows[-1][1].isoformat()}"
        )
        return 0

    output_dir = args.output_dir.expanduser().resolve()
    manifest_path = output_dir / "manifest.json"
    results: dict[str, dict] = {}
    if manifest_path.exists():
        results.update(json.loads(manifest_path.read_text()).get("results", {}))
    client = NSELive()
    requested = 0
    failed = 0
    for position, (start, end) in enumerate(windows, 1):
        key = f"{start.isoformat()}_{end.isoformat()}"
        path = output_dir / "raw" / str(start.year) / f"{key}.json"
        result = None if args.force else existing_result(path)
        if result is None:
            if requested and args.request_delay:
                time.sleep(args.request_delay)
            last_error: Exception | None = None
            for attempt in range(1, args.retries + 1):
                if attempt > 1 and args.request_delay:
                    time.sleep(max(args.request_delay, 2.0) * attempt)
                try:
                    requested += 1
                    raw = fetch_window(client, start, end, args.timeout)
                    _, rows = validate_payload(raw)
                    atomic_bytes(path, raw)
                    result = {
                        "status": "downloaded",
                        "rows": rows,
                        "possible_truncation": rows >= 2000,
                        "bytes": len(raw),
                        "sha256": sha256_bytes(raw),
                        "path": str(path),
                    }
                    last_error = None
                    break
                except Exception as error:
                    last_error = error
            if last_error is not None:
                failed += 1
                result = {
                    "status": "failed",
                    "error": f"{type(last_error).__name__}: {last_error}",
                    "path": str(path),
                }
        results[key] = result
        atomic_json(manifest_path, {
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "source": "NSE equity corporate announcements via jugaad-data",
            "status": "supporting_evidence_not_authoritative_action_ledger",
            "requested_range": {
                "from": args.from_date.isoformat(),
                "to": args.to_date.isoformat(),
            },
            "sequential": True,
            "request_delay_seconds": args.request_delay,
            "results": results,
        })
        print(
            f"[{position}/{len(windows)}] {key}: {result['status']} "
            f"{result.get('rows', 0)} rows"
            + (" possible-truncation" if result.get("possible_truncation") else ""),
            flush=True,
        )
        if result["status"] == "failed" and not args.continue_on_error:
            return 1
    print(
        f"complete: {len(windows) - failed}/{len(windows)} windows available; "
        f"{failed} failed; {requested} network requests",
        flush=True,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
