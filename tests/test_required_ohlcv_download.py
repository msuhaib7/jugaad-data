import importlib.util
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "download_required_ohlcv.py"
SPEC = importlib.util.spec_from_file_location("download_required_ohlcv", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def raw_row(day="2023-02-01T18:30:00.000Z", symbol="EQUITAS"):
    return {
        "CH_TIMESTAMP": day,
        "CH_SYMBOL": symbol,
        "CH_SERIES": "EQ",
        "CH_OPENING_PRICE": 50.0,
        "CH_TRADE_HIGH_PRICE": 52.0,
        "CH_TRADE_LOW_PRICE": 49.0,
        "CH_CLOSING_PRICE": 51.0,
        "CH_TOT_TRADED_QTY": 1000,
        "CH_PREVIOUS_CLS_PRICE": 50.0,
        "CH_LAST_TRADED_PRICE": 51.0,
        "VWAP": 50.5,
        "CH_TOT_TRADED_VAL": 50500,
        "CH_TOTAL_TRADES": 100,
        "COP_DELIV_QTY": 500,
        "COP_DELIV_PERC": 50.0,
    }


def test_normalize_rows_sorts_and_deduplicates():
    target = MODULE.Target("EQUITAS", date(2023, 2, 1), date(2023, 2, 2), "test")
    earlier = raw_row("2023-01-31T18:30:00.000Z")
    later = raw_row()

    rows = MODULE.normalize_rows(target, [later, earlier, later])

    assert [row["DATE"] for row in rows] == ["2023-02-01", "2023-02-02"]
    assert rows[-1]["SYMBOL"] == "EQUITAS"
    assert rows[-1]["CLOSE"] == 51.0


def test_normalize_rows_rejects_wrong_symbol():
    target = MODULE.Target("EQUITAS", date(2023, 2, 1), date(2023, 2, 2), "test")

    with pytest.raises(ValueError, match="another symbol"):
        MODULE.normalize_rows(target, [raw_row(symbol="EQUITASBNK")])


def test_normalize_rows_accepts_declared_symbol_alias():
    target = MODULE.Target(
        "HEG",
        date(2026, 9, 1),
        date(2026, 9, 2),
        "renamed",
        ("EQ",),
        ("HEG", "HEGAM"),
    )

    rows = MODULE.normalize_rows(
        target, [raw_row("2026-09-01T18:30:00.000Z", symbol="HEGAM")]
    )

    assert rows[0]["SYMBOL"] == "HEGAM"


def test_normalize_rows_ignores_non_equity_series():
    target = MODULE.Target("DHFL", date(2021, 6, 1), date(2021, 6, 11), "test")
    debt = raw_row("2021-06-01T18:30:00.000Z", symbol="DHFL")
    debt["CH_SERIES"] = "N2"

    assert MODULE.normalize_rows(target, [debt]) == []


def test_select_targets_preserves_requested_order():
    targets = MODULE.select_targets("EQUITAS,DHFL")

    assert [target.symbol for target in targets] == ["EQUITAS", "DHFL"]


def test_parse_nse_utc_midnight_as_ist_trading_date():
    assert MODULE.parse_nse_date("2023-02-01T18:30:00.000Z") == date(2023, 2, 2)


def test_existing_result_records_large_date_gaps(tmp_path):
    target = MODULE.Target("DHFL", date(2019, 1, 1), date(2019, 2, 28), "test")
    path = tmp_path / "DHFL.csv"
    first = MODULE.normalize_rows(
        target, [raw_row("2019-01-01T18:30:00.000Z", symbol="DHFL")]
    )[0]
    second = MODULE.normalize_rows(
        target, [raw_row("2019-02-19T18:30:00.000Z", symbol="DHFL")]
    )[0]
    MODULE.write_csv_atomic(path, [first, second])

    assert MODULE.existing_result(path, target)["max_gap_days"] == 49


def test_existing_result_rejects_truncated_tail(tmp_path):
    target = MODULE.Target("FRETAIL", date(2024, 7, 1), date(2024, 8, 1), "test")
    path = tmp_path / "FRETAIL.csv"
    row = MODULE.normalize_rows(
        target, [raw_row("2024-07-01T18:30:00.000Z", symbol="FRETAIL")]
    )[0]
    MODULE.write_csv_atomic(path, [row])

    assert MODULE.existing_result(path, target) is None


def test_nse_history_requests_have_bounded_timeout():
    history = MODULE.NSEHistory()
    history.s.cookies.set("nseappid", "test")
    response = MagicMock()
    with patch.object(history.s, "get", return_value=response) as get:
        assert history._get("stock_history", {"symbol": "SBIN"}) is response

    assert get.call_args.kwargs["timeout"] == 30


def test_wait_between_targets_only_pauses_after_first_symbol():
    with patch.object(MODULE.time, "sleep") as sleep:
        MODULE.wait_between_targets(1, 5.0)
        sleep.assert_not_called()

        MODULE.wait_between_targets(2, 5.0)
        sleep.assert_called_once_with(5.0)
