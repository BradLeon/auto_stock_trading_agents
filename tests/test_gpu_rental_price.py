"""GPU rental list price adapter, trend and coverage status (ant-data-onboarding 12.2)."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.error import URLError

import pytest

from ats.data.adapters.structured.registry import validate_source_registration
from ats.data.catalog.structured import StructuredCatalog
from ats.data.core.structured_models import FetchRequest, IngestionStatus
from ats.data.pipelines.structured.ingestion import IngestionPipeline
from ats.data.products import DataProducts
from ats.data.sources.getdeploying_gpu_prices import (
    GetDeployingGpuPriceAdapter,
    GpuPriceError,
    dataset_settings,
    parse_weekly_payload,
)
from ats.data.stores.structured.repository import SQLiteStructuredRepository

SOURCE = "getdeploying_gpu_prices"
DATASET = "gpu_rental_price_weekly"
NOW = datetime(2026, 11, 27, 8, 10, tzinfo=UTC)
MODELS = ("nvidia-h100", "nvidia-b200")
HEADER = ("gpu_slug,date,billing_type,reservation_months,min_price,max_price,median_price,"
          "provider_median_price,provider_count,offering_count")
REPO = Path(__file__).resolve().parents[1]


def line(week, price, slug="nvidia-h100", billing="ON_DEMAND", term="", providers=40,
         offerings=90, low=1.0, high=20.0):
    return (f"{slug},{week},{billing},{term},{low},{high},{price},{price},"
            f"{providers},{offerings}")


def mondays(first: str, count: int):
    start = date.fromisoformat(first)
    return [(start + timedelta(weeks=index)).isoformat() for index in range(count)]


def csv_bytes(h100: dict[str, float], b200: dict[str, float] | None = None, extra=(),
              b200_providers=24):
    rows = [line(week, price) for week, price in h100.items()]
    rows += [line(week, price, slug="nvidia-b200", providers=b200_providers)
             for week, price in (b200 or {"2026-11-23": 8.5}).items()]
    return ("\n".join([HEADER, *rows, *extra]) + "\n").encode()


def monthly_prices(by_month: dict[str, float], through="2026-11-23"):
    """One price for every Monday of each month, up to `through`."""
    prices = {}
    for month, price in by_month.items():
        day = date.fromisoformat(f"{month}-01")
        day += timedelta(days=(7 - day.weekday()) % 7)
        while day.isoformat().startswith(month) and day.isoformat() <= through:
            prices[day.isoformat()] = price
            day += timedelta(weeks=1)
    return prices


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, *_args):
        return self.payload


FORMS = ("spot", "reserved_12m")


def parse(payload, models=MODELS, forms=FORMS):
    return parse_weekly_payload(payload, fetched_at=NOW, gpu_models=models, min_rows=1,
                                corroboration_forms=forms)


def spot(week, price, **kwargs):
    return line(week, price, billing="SPOT", **kwargs)


def reserved(week, price, term="12", **kwargs):
    return line(week, price, billing="RESERVATION", term=term, **kwargs)


def ingest(repository, payload, moment=NOW, expect="succeeded"):
    adapter = GetDeployingGpuPriceAdapter(
        opener=lambda _request, timeout=30: _Response(payload), gpu_models=MODELS,
        min_rows=1, clock=lambda: moment)
    result = IngestionPipeline(repository).run(
        adapter, FetchRequest(source_id=SOURCE, dataset_id=DATASET))
    assert result["status"] == expect, result
    return result


def read(repository, moment=NOW, **kwargs):
    return DataProducts(structured_repository=repository).gpu_rental_price(as_of=moment, **kwargs)


def series(packet, slug="nvidia-h100"):
    return next(item for item in packet["series"] if item["gpu_model"] == slug)


@pytest.fixture
def repository(tmp_path):
    store = SQLiteStructuredRepository(tmp_path / "gpu.sqlite", artifact_root=tmp_path / "artifacts")
    store.bootstrap_catalog(StructuredCatalog.load())
    return store


def test_source_registration_is_governed():
    assert validate_source_registration(SOURCE)["valid"]
    assert "ai.gpu.rental.listed_price" in {item.id for item in StructuredCatalog.load().metrics()}
    settings = dataset_settings()
    assert settings["rental_form"] == "on_demand" and "nvidia-h100" in settings["gpu_models"]
    assert tuple(settings["corroboration_forms"]) == FORMS
    command = f"ats data ingest --source {SOURCE} --dataset {DATASET}"
    assert command in (REPO / "config/data/schedules.yaml").read_text()


def test_collected_forms_of_listed_models_are_kept_apart():
    payload = csv_bytes({"2026-11-23": 4.03}, extra=[
        spot("2026-11-23", 2.0),
        reserved("2026-11-23", 3.0),
        # Not collected: other terms, an unknown term, custom billing, unlisted models.
        reserved("2026-11-23", 3.1, term="1"),
        reserved("2026-11-23", 3.2, term="36"),
        reserved("2026-11-23", 3.3, term=""),
        line("2026-11-23", 2.5, billing="CUSTOM"),
        line("2026-11-23", 9.9, slug="amd-mi300x"),
    ])
    records, diagnostics = parse(payload)
    assert [(r.entity_id, r.dimensions["rental_form"], r.value) for r in records] == [
        ("GPU_MODEL:NVIDIA-B200", "on_demand", 8.5), ("GPU_MODEL:NVIDIA-H100", "on_demand", 4.03),
        ("GPU_MODEL:NVIDIA-H100", "reserved_12m", 3.0), ("GPU_MODEL:NVIDIA-H100", "spot", 2.0)]
    record = records[1]
    assert (record.unit, record.currency, record.period) == ("USD_per_GPU_hour", "USD", "2026-11-23")
    assert record.period_end == "2026-11-29"
    assert record.raw["provider_count"] == 40
    assert diagnostics["rows_by_form"] == {"on_demand": 2, "spot": 1, "reserved_12m": 1}
    assert diagnostics["forms_absent"] == [] and diagnostics["quarantined_rows"] == 0
    assert diagnostics["kept_rows"] == 4 and diagnostics["license"] == "CC BY 4.0"
    # Without declared corroboration only the reading is collected.
    only, _ = parse(payload, forms=())
    assert {r.dimensions["rental_form"] for r in only} == {"on_demand"}
    with pytest.raises(GpuPriceError, match="corroboration_form_unsupported:on_demand"):
        parse(payload, forms=("on_demand",))


def test_bad_corroboration_row_is_quarantined_and_the_reading_still_lands(repository):
    payload = csv_bytes({"2026-11-23": 4.03}, extra=[
        spot("2026-11-23", 25.0),                          # above the row's own maximum
        spot("2026-11-16", 2.0, term="3"),
        reserved("2026-11-23", "n/a"),
        reserved("2026-11-16", 3.0),
    ])
    records, diagnostics = parse(payload)
    reasons = sorted(r.raw["quarantine_reasons"][0] for r in records
                     if "quarantine_reasons" in r.raw)
    assert reasons == ["median_outside_range", "provider_median_price_not_numeric",
                       "spot_with_reservation_term"]
    assert diagnostics["quarantined_rows"] == 3 and diagnostics["forms_absent"] == ["spot"]
    result = ingest(repository, payload, expect="partial")
    assert (result["accepted"], result["quarantined"]) == (3, 3)
    item = series(read(repository))
    assert item["latest"]["price"] == 4.03
    assert item["corroboration"]["spot"]["status"] == "uncovered"
    assert item["corroboration"]["reserved_12m"]["latest"]["week"] == "2026-11-16"


def test_corroboration_never_decides_coverage_or_the_reading(repository):
    falling = monthly_prices({"2026-08": 4.0, "2026-09": 3.6, "2026-10": 3.0, "2026-11": 3.0})
    rising = monthly_prices({"2026-08": 2.0, "2026-09": 2.5, "2026-10": 3.0, "2026-11": 3.0})
    ended = monthly_prices({"2026-08": 5.0, "2026-09": 4.5})
    ingest(repository, csv_bytes(falling, extra=[
        *(reserved(week, price) for week, price in rising.items()),
        *(spot(week, price, providers=4) for week, price in ended.items())]))
    packet = read(repository)
    item = series(packet)
    assert (packet["status"], item["status"], item["role"]) == ("covered", "covered", "primary")
    assert item["trend"]["direction"] == "down" and item["trend"]["consecutive_declines"] == 2
    assert [w["price"] for w in item["weekly"]] == list(falling.values())
    held = item["corroboration"]["reserved_12m"]
    assert (held["role"], held["status"], held["trend"]["direction"]) == (
        "corroboration", "covered", "up")
    gone = item["corroboration"]["spot"]
    assert (gone["status"], gone["trend"]["direction"]) == ("stale", "")
    assert "只作旁证" in packet["corroboration_note"]
    assert packet["latest_week"] == "2026-11-23" and packet["rental_form"] == "on_demand"
    # A model with no corroboration rows says so instead of repeating the reading.
    other = series(packet, "nvidia-b200")["corroboration"]
    assert {entry["status"] for entry in other.values()} == {"uncovered"}


def test_forms_do_not_revise_each_other(repository):
    extra = [spot("2026-11-23", 2.0), reserved("2026-11-23", 3.0)]
    ingest(repository, csv_bytes({"2026-11-23": 4.0}, extra=extra))
    week_later = datetime(2026, 12, 1, 8, 10, tzinfo=UTC)
    changed = [spot("2026-11-23", 2.2), extra[1]]
    result = ingest(repository, csv_bytes({"2026-11-23": 4.0}, extra=changed), week_later)
    assert (result["accepted"], result["unchanged"]) == (1, 3)
    stored = repository.observations(dataset_id=DATASET, entity_id="GPU_MODEL:NVIDIA-H100",
                                     latest_only=False)
    assert sorted(r["value"] for r in stored) == [2.0, 2.2, 3.0, 4.0]
    item = series(read(repository, week_later))
    assert item["latest"]["price"] == 4.0
    assert item["corroboration"]["spot"]["latest"]["price"] == 2.2
    assert item["corroboration"]["reserved_12m"]["latest"]["price"] == 3.0


@pytest.mark.parametrize("payload, reason", [
    (b"gpu_slug,date\nnvidia-h100,2026-11-23\n", "columns_missing"),
    (csv_bytes({"2026-11-24": 4.0}), "date_not_a_past_monday"),
    (csv_bytes({"2026-11-30": 4.0}), "date_not_a_past_monday"),
    (csv_bytes({"soon": 4.0}), "date_invalid"),
    (csv_bytes({"2026-11-23": "n/a"}), "provider_median_price_not_numeric"),
    (csv_bytes({"2026-11-23": 0}), "provider_median_price_out_of_range"),
    (csv_bytes({"2026-11-23": 25.0}), "median_outside_range"),
    (csv_bytes({"2026-11-23": 4.0}, extra=[line("2026-11-23", 4.1)]), "duplicate_week"),
    (csv_bytes({"2026-11-23": 4.0}, extra=[line("2026-11-16", 4.0, term="1")]),
     "on_demand_with_reservation_term"),
    (csv_bytes({"2026-11-23": 4.0}, extra=[line("2026-11-16", 4.0, providers=9, offerings=3)]),
     "more_providers_than_offerings"),
    (csv_bytes({"2026-11-23": 4.0}, extra=["nvidia-h100,2026-11-16,ON_DEMAND"]), "row_incomplete"),
    # The file's shape is not forgiven on a corroboration row either.
    (csv_bytes({"2026-11-23": 4.0}, extra=[spot("2026-11-23", 2.0), spot("2026-11-23", 2.1)]),
     "duplicate_week"),
    (csv_bytes({"2026-11-23": 4.0}, extra=[spot("2026-11-17", 2.0)]), "date_not_a_past_monday"),
    (csv_bytes({"2026-11-23": 4.0}, extra=["nvidia-h100,2026-11-16,SPOT"]), "row_incomplete"),
])
def test_unusable_file_is_rejected(payload, reason):
    with pytest.raises(GpuPriceError, match=reason):
        parse(payload)


def test_truncated_file_and_missing_model_are_rejected():
    with pytest.raises(GpuPriceError, match="file_too_short"):
        parse_weekly_payload(csv_bytes({"2026-11-23": 4.0}), fetched_at=NOW,
                             gpu_models=MODELS, min_rows=5000)
    with pytest.raises(GpuPriceError, match="models_missing:nvidia-h200"):
        parse(csv_bytes({"2026-11-23": 4.0}), models=(*MODELS, "nvidia-h200"))


def test_outage_leaves_the_indicator_uncovered(repository):
    def offline(_request, timeout=30):
        raise URLError("offline")

    result = IngestionPipeline(repository).run(
        GetDeployingGpuPriceAdapter(opener=offline, clock=lambda: NOW, gpu_models=MODELS,
                                    min_rows=1),
        FetchRequest(source_id=SOURCE, dataset_id=DATASET))
    assert result["status"] == IngestionStatus.UNREACHABLE.value
    packet = read(repository)
    assert (packet["status"], packet["status_label"]) == ("uncovered", "未覆盖")
    assert packet["series"] == [] and "CC BY 4.0" == packet["license"]


def test_two_complete_months_are_insufficient_history(repository):
    ingest(repository, csv_bytes(
        monthly_prices({"2026-09": 4.0, "2026-10": 3.5, "2026-11": 3.0})))
    item = series(read(repository))
    assert item["status"] == "covered"
    assert item["trend"]["status_label"] == "历史不足"
    assert item["trend"]["direction"] == "" and item["trend"]["consecutive_declines"] is None


def test_three_falling_months_are_read_and_the_open_month_is_left_out(repository):
    prices = monthly_prices({"2026-07": 4.4, "2026-08": 4.0, "2026-09": 3.6, "2026-10": 3.0,
                             "2026-11": 9.0})
    ingest(repository, csv_bytes(prices))
    packet = read(repository)
    item = series(packet)
    assert [m["month"] for m in item["monthly"]] == ["2026-07", "2026-08", "2026-09", "2026-10"]
    trend = item["trend"]
    assert (trend["status"], trend["direction"]) == ("ok", "down")
    assert trend["months"] == ["2026-08", "2026-09", "2026-10"]
    assert trend["consecutive_declines"] == 3 and trend["thin_sample"] is False
    assert trend["monthly_changes"][-1] == pytest.approx(3.0 / 3.6 - 1)
    assert item["latest"]["price"] == 9.0 and packet["latest_week"] == "2026-11-23"
    assert packet["collected_through"] == "2026-11-27"


def test_mixed_months_and_thin_sample_are_stated(repository):
    months = {"2026-08": 4.0, "2026-09": 3.6, "2026-10": 3.9, "2026-11": 3.9}
    ingest(repository, csv_bytes(monthly_prices(months), b200=monthly_prices(months),
                                 b200_providers=3))
    packet = read(repository)
    assert series(packet)["trend"]["direction"] == "mixed"
    assert series(packet)["trend"]["consecutive_declines"] == 0
    assert series(packet, "nvidia-b200")["trend"]["thin_sample"] is True


def test_a_missing_month_breaks_the_run(repository):
    prices = monthly_prices({"2026-07": 4.4, "2026-08": 4.0, "2026-10": 3.0, "2026-11": 3.0})
    # September has only two captured weeks, below the declared minimum.
    prices.update({week: 3.6 for week in mondays("2026-09-07", 2)})
    ingest(repository, csv_bytes(prices))
    trend = series(read(repository))["trend"]
    assert (trend["status"], trend["consecutive_months"]) == ("insufficient_history", 1)


def test_old_data_is_stale_and_gives_no_direction(repository):
    ingest(repository, csv_bytes(
        monthly_prices({"2026-08": 4.0, "2026-09": 3.6, "2026-10": 3.0}, through="2026-11-23")))
    packet = read(repository, datetime(2027, 1, 15, tzinfo=UTC))
    item = series(packet)
    assert (packet["status"], item["status"], item["status_label"]) == ("stale", "stale", "数据过期")
    assert item["trend"]["status"] == "stale" and item["trend"]["direction"] == ""


def test_weekly_reread_records_only_changes_and_keeps_earlier_readings(repository):
    first = {"2026-11-16": 4.0, "2026-11-23": 4.2}
    ingest(repository, csv_bytes(first))
    week_later = datetime(2026, 12, 1, 8, 10, tzinfo=UTC)
    changed = csv_bytes({**first, "2026-11-23": 4.4})
    ingest(repository, changed, week_later)

    def stored():
        return repository.observations(dataset_id=DATASET, entity_id="GPU_MODEL:NVIDIA-H100",
                                       latest_only=False)

    assert sorted((r["period"], r["value"]) for r in stored()) == [
        ("2026-11-16", 4.0), ("2026-11-23", 4.2), ("2026-11-23", 4.4)]
    assert series(read(repository, week_later))["latest"]["price"] == 4.4
    assert series(read(repository))["latest"]["price"] == 4.2  # as known on the first day
    # A value that returns to an earlier one is a new vintage, not lost.
    later = datetime(2026, 12, 8, 8, 10, tzinfo=UTC)
    ingest(repository, csv_bytes(first), later)
    assert len(stored()) == 4
    assert series(read(repository, later))["latest"]["price"] == 4.2


def test_unchanged_reread_is_no_change(repository):
    payload = csv_bytes({"2026-11-23": 4.2})
    ingest(repository, payload)
    ingest(repository, payload, datetime(2026, 12, 1, 8, 10, tzinfo=UTC), expect="no_change")


def test_one_model_can_be_read_alone(repository):
    ingest(repository, csv_bytes({"2026-11-23": 4.2}))
    packet = read(repository, gpu_model="nvidia-b200")
    assert [item["gpu_model"] for item in packet["series"]] == ["nvidia-b200"]
    assert packet["series"][0]["rental_form"] == "on_demand"
    # Configured models without observations are listed as uncovered, not as flat.
    all_models = {item["gpu_model"]: item for item in read(repository)["series"]}
    assert all_models["nvidia-h200"]["status"] == "uncovered"
    assert all_models["nvidia-h200"]["trend"] is None
