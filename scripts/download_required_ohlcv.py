#!/usr/bin/env python3
"""Download the historical daily stock data needed by bull-momentum research.

The downloader is deliberately conservative: symbols and monthly windows are
processed sequentially, completed symbol files are reusable, and every output
is validated before it replaces an existing file.

Examples:
    env/bin/python scripts/download_required_ohlcv.py --symbols EQUITAS
    env/bin/python scripts/download_required_ohlcv.py
    env/bin/python scripts/download_required_ohlcv.py --list
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from jugaad_data.nse.history import NSEHistory  # noqa: E402
from jugaad_data.util import break_dates  # noqa: E402


@dataclass(frozen=True)
class Target:
    symbol: str
    from_date: date
    to_date: date
    event: str
    allowed_series: tuple[str, ...] = ("EQ",)
    allowed_symbols: tuple[str, ...] = ()


START = date(2019, 1, 1)
TODAY = date.today()

# The dates are data cut-offs, not an attempt to splice the successor's prices
# onto the old security. Successor securities remain separate instruments.
TARGETS = (
    Target("DHFL", START, date(2021, 6, 11), "suspended 2021-06-14"),
    Target("EQUITAS", START, date(2023, 2, 2), "merged; trading ceased 2023-02-03"),
    Target("INOXLEISUR", START, date(2023, 2, 16), "merged; suspended 2023-02-17"),
    Target("MINDTREE", START, date(2022, 11, 22), "merged; suspended 2022-11-23"),
    Target("HDFC", START, date(2023, 7, 12), "merged; trading ceased 2023-07-13"),
    Target("UJJIVAN", START, date(2024, 5, 2), "merged; trading ceased 2024-05-03"),
    Target(
        "FRETAIL",
        START,
        date(2024, 8, 1),
        "suspended 2024-08-02",
        ("EQ", "BZ"),
    ),
    Target("TATAMTRDVR", START, date(2024, 8, 29), "suspended 2024-08-30"),
    Target("IDFC", START, date(2024, 10, 9), "merged; suspended 2024-10-10"),
    Target("ISEC", START, date(2025, 3, 21), "delisted 2025-03-24"),
    Target("PEL", START, date(2025, 9, 22), "merged; suspended 2025-09-23"),
    Target("GSPL", START, date(2026, 5, 11), "merged; suspended 2026-05-12"),
    Target(
        "FCONSUMER",
        START,
        date(2026, 8, 31),
        "last known trading opportunity",
        ("EQ", "BZ"),
    ),
    Target(
        "FLFL",
        START,
        TODAY,
        "active/suspended history; no final event date",
        ("EQ", "BZ"),
    ),
    Target("HEG", START, TODAY, "renamed to HEGAM", ("EQ",), ("HEG", "HEGAM")),
    Target(
        "JBCHEPHARM",
        START,
        date(2026, 7, 16),
        "merged; suspended 2026-07-17",
    ),
    Target(
        "MTARTECH",
        date(2021, 3, 15),
        TODAY,
        "active; download from listing",
        ("EQ", "BE"),
    ),
    Target("RAJESHEXPO", START, TODAY, "active surveillance", ("EQ", "BZ")),
    Target(
        "STLTECH",
        START,
        TODAY,
        "active surveillance; historical STRTECH alias",
        ("EQ", "BE"),
        ("STLTECH", "STRTECH"),
    ),
)

CSV_FIELDS = (
    "DATE",
    "SYMBOL",
    "SERIES",
    "OPEN",
    "HIGH",
    "LOW",
    "CLOSE",
    "VOLUME",
    "PREV_CLOSE",
    "LTP",
    "VWAP",
    "VALUE",
    "NO_OF_TRADES",
    "DELIVERY_QTY",
    "DELIVERY_PCT",
)

RAW_TO_OUTPUT = {
    "CH_SYMBOL": "SYMBOL",
    "CH_SERIES": "SERIES",
    "CH_OPENING_PRICE": "OPEN",
    "CH_TRADE_HIGH_PRICE": "HIGH",
    "CH_TRADE_LOW_PRICE": "LOW",
    "CH_CLOSING_PRICE": "CLOSE",
    "CH_TOT_TRADED_QTY": "VOLUME",
    "CH_PREVIOUS_CLS_PRICE": "PREV_CLOSE",
    "CH_LAST_TRADED_PRICE": "LTP",
    "VWAP": "VWAP",
    "CH_TOT_TRADED_VAL": "VALUE",
    "CH_TOTAL_TRADES": "NO_OF_TRADES",
    "COP_DELIV_QTY": "DELIVERY_QTY",
    "COP_DELIV_PERC": "DELIVERY_PCT",
}


def parse_nse_date(value: object) -> date:
    text = str(value).strip()
    if text.endswith("Z"):
        # NSE represents midnight IST as 18:30 on the preceding UTC date.
        # Taking the literal UTC date shifts every daily bar backwards.
        ist = timezone(timedelta(hours=5, minutes=30))
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(ist).date()
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d %b %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"unsupported NSE date: {value!r}")


def normalize_rows(target: Target, raw_rows: Iterable[dict]) -> list[dict]:
    rows: dict[tuple[date, str], dict] = {}
    allowed_symbols = set(target.allowed_symbols or (target.symbol,))
    for raw in raw_rows:
        row_symbol = str(raw.get("CH_SYMBOL", "")).strip().upper()
        if row_symbol not in allowed_symbols:
            raise ValueError(
                f"{target.symbol}: response contains another symbol {row_symbol!r}"
            )
        raw_series = str(raw.get("CH_SERIES", "")).strip().upper()
        if raw_series not in target.allowed_series:
            continue
        trading_date = parse_nse_date(raw.get("CH_TIMESTAMP"))
        if not target.from_date <= trading_date <= target.to_date:
            raise ValueError(
                f"{target.symbol}: out-of-range row {trading_date.isoformat()}"
            )
        row = {field: "" for field in CSV_FIELDS}
        row["DATE"] = trading_date.isoformat()
        for raw_name, output_name in RAW_TO_OUTPUT.items():
            value = raw.get(raw_name)
            row[output_name] = "" if value is None else value

        for field in ("OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"):
            if row[field] == "":
                raise ValueError(
                    f"{target.symbol} {trading_date}: missing required {field}"
                )
        open_, high, low, close = (
            float(row["OPEN"]),
            float(row["HIGH"]),
            float(row["LOW"]),
            float(row["CLOSE"]),
        )
        if min(open_, high, low, close) <= 0:
            raise ValueError(f"{target.symbol} {trading_date}: non-positive OHLC")
        if high < max(open_, low, close) or low > min(open_, high, close):
            raise ValueError(f"{target.symbol} {trading_date}: invalid OHLC bounds")
        if float(row["VOLUME"]) < 0:
            raise ValueError(f"{target.symbol} {trading_date}: negative volume")

        key = (trading_date, str(row["SERIES"]))
        previous = rows.get(key)
        if previous is not None and previous != row:
            raise ValueError(
                f"{target.symbol} {trading_date}: conflicting duplicate {key[1]} row"
            )
        rows[key] = row

    return [rows[key] for key in sorted(rows)]


def write_csv_atomic(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def existing_result(path: Path, target: Target) -> dict | None:
    if not path.exists():
        return None
    try:
        with path.open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        if not rows or tuple(rows[0]) != CSV_FIELDS:
            return None
        dates = [date.fromisoformat(row["DATE"]) for row in rows]
        symbols = {row["SYMBOL"] for row in rows}
        if not symbols.issubset(set(target.allowed_symbols or (target.symbol,))):
            return None
        if not {row["SERIES"] for row in rows}.issubset(set(target.allowed_series)):
            return None
        max_gap_days = validate_coverage(rows, target)
        return {
            "status": "already_downloaded",
            "rows": len(rows),
            "first_date": min(dates).isoformat(),
            "last_date": max(dates).isoformat(),
            "max_gap_days": max_gap_days,
            "path": str(path),
        }
    except (OSError, ValueError, KeyError):
        return None


def validate_coverage(rows: list[dict], target: Target) -> int:
    """Require coverage at both boundaries; return the largest observed gap."""
    dates = sorted({date.fromisoformat(row["DATE"]) for row in rows})
    if not dates:
        raise ValueError(f"{target.symbol}: no rows")
    if (dates[0] - target.from_date).days > 14:
        raise ValueError(
            f"{target.symbol}: first row {dates[0]} is too far after {target.from_date}"
        )
    if (target.to_date - dates[-1]).days > 14:
        raise ValueError(
            f"{target.symbol}: last row {dates[-1]} does not reach {target.to_date}"
        )
    return max(
        ((later - earlier).days for earlier, later in zip(dates, dates[1:])),
        default=0,
    )


def fetch_exact_series(
    history: NSEHistory,
    target: Target,
    window_start: date,
    window_end: date,
    series: str,
    cache_dir: Path,
) -> tuple[list[dict], bool]:
    """Fetch one exact series/window, with a resumable local response cache."""
    cache_path = (
        cache_dir
        / target.symbol
        / series
        / f"{window_start.isoformat()}_{window_end.isoformat()}.json"
    )
    if cache_path.exists():
        cached_rows = json.loads(cache_path.read_text())
        if not isinstance(cached_rows, list):
            raise ValueError(f"invalid response cache: {cache_path}")
        return cached_rows, True

    response = history._get(
        "stock_history",
        {
            "symbol": target.symbol,
            "from": window_start.strftime("%d-%m-%Y"),
            "to": window_end.strftime("%d-%m-%Y"),
            "type": "priceVolumeDeliverable",
            "series": series,
        },
    )
    response.raise_for_status()
    payload = response.json()
    if "data" not in payload or not isinstance(payload["data"], list):
        raise ValueError(f"unexpected NSE response keys: {sorted(payload)}")

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload["data"]))
    temporary.replace(cache_path)
    return payload["data"], False


def fetch_target(
    target: Target, delay: float, retries: int, cache_dir: Path
) -> list[dict]:
    history = NSEHistory()
    raw_rows: list[dict] = []
    windows = break_dates(target.from_date, target.to_date)
    total_requests = len(windows) * len(target.allowed_series)
    request_number = 0
    for index, (window_start, window_end) in enumerate(windows, 1):
        for series in target.allowed_series:
            request_number += 1
            last_error: Exception | None = None
            for attempt in range(1, retries + 1):
                try:
                    chunk, from_cache = fetch_exact_series(
                        history,
                        target,
                        window_start,
                        window_end,
                        series,
                        cache_dir,
                    )
                    raw_rows.extend(chunk)
                    print(
                        f"  {target.symbol}: {index}/{len(windows)} "
                        f"{window_start}..{window_end} {series}: {len(chunk)} rows"
                        f"{' (cached)' if from_cache else ''}",
                        flush=True,
                    )
                    last_error = None
                    break
                except Exception as error:  # requests exposes several error types
                    last_error = error
                    if attempt < retries:
                        wait = max(delay, 2.0) * attempt
                        print(
                            f"  {target.symbol}: attempt {attempt}/{retries} failed "
                            f"for {window_start}..{window_end} {series}: {error}; "
                            f"waiting {wait:.1f}s",
                            flush=True,
                        )
                        time.sleep(wait)
            if last_error is not None:
                raise RuntimeError(
                    f"{window_start}..{window_end} {series} failed after "
                    f"{retries} attempts"
                ) from last_error
            if request_number < total_requests and delay and not from_cache:
                time.sleep(delay)
    return normalize_rows(target, raw_rows)


def select_targets(symbols: str | None) -> list[Target]:
    if not symbols:
        return list(TARGETS)
    requested = [symbol.strip().upper() for symbol in symbols.split(",") if symbol.strip()]
    known = {target.symbol: target for target in TARGETS}
    unknown = sorted(set(requested) - set(known))
    if unknown:
        raise ValueError(f"unknown required symbol(s): {', '.join(unknown)}")
    return [known[symbol] for symbol in requested]


def target_metadata(target: Target) -> dict:
    """JSON-safe target metadata for the progress manifest."""
    return {
        "symbol": target.symbol,
        "from_date": target.from_date.isoformat(),
        "to_date": target.to_date.isoformat(),
        "event": target.event,
        "allowed_series": list(target.allowed_series),
        "allowed_symbols": list(target.allowed_symbols or (target.symbol,)),
    }


def write_manifest(path: Path, results: dict[str, dict]) -> None:
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "NSE security-wise historical API via jugaad-data",
        "sequential": True,
        "results": results,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n")
    temporary.replace(path)


def wait_between_targets(position: int, delay: float) -> None:
    """Pause before every symbol after the first one in a batch."""
    if position > 1 and delay > 0:
        print(f"waiting {delay:.1f}s before next symbol", flush=True)
        time.sleep(delay)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--symbols",
        help="comma-separated subset, for example EQUITAS or DHFL,EQUITAS",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "required_daily_ohlcv",
    )
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument(
        "--symbol-delay",
        type=float,
        default=5.0,
        help="seconds to wait between symbols in a batch (default: 5)",
    )
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=PROJECT_ROOT / ".cache" / "required_daily_ohlcv",
        help="resumable raw-response cache (default: repository .cache)",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--list", action="store_true", dest="list_only")
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="continue to later symbols after a symbol fails",
    )
    args = parser.parse_args()
    if args.delay < 0 or args.symbol_delay < 0 or args.retries < 1:
        parser.error(
            "--delay and --symbol-delay must be >= 0 and --retries must be >= 1"
        )

    try:
        targets = select_targets(args.symbols)
    except ValueError as error:
        parser.error(str(error))

    if args.list_only:
        for target in targets:
            print(
                f"{target.symbol:12} {target.from_date}..{target.to_date}  "
                f"{target.event}"
            )
        return 0

    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output_dir / "manifest.json"
    results: dict[str, dict] = {}

    for position, target in enumerate(targets, 1):
        wait_between_targets(position, args.symbol_delay)
        output_path = args.output_dir / f"{target.symbol}.csv"
        print(
            f"[{position}/{len(targets)}] {target.symbol} "
            f"{target.from_date}..{target.to_date}",
            flush=True,
        )
        previous = None if args.force else existing_result(output_path, target)
        if previous:
            results[target.symbol] = target_metadata(target) | previous
            print(f"  reused {previous['rows']} validated rows", flush=True)
            write_manifest(manifest_path, results)
            continue
        try:
            rows = fetch_target(target, args.delay, args.retries, args.cache_dir)
            if not rows:
                raise ValueError("NSE returned no rows for the requested period")
            max_gap_days = validate_coverage(rows, target)
            write_csv_atomic(output_path, rows)
            result = {
                "status": "downloaded",
                "rows": len(rows),
                "first_date": rows[0]["DATE"],
                "last_date": rows[-1]["DATE"],
                "series": sorted({row["SERIES"] for row in rows}),
                "max_gap_days": max_gap_days,
                "path": str(output_path),
            }
            results[target.symbol] = target_metadata(target) | result
            print(
                f"  saved {result['rows']} rows, "
                f"{result['first_date']}..{result['last_date']}, "
                f"series={','.join(result['series'])}",
                flush=True,
            )
        except Exception as error:
            results[target.symbol] = target_metadata(target) | {
                "status": "failed",
                "error": f"{type(error).__name__}: {error}",
                "path": str(output_path),
            }
            print(f"  FAILED: {type(error).__name__}: {error}", flush=True)
            write_manifest(manifest_path, results)
            if not args.continue_on_error:
                return 1
        write_manifest(manifest_path, results)

    failures = [symbol for symbol, result in results.items() if result["status"] == "failed"]
    print(
        f"finished: {len(results) - len(failures)} succeeded, "
        f"{len(failures)} failed; manifest={manifest_path}",
        flush=True,
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
