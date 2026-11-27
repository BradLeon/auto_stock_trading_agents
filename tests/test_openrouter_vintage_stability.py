"""Overlapping-window re-fetches of OpenRouter rankings must not rewrite unchanged daily values."""

from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path

from ats.data.catalog.structured import StructuredCatalog
from ats.data.core.structured_models import FetchRequest
from ats.data.pipelines.structured.ingestion import IngestionPipeline
from ats.data.products import DataProducts
from ats.data.sources import openrouter_rankings as source
from ats.data.sources.openrouter_rankings import OpenRouterRankingsAdapter
from ats.data.stores.structured.repository import SQLiteStructuredRepository

MODELS = ("openai/gpt-4o", "anthropic/claude-3", "newco/unknown-model", "other")
FIRST_DAY = date(2026, 9, 1)


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.payload


class _Harness:
    """One governed store; each `ingest` is a separate scheduled run with its own fetch time and window."""

    def __init__(self, tmp_path):
        root = Path(tmp_path)
        self.repository = SQLiteStructuredRepository(root / "openrouter.sqlite", artifact_root=root / "artifacts")
        self.repository.bootstrap_catalog(StructuredCatalog.load())

    def ingest(self, *, run: int, start: date, days: int, revised: dict | None = None):
        fetched = datetime(2026, 9, 16, tzinfo=timezone.utc) + timedelta(days=7 * run)
        end = start + timedelta(days=days - 1)
        rows = []
        for offset in range(days):
            day = start + timedelta(days=offset)
            for index, model in enumerate(MODELS):
                tokens = (revised or {}).get((day, model), 1000 * (index + 1) + (day - FIRST_DAY).days)
                rows.append({"date": day.isoformat(), "model_permaslug": model, "total_tokens": str(tokens)})
        payload = json.dumps({"data": rows, "meta": {
            "as_of": fetched.isoformat(), "version": "v1",
            "start_date": start.isoformat(), "end_date": end.isoformat()}}).encode()
        adapter = OpenRouterRankingsAdapter(api_key="fixture", clock=lambda: fetched,
                                            opener=lambda _request, timeout=30: _Response(payload))
        return IngestionPipeline(self.repository).run(adapter, FetchRequest(
            source_id="openrouter_rankings", dataset_id="openrouter_rankings_daily",
            query_scope={"start_date": start.isoformat(), "end_date": end.isoformat()}))

    def rows(self, **filters):
        return self.repository.observations(dataset_id="openrouter_rankings_daily", limit=100_000, **filters)

    def vintages(self):
        return len(self.rows(latest_only=False))

    def author_tokens(self, **filters):
        """Tokens by author in the one complete week of the first window, as the current-view product sums them."""
        volume = DataProducts(structured_repository=self.repository).openrouter_token_volume_series(**filters)
        return volume["rows"][0]["author_tokens"]

    def cell(self, day: date, model: str, **filters):
        entity = f"OPENROUTER_MODEL:{model.upper().replace('/', ':')}"
        return [row["value"] for row in self.rows(entity_id=entity, **filters) if row["period"] == day.isoformat()]


def test_same_window_refetched_later_is_no_change(tmp_path):
    store = _Harness(tmp_path)
    store.ingest(run=0, start=FIRST_DAY, days=14)
    before = store.vintages()
    second = store.ingest(run=1, start=FIRST_DAY, days=14)
    assert (second["status"], second["accepted"], second["unchanged"]) == ("no_change", 0, 56)
    assert store.vintages() == before


def test_shifted_overlap_window_appends_only_new_days(tmp_path):
    store = _Harness(tmp_path)
    store.ingest(run=0, start=FIRST_DAY, days=14)
    second = store.ingest(run=1, start=FIRST_DAY + timedelta(days=7), days=14)
    assert (second["status"], second["accepted"], second["unchanged"]) == ("succeeded", 28, 28)
    assert store.vintages() == 56 + 28


def test_revised_day_appends_one_vintage_and_replays_both(tmp_path):
    store = _Harness(tmp_path)
    store.ingest(run=0, start=FIRST_DAY, days=14)
    cell = (FIRST_DAY, "openai/gpt-4o")
    second = store.ingest(run=1, start=FIRST_DAY, days=14, revised={cell: 9999})
    assert (second["status"], second["accepted"], second["unchanged"]) == ("succeeded", 1, 55)
    assert store.cell(*cell) == [9999]
    assert store.cell(*cell, as_of=datetime(2026, 9, 17, tzinfo=timezone.utc)) == [1000]


def test_value_returning_to_an_earlier_one_appends_again(tmp_path):
    store = _Harness(tmp_path)
    cell = (FIRST_DAY, "openai/gpt-4o")
    store.ingest(run=0, start=FIRST_DAY, days=14)
    store.ingest(run=1, start=FIRST_DAY, days=14, revised={cell: 9999})
    third = store.ingest(run=2, start=FIRST_DAY, days=14)
    assert third["accepted"] == 1
    assert store.cell(*cell) == [1000]


def test_one_day_refetch_does_not_duplicate_the_day(tmp_path):
    """A narrow request must not open a second series that the current query then sums with the first."""
    store = _Harness(tmp_path)
    store.ingest(run=0, start=FIRST_DAY, days=14)
    monday = date(2026, 9, 7)  # inside the only complete UTC week of the first window
    second = store.ingest(run=1, start=monday, days=1)
    assert store.cell(monday, "openai/gpt-4o") == [1006]
    assert (second["status"], second["accepted"]) == ("no_change", 0)
    volume = DataProducts(structured_repository=store.repository).openrouter_token_volume_series()
    assert volume["rows"][0]["daily_tokens"][monday.isoformat()] == 1006 + 2006 + 3006 + 4006


def test_one_day_refetch_with_a_revision_replaces_the_value(tmp_path):
    store = _Harness(tmp_path)
    cell = (FIRST_DAY, "openai/gpt-4o")
    store.ingest(run=0, start=FIRST_DAY, days=14)
    second = store.ingest(run=1, start=FIRST_DAY, days=1, revised={cell: 9999})
    assert second["accepted"] == 1
    assert store.cell(*cell) == [9999]


def _week(base: int) -> int:
    """Seven-day total of one fixture model over 2026-09-07..13."""
    return sum(base + offset for offset in range(6, 13))


def test_author_correction_appends_and_counts_the_day_once(tmp_path, monkeypatch):
    store = _Harness(tmp_path)
    store.ingest(run=0, start=FIRST_DAY, days=14)
    monkeypatch.setitem(source.AUTHOR_ALIASES, "newco", "newco")
    second = store.ingest(run=1, start=FIRST_DAY, days=14)
    assert (second["status"], second["accepted"], second["unchanged"]) == ("succeeded", 14, 42)
    current = store.author_tokens()
    assert current["newco"] == _week(3000) and "unknown_author" not in current
    earlier = store.author_tokens(as_of=datetime(2026, 9, 17, tzinfo=timezone.utc))
    assert earlier["unknown_author"] == _week(3000) and "newco" not in earlier


def test_author_correction_taken_back_shows_the_old_author_once(tmp_path, monkeypatch):
    store = _Harness(tmp_path)
    store.ingest(run=0, start=FIRST_DAY, days=14)
    with monkeypatch.context() as patched:
        patched.setitem(source.AUTHOR_ALIASES, "newco", "newco")
        store.ingest(run=1, start=FIRST_DAY, days=14)
    third = store.ingest(run=2, start=FIRST_DAY, days=14)
    assert third["accepted"] == 14
    current = store.author_tokens()
    assert current["unknown_author"] == _week(3000) and "newco" not in current


def test_a_second_methodology_regime_is_still_reported_as_drift(tmp_path, monkeypatch):
    store = _Harness(tmp_path)
    store.ingest(run=0, start=FIRST_DAY, days=14)
    monkeypatch.setattr(source, "METHODOLOGY_REGIME", "openrouter_public_routed_tokens/v2")
    store.ingest(run=1, start=FIRST_DAY, days=14)
    volume = DataProducts(structured_repository=store.repository).openrouter_token_volume_series()
    assert volume["quality"]["status"] == "methodology_drift"
