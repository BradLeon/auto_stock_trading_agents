"""Minimal local TWS read probe; run outside sandbox, never submit/cancel orders.

Raw responses remain local. This receipt proves read availability only, not
qualification, full broker completeness, deployment permission or trade approval.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path


def probe(broker):
    from ats.execution import broker_write_guard as guard
    from ats.data.runtime.broker import broker_state
    if not guard.is_prohibited():
        raise ValueError("broker write prohibition required before any read")
    result = {"started_at": datetime.now(UTC).isoformat(), "readonly": True,
        "broker_writes_prohibited": True, "qualification_granted": False,
        "permission_granted": False, "queries": {}}
    state = broker_state(broker)
    result["broker_state"] = state
    try:
        values = broker.open_orders()
        if not isinstance(values, list):
            raise ValueError("open orders response must be a collection")
        result["open_orders"] = [v.model_dump(mode="json") if hasattr(v, "model_dump") else v for v in values]
    except Exception as exc:
        result["open_orders"] = None
        result["open_orders_error"] = type(exc).__name__
    result["status"] = "passed" if state["status"] == "complete" and result["open_orders"] is not None else "pending"
    result["ended_at"] = datetime.now(UTC).isoformat()
    result["response_sha256"] = hashlib.sha256(json.dumps(
        {"state": state, "open_orders": result["open_orders"]}, sort_keys=True).encode()).hexdigest()
    return result


def main():
    from ats.execution import broker_write_guard as guard
    from ats.broker.ibkr import IBKRBroker
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    # Reserve a distinct receipt before connecting; never overwrite old evidence.
    descriptor = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        guard.startup(caller="phase-f-readonly-probe", mode="shadow")
        result = probe(IBKRBroker())
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"output": str(args.out), "status": result["status"], "broker_writes_prohibited": True}))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
