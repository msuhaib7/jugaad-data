import importlib.util
import io
import json
import sys
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "download_phase6_cash_bhavcopies.py"
)
SPEC = importlib.util.spec_from_file_location("download_phase6_cash_bhavcopies", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def legacy_csv(day="01-JAN-2020", rows=100):
    header = "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,TOTTRDQTY,TIMESTAMP,ISIN\n"
    body = "".join(
        f"S{number},EQ,1,2,1,2,100,{day},INE000000{number:03d}\n"
        for number in range(rows)
    )
    return (header + body).encode()


def udiff_csv(day="2024-07-08", rows=100):
    fields = sorted(MODULE.UDIFF_REQUIRED)
    output = [",".join(fields)]
    for number in range(rows):
        values = {field: "1" for field in fields}
        values["TradDt"] = day
        values["TckrSymb"] = f"S{number}"
        values["SctySrs"] = "EQ"
        values["ISIN"] = f"INE000000{number:03d}"
        output.append(",".join(values[field] for field in fields))
    return ("\n".join(output) + "\n").encode()


def test_validates_legacy_and_udiff_schemas():
    assert MODULE.validate_csv(legacy_csv(), date(2020, 1, 1)) == ("legacy", 100)
    assert MODULE.validate_csv(udiff_csv(), date(2024, 7, 8)) == ("udiff", 100)


def test_rejects_wrong_session_date():
    with pytest.raises(ValueError, match="response dates"):
        MODULE.validate_csv(legacy_csv(), date(2020, 1, 2))


def test_accepts_legacy_two_digit_year():
    assert MODULE.validate_csv(
        legacy_csv(day="13-Jul-20"), date(2020, 7, 13),
    ) == ("legacy", 100)


def test_loads_json_calendar_in_india_timezone(tmp_path):
    calendar = tmp_path / "calendar.json"
    timestamp = datetime(2020, 1, 2, 4, tzinfo=timezone.utc).timestamp()
    calendar.write_text(json.dumps({"timestamp": [timestamp, timestamp]}))
    assert MODULE.load_calendar(calendar) == [date(2020, 1, 2)]


def test_legacy_fetch_falls_back_to_direct_archive():
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as zipped:
        zipped.writestr("cm05FEB2019bhav.csv", legacy_csv(day="05-FEB-2019"))

    class Response:
        content = payload.getvalue()

        @staticmethod
        def raise_for_status():
            return None

    class Archive:
        @staticmethod
        def bhavcopy_raw(trading_date):
            raise RuntimeError("reports API unavailable")

        @staticmethod
        def get(route, **params):
            assert route == "bhavcopy"
            assert params == {"yyyy": 2019, "MMM": "FEB", "dd": "05"}
            return Response()

    raw, request_count = MODULE.fetch_one(Archive(), date(2019, 2, 5))
    assert request_count == 2
    assert MODULE.validate_csv(raw, date(2019, 2, 5)) == ("legacy", 100)
