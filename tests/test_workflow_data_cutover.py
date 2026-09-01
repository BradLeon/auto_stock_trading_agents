"""Phase 10.4 acceptance: orchestration inputs, release records and rollback."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from ats.data.pipelines.structured.release import ReleaseManager, load_release_overlay
from ats.data.stores.structured.repository import SQLiteStructuredRepository
from ats.graph.chief import assemble_context
from ats.graph.chief_state import ChiefDecisionState
from ats.runtime import scheduler


def _release_manager(tmp_path) -> ReleaseManager:
    repository = SQLiteStructuredRepository(
        tmp_path / "data.sqlite", artifact_root=tmp_path / "artifacts")
    repository.bootstrap_catalog()
    return ReleaseManager(repository, path=tmp_path / "releases.yaml")


def test_workflow_boundaries_keep_memory_outputs_out_of_data_products(monkeypatch):
    from ats.data.products import workflow_data_boundary

    monkeypatch.delenv("ATS_STRUCTURED_CHIEF_GRAPH_MODE", raising=False)
    monkeypatch.delenv("ATS_STRUCTURED_RUNTIME_SCHEDULER_MODE", raising=False)
    chief = workflow_data_boundary("chief-graph")
    scheduler_boundary = workflow_data_boundary("runtime_scheduler")

    assert chief.consumer == "chief_graph" and chief.mode == "shadow"
    assert "decision" in chief.memory_outputs[0]
    assert scheduler_boundary.consumer == "runtime_scheduler"
    assert scheduler_boundary.mode == "shadow"
    assert "earnings calendar" in scheduler_boundary.runtime_inputs[0]
    assert "reports" in scheduler_boundary.memory_outputs[0]


def test_chief_uses_boundary_metadata_without_routing_memory_through_data(monkeypatch):
    from ats.data.products import WorkflowDataBoundary
    import ats.data.products as products
    from ats.graph import chief as chief_graph

    seen = []
    monkeypatch.setattr(products, "workflow_data_boundary", lambda consumer: (
        seen.append(consumer) or WorkflowDataBoundary(
            consumer="chief_graph", mode="shadow", persistent_inputs=(),
            runtime_inputs=(), memory_outputs=("decisions",))))
    monkeypatch.setattr("ats.trader.execute.pead_event_data", lambda: {"events": []})
    state = ChiefDecisionState(
        cycle_id="boundary-test", as_of=chief_graph._now(), source="chief",
        decide=False, dry_run=True,
    )

    assert assemble_context(state)["event_data"] == {"events": []}
    assert seen == ["chief_graph"]


def test_scheduler_daily_uses_its_boundary_before_workflow_side_effects(monkeypatch):
    import ats.data.products as products
    from ats.data.products import WorkflowDataBoundary

    seen = []
    monkeypatch.setattr(products, "workflow_data_boundary", lambda consumer: (
        seen.append(consumer) or WorkflowDataBoundary(
            consumer="runtime_scheduler", mode="shadow", persistent_inputs=(),
            runtime_inputs=(), memory_outputs=())))
    for name in (
        "_event_triggers", "_news_backfill_daily", "pead_daily", "_technical_daily",
        "_intel_digest", "_perf_snapshot", "_perf_risk_digest", "_journal_marks", "_chief_daily",
    ):
        monkeypatch.setattr(scheduler, name, lambda **_kwargs: None)

    scheduler._daily(dry_run=True)

    assert seen == ["runtime_scheduler"]


def test_scheduler_isolates_a_failed_stage_and_runs_later_stages(monkeypatch, caplog):
    """A source/batch failure must not suppress the rest of the daily cascade."""
    stages: list[str] = []
    monkeypatch.setattr(scheduler, "_event_triggers", lambda **_kwargs: stages.append("events"))

    def fail_news():
        stages.append("news")
        raise RuntimeError("publisher unavailable")

    monkeypatch.setattr(scheduler, "_news_backfill_daily", fail_news)
    monkeypatch.setattr(scheduler, "pead_daily",
                        lambda **_kwargs: stages.append("pead"))
    monkeypatch.setattr(scheduler, "_technical_daily", lambda: stages.append("technical"))
    monkeypatch.setattr(scheduler, "_intel_digest", lambda **_kwargs: stages.append("intel"))
    monkeypatch.setattr(scheduler, "_perf_snapshot", lambda: stages.append("performance"))
    monkeypatch.setattr(scheduler, "_perf_risk_digest", lambda: stages.append("risk"))
    monkeypatch.setattr(scheduler, "_journal_marks", lambda: stages.append("journal"))
    monkeypatch.setattr(scheduler, "_chief_daily",
                        lambda **_kwargs: stages.append("chief"))

    with caplog.at_level("WARNING"):
        scheduler._daily(dry_run=True)

    assert stages == [
        "events", "news", "pead", "technical", "intel", "performance", "risk", "journal", "chief",
    ]
    assert "daily stage news backfill failed: publisher unavailable" in caplog.text


def test_scheduler_data_accesses_use_unified_runtime_products_and_pipelines():
    source = (Path(__file__).resolve().parents[1] / "src/ats/runtime/scheduler.py").read_text(
        encoding="utf-8")

    for legacy_import in (
        "from ..data import documents",
        "from ..data import earnings_calendar, period",
        "from ..data import research as research_data",
        "from ..data import yahoo_news",
    ):
        assert legacy_import not in source
    for unified_import in (
        "from ..data.runtime import earnings as earnings_calendar",
        "from ..data.products import earnings",
        "from ..data.pipelines.unstructured import news as news_pipeline",
        "from ..data.pipelines.unstructured import research as research_pipeline",
    ):
        assert unified_import in source


def test_release_confirmation_product_keeps_event_date_guard(monkeypatch):
    from ats.data.products import earnings

    class Print:
        date = date(2026, 8, 4)
        reported = False
        eps_actual = None

    monkeypatch.setattr(
        "ats.data.pipelines.unstructured.official.release_filed_on_or_after",
        lambda symbol, *, expected_date: (expected_date == Print.date, f"8-K {symbol}"),
    )

    assert earnings.confirm_reported("MSFT", Print()) == (True, "8-K MSFT")


def test_shadow_release_and_rollback_are_isolated_per_orchestration_consumer(tmp_path):
    manager = _release_manager(tmp_path)

    chief_check = manager.check_consumer("chief_graph", mode="shadow")
    scheduler_check = manager.check_consumer("runtime_scheduler", mode="shadow")
    assert chief_check["ready"] is True
    assert scheduler_check["ready"] is True

    assert manager.apply(chief_check)["applied"] is True
    assert manager.apply(scheduler_check)["applied"] is True
    chief_rollback = manager.rollback(
        kind="consumer", target_id="chief_graph", mode="legacy")
    assert chief_rollback["previous_mode"] == "shadow"

    overlay = load_release_overlay(tmp_path / "releases.yaml")
    assert overlay["consumers"] == {
        "chief_graph": "legacy",
        "runtime_scheduler": "shadow",
    }
    assert [row["action"] for row in overlay["history"]] == [
        "set_mode", "set_mode", "rollback",
    ]
