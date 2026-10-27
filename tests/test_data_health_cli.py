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
    monkeypatch.setattr("ats.workflow.consumer_cutover.log",
                        lambda consumer_id=None, path=None: seen.update(consumer=consumer_id) or rows)
    code, out = _run(capsys, ["data", "health-log", "--consumer", "technical"])
    assert code == 0 and json.loads(out) == rows and seen == {"consumer": "technical"}


def test_the_old_command_leaves_a_prompt_stub_for_status_health_and_log(capsys):
    for action, replacement in (("status", "ats data health --consumer"), ("health", "ats data health --consumer"),
                                ("log", "ats data health-log")):
        code, out = _run(capsys, ["consumer-cutover", action])
        assert code == 2 and "已退役" in out and replacement in out


def test_restore_still_needs_a_consumer_id():
    import pytest

    with pytest.raises(SystemExit):
        cli.main(["consumer-cutover", "restore"])
