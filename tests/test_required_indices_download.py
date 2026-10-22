import importlib.util
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "download_required_indices.py"
SPEC = importlib.util.spec_from_file_location("download_required_indices", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_normalize_price_rows_sorts_and_validates_ohlc():
    target = MODULE.IndexTarget("NIFTY 500")
    rows = MODULE.normalize_price_rows(target, [
        {
            "INDEX_NAME": "Nifty 500", "HistoricalDate": "02 Jan 2020",
            "OPEN": "101", "HIGH": "103", "LOW": "100", "CLOSE": "102",
        },
        {
            "INDEX_NAME": "Nifty 500", "HistoricalDate": "01 Jan 2020",
            "OPEN": "100", "HIGH": "102", "LOW": "99", "CLOSE": "101",
        },
    ])

    assert [row["DATE"] for row in rows] == ["2020-01-01", "2020-01-02"]


def test_normalize_price_rows_rejects_invalid_bounds():
    target = MODULE.IndexTarget("NIFTY 500")
    with pytest.raises(ValueError, match="invalid OHLC"):
        MODULE.normalize_price_rows(target, [{
            "INDEX_NAME": "Nifty 500", "HistoricalDate": "01 Jan 2020",
            "OPEN": "100", "HIGH": "99", "LOW": "98", "CLOSE": "101",
        }])


def test_normalize_price_rows_preserves_missing_optional_ohlc():
    rows = MODULE.normalize_price_rows(MODULE.IndexTarget("NIFTY 200"), [{
        "INDEX_NAME": "Nifty 200", "HistoricalDate": "01 Jan 2020",
        "OPEN": "-", "HIGH": "-", "LOW": "-", "CLOSE": "101",
    }])

    assert rows[0]["OPEN"] == ""
    assert rows[0]["CLOSE"] == 101.0


def test_normalize_price_rows_treats_zero_optional_ohlc_as_missing():
    rows = MODULE.normalize_price_rows(MODULE.IndexTarget("INDIA VIX", False), [{
        "INDEX_NAME": "INDIA VIX", "HistoricalDate": "30 Mar 2021",
        "OPEN": "0", "HIGH": "0", "LOW": "0", "CLOSE": "20.65",
    }])

    assert rows[0]["OPEN"] == rows[0]["HIGH"] == rows[0]["LOW"] == ""
    assert rows[0]["CLOSE"] == 20.65


def test_normalize_tri_rows():
    rows = MODULE.normalize_tri_rows(MODULE.IndexTarget("NIFTY 500"), [{
        "Index Name": "Nifty 500", "Date": "01 Jan 2020",
        "TotalReturnsIndex": "14,843.29", "NTR_Value": "14,334.03",
    }])

    assert rows == [{
        "DATE": "2020-01-01", "INDEX_NAME": "Nifty 500",
        "TOTAL_RETURN_INDEX": 14843.29, "NET_TOTAL_RETURN_INDEX": 14334.03,
    }]


def test_normalize_tri_rows_preserves_missing_optional_ntr():
    rows = MODULE.normalize_tri_rows(MODULE.IndexTarget("NIFTY 200"), [{
        "Index Name": "Nifty 200", "Date": "01 Jan 2020",
        "TotalReturnsIndex": "10,000", "NTR_Value": "-",
    }])

    assert rows[0]["TOTAL_RETURN_INDEX"] == 10000.0
    assert rows[0]["NET_TOTAL_RETURN_INDEX"] == ""


def test_validate_coverage_rejects_truncated_tail():
    with pytest.raises(ValueError, match="last row"):
        MODULE.validate_coverage(
            [{"DATE": "2020-01-01"}], date(2020, 1, 1), date(2020, 2, 1), "test"
        )


def test_index_post_uses_timeout():
    history = MODULE.NSEIndexHistory()
    response = MagicMock()
    response.json.return_value = []
    with patch.object(history.s, "post", return_value=response) as post:
        history._post_json("index_history", {"cinfo": "test"})

    assert post.call_args.kwargs["timeout"] == 30
