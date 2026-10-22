import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "download_phase6_corporate_announcements.py"
)
SPEC = importlib.util.spec_from_file_location(
    "download_phase6_corporate_announcements", SCRIPT,
)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_month_windows_cover_partial_boundaries():
    assert MODULE.month_windows(date(2020, 1, 15), date(2020, 3, 2)) == [
        (date(2020, 1, 15), date(2020, 1, 31)),
        (date(2020, 2, 1), date(2020, 2, 29)),
        (date(2020, 3, 1), date(2020, 3, 2)),
    ]


def test_validates_announcement_list():
    payload, rows = MODULE.validate_payload(
        json.dumps([{"symbol": "ABC"}, {"symbol": "XYZ"}]).encode()
    )
    assert rows == 2
    assert payload[0]["symbol"] == "ABC"


def test_rejects_non_list_response():
    with pytest.raises(ValueError, match="expected announcement list"):
        MODULE.validate_payload(json.dumps({"error": "blocked"}).encode())
