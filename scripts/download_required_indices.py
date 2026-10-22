#!/usr/bin/env python3
"""Download validated index price and total-return histories sequentially.

The default target set covers the broad-market universes, India VIX, and the
sector benchmarks needed by the bull-momentum research project. Each index is
handled completely before the next index begins, with a configurable pause
between indices.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from jugaad_data.nse.history import NSEIndexHistory  # noqa: E402


@dataclass(frozen=True)
class IndexTarget:
    name: str
    include_tri: bool = True


START = date(2020, 1, 1)
TODAY = date.today()

TARGETS = (
    IndexTarget("NIFTY 50"),
    IndexTarget("NIFTY 100"),
    IndexTarget("NIFTY 200"),
    IndexTarget("NIFTY 500"),
    IndexTarget("NIFTY LARGEMIDCAP 250"),
    IndexTarget("NIFTY MIDCAP 150"),
    IndexTarget("NIFTY SMALLCAP 100"),
    IndexTarget("NIFTY SMALLCAP 250"),
    IndexTarget("INDIA VIX", include_tri=False),
    IndexTarget("NIFTY AUTO"),
    IndexTarget("NIFTY BANK"),
    IndexTarget("NIFTY CONSUMER DURABLES"),
    IndexTarget("NIFTY ENERGY"),
    IndexTarget("NIFTY FINANCIAL SERVICES"),
    IndexTarget("NIFTY FMCG"),
    IndexTarget("NIFTY HEALTHCARE"),
    IndexTarget("NIFTY INFRASTRUCTURE"),
    IndexTarget("NIFTY IT"),
    IndexTarget("NIFTY MEDIA"),
    IndexTarget("NIFTY METAL"),
    IndexTarget("NIFTY OIL & GAS"),
    IndexTarget("NIFTY PHARMA"),
    IndexTarget("NIFTY PRIVATE BANK"),
    IndexTarget("NIFTY PSU BANK"),
    IndexTarget("NIFTY PSE"),
    IndexTarget("NIFTY REALTY"),
    IndexTarget("NIFTY SERVICES SECTOR"),
)


def slug(name: str) -> str:
    return "_".join(name.upper().replace("&", "AND").split())


def parse_date(value: object) -> date:
    text = str(value).strip()
    for fmt in ("%d %b %Y", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"unsupported index date: {value!r}")


def normalize_price_rows(target: IndexTarget, raw_rows: list[dict]) -> list[dict]:
    rows = {}
    for raw in raw_rows:
        trading_date = parse_date(raw.get("HistoricalDate"))
        name = str(raw.get("INDEX_NAME") or raw.get("Index Name") or "").strip()
        values = {}
        for field in ("OPEN", "HIGH", "LOW", "CLOSE"):
            text = str(raw.get(field) or "").replace(",", "").strip()
            values[field] = "" if text in {"", "-"} else float(text)
        if values["CLOSE"] == "" or values["CLOSE"] <= 0:
            raise ValueError(f"{target.name} {trading_date}: missing/non-positive close")
        for field in ("OPEN", "HIGH", "LOW"):
            if values[field] != "" and values[field] <= 0:
                values[field] = ""
        complete_ohlc = [values[field] for field in ("OPEN", "HIGH", "LOW", "CLOSE")]
        if all(value != "" for value in complete_ohlc):
            if min(complete_ohlc) <= 0:
                raise ValueError(f"{target.name} {trading_date}: non-positive OHLC")
            if values["HIGH"] < max(complete_ohlc) or values["LOW"] > min(complete_ohlc):
                raise ValueError(f"{target.name} {trading_date}: invalid OHLC bounds")
        row = {"DATE": trading_date.isoformat(), "INDEX_NAME": name, **values}
        previous = rows.get(trading_date)
        if previous is not None and previous != row:
            raise ValueError(f"{target.name} {trading_date}: conflicting duplicate")
        rows[trading_date] = row
    return [rows[key] for key in sorted(rows)]


def normalize_tri_rows(target: IndexTarget, raw_rows: list[dict]) -> list[dict]:
    rows = {}
    for raw in raw_rows:
        trading_date = parse_date(raw.get("Date"))
        tri = float(str(raw["TotalReturnsIndex"]).replace(",", ""))
        ntr_text = str(raw.get("NTR_Value") or "").replace(",", "").strip()
        ntr = "" if ntr_text in {"", "-"} else float(ntr_text)
        if tri <= 0 or (ntr != "" and ntr <= 0):
            raise ValueError(f"{target.name} {trading_date}: non-positive TRI")
        row = {
            "DATE": trading_date.isoformat(),
            "INDEX_NAME": str(raw.get("Index Name") or "").strip(),
            "TOTAL_RETURN_INDEX": tri,
            "NET_TOTAL_RETURN_INDEX": ntr,
        }
        previous = rows.get(trading_date)
        if previous is not None and previous != row:
            raise ValueError(f"{target.name} {trading_date}: conflicting TRI duplicate")
        rows[trading_date] = row
    return [rows[key] for key in sorted(rows)]


def validate_coverage(rows: list[dict], start: date, end: date, name: str) -> None:
    if not rows:
        raise ValueError(f"{name}: no rows")
    first = date.fromisoformat(rows[0]["DATE"])
    last = date.fromisoformat(rows[-1]["DATE"])
    if (first - start).days > 14:
        raise ValueError(f"{name}: first row {first} does not reach {start}")
    if (end - last).days > 14:
        raise ValueError(f"{name}: last row {last} does not reach {end}")


def write_csv_atomic(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def existing_result(path: Path, start: date, end: date, name: str) -> dict | None:
    if not path.exists():
        return None
    try:
        with path.open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        validate_coverage(rows, start, end, name)
        dates = [date.fromisoformat(row["DATE"]) for row in rows]
        if len(dates) != len(set(dates)):
            return None
        return {
            "status": "already_downloaded",
            "rows": len(rows),
            "first_date": min(dates).isoformat(),
            "last_date": max(dates).isoformat(),
            "path": str(path),
        }
    except (OSError, ValueError, KeyError):
        return None


def write_manifest(path: Path, results: dict) -> None:
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "NSE Indices historical endpoints via jugaad-data",
        "sequential": True,
        "results": results,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n")
    temporary.replace(path)


def select_targets(names: str | None) -> list[IndexTarget]:
    if not names:
        return list(TARGETS)
    requested = [name.strip().upper() for name in names.split(",") if name.strip()]
    known = {target.name: target for target in TARGETS}
    unknown = sorted(set(requested) - set(known))
    if unknown:
        raise ValueError(f"unknown required index(es): {', '.join(unknown)}")
    return [known[name] for name in requested]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--indices", help="comma-separated subset of index names")
    parser.add_argument("--from-date", type=date.fromisoformat, default=START)
    parser.add_argument("--to-date", type=date.fromisoformat, default=TODAY)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "required_market_indices",
    )
    parser.add_argument("--request-delay", type=float, default=1.0)
    parser.add_argument("--index-delay", type=float, default=5.0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--list", action="store_true", dest="list_only")
    args = parser.parse_args()
    if args.from_date > args.to_date:
        parser.error("--from-date cannot be after --to-date")
    if args.request_delay < 0 or args.index_delay < 0:
        parser.error("delays must be >= 0")
    try:
        targets = select_targets(args.indices)
    except ValueError as error:
        parser.error(str(error))
    if args.list_only:
        for target in targets:
            print(f"{target.name:28} price=yes tri={'yes' if target.include_tri else 'no'}")
        return 0

    history = NSEIndexHistory()
    results = {}
    manifest_path = args.output_dir / "manifest.json"
    for position, target in enumerate(targets, 1):
        if position > 1 and args.index_delay:
            print(f"waiting {args.index_delay:.1f}s before next index", flush=True)
            time.sleep(args.index_delay)
        print(f"[{position}/{len(targets)}] {target.name}", flush=True)
        target_result = {"price": None, "tri": None}
        try:
            price_path = args.output_dir / "price" / f"{slug(target.name)}.csv"
            price_result = None if args.force else existing_result(
                price_path, args.from_date, args.to_date, target.name,
            )
            if price_result is None:
                rows = normalize_price_rows(
                    target, history._index(target.name, args.from_date, args.to_date)
                )
                validate_coverage(rows, args.from_date, args.to_date, target.name)
                write_csv_atomic(price_path, rows)
                price_result = {
                    "status": "downloaded",
                    "rows": len(rows),
                    "first_date": rows[0]["DATE"],
                    "last_date": rows[-1]["DATE"],
                    "path": str(price_path),
                }
            target_result["price"] = price_result
            print(f"  price: {price_result['rows']} rows ({price_result['status']})", flush=True)

            if target.include_tri:
                if args.request_delay:
                    time.sleep(args.request_delay)
                tri_path = args.output_dir / "tri" / f"{slug(target.name)}.csv"
                tri_result = None if args.force else existing_result(
                    tri_path, args.from_date, args.to_date, f"{target.name} TRI",
                )
                if tri_result is None:
                    rows = normalize_tri_rows(
                        target,
                        history._index_tri(
                            target.name, target.name, args.from_date, args.to_date
                        ),
                    )
                    validate_coverage(rows, args.from_date, args.to_date, f"{target.name} TRI")
                    write_csv_atomic(tri_path, rows)
                    tri_result = {
                        "status": "downloaded",
                        "rows": len(rows),
                        "first_date": rows[0]["DATE"],
                        "last_date": rows[-1]["DATE"],
                        "path": str(tri_path),
                    }
                target_result["tri"] = tri_result
                print(f"  tri: {tri_result['rows']} rows ({tri_result['status']})", flush=True)
            results[target.name] = target_result | {"status": "success"}
        except Exception as error:
            results[target.name] = target_result | {
                "status": "failed",
                "error": f"{type(error).__name__}: {error}",
            }
            print(f"  FAILED: {type(error).__name__}: {error}", flush=True)
            write_manifest(manifest_path, results)
            if not args.continue_on_error:
                return 1
        write_manifest(manifest_path, results)

    failures = [name for name, result in results.items() if result["status"] == "failed"]
    print(
        f"finished: {len(results) - len(failures)} succeeded, "
        f"{len(failures)} failed; manifest={manifest_path}",
        flush=True,
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
