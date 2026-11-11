"""`ats data health --consumer` / `ats data health-log` replace the health half of `ats consumer-cutover` (tasks 5.1)."""

import json

from ats.runtime import cli


def _run(capsys, argv):
    code = cli.main(argv)
    return code, capsys.readouterr().out


def test_data_health_with_a_consumer_prints_that_consumers_data_product_health(capsys, monkeypatch):
    seen = {}

    def check(consumer_id, **_):
        seen["consumer"] = consumer_id
        return {"consumer": consumer_id, "status": "ok", "problems": []}

    monkeypatch.setattr("ats.workflow.consumer_health.check", check)
    code, out = _run(capsys, ["data", "health", "--consumer", "information"])
    assert code == 0 and seen == {"consumer": "information"}
    assert json.loads(out)["status"] == "ok"


def test_an_unhealthy_consumer_exits_nonzero(capsys, monkeypatch):
    monkeypatch.setattr("ats.workflow.consumer_health.check",
                        lambda consumer_id, **_: {"consumer": consumer_id, "status": "failed", "problems": ["stale"]})
    code, out = _run(capsys, ["data", "health", "--consumer", "technical"])
    assert code == 1 and json.loads(out)["problems"] == ["stale"]


def test_data_health_without_a_consumer_is_still_the_platform_health(capsys, monkeypatch):
    monkeypatch.setattr("ats.workflow.consumer_health.check",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not be asked")))
    monkeypatch.setattr("ats.data.products.base.DataProducts.health", lambda self: {"platform": "ok"})
    code, out = _run(capsys, ["data", "health"])
    assert code == 0 and json.loads(out) == {"platform": "ok"}


def test_health_log_reads_the_recorded_changes(capsys, monkeypatch):
    rows = [{"seq": 1, "consumer_id": "technical", "event": "health"}]
    seen = {}
    monkeypatch.setattr("ats.workflow.consumer_health.health_log",
                        lambda consumer_id=None, path=None: seen.update(consumer=consumer_id) or rows)
    code, out = _run(capsys, ["data", "health-log", "--consumer", "technical"])
    assert code == 0 and json.loads(out) == rows and seen == {"consumer": "technical"}


def test_the_retired_command_leaves_a_prompt_stub(capsys):
    """`ats consumer-cutover` went entirely with the read-route tables (switch-trade r5 9.3), `restore` included."""
    for action in ("status", "health", "log", "restore"):
        code, out = _run(capsys, ["consumer-cutover", action])
        assert code == 2 and "已退役" in out and "ats data health" in out


def test_the_retired_assurance_action_leaves_a_prompt_stub(capsys):
    code, out = _run(capsys, ["data", "assurance", "query"])
    assert code == 2 and "已退役" in out and "dataflow_assurance_events" in out


