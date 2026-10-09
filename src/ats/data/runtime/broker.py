"""Read-only broker gateway. No connection creation, writes or research storage."""

from datetime import datetime, timezone
from ats.workflow.evaluation_clock import now as evaluation_now


def broker_state(broker) -> dict:
    """Read account, completed orders and executions, without ledger writes."""
    envelope = portfolio_snapshot(broker)
    errors = [envelope["reason"]] if envelope["reason"] else []
    for key, method in (("orders", "completed_orders"), ("fills", "get_fills")):
        try:
            values = getattr(broker, method)()
            if not isinstance(values, list):
                raise ValueError("broker_query_not_a_collection")
            envelope[key] = [v.model_dump(mode="json") if hasattr(v, "model_dump") else v
                             for v in values]
        except Exception as exc:
            envelope[key] = None
            envelope["status"] = "partial" if envelope["payload"] is not None else "unavailable"
            errors.append(f"broker_{key}_failed:{type(exc).__name__}")
    envelope["reason"] = ";".join(errors)
    return envelope


def portfolio_snapshot(broker) -> dict:
    queried_at = evaluation_now(timezone.utc)
    envelope = {"schema_version": "runtime-broker-v1", "owner": "ats.data.runtime",
                "input_mode": "runtime", "queried_at": queried_at.isoformat(),
                "source_as_of": None, "status": "unavailable", "payload": None,
                "reason": "broker_unavailable"}
    try:
        portfolio = broker.get_portfolio()
        # A snapshot may be created while the broker request is in flight.
        # Compare its source clock with response receipt, not request start.
        queried_at = evaluation_now(timezone.utc)
        envelope["queried_at"] = queried_at.isoformat()
        if portfolio is None:
            return envelope
        payload = portfolio.model_dump(mode="json") if hasattr(portfolio, "model_dump") else dict(portfolio)
        stamp = payload.get("as_of")
        if stamp:
            point = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            if point.tzinfo is None or point > queried_at:
                raise ValueError("invalid_broker_timestamp")
        return {**envelope, "payload": payload, "source_as_of": stamp,
                "status": "complete" if stamp else "partial",
                "reason": "" if stamp else "broker_timestamp_missing"}
    except Exception as exc:
        return {**envelope, "reason": f"broker_read_failed:{type(exc).__name__}"}
