"""Read-only broker gateway. No connection creation, writes or research storage."""

from datetime import datetime, timezone


def portfolio_snapshot(broker) -> dict:
    queried_at = datetime.now(timezone.utc)
    envelope = {"schema_version": "runtime-broker-v1", "owner": "ats.data.runtime",
                "input_mode": "runtime", "queried_at": queried_at.isoformat(),
                "source_as_of": None, "status": "unavailable", "payload": None,
                "reason": "broker_unavailable"}
    try:
        portfolio = broker.get_portfolio()
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
