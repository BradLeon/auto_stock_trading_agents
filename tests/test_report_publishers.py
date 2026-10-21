"""Readable reports published after a Dispatcher run (report_publishers).

Real projections go through the real technical publisher into the real task-projection store; only the
output directory is redirected. The chain-evidence writer is replaced by a recorder because rendering it
needs the whole evidence ledger (it has its own tests)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ats.agents.technical import review as tech_review
from ats.memory import get_store
from ats.schemas.technical import TechnicalReading, TechnicalReview
from ats.workflow import report_publishers as rp

NOW = datetime(2026, 10, 21, 14, 0, tzinfo=timezone.utc)


def _review(symbols=("NVDA", "COHR")) -> TechnicalReview:
    readings = [
        TechnicalReading(symbol="NVDA", score=7, target_exposure=1.0, prev_exposure=1.0, close=190.0,
                         sma20=185.0, sma50=180.0, sma200=150.0, score_detail={"above_sma20": True}),
        TechnicalReading(symbol="COHR", score=2, target_exposure=0.45, prev_exposure=1.0, bear_fired=True,
                         close=100.0, sma20=104.0, sma50=110.0, sma200=105.0),
    ]
    return TechnicalReview(as_of=NOW, vix=18.0, vix3m=19.5,
                           readings=[r for r in readings if r.symbol in symbols])


def _publish(review: TechnicalReview) -> list[str]:
    store = get_store()
    assert tech_review._publish_projection(store, review) == len(review.readings)
    return [row["projection_id"] for row in store.task_projection_envelopes(
        agent_role="technical_review", status="published", limit=50)]


def _outcomes(refs, *, reused=False, status="succeeded"):
    return [{"task_id": "technical-review", "status": status, "reused": reused,
             "scope": {"kind": "entity", "id": "X"}, "projection_refs": [ref]} for ref in refs]


@pytest.fixture
def out_dir(tmp_path, monkeypatch):
    from ats.config import load_technical_config

    real = load_technical_config("technical")
    cfg = real.model_copy(update={"output_dir": str(tmp_path)})
    monkeypatch.setattr("ats.config.load_technical_config", lambda name="technical": cfg)
    return tmp_path


def _store_path():
    return str(get_store().path)


def test_one_summary_report_covers_every_symbol_of_the_run(out_dir):
    refs = _publish(_review())
    result = rp.technical_report(_outcomes(refs), store_path=_store_path())
    assert result["status"] == "written" and result["symbols"] == 2
    files = list(out_dir.glob("*.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "NVDA" in text and "COHR" in text
    assert "100% → 45%" in text                         # the change against the previous reading survives


def test_a_second_report_on_the_same_day_is_a_new_version_file(out_dir):
    refs = _publish(_review())
    first = rp.technical_report(_outcomes(refs), store_path=_store_path())
    second = rp.technical_report(_outcomes(refs), store_path=_store_path())
    assert first["path"] != second["path"] and second["path"].endswith("-v2.md")
    assert len(list(out_dir.glob("*.md"))) == 2


def test_a_run_that_only_reused_projections_writes_no_report(out_dir):
    refs = _publish(_review())
    result = rp.technical_report(_outcomes(refs, reused=True), store_path=_store_path())
    assert result == {"report": "technical", "status": "skipped", "reason": "no_new_projection"}
    assert list(out_dir.glob("*.md")) == []


def test_failed_tasks_do_not_count_as_published(out_dir):
    refs = _publish(_review())
    result = rp.technical_report(_outcomes(refs, status="missing"), store_path=_store_path())
    assert result["reason"] == "no_new_projection"


def test_an_unset_output_directory_is_a_skip_not_an_error(tmp_path, monkeypatch):
    from ats.config import load_technical_config

    cfg = load_technical_config("technical").model_copy(update={"output_dir": ""})
    monkeypatch.setattr("ats.config.load_technical_config", lambda name="technical": cfg)
    refs = _publish(_review())
    result = rp.technical_report(_outcomes(refs), store_path=_store_path())
    assert result["status"] == "skipped" and result["reason"] == "output_dir_unset_or_missing"


def test_chain_evidence_report_follows_a_new_sector_review_only(monkeypatch, tmp_path):
    written = []

    def fake_write(cfg, store, *, as_of, ind_cfg=None, **kwargs):
        written.append(cfg.name)
        return tmp_path / f"{cfg.name}.md"

    monkeypatch.setattr("ats.chain.report.write", fake_write)
    sector = {"task_id": "sector-review", "status": "succeeded", "reused": False,
              "scope": {"kind": "sector", "id": "ai_hardware"}, "projection_refs": ["p1"]}
    result = rp.chain_evidence_report([sector], store_path=_store_path())
    assert result["status"] == "written" and written == ["ai_hardware"]

    again = rp.chain_evidence_report([{**sector, "reused": True}], store_path=_store_path())
    assert again["reason"] == "no_new_sector_review" and written == ["ai_hardware"]
    failed = rp.chain_evidence_report([{**sector, "status": "missing"}], store_path=_store_path())
    assert failed["reason"] == "no_new_sector_review"


def test_publish_ignores_duplicates_and_runs_without_a_result():
    assert rp.publish("technical-review", {"duplicate": True, "result": {"outcomes": []}},
                      store_path=None) == []
    assert rp.publish("technical-review", {"status": "skipped"}, store_path=None) == []
    assert rp.publish("macro-review", {"result": {"outcomes": []}}, store_path=None) == []


def test_a_publisher_failure_is_reported_and_never_raised(monkeypatch):
    def boom(outcomes, *, store_path):
        raise RuntimeError("disk full")

    monkeypatch.setattr(rp, "PUBLISHERS", {"technical-review": (boom,)})
    results = rp.publish("technical-review", {"result": {"outcomes": []}}, store_path=None)
    assert results == [{"report": "boom", "status": "failed", "error": "RuntimeError: disk full"}]
