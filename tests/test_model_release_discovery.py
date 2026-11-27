"""Model release candidates from stored OpenRouter lists (ant-data-onboarding 11.2)."""
from __future__ import annotations

import json
import shutil
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from ats.data import model_release_discovery as discovery
from ats.data.catalog.structured import StructuredCatalog
from ats.data.core.structured_models import FetchRequest
from ats.data.model_releases import ModelReleaseError, ModelReleaseRegistry
from ats.data.pipelines.structured.ingestion import IngestionPipeline
from ats.data.sources.openrouter_pricing import OpenRouterPricingAdapter
from ats.data.stores.structured.repository import SQLiteStructuredRepository
from ats.runtime import model_release_cli

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE = date(2026, 11, 27)
NEXT = date(2026, 11, 28)
LATER = date(2026, 11, 29)
SETTINGS = discovery.DiscoverySettings(
    "2026-11-27", (("anthropic", "ANTHROPIC"), ("openai", "OPENAI"), ("z-ai", "ZAI")))


def _listed(model_id, listed=date(2026, 11, 28), name="", **extra):
    created = int(datetime(listed.year, listed.month, listed.day, 16, tzinfo=UTC).timestamp())
    base = model_id.partition(":")[0]
    return {"id": model_id, "canonical_slug": base + "-20261128", "name": name or model_id, "created": created,
            "pricing": {"prompt": "0.000001", "completion": "0.000005"}, **extra}


OLD = [_listed("openai/gpt-6.2-astra", date(2026, 11, 17)), _listed("openai/gpt-5", date(2026, 8, 1))]
NEW = [
    _listed("openai/gpt-7:batch", name="OpenAI: GPT-7 (batch)"),
    _listed("openai/gpt-7", name="OpenAI: GPT-7"),
    _listed("openai/gpt-7-pro"),
    _listed("openai/gpt-latest", alias_target="openai/gpt-7"),
    _listed("openai/gpt-chat-latest"),
    _listed("~anthropic/claude-latest", alias_target="anthropic/claude-new"),
    _listed("mistralai/mistral-next"),
]


def _repository(tmp_path):
    repository = SQLiteStructuredRepository(tmp_path / "data.sqlite", artifact_root=tmp_path / "artifacts")
    repository.bootstrap_catalog(StructuredCatalog.load())
    return repository


def _ingest(repository, day, rows):
    payload = json.dumps({"data": rows}).encode()

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, *_args):
            return payload

    adapter = OpenRouterPricingAdapter(
        opener=lambda _request, timeout=30: Response(), min_models=1,
        clock=lambda: datetime(day.year, day.month, day.day, 7, 45, tzinfo=UTC))
    result = IngestionPipeline(repository).run(adapter, FetchRequest(
        source_id="openrouter_pricing", dataset_id="openrouter_model_pricing_daily"))
    assert result["status"] in {"succeeded", "no_change"}, result


def _record(**over):
    return {"lab_id": "OPENAI", "model_id": "gpt-7", "display_name": "GPT-7", "tier": "general_flagship",
            "release_date": "2026-11-28", "announcement_url": "https://openai.com/index/gpt-7/",
            "status": "confirmed", "registered_at": "2026-11-28T09:00:00Z", **over}


def _registry(*records):
    return ModelReleaseRegistry.from_payload({
        "version": 1, "official_domains": {"OPENAI": ["openai.com"], "ZAI": ["z.ai"]},
        "releases": list(records)})


def _report(repository, registry=None, dismissed=()):
    return discovery.candidates(repository, registry=registry or _registry(), settings=SETTINGS,
                                dismissed=dismissed)


def _ids(report):
    return [item["candidate_id"] for item in report["candidates"]]


@pytest.fixture
def stored(tmp_path):
    repository = _repository(tmp_path)
    _ingest(repository, BASELINE, OLD)
    _ingest(repository, NEXT, OLD + NEW)
    yield repository
    repository.close()


def test_only_new_models_of_watched_labs_become_candidates(stored):
    report = _report(stored)
    assert report["status"] == "ok" and report["latest_snapshot"] == "2026-11-28"
    assert _ids(report) == ["openai/gpt-7", "openai/gpt-7-pro"]


def test_variants_of_one_model_are_one_candidate(stored):
    first = _report(stored)["candidates"][0]
    assert first["variants"] == ["openai/gpt-7", "openai/gpt-7:batch"]
    assert first["name"] == "OpenAI: GPT-7" and first["listed_date"] == "2026-11-28"
    assert first["lab_id"] == "OPENAI" and first["first_seen"] == "2026-11-28"


def test_a_new_variant_of_an_old_model_is_not_new(tmp_path):
    repository = _repository(tmp_path)
    _ingest(repository, BASELINE, OLD)
    _ingest(repository, NEXT, OLD + [_listed("openai/gpt-5:free")])
    assert _report(repository)["candidates"] == []


def test_a_model_later_delisted_stays_a_candidate(stored):
    _ingest(stored, LATER, OLD)
    assert _ids(_report(stored)) == ["openai/gpt-7", "openai/gpt-7-pro"]


def test_a_model_hidden_before_the_baseline_still_counts_as_new(tmp_path):
    repository = _repository(tmp_path)
    _ingest(repository, BASELINE, OLD)
    _ingest(repository, NEXT, OLD + [_listed("openai/gpt-quiet", date(2026, 11, 1))])
    assert _ids(_report(repository)) == ["openai/gpt-quiet"]


def test_registered_and_dismissed_models_drop_out(stored):
    registry = _registry(_record(source_aliases=["gpt-7-pro"]))
    assert _ids(_report(stored, registry)) == []
    assert _ids(_report(stored, dismissed=[{"candidate_id": "OpenAI/GPT-7-Pro"}])) == ["openai/gpt-7"]
    # A record kept as "candidate" in the registry has been looked at; it is not proposed again.
    undecided = _registry(_record(status="candidate", release_date=None))
    assert _ids(_report(stored, undecided)) == ["openai/gpt-7-pro"]


def test_without_a_baseline_list_nothing_is_called_new(tmp_path):
    repository = _repository(tmp_path)
    assert _report(repository)["reason_code"] == "no_snapshot"
    _ingest(repository, NEXT, OLD + NEW)
    report = _report(repository)
    assert report["status"] == "unavailable" and report["reason_code"] == "no_baseline_snapshot"
    assert report["candidates"] == []
    assert "无法判断" in discovery.digest_note(report)


def test_digest_note_names_pending_models(stored):
    note = discovery.digest_note(_report(stored))
    assert note.startswith("模型发布待确认 2 个：")
    assert "openai/gpt-7（上架 2026-11-28）" in note and "model-release-candidates" in note
    assert discovery.digest_note(_report(stored, _registry(_record(source_aliases=["gpt-7-pro"])))) == ""


def test_digest_note_reports_a_read_failure(monkeypatch):
    def broken():
        raise OSError("store unavailable")

    monkeypatch.setattr(discovery, "current_report", broken)
    assert discovery.pending_note() == "模型发布候选读取失败：OSError: store unavailable"


@pytest.fixture
def files(tmp_path, monkeypatch, stored):
    """Copies of the committed config files, and the CLI reading the temporary store."""
    config = tmp_path / "config"
    (config / "data").mkdir(parents=True)
    for name in ("model_releases.yaml", "model_release_dismissed.yaml"):
        shutil.copy(REPO_ROOT / "config" / "data" / name, config / "data" / name)
    monkeypatch.setenv("ATS_CONFIG_DIR", str(config))
    monkeypatch.setenv("ATS_DATA_DB_PATH", str(tmp_path / "data.sqlite"))
    monkeypatch.setenv("ATS_DATA_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    assert discovery.registry_path().is_relative_to(config)
    return config / "data"


def _cli(capsys, action, **kwargs):
    code = model_release_cli.run(f"model-release-{action}", **kwargs)
    return code, json.loads(capsys.readouterr().out)


def test_committed_scope_names_nine_labs():
    settings = discovery.load_settings(REPO_ROOT / "config" / "data" / "model_releases.yaml")
    registry = ModelReleaseRegistry.load(REPO_ROOT / "config" / "data" / "model_releases.yaml")
    assert len(settings.labs) == 9 and settings.baseline_date == "2026-11-27"
    assert {lab for _, lab in settings.labs} >= {record.lab_id for record in registry.records}
    discovery.load_dismissed(REPO_ROOT / "config" / "data" / "model_release_dismissed.yaml")


def test_confirm_appends_a_record_and_keeps_the_file(files, capsys):
    before = (files / "model_releases.yaml").read_text(encoding="utf-8")
    code, listed = _cli(capsys, "candidates")
    assert code == 0 and [item["candidate_id"] for item in listed["candidates"]] == [
        "openai/gpt-7", "openai/gpt-7-pro"]

    code, result = _cli(capsys, "confirm", candidate_ids=["openai/gpt-7"], tier="general_flagship",
                        release_date="2026-11-28", url="https://openai.com/index/gpt-7/",
                        aliases=["openai/gpt-7-pro"])
    assert code == 0, result
    after = (files / "model_releases.yaml").read_text(encoding="utf-8")
    assert after.startswith(before.rstrip("\n"))  # comments and earlier records untouched
    registry = ModelReleaseRegistry.load(files / "model_releases.yaml")
    record = registry.flagship("OPENAI", as_of="2027-12-31")
    assert record.model_id == "gpt-7" and record.display_name == "GPT-7"
    assert record.release_date == "2026-11-28" and record.source_aliases == ("gpt-7-pro",)
    assert _cli(capsys, "candidates")[1]["candidates"] == []


@pytest.mark.parametrize("missing", ["tier", "release_date", "url"])
def test_confirm_needs_tier_date_and_announcement(files, capsys, missing):
    arguments = {"candidate_ids": ["openai/gpt-7"], "tier": "other", "release_date": "2026-11-28",
                 "url": "https://openai.com/index/gpt-7/", missing: ""}
    before = (files / "model_releases.yaml").read_bytes()
    code, result = _cli(capsys, "confirm", **arguments)
    assert code == 1 and "缺少" in result["reason_code"]
    assert (files / "model_releases.yaml").read_bytes() == before


def test_confirm_refuses_an_unofficial_link_and_writes_nothing(files, capsys):
    before = (files / "model_releases.yaml").read_bytes()
    code, result = _cli(capsys, "confirm", candidate_ids=["openai/gpt-7"], tier="other",
                        release_date="2026-11-28", url="https://news.example.com/gpt-7")
    assert code == 1 and "lab's own domain" in result["reason_code"]
    assert (files / "model_releases.yaml").read_bytes() == before
    assert not list(files.glob(".*.tmp"))


def test_confirm_only_accepts_a_pending_candidate(files, capsys):
    code, result = _cli(capsys, "confirm", candidate_ids=["openai/gpt-5"], tier="other",
                        release_date="2026-11-28", url="https://openai.com/index/gpt-5/")
    assert code == 1 and result["reason_code"] == "not_a_pending_candidate:openai/gpt-5"


def test_dismiss_records_time_and_reason(files, capsys):
    code, _ = _cli(capsys, "dismiss", candidate_ids=["openai/gpt-7-pro"], note="同一型号的高配版")
    assert code == 0
    code, _ = _cli(capsys, "dismiss", candidate_ids=["openai/gpt-7"])
    assert code == 0
    items = discovery.load_dismissed(files / "model_release_dismissed.yaml")
    assert [item["candidate_id"] for item in items] == ["openai/gpt-7-pro", "openai/gpt-7"]
    assert items[0]["reason"] == "同一型号的高配版" and items[0]["dismissed_at"].endswith("Z")
    assert "reason" not in items[1]
    assert _cli(capsys, "candidates")[1]["candidates"] == []


def test_add_registers_a_release_the_list_never_carried(files, capsys):
    code, result = _cli(capsys, "add", lab="zai", model="glm-9", tier="general_flagship",
                        release_date="2026-12-01", url="https://z.ai/blog/glm-9", name="GLM-9")
    assert code == 0, result
    registry = ModelReleaseRegistry.load(files / "model_releases.yaml")
    assert registry.flagship("ZAI", as_of="2027-12-31").model_id == "glm-9"
    code, result = _cli(capsys, "add", lab="ZAI", model="glm-9", tier="other",
                        release_date="2026-12-02", url="https://z.ai/blog/glm-9")
    assert code == 1 and "more than once" in result["reason_code"]


def test_add_can_hold_an_undated_release_without_starting_a_clock(files, capsys):
    code, result = _cli(capsys, "add", lab="XAI", model="grok-9", tier="general_flagship", status="candidate")
    assert code == 0, result
    registry = ModelReleaseRegistry.load(files / "model_releases.yaml")
    assert registry.flagship("XAI", as_of="2027-12-31").model_id != "grok-9"


def test_append_refuses_a_file_whose_list_is_not_last(tmp_path):
    path = tmp_path / "model_releases.yaml"
    path.write_text("version: 1\nofficial_domains:\n  OPENAI: [openai.com]\nreleases:\nother: {}\n",
                    encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ModelReleaseError):
        discovery.register(lab_id="OPENAI", model_id="gpt-7", tier="other", release_date="2026-11-28",
                           announcement_url="https://openai.com/index/gpt-7/", path=path)
    assert path.read_bytes() == before
