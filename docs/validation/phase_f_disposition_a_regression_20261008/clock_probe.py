"""Reproduce collection-to-execution delay against before/current test bytes."""
import json
import runpy
import tempfile
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from ats.config import REPO_ROOT
from ats.data import execution_prices as prices


folder = Path(__file__).resolve().parent
path = "tests/test_phase_f_trader_a.py"
with zipfile.ZipFile(folder / "before_sources.zip") as archive:
    old_bytes = archive.read(path)
with tempfile.TemporaryDirectory(prefix="phase-f-clock-before-") as temporary:
    old_path = Path(temporary) / "old_test.py"
    old_path.write_bytes(old_bytes)
    old = runpy.run_path(str(old_path))["test_quote_fail_closed"]
    current = runpy.run_path(str(REPO_ROOT / path))["test_quote_fail_closed"]
    point = datetime.now(UTC) + timedelta(minutes=6)
    class LaterClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return point.astimezone(tz) if tz else point.replace(tzinfo=None)
    results = {}
    with patch.object(prices, "datetime", LaterClock), patch.object(prices, "session_context",
            lambda now: (True, now - timedelta(days=1))):
        for name, function in [("before", old), ("current", current)]:
            function.__globals__["datetime"] = LaterClock
            changes, reason = function.pytestmark[0].args[1][14]
            try:
                function(None, changes, reason)
            except AssertionError as exc:
                assert name == "before" and "quote_stale" in str(exc)
                results[name] = {"test_passed": False, "failure": str(exc)}
            else:
                assert name == "current"
                results[name] = {"test_passed": True, "future_quote_rejected": True}
    assert set(results) == {"before", "current"}
(folder / "clock-probe.json").write_text(json.dumps({"simulated_collection_delay_seconds": 360,
    "production_price_validation_modified": False, "results": results}, indent=2))
print(json.dumps({"baseline_reproduced": True, "current_fixed": True}))
