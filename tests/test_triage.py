"""News triage — batching and the stored score columns (no network).

The old monitor-driven cases (noise filtering inside `pead.monitor`) went with that entry on 2026-10-27;
the information analyst reads admitted documents and no longer scores a fresh news batch.
"""

from datetime import datetime, timezone

from ats.agents.information import triage
from ats.agents.pead.outputs import TriageBatchView, TriageItemView
from ats.memory import get_store
from ats.schemas.news import NewsItem

NOW = datetime.now(timezone.utc)


def test_score_items_batches_and_maps_idx(monkeypatch):
    items = [NewsItem(id=f"i{n}", source="finnhub", headline=f"h{n}", published_at=NOW)
             for n in range(3)]
    view = TriageBatchView(items=[
        TriageItemView(idx=0, materiality=0.8, category="capex"),
        TriageItemView(idx=2, materiality=0.1, category="noise"),
        TriageItemView(idx=99, materiality=1.0, category="bogus"),   # out of range -> dropped
    ])
    monkeypatch.setattr("ats.agents.information.triage.run_structured", lambda *a, **k: view)
    scores = triage.score_items("COHR", "thesis", items)
    assert scores == {"i0": (0.8, "capex"), "i2": (0.1, "noise")}


def test_migration_adds_triage_columns():
    cols = {r["name"] for r in get_store().conn.execute("PRAGMA table_info(pead_events)")}
    assert "triage_score" in cols and "triage_category" in cols
