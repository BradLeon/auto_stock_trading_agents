"""Consumer-facing Ramp AI Index products.

Ramp slices are intentionally queried independently.  This module never joins
Ramp percentages to BTOS, RPS or Anthropic observations and never turns the
Ramp-network cohort into a national adoption estimate.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Iterable


SOURCE_ID = "ramp_ai_index"
FRED_SOURCE_ID = "ramp_ai_index_fred"
# v2: FRED is the only source of the series it publishes; the page runner stores the rest. Both feed the same
# datasets, so every product reads both sources.
SOURCE_IDS = (FRED_SOURCE_ID, SOURCE_ID)
ADOPTION_DATASET = "ramp_ai_adoption"
SPEND_DATASET = "ramp_ai_spend"
DERIVATION_VERSION = "ramp_ai_index/v2"
REGIME_V1 = "ramp_ai_index_v1"
REGIME_V2 = "ramp_ai_index_v2"
DEFAULT_REGIME = REGIME_V2
ADOPTION_SCOPES = {"adoption_overall", "adoption_overall_models", "adoption_sector", "adoption_business_size"}
TOKEN_SCOPES = {"token_spend_share_by_maker", "token_spend_share_by_model"}
SPEND_SCOPES = {"spend_per_employee_overall", "model_market_share_overall", *TOKEN_SCOPES}
# v1 only: Ramp replaced the monthly API spend share with weekly token-spend shares.
DISCONTINUED_SCOPES = {"model_market_share_overall": REGIME_V1}


def _dims(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("dimensions")
    if isinstance(value, dict):
        return value
    try:
        value = json.loads(row.get("dimensions_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        value = {}
    return value if isinstance(value, dict) else {}


def _raw(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("raw")
    if isinstance(value, dict):
        return value
    try:
        value = json.loads(row.get("raw_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        value = {}
    return value if isinstance(value, dict) else {}


def _period_key(value: str) -> str:
    text = str(value or "")
    return text[:7] if len(text) >= 7 and text[4] in "-_/" else text


def _period_matches(value: str, requested: str) -> bool:
    return value == requested or _period_key(value) == _period_key(requested)


def _period_gap(periods: Iterable[str]) -> dict[str, Any]:
    """Describe missing calendar months without forward filling them."""
    keys = sorted({_period_key(value) for value in periods if _period_key(value)})
    if len(keys) < 2:
        return {"status": "not_applicable", "observed_periods": keys, "missing_periods": []}
    try:
        year, month = map(int, keys[0].split("-"))
        expected: list[str] = []
        end_year, end_month = map(int, keys[-1].split("-"))
        while (year, month) <= (end_year, end_month):
            expected.append(f"{year:04d}-{month:02d}")
            month += 1
            if month == 13:
                year, month = year + 1, 1
        missing = [value for value in expected if value not in keys]
        return {"status": "gap" if missing else "continuous", "observed_periods": keys,
                "missing_periods": missing}
    except (TypeError, ValueError):
        return {"status": "unparseable", "observed_periods": keys, "missing_periods": []}


def _lineage(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    observations = sorted({row.get("observation_id", "") for row in rows if row.get("observation_id")})
    artifacts = sorted({row.get("artifact_id", "") for row in rows if row.get("artifact_id")})
    return {
        "derivation_version": DERIVATION_VERSION,
        "input_observation_ids": observations,
        "input_observation_ids_hash": hashlib.sha256(json.dumps(observations, separators=(",", ":")).encode()).hexdigest(),
        "artifact_ids": artifacts,
    }


def _freshness(repository, *, latest_period: str = "") -> dict[str, Any]:
    # ``source_health()`` intentionally builds diagnostics for every source and
    # performs an expensive Anthropic product scan.  Ramp only needs its own
    # latest probe metadata, so query the governed source-check/run records
    # directly.  This keeps report generation bounded on large production DBs.
    parts = {}
    for source_id in SOURCE_IDS:
        checks = repository.source_checks(source_id=source_id, limit=1)
        runs = repository.ingestion_history(source_id=source_id, limit=1)
        check = checks[0] if checks else {}
        run = runs[0] if runs else {}
        parts[source_id] = {"last_checked_at": check.get("checked_at") or run.get("started_at"),
                            "latest_available_period": check.get("latest_available_period") or "",
                            "latest_status": check.get("status") or run.get("status")}
    page = parts[SOURCE_ID]
    return {"last_checked_at": page["last_checked_at"],
            "latest_available_period": page["latest_available_period"] or latest_period,
            "latest_status": page["latest_status"],
            "source_native_cadence": "monthly",
            # FRED and the page runner fail independently; a reader sees each part's own state.
            "parts": parts}


def _manifest(products, rows: list[dict[str, Any]], *, purpose: str, as_of: datetime | None,
              metadata: dict[str, Any]) -> dict[str, Any] | None:
    if not rows:
        return None
    cutoff = as_of or datetime.now(timezone.utc)
    selected = [dict(row, selected_source=row.get("source_id") or SOURCE_ID, derivation_version=DERIVATION_VERSION)
                for row in rows if row.get("observation_id")]
    return products.snapshot_manifest(consumer="evidence_observer", purpose=purpose,
                                      as_of=cutoff, rows=selected, metadata=metadata)


def _query(repository, *, dataset_id: str, scope: str, as_of=None,
           include_vintages: bool = False, limit: int = 100_000,
           methodology_regime: str = DEFAULT_REGIME) -> list[dict[str, Any]]:
    """Rows of one scope and one methodology regime. v1 and v2 are never mixed into one series."""
    rows: list[dict[str, Any]] = []
    for source_id in SOURCE_IDS:
        rows.extend(repository.observations(dataset_id=dataset_id, source_id=source_id, as_of=as_of,
                                            latest_only=not include_vintages, accepted_only=True, limit=limit))
    return [row for row in rows if _dims(row).get("scope") == scope
            and (_dims(row).get("methodology_regime") or REGIME_V1) == methodology_regime]


def _discontinued(scope: str, methodology_regime: str) -> str:
    only = DISCONTINUED_SCOPES.get(scope)
    return "scope_discontinued" if only and only != methodology_regime else ""


def ramp_paid_adoption_snapshot(products, *, scope: str = "adoption_overall", period: str = "",
                                periods: list[str] | None = None, as_of=None,
                                include_vintages: bool = False, as_frame: bool = False,
                                methodology_regime: str = DEFAULT_REGIME) -> dict[str, Any]:
    if scope not in ADOPTION_SCOPES:
        raise ValueError(f"unsupported_ramp_adoption_scope:{scope}")
    rows = _query(products.structured, dataset_id=ADOPTION_DATASET, scope=scope,
                  as_of=as_of, include_vintages=include_vintages, methodology_regime=methodology_regime)
    periods_requested = periods or ([period] if period else [])
    if periods_requested:
        rows = [row for row in rows if any(_period_matches(row.get("period", ""), item) for item in periods_requested)]
    rows.sort(key=lambda row: (row.get("period", ""), row.get("entity_id", ""), row.get("metric_id", "")))
    latest_period = max((row.get("period", "") for row in rows), default="")
    payload_rows = []
    for row in rows:
        dims = _dims(row)
        raw = _raw(row)
        payload_rows.append({"observation_id": row.get("observation_id"), "artifact_id": row.get("artifact_id"),
                             "entity_id": row.get("entity_id"), "entity_name": dims.get("segment") or row.get("entity_id"),
                             "metric_id": row.get("metric_id"), "value": row.get("value"), "unit": row.get("unit"),
                             "period": row.get("period"), "scope": scope, "segment": dims.get("segment"),
                             "statistical_unit": dims.get("statistical_unit"), "denominator_scope": dims.get("denominator_scope"),
                             "technology_scope": dims.get("technology_scope"), "provider_monthly_change_pp": raw.get("provider_row", {}).get("Monthly change (pp)"),
                             "provider_yearly_change_pp": raw.get("provider_row", {}).get("Yearly change (pp)"),
                             "methodology_regime": dims.get("methodology_regime"), "quality_status": row.get("quality_status", "accepted"),
                             "source_id": row.get("source_id"), "raw": raw})
    manifest = _manifest(products, rows, purpose=f"ramp_paid_adoption:{scope}", as_of=as_of,
                         metadata={"scope": scope, "period": latest_period, "derivation_version": DERIVATION_VERSION})
    gap = _period_gap(row.get("period", "") for row in payload_rows)
    result = {"status": "ok" if payload_rows else "no_coverage", "source_id": SOURCE_ID,
            "source_ids": sorted({row["source_id"] for row in payload_rows if row.get("source_id")}),
            "methodology_regime": methodology_regime,
            "dataset_id": ADOPTION_DATASET, "scope": scope, "period": latest_period,
            "periods": sorted({_period_key(row["period"]) for row in payload_rows}),
            "rows": payload_rows, "statistical_unit": "Ramp relevant American businesses",
            "denominator": "Ramp relevant business cohort with positive AI transaction",
            "quality": {"status": "accepted" if payload_rows else "no_coverage",
                        "vendor_shares_may_overlap": scope == "adoption_overall_models",
                        "period_gap": gap},
            "period_gap": gap,
            "freshness": _freshness(products.structured, latest_period=latest_period),
            "lineage": _lineage(rows), "manifest": manifest,
            "limitations": ["Ramp network cohort, not all US businesses", "positive paid transaction, not employee adoption"]}
    if as_frame:
        try:
            import pandas as pd
        except ImportError as exc:  # optional dependency
            raise RuntimeError("pandas is required for as_frame=True") from exc
        frame = pd.DataFrame(result["rows"])
        frame.attrs.update({key: value for key, value in result.items() if key != "rows"})
        return frame
    return result


def ramp_spend_per_employee_series(products, *, scope: str = "spend_per_employee_overall",
                                   quantiles: list[str] | None = None, period: str = "",
                                   as_of=None, include_vintages: bool = False,
                                   as_frame: bool = False,
                                   methodology_regime: str = DEFAULT_REGIME) -> dict[str, Any]:
    if scope != "spend_per_employee_overall":
        raise ValueError(f"unsupported_ramp_spend_scope:{scope}")
    rows = _query(products.structured, dataset_id=SPEND_DATASET, scope=scope,
                  as_of=as_of, include_vintages=include_vintages, methodology_regime=methodology_regime)
    if period:
        rows = [row for row in rows if _period_matches(row.get("period", ""), period)]
    allowed = {str(value).casefold() for value in (quantiles or [])}
    if allowed:
        rows = [row for row in rows if str(_dims(row).get("quantile", "")).casefold() in allowed]
    rows.sort(key=lambda row: (row.get("period", ""), row.get("entity_id", "")))
    output = [{"observation_id": row.get("observation_id"), "artifact_id": row.get("artifact_id"),
               "period": row.get("period"), "quantile": _dims(row).get("quantile"),
               "value": row.get("value"), "unit": row.get("unit"), "scope": scope,
               "statistical_unit": _dims(row).get("statistical_unit"),
               "denominator_scope": _dims(row).get("denominator_scope"),
               "quality_status": row.get("quality_status", "accepted"), "raw": _raw(row)} for row in rows]
    latest = max((row.get("period", "") for row in rows), default="")
    manifest = _manifest(products, rows, purpose=f"ramp_spend:{scope}", as_of=as_of,
                         metadata={"scope": scope, "period": latest, "derivation_version": DERIVATION_VERSION})
    gap = _period_gap(row.get("period", "") for row in output)
    result = {"status": "ok" if output else "no_coverage", "source_id": SOURCE_ID,
            "methodology_regime": methodology_regime,
            "dataset_id": SPEND_DATASET, "scope": scope, "period": latest,
            "rows": output, "period_gap": gap, "freshness": _freshness(products.structured, latest_period=latest),
            "lineage": _lineage(rows), "manifest": manifest,
            "limitations": ["Quantiles are not mean enterprise spend", "Ramp network cohort"]}
    if as_frame:
        try:
            import pandas as pd
        except ImportError as exc:
            raise RuntimeError("pandas is required for as_frame=True") from exc
        frame = pd.DataFrame(result["rows"])
        frame.attrs.update({key: value for key, value in result.items() if key != "rows"})
        return frame
    return result


def ramp_model_market_share_series(products, *, scope: str = "model_market_share_overall",
                                   period: str = "", provider: str = "", model: str = "",
                                   as_of=None, include_vintages: bool = False,
                                   as_frame: bool = False,
                                   methodology_regime: str = DEFAULT_REGIME) -> dict[str, Any]:
    """v1 monthly API spend share. Under v2 it returns no rows and `scope_discontinued`."""
    if scope != "model_market_share_overall":
        raise ValueError(f"unsupported_ramp_model_scope:{scope}")
    rows = _query(products.structured, dataset_id=SPEND_DATASET, scope=scope,
                  as_of=as_of, include_vintages=include_vintages, methodology_regime=methodology_regime)
    if period:
        rows = [row for row in rows if _period_matches(row.get("period", ""), period)]
    if provider:
        rows = [row for row in rows if str(_dims(row).get("provider", "")).casefold() == provider.casefold()]
    if model:
        rows = [row for row in rows if str(_dims(row).get("model", "")).casefold() == model.casefold()]
    rows.sort(key=lambda row: (row.get("period", ""), row.get("entity_id", "")))
    output = [{"observation_id": row.get("observation_id"), "artifact_id": row.get("artifact_id"),
               "period": row.get("period"), "provider": _dims(row).get("provider"),
               "model": _dims(row).get("model"), "spend_type": _dims(row).get("spend_type"),
               "cohort": _dims(row).get("cohort"), "technology_scope": _dims(row).get("technology_scope"),
               "value": row.get("value"), "unit": row.get("unit"), "scope": scope,
               "quality_status": row.get("quality_status", "accepted"), "raw": _raw(row)} for row in rows]
    latest = max((row.get("period", "") for row in rows), default="")
    manifest = _manifest(products, rows, purpose=f"ramp_model_share:{scope}", as_of=as_of,
                         metadata={"scope": scope, "period": latest, "derivation_version": DERIVATION_VERSION})
    gap = _period_gap(row.get("period", "") for row in output)
    discontinued = _discontinued(scope, methodology_regime)
    result = {"status": "ok" if output else (discontinued or "no_coverage"), "source_id": SOURCE_ID,
            "methodology_regime": methodology_regime,
            "dataset_id": SPEND_DATASET, "scope": scope, "period": latest, "rows": output,
            "period_gap": gap,
            "freshness": _freshness(products.structured, latest_period=latest),
            "lineage": _lineage(rows), "manifest": manifest,
            "limitations": ["Token Spend Management connected businesses only", "API spend share, not enterprise adoption"]}
    if as_frame:
        try:
            import pandas as pd
        except ImportError as exc:
            raise RuntimeError("pandas is required for as_frame=True") from exc
        frame = pd.DataFrame(result["rows"])
        frame.attrs.update({key: value for key, value in result.items() if key != "rows"})
        return frame
    return result


def ramp_token_spend_share_series(products, *, scope: str = "token_spend_share_by_maker",
                                  period: str = "", maker: str = "", as_of=None,
                                  include_vintages: bool = False, as_frame: bool = False,
                                  methodology_regime: str = DEFAULT_REGIME) -> dict[str, Any]:
    """Weekly share of token spend by model maker or by model (Token Spend Management cohort)."""
    if scope not in TOKEN_SCOPES:
        raise ValueError(f"unsupported_ramp_token_scope:{scope}")
    rows = _query(products.structured, dataset_id=SPEND_DATASET, scope=scope,
                  as_of=as_of, include_vintages=include_vintages, methodology_regime=methodology_regime)
    if period:
        rows = [row for row in rows if row.get("period", "") == period]
    if maker:
        rows = [row for row in rows if str(_dims(row).get("maker", "")).casefold() == maker.casefold()]
    rows.sort(key=lambda row: (row.get("period", ""), row.get("entity_id", "")))
    output = [{"observation_id": row.get("observation_id"), "artifact_id": row.get("artifact_id"),
               "period": row.get("period"), "maker": _dims(row).get("maker"), "model": _dims(row).get("model"),
               "segment": _dims(row).get("segment"), "cohort": _dims(row).get("cohort"),
               "technology_scope": _dims(row).get("technology_scope"), "value": row.get("value"),
               "unit": row.get("unit"), "scope": scope, "period_basis": "calendar_week",
               "quality_status": row.get("quality_status", "accepted"), "raw": _raw(row)} for row in rows]
    latest = max((row.get("period", "") for row in rows), default="")
    manifest = _manifest(products, rows, purpose=f"ramp_token_share:{scope}", as_of=as_of,
                         metadata={"scope": scope, "period": latest, "derivation_version": DERIVATION_VERSION})
    result = {"status": "ok" if output else "no_coverage", "source_id": SOURCE_ID,
              "methodology_regime": methodology_regime,
              "dataset_id": SPEND_DATASET, "scope": scope, "period": latest, "rows": output,
              "periods": sorted({row["period"] for row in output}),
              "freshness": _freshness(products.structured, latest_period=latest),
              "lineage": _lineage(rows), "manifest": manifest,
              "limitations": ["Token Spend Management connected businesses only",
                              "weekly share of attributed token spend, not enterprise adoption"]}
    if as_frame:
        try:
            import pandas as pd
        except ImportError as exc:
            raise RuntimeError("pandas is required for as_frame=True") from exc
        frame = pd.DataFrame(result["rows"])
        frame.attrs.update({key: value for key, value in result.items() if key != "rows"})
        return frame
    return result


PAGE_STALE_DAYS = 35
_PAGE_FAILURES = {"export_unreadable", "methodology_drift", "validation_failed", "unreachable",
                  "source_conflict", "access_required"}


def ramp_page_part_status(products, *, as_of=None) -> dict[str, Any]:
    """State of the page runner part (`ramp_ai_index`): ok, its latest failure, or `export_stale`.

    The FRED part is independent; this never looks at it. Stale = no accepted page run for 35 days.
    """
    repository = products.structured
    checks = repository.source_checks(source_id=SOURCE_ID, limit=1)
    runs = repository.ingestion_history(source_id=SOURCE_ID, limit=50)
    latest_check = (checks[0].get("status") if checks else "") or ""
    successes = [row for row in runs if row.get("status") in {"succeeded", "partial", "no_change"}]
    last_success = max((str(row.get("started_at") or "") for row in successes), default="")
    now = as_of or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    base = {"last_success_at": last_success, "latest_check": latest_check, "stale_after_days": PAGE_STALE_DAYS}
    if last_success:
        age = (now - datetime.fromisoformat(last_success)).total_seconds() / 86400
        if age > PAGE_STALE_DAYS:
            return {**base, "status": "export_stale", "reason": f"网页部分最近一次成功入库在 {age:.0f} 天前"}
    if latest_check in _PAGE_FAILURES:
        return {**base, "status": latest_check, "reason": f"网页部分最近一次探测为 {latest_check}"}
    if not last_success:
        return {**base, "status": "export_stale", "reason": "网页部分尚无成功入库"}
    return {**base, "status": "ok", "reason": ""}


def ramp_comparability(products, *, as_of=None) -> dict[str, Any]:
    """Return an explicit matrix; values from different providers are not joined."""
    return {"status": "directional_only", "sources": {
        "ramp_ai_index": {"statistical_unit": "Ramp relevant businesses", "role": "paid business adoption / AI spend"},
        "us_census_btos": {"statistical_unit": "surveyed US businesses", "role": "self-reported business AI use"},
        "rps_genai_adoption": {"statistical_unit": "US employed adults 18-64", "role": "self-reported worker use"},
        "anthropic_economic_index": {"statistical_unit": "Claude product traffic", "role": "task/product traffic proxy"},
    }, "allowed": ["directional corroboration", "contextual juxtaposition"],
    "forbidden": ["average", "difference", "weighted fusion", "forward fill"]}
