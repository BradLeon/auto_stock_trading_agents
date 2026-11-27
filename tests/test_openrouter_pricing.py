"""OpenRouter listed prices and the estimated spend share built from them."""

from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from urllib.error import URLError

import pytest

from ats.data.adapters.structured.registry import validate_source_registration
from ats.data.catalog.structured import StructuredCatalog
from ats.data.core.structured_models import FetchRequest, IngestionStatus
from ats.data.pipelines.structured.ingestion import IngestionPipeline
from ats.data.products import DataProducts
from ats.data.sources.openrouter_pricing import (
    OpenRouterPricingAdapter, OpenRouterPricingError, UnresolvedPrice, cache_read_price,
    parse_models_payload, reference_price,
)
from ats.data.sources.openrouter_rankings import OpenRouterRankingsAdapter
from ats.data.stores.structured.repository import SQLiteStructuredRepository


NOW = datetime(2026, 12, 20, tzinfo=timezone.utc)
WEEK_BEFORE = date(2026, 11, 30)   # Monday, usage only
WEEK_PRICED = date(2026, 12, 7)    # Monday, usage and prices
CLAUDE = "anthropic/claude-opus-5.5-20260921"
GPT = "openai/gpt-6-20261001"


def _model(model_id, canonical, prompt, completion, **extra):
    pricing = {"prompt": prompt, "completion": completion}
    pricing.update(extra.pop("pricing", {}))
    return {"id": model_id, "canonical_slug": canonical, "name": model_id, "pricing": pricing,
            **extra}


def _models(claude=("0.000004", "0.00002")):
    return [
        _model("anthropic/claude-opus-5.5", CLAUDE, *claude),
        _model("anthropic/claude-opus-5.5:batch", CLAUDE, "0.000002", "0.00001"),
        _model("openai/gpt-6", GPT, "0.000001", "0.000005"),
        _model("google/gemma-5:free", "google/gemma-5-20261001", "0", "0"),
        _model("openrouter/auto", "openrouter/auto", "-1", "-1"),
        _model("~anthropic/claude-latest", "~anthropic/claude-latest", "0.000004", "0.00002",
               alias_target="anthropic/claude-opus-5.5"),
    ]


def _bytes(models):
    return json.dumps({"data": models}).encode()


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, *_args):
        return self.payload


def _repository(tmp_path):
    root = Path(tmp_path)
    repository = SQLiteStructuredRepository(root / "pricing.sqlite",
                                            artifact_root=root / "artifacts")
    repository.bootstrap_catalog(StructuredCatalog.load())
    return repository


def _ingest_prices(repository, day, models=None, hour=7, expect="succeeded"):
    payload = _bytes(models or _models())
    adapter = OpenRouterPricingAdapter(
        opener=lambda _request, timeout=30: _Response(payload), min_models=1,
        clock=lambda: datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc))
    result = IngestionPipeline(repository).run(
        adapter, FetchRequest(source_id="openrouter_pricing",
                              dataset_id="openrouter_model_pricing_daily"))
    assert result["status"] == expect, result
    return result


def _ingest_usage(repository, start, days, rows):
    data = [{"date": (start + timedelta(days=offset)).isoformat(), "model_permaslug": model,
             "total_tokens": str(tokens)}
            for offset in range(days) for model, tokens in rows]
    payload = json.dumps({"data": data, "meta": {"version": "v1"}}).encode()
    adapter = OpenRouterRankingsAdapter(
        api_key="fixture", opener=lambda _request, timeout=30: _Response(payload),
        clock=lambda: NOW)
    result = IngestionPipeline(repository).run(
        adapter, FetchRequest(source_id="openrouter_rankings",
                              dataset_id="openrouter_rankings_daily",
                              query_scope={"start_date": start.isoformat(),
                                           "end_date": (start + timedelta(days=days - 1)).isoformat()}))
    assert result["status"] == "succeeded", result


USAGE = [(CLAUDE, 100), (GPT, 300), ("google/gemma-5-20261001:free", 50), ("other", 50)]


def _priced_products(tmp_path, *, price_days=range(7), usage=USAGE):
    repository = _repository(tmp_path)
    _ingest_usage(repository, WEEK_BEFORE, 14, usage)
    for offset in price_days:
        _ingest_prices(repository, WEEK_PRICED + timedelta(days=offset))
    return DataProducts(structured_repository=repository)


# --- parsing -------------------------------------------------------------------

def test_source_registration_is_governed():
    assert validate_source_registration("openrouter_pricing")["valid"]
    metrics = {item.id for item in StructuredCatalog.load().metrics()}
    assert {"ai.openrouter.price.input", "ai.openrouter.price.output",
            "ai.openrouter.estimated_spend_share"} <= metrics


def test_prices_become_two_records_per_model_in_usd_per_million_tokens():
    records, diagnostics, kept = parse_models_payload(_bytes(_models()), fetched_at=NOW,
                                                      min_models=1)
    cells = {(r.dimensions["model_permaslug"], r.provider_field): r for r in records}
    claude = cells[(CLAUDE, "input_price")]
    assert claude.value == pytest.approx(4.0)
    assert cells[(CLAUDE, "output_price")].value == pytest.approx(20.0)
    assert claude.entity_id == "OPENROUTER_MODEL:ANTHROPIC:CLAUDE-OPUS-5.5-20260921"
    assert (claude.unit, claude.currency, claude.period) == (
        "USD_per_1M_tokens", "USD", "2026-12-20")
    assert claude.dimensions["author"] == "anthropic"
    # The batch route keeps its own price under the variant usage would name it by.
    assert cells[(f"{CLAUDE}:batch", "input_price")].value == pytest.approx(2.0)
    assert cells[("google/gemma-5-20261001:free", "output_price")].value == 0
    assert diagnostics["skipped"] == {"alias": ["~anthropic/claude-latest"],
                                      "variable_price": ["openrouter/auto"]}
    assert diagnostics["priced_models"] == 4
    assert b"description" not in kept and diagnostics["original_sha256"]


@pytest.mark.parametrize("payload, reason", [
    (b"<html>", "invalid_json"),
    (b'{"models": []}', "schema_drift"),
    (_bytes([{"id": "a/b", "canonical_slug": "a/b"}]), "schema_drift"),
    (_bytes([_model("a/b", "a/b-1", "1", "1"), _model("a/c", "a/b-1", "2", "2")]),
     "duplicate_price_key"),
])
def test_unusable_model_list_is_rejected(payload, reason):
    with pytest.raises(OpenRouterPricingError, match=reason):
        parse_models_payload(payload, fetched_at=NOW, min_models=1)


def test_truncated_model_list_is_rejected():
    with pytest.raises(OpenRouterPricingError, match="model_list_too_short"):
        parse_models_payload(_bytes(_models()), fetched_at=NOW)


WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday"]


def _time_pricing(top):
    return {"prompt": top, "completion": top, "overrides": [
        {"utc_days": ["saturday", "sunday"], "prompt": "1", "completion": "2"},
        {"utc_days": WEEKDAYS, "utc_start": 100, "utc_end": 700, "prompt": "3", "completion": "4"},
        {"utc_days": WEEKDAYS, "utc_start": 700, "utc_end": 100, "prompt": "1", "completion": "2"},
    ]}


def test_time_of_day_price_is_a_week_average_independent_of_fetch_time():
    peak_hours = 5 * 6
    expected_input = (peak_hours * 3 + (168 - peak_hours) * 1) / 168
    for top in ("1", "3"):
        input_price, output_price, basis, tiered = reference_price(_time_pricing(top))
        assert float(input_price) == pytest.approx(expected_input)
        assert float(output_price) == pytest.approx(expected_input + 1)
        assert (basis, tiered) == ("time_weighted_week", False)


@pytest.mark.parametrize("overrides, reason", [
    ([{"utc_days": ["monday"], "prompt": "1", "completion": "1"}], "time_rules_incomplete"),
    ([{"utc_start": 0, "utc_end": 0, "prompt": "1", "completion": "1"},
      {"utc_start": 100, "utc_end": 200, "prompt": "2", "completion": "2"}], "time_rules_overlap"),
    ([{"min_requests": 10, "prompt": "1", "completion": "1"}], "unknown_price_condition"),
])
def test_price_conditions_that_cannot_be_averaged_leave_no_price(overrides, reason):
    with pytest.raises(UnresolvedPrice, match=reason):
        reference_price({"prompt": "1", "completion": "1", "overrides": overrides})


def test_cache_read_price_is_kept_beside_the_input_price_when_listed():
    pricing = _time_pricing("1")
    assert cache_read_price(pricing) is None
    for rule, value in zip(pricing["overrides"], ("0.1", "0.3", "0.1"), strict=True):
        rule["input_cache_read"] = value
    assert float(cache_read_price(pricing)) == pytest.approx((30 * 0.3 + 138 * 0.1) / 168)
    models = [_model("a/b", "a/b-1", "0.000004", "0.00002",
                     pricing={"input_cache_read": "0.0000002"})]
    records, _diagnostics, _kept = parse_models_payload(_bytes(models), fetched_at=NOW,
                                                        min_models=1)
    assert records[0].raw["input_cache_read"] == pytest.approx(0.2)


def test_prompt_length_tier_keeps_the_shortest_prompt_price():
    input_price, output_price, basis, tiered = reference_price({
        "prompt": "1", "completion": "2",
        "overrides": [{"min_prompt_tokens": 100000, "prompt": "5", "completion": "9"}]})
    assert (float(input_price), float(output_price), basis, tiered) == (1.0, 2.0, "listed", True)


def test_unreachable_list_is_a_failure_not_an_empty_snapshot():
    def opener(_request, timeout=30):
        raise URLError("blocked")

    batch = OpenRouterPricingAdapter(opener=opener).fetch(
        FetchRequest(source_id="openrouter_pricing",
                     dataset_id="openrouter_model_pricing_daily"))
    assert batch.status == IngestionStatus.UNREACHABLE and not batch.records


# --- stored vintages -------------------------------------------------------------

def test_price_cut_keeps_both_prices_with_their_dates(tmp_path):
    repository = _repository(tmp_path)
    first, second = date(2026, 12, 7), date(2026, 12, 8)
    _ingest_prices(repository, first)
    _ingest_prices(repository, first, hour=9, expect="no_change")
    _ingest_prices(repository, second, _models(claude=("0.000004", "0.000015")))
    series = DataProducts(structured_repository=repository).openrouter_model_price_series(
        model_permaslug=CLAUDE)
    assert [(row["period"], row["output_price"]) for row in series["rows"]] == [
        ("2026-12-07", pytest.approx(20.0)), ("2026-12-08", pytest.approx(15.0))]
    assert series["price_history_start"] == "2026-12-07"
    # Re-reading the same day at the same price adds no vintage.
    stored = repository.observations(dataset_id="openrouter_model_pricing_daily",
                                     latest_only=False, limit=1000)
    assert len([row for row in stored if row["period"] == "2026-12-07"]) == 8


def test_same_day_revision_replaces_the_price_and_replays_at_the_old_time(tmp_path):
    repository = _repository(tmp_path)
    day = date(2026, 12, 7)
    _ingest_prices(repository, day, hour=7)
    _ingest_prices(repository, day, _models(claude=("0.000003", "0.00002")), hour=9)
    products = DataProducts(structured_repository=repository)
    assert products.openrouter_model_price_series(
        model_permaslug=CLAUDE)["rows"][0]["input_price"] == pytest.approx(3.0)
    earlier = datetime(2026, 12, 7, 8, tzinfo=timezone.utc)
    assert products.openrouter_model_price_series(
        model_permaslug=CLAUDE, as_of=earlier)["rows"][0]["input_price"] == pytest.approx(4.0)


# --- estimated spend share ---------------------------------------------------------

def test_estimated_share_uses_declared_ratio_and_never_backfills_earlier_weeks(tmp_path):
    result = _priced_products(tmp_path).openrouter_estimated_spend_share_series(
        input_token_share=0.9)
    before, priced = result["weeks"]
    assert before["status"] == "no_price_data" and before["label"] == "无价格数据"
    assert "价格采集开始于 2026-12-07" in before["reason"]
    assert priced["status"] == "estimated" and priced["label"] == "估算花费份额"
    assert not [row for row in result["rows"] if row["period"] == before["period"]]
    # 0.9 x input + 0.1 x output, per day: Claude 100 x 5.6, GPT 300 x 1.4, free route 0.
    rows = {row["author"]: row for row in result["rows"]}
    assert rows["anthropic"]["estimated_spend_share"] == pytest.approx(560 / 980)
    assert rows["openai"]["estimated_spend_share"] == pytest.approx(420 / 980)
    assert rows["google"]["estimated_spend_share"] == 0
    assert rows["anthropic"]["token_share"] == pytest.approx(100 / 500)
    assert priced["no_price_token_share"] == pytest.approx(0.1)
    assert priced["zero_price_token_share"] == pytest.approx(0.1)
    assert priced["warnings"] == []
    assert result["assumption"]["input_token_share"] == 0.9
    assert result["assumption"]["source"] == "caller_override"
    assert result["label"] == "估算花费份额" and result["latest_period"] == "2026-12-07"
    assert not any("revenue" in str(key) or "usd" in str(key).lower()
                   for row in result["rows"] for key in row)


def test_declared_ratio_is_disclosed_and_author_filter_keeps_week_disclosures(tmp_path):
    declared = StructuredCatalog.load().raw["datasets"]["openrouter_model_pricing_daily"][
        "quality"]["estimated_spend"]["input_token_share"]
    result = _priced_products(tmp_path).openrouter_estimated_spend_share_series(author="anthropic")
    assert result["assumption"]["source"] == "declared"
    assert result["assumption"]["input_token_share"] == declared
    assert [row["author"] for row in result["rows"]] == ["anthropic"]
    assert result["rows"][0]["no_price_token_share"] == pytest.approx(0.1)


def test_large_unpriced_share_carries_a_quality_warning(tmp_path):
    usage = [(CLAUDE, 100), ("typesafe/jev-1-20260917", 40), ("other", 10)]
    week = _priced_products(tmp_path, usage=usage).openrouter_estimated_spend_share_series()[
        "weeks"][-1]
    assert week["no_price_token_share"] == pytest.approx(50 / 150)
    assert "超过 20%" in week["warnings"][0]
    assert week["largest_unpriced_models"][0]["model_permaslug"] == "typesafe/jev-1-20260917"


def test_collection_gap_beyond_the_limit_leaves_the_week_without_price_data(tmp_path):
    # Snapshots on Monday and from Friday: Thursday is three days stale.
    products = _priced_products(tmp_path, price_days=[0, 4, 5, 6])
    result = products.openrouter_estimated_spend_share_series()
    assert result["status"] == "no_price_data" and result["rows"] == []
    assert result["weeks"][-1]["days_without_price"] == ["2026-12-10"]


def test_short_gap_uses_the_earlier_snapshot_never_a_later_one(tmp_path):
    products = _priced_products(tmp_path, price_days=[0, 3, 4, 5, 6])
    week = products.openrouter_estimated_spend_share_series()["weeks"][-1]
    assert week["status"] == "estimated"
    assert week["price_snapshot_dates"] == ["2026-12-07", "2026-12-10", "2026-12-11",
                                            "2026-12-12", "2026-12-13"]


def test_without_any_price_snapshot_every_week_reports_no_price_data(tmp_path):
    result = _priced_products(tmp_path, price_days=[]).openrouter_estimated_spend_share_series()
    assert result["status"] == "no_price_data" and result["price_history_start"] == ""
    assert {week["status"] for week in result["weeks"]} == {"no_price_data"}
