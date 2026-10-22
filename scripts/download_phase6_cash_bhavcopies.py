#!/usr/bin/env python3
"""Download a validated, sequential NSE cash bhavcopy panel for Phase 6.

The session calendar is supplied locally so the downloader never probes
weekends or holidays. Every completed CSV is reusable and fingerprinted in a
resumable manifest. Requests are strictly sequential.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sys
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from jugaad_data.nse.archives import NSEArchives  # noqa: E402


IST = timezone(timedelta(hours=5, minutes=30))
UDIFF_START = date(2024, 7, 8)
LEGACY_REQUIRED = {
    "SYMBOL",
    "SERIES",
    "OPEN",
    "HIGH",
    "LOW",
    "CLOSE",
    "TOTTRDQTY",
    "TIMESTAMP",
    "ISIN",
}
UDIFF_REQUIRED = {
    "TradDt",
    "FinInstrmTp",
    "FinInstrmId",
    "ISIN",
    "TckrSymb",
    "SctySrs",
    "OpnPric",
    "HghPric",
    "LwPric",
    "ClsPric",
    "TtlTradgVol",
}


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def parse_calendar_date(value: object) -> date:
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d-%b-%y", "%d %b %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"unsupported calendar date: {value!r}")


def load_calendar(path: Path) -> list[date]:
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text())
        timestamps = payload.get("timestamp") if isinstance(payload, dict) else None
        if not isinstance(timestamps, list):
            raise ValueError(f"{path}: JSON calendar requires a timestamp list")
        values = [
            datetime.fromtimestamp(float(value), tz=timezone.utc).astimezone(IST).date()
            for value in timestamps
        ]
    else:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
        if not rows:
            raise ValueError(f"{path}: empty calendar")
        fields = rows[0]
        column = next(
            (name for name in ("DATE", "HistoricalDate", "date") if name in fields),
            None,
        )
        if column is None:
            raise ValueError(f"{path}: no supported date column")
        values = [parse_calendar_date(row[column]) for row in rows]
    ordered = sorted(set(values))
    if not ordered:
        raise ValueError(f"{path}: no calendar dates")
    return ordered


def validate_csv(raw: bytes, expected_date: date) -> tuple[str, int]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError(f"{expected_date}: response is not UTF-8 CSV") from error
    reader = csv.DictReader(text.splitlines())
    fields = set(reader.fieldnames or ())
    if UDIFF_REQUIRED.issubset(fields):
        kind = "udiff"
        date_field = "TradDt"
    elif LEGACY_REQUIRED.issubset(fields):
        kind = "legacy"
        date_field = "TIMESTAMP"
    else:
        missing_legacy = sorted(LEGACY_REQUIRED - fields)
        missing_udiff = sorted(UDIFF_REQUIRED - fields)
        raise ValueError(
            f"{expected_date}: unexpected schema; legacy missing={missing_legacy}; "
            f"udiff missing={missing_udiff}"
        )
    count = 0
    observed_dates = set()
    for row in reader:
        if not any(str(value or "").strip() for value in row.values()):
            continue
        count += 1
        value = str(row.get(date_field) or "").strip()
        if value:
            observed_dates.add(parse_calendar_date(value))
    if count < 100:
        raise ValueError(f"{expected_date}: implausibly small file ({count} rows)")
    if observed_dates != {expected_date}:
        raise ValueError(
            f"{expected_date}: response dates are "
            f"{sorted(item.isoformat() for item in observed_dates)}"
        )
    return kind, count


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


def existing_result(path: Path, trading_date: date) -> dict | None:
    if not path.exists():
        return None
    try:
        raw = path.read_bytes()
        kind, rows = validate_csv(raw, trading_date)
    except (OSError, ValueError):
        return None
    return {
        "status": "already_downloaded",
        "format": kind,
        "rows": rows,
        "bytes": len(raw),
        "sha256": sha256_bytes(raw),
        "path": str(path),
    }


def fetch_one(
    archive: NSEArchives,
    trading_date: date,
    fallback_delay: float = 0.0,
) -> tuple[bytes, int]:
    try:
        return archive.bhavcopy_raw(trading_date).encode("utf-8"), 1
    except Exception:
        if trading_date >= UDIFF_START:
            raise
        if fallback_delay:
            time.sleep(fallback_delay)
        response = archive.get(
            "bhavcopy",
            yyyy=trading_date.year,
            MMM=trading_date.strftime("%b").upper(),
            dd=trading_date.strftime("%d"),
        )
        response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as zipped:
            members = [name for name in zipped.namelist() if not name.endswith("/")]
            if len(members) != 1:
                raise ValueError(
                    f"{trading_date}: expected one archive member, got {members}"
                )
            return zipped.read(members[0]), 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calendar-file", required=True, type=Path)
    parser.add_argument("--from-date", type=date.fromisoformat, default=date(2019, 1, 1))
    parser.add_argument("--to-date", type=date.fromisoformat, default=date.today())
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "phase6_cash_bhavcopies",
    )
    parser.add_argument("--request-delay", type=float, default=2.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--max-files", type=int)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--list", action="store_true", dest="list_only")
    args = parser.parse_args()
    if args.from_date > args.to_date:
        parser.error("--from-date cannot be after --to-date")
    if args.request_delay < 0 or args.retries < 1:
        parser.error("--request-delay must be >= 0 and --retries must be >= 1")
    if args.max_files is not None and args.max_files < 1:
        parser.error("--max-files must be >= 1")

    calendar_file = args.calendar_file.expanduser().resolve()
    dates = [
        value for value in load_calendar(calendar_file)
        if args.from_date <= value <= args.to_date
    ]
    if args.max_files is not None:
        dates = dates[: args.max_files]
    if not dates:
        parser.error("calendar contains no sessions in the requested range")
    if args.list_only:
        print(
            f"{len(dates)} sessions: {dates[0].isoformat()}..{dates[-1].isoformat()}"
        )
        return 0

    output_dir = args.output_dir.expanduser().resolve()
    manifest_path = output_dir / "manifest.json"
    results: dict[str, dict] = {}
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text())
        results.update(previous.get("results", {}))
    archive = NSEArchives()
    requested = 0
    failed = 0
    for position, trading_date in enumerate(dates, 1):
        path = output_dir / "csv" / str(trading_date.year) / f"{trading_date}.csv"
        result = None if args.force else existing_result(path, trading_date)
        if result is None:
            if requested and args.request_delay:
                time.sleep(args.request_delay)
            last_error: Exception | None = None
            for attempt in range(1, args.retries + 1):
                if attempt > 1 and args.request_delay:
                    time.sleep(max(args.request_delay, 2.0) * attempt)
                try:
                    raw, request_count = fetch_one(
                        archive, trading_date, args.request_delay,
                    )
                    requested += request_count
                    kind, rows = validate_csv(raw, trading_date)
                    atomic_bytes(path, raw)
                    result = {
                        "status": "downloaded",
                        "format": kind,
                        "rows": rows,
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
        results[trading_date.isoformat()] = result
        manifest = {
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "source": "NSE cash bhavcopy via jugaad-data",
            "calendar_file": str(calendar_file),
            "calendar_sha256": sha256_bytes(calendar_file.read_bytes()),
            "requested_range": {
                "from": args.from_date.isoformat(),
                "to": args.to_date.isoformat(),
            },
            "sequential": True,
            "request_delay_seconds": args.request_delay,
            "results": results,
        }
        atomic_json(manifest_path, manifest)
        print(
            f"[{position}/{len(dates)}] {trading_date}: "
            f"{result['status']}"
            + (f" {result.get('format')} {result.get('rows')} rows" if result.get("rows") else ""),
            flush=True,
        )
        if result["status"] == "failed" and not args.continue_on_error:
            return 1

    successful = sum(
        item.get("status") in {"downloaded", "already_downloaded"}
        for day, item in results.items()
        if args.from_date.isoformat() <= day <= args.to_date.isoformat()
    )
    print(
        f"complete: {successful}/{len(dates)} requested sessions available; "
        f"{failed} failed; {requested} network requests",
        flush=True,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
