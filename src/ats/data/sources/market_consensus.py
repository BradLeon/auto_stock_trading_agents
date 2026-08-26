"""Persistent Yahoo Finance consensus snapshots with concrete target bindings."""

from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from typing import Any

from ...structured import (
    AdapterArtifact,
    AdapterBatch,
    AdapterFailure,
    FetchRequest,
    IngestionStatus,
    NativeRecord,
)


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _json_safe(value: Any) -> Any:
    """Convert pandas/numpy timestamp and scalar objects to JSON primitives."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            return _json_safe(value.item())
        except (TypeError, ValueError):
            pass
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except (TypeError, ValueError):
            pass
    return str(value)


def _iso_date(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if hasattr(value, "date"):
        try:
            value = value.date()
        except TypeError:
            pass
    text = str(value or "")[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return ""


def _next_quarter_end(value: str) -> str:
    """Advance a reported fiscal-period end by three calendar months."""
    current = date.fromisoformat(value)
    month_index = current.month - 1 + 3
    year = current.year + month_index // 12
    month = month_index % 12 + 1
    day = min(current.day, monthrange(year, month)[1])
    return date(year, month, day).isoformat()


def _relative_month_end(value: str, reference: date) -> str:
    try:
        offset = int(value.removesuffix("m"))
    except (AttributeError, ValueError):
        offset = 0
    month_index = reference.month - 1 + offset
    year = reference.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, monthrange(year, month)[1]).isoformat()


def _frame_row(frame: Any, label: str) -> dict:
    if frame is None or not hasattr(frame, "index") or label not in frame.index:
        return {}
    row = frame.loc[label]
    return {str(column): row[column] for column in getattr(frame, "columns", [])}


def _frame_records(frame: Any) -> list[dict]:
    if frame is None or not hasattr(frame, "iterrows"):
        return []
    output = []
    for index, row in frame.iterrows():
        item = {str(column): row[column] for column in getattr(frame, "columns", [])}
        item["_index"] = index
        output.append(item)
    return output


def load_yfinance_snapshot(symbol: str) -> dict:
    """Read all currently used low-frequency analyst endpoints for one ticker.

    Yahoo does not expose a reliable publication timestamp for these tables. The
    returned snapshot therefore intentionally carries no ``published_at``; the
    ingestion batch's fetch time is the first defensible ``known_at``.
    """
    import yfinance as yf

    from ..base import yf_symbol

    ticker = yf.Ticker(yf_symbol(symbol))
    estimates = {
        "eps_0q": _frame_row(getattr(ticker, "earnings_estimate", None), "0q"),
        "revenue_0q": _frame_row(getattr(ticker, "revenue_estimate", None), "0q"),
    }
    calendar = getattr(ticker, "calendar", None)
    calendar = calendar if isinstance(calendar, dict) else {}
    if not estimates["eps_0q"] and calendar.get("EPS Estimate") is not None:
        estimates["eps_0q"] = {"avg": calendar.get("EPS Estimate")}
    if not estimates["revenue_0q"] and calendar.get("Revenue Estimate") is not None:
        estimates["revenue_0q"] = {"avg": calendar.get("Revenue Estimate")}

    latest_reported_period = ""
    quarterly = getattr(ticker, "quarterly_income_stmt", None)
    for column in getattr(quarterly, "columns", []):
        candidate = _iso_date(column)
        if candidate and candidate > latest_reported_period:
            latest_reported_period = candidate
    target_period = _next_quarter_end(latest_reported_period) \
        if latest_reported_period else ""

    return {
        "symbol": symbol.upper(),
        "currency": str((getattr(ticker, "fast_info", {}) or {}).get(
            "currency", "USD") or "USD").upper(),
        "estimates": estimates,
        "target_period": target_period,
        "target_event_date": _iso_date(calendar.get("Earnings Date")),
        "price_targets": dict(getattr(ticker, "analyst_price_targets", None) or {}),
        "rating_trend": _frame_records(getattr(ticker, "recommendations_summary", None)),
        "rating_changes": _frame_records(getattr(ticker, "upgrades_downgrades", None)),
        "reported_actuals": _frame_records(getattr(ticker, "earnings_history", None)),
    }


class YFinanceConsensusAdapter:
    source_id = "yfinance_consensus"
    dataset_id = "market_consensus"

    _ESTIMATES = {
        ("eps_0q", "avg"): ("eps_0q_avg", "currency_per_share"),
        ("eps_0q", "low"): ("eps_0q_low", "currency_per_share"),
        ("eps_0q", "high"): ("eps_0q_high", "currency_per_share"),
        ("revenue_0q", "avg"): ("revenue_0q_avg", "currency"),
        ("revenue_0q", "low"): ("revenue_0q_low", "currency"),
        ("revenue_0q", "high"): ("revenue_0q_high", "currency"),
    }
    _TARGETS = {
        "mean": "price_target_mean", "median": "price_target_median",
        "low": "price_target_low", "high": "price_target_high",
    }
    _RATINGS = {
        "strongBuy": "rating_0m_strong_buy", "strong_buy": "rating_0m_strong_buy",
        "buy": "rating_0m_buy", "hold": "rating_0m_hold",
        "sell": "rating_0m_sell", "strongSell": "rating_0m_strong_sell",
        "strong_sell": "rating_0m_strong_sell",
    }

    def __init__(self, *, snapshot_loader=None, clock=None):
        self.snapshot_loader = snapshot_loader or load_yfinance_snapshot
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def fetch(self, request: FetchRequest) -> AdapterBatch:
        if len(request.entities) != 1:
            raise ValueError("consensus adapter requires exactly one entity per snapshot")
        symbol = request.entities[0].upper()
        fetched_at = self.clock().astimezone(timezone.utc)
        snapshot = dict(self.snapshot_loader(symbol) or {})
        currency = str(request.query_scope.get("currency") or
                       snapshot.get("currency") or "USD").upper()
        target_period = _iso_date(request.query_scope.get("target_period")) or \
            _iso_date(snapshot.get("target_period"))
        target_event = _iso_date(request.query_scope.get("target_event_date")) or \
            _iso_date(snapshot.get("target_event_date"))
        target = target_period or target_event
        records: list[NativeRecord] = []
        failures: list[AdapterFailure] = []

        estimates = snapshot.get("estimates") or {}
        has_estimate = any(_number((estimates.get(section) or {}).get(field)) is not None
                           for section, field in self._ESTIMATES)
        if has_estimate and not target:
            failures.append(AdapterFailure(
                status=IngestionStatus.VALIDATION_FAILED, entity_id=symbol,
                slice_key="0q", message="relative 0q estimate has no concrete target binding"))
        elif target:
            for (section, field), (provider_field, unit_kind) in self._ESTIMATES.items():
                value = _number((estimates.get(section) or {}).get(field))
                if value is None:
                    continue
                unit = f"{currency}/share" if unit_kind == "currency_per_share" else currency
                records.append(NativeRecord(
                    entity_id=symbol, provider_field=provider_field, period=target,
                    value=value, unit=unit, currency=currency,
                    period_basis="target_quarter", dimensions={
                        "provider_relative_period": "0q",
                        "target_binding": "fiscal_period" if target_period else "earnings_event",
                        "target_period": target_period, "target_event_date": target_event,
                    }, raw={"section": section, "field": field,
                            "provider_value": value}))

        snapshot_period = fetched_at.date().isoformat()
        for field, provider_field in self._TARGETS.items():
            value = _number((snapshot.get("price_targets") or {}).get(field))
            if value is not None:
                records.append(NativeRecord(
                    entity_id=symbol, provider_field=provider_field,
                    period=snapshot_period, value=value, unit=f"{currency}/share",
                    currency=currency, period_basis="snapshot",
                    dimensions={"snapshot_kind": "analyst_price_target"},
                    raw={"field": field, "provider_value": value}))

        rating_rows = snapshot.get("rating_trend") or []
        for rating in rating_rows:
            relative = str(rating.get("period", rating.get("_index", "")))
            if not relative.endswith("m"):
                continue
            concrete_month = _relative_month_end(relative, fetched_at.date())
            for field, provider_field in self._RATINGS.items():
                value = _number(rating.get(field))
                if value is not None:
                    records.append(NativeRecord(
                        entity_id=symbol, provider_field=provider_field,
                        period=concrete_month, value=value, unit="count",
                        period_basis="snapshot", dimensions={
                            "provider_relative_period": relative,
                            "snapshot_kind": "rating_distribution"},
                        raw={"field": field, "provider_value": value}))

        recent_changes = []
        cutoff = fetched_at.date() - timedelta(days=120)
        for change in snapshot.get("rating_changes") or []:
            event_date = _iso_date(change.get("GradeDate") or change.get("date") or
                                   change.get("_index"))
            if not event_date or date.fromisoformat(event_date) < cutoff:
                continue
            recent_changes.append((event_date, change))
        for event_date, change in sorted(
                recent_changes, key=lambda item: item[0], reverse=True)[:8]:
            action = str(change.get("Action") or change.get("action") or "").lower()
            score = 1 if action.startswith("up") else -1 if action.startswith("down") else 0
            records.append(NativeRecord(
                entity_id=symbol, provider_field="rating_change_score", period=event_date,
                value=score, unit="score", period_basis="event",
                event_time=datetime.fromisoformat(event_date).replace(tzinfo=timezone.utc),
                dimensions={
                    "firm": str(change.get("Firm") or change.get("firm") or ""),
                    "from_grade": str(change.get("FromGrade") or change.get("from_grade") or ""),
                    "to_grade": str(change.get("ToGrade") or change.get("to_grade") or ""),
                    "action": action,
                }, raw={key: str(value) for key, value in change.items()}))

        for actual in snapshot.get("reported_actuals") or []:
            period = _iso_date(actual.get("quarter") or actual.get("Quarter") or
                               actual.get("_index"))
            value = _number(actual.get("epsActual", actual.get("reported_eps")))
            if period and value is not None:
                records.append(NativeRecord(
                    entity_id=symbol, provider_field="reported_eps_actual", period=period,
                    value=value, unit=f"{currency}/share", currency=currency,
                    period_basis="quarter", dimensions={"statement_scope": "reported_actual"},
                    raw={key: str(item) for key, item in actual.items()}))

        if not records and not failures:
            failures.append(AdapterFailure(
                status=IngestionStatus.NO_COVERAGE, entity_id=symbol,
                message="Yahoo Finance returned no usable consensus fields"))
        for record in records:
            # A consensus observation is a snapshot, not a source publication. Even
            # unchanged values must create a new vintage at each real fetch so that
            # later as_of replay can prove what was knowable at that time.
            record.raw["snapshot_fetched_at"] = fetched_at.isoformat()
        status = (IngestionStatus.PARTIAL if records and failures else
                  IngestionStatus.SUCCEEDED if records else failures[0].status)
        artifact_payload = {
            "symbol": symbol, "fetched_at": fetched_at.isoformat(),
            "published_at": None, "target_period": target_period,
            "target_event_date": target_event, "snapshot": _json_safe(snapshot),
        }
        return AdapterBatch(
            source_id=request.source_id, dataset_id=request.dataset_id,
            status=status, fetched_at=fetched_at, records=records, failures=failures,
            artifacts=[AdapterArtifact(
                payload=artifact_payload,
                query_scope={**request.query_scope, "entity": symbol},
                source_url=f"https://finance.yahoo.com/quote/{symbol}/analysis/",
                source_version=fetched_at.isoformat(), media_type="application/json",
                retention="normalized_snapshot", metadata={
                    "published_at_available": False,
                    "target_binding": "fiscal_period" if target_period else
                    "earnings_event" if target_event else "unresolved",
                })],
            provider_metadata={"published_at_available": False,
                               "target_period": target_period,
                               "target_event_date": target_event})
