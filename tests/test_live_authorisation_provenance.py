"""Live authorisation records are only relied on after their source is verified.

A row that names an issuer, an account and a 2099 expiry proves that somebody wrote it,
not that the account owner issued it. The gate trusts a record only once a verification
fact citing the user's own confirmation has been appended; verification and voiding are
appends, so the original row stays readable and the history shows what changed.
"""
from datetime import datetime, timedelta, timezone

import pytest

from ats.execution import live_authorisation as live

FUTURE = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
NOW = datetime.now(timezone.utc).isoformat()
USER_WORDS = "这不是我签发的实盘授权，同意作废。"


@pytest.fixture
def db(tmp_path) -> str:
    return str(tmp_path / "live.sqlite")


def _record(db, reference="LIVE-X", **overrides):
    fields = dict(reference=reference, issuer=live.ISSUER_HUMAN, authorised_by="o",
                  issued_by="i", scope=("trader",), environment="live", account="U1",
                  valid_until=FUTURE)
    fields.update(overrides)
    live.record_authorisation(live.LiveAuthorisation(**fields), path=db)


def _confirm(**overrides):
    fields = dict(confirmed_by="account owner", confirmation="I issued LIVE-X",
                  confirmation_source="chat 2026-10-28", confirmed_at=NOW, actor="test")
    fields.update(overrides)
    return fields


def test_a_complete_unexpired_record_without_verification_is_refused(db):
    _record(db)
    decision = live.evaluate_live_switch(authorisation_ref="LIVE-X", path=db)
    assert not decision.opened
    assert decision.reason == live.REASON_UNVERIFIED
    assert "授权来源未核实" in decision.detail
    assert decision.checks["source_verification"] == live.UNVERIFIED


def test_a_verified_record_is_accepted(db):
    _record(db)
    live.verify_authorisation("LIVE-X", path=db, **_confirm())
    assert live.evaluate_live_switch(authorisation_ref="LIVE-X", path=db).opened


def test_voiding_appends_a_record_and_keeps_the_original_readable(db):
    _record(db)
    live.void_authorisation("LIVE-X", path=db, **_confirm(
        confirmed_by="user", confirmation=USER_WORDS, confirmation_source="chat 2026-10-21"))
    decision = live.evaluate_live_switch(authorisation_ref="LIVE-X", path=db)
    assert not decision.opened and decision.reason == live.REASON_VOIDED
    assert "已作废" in decision.detail
    original = live.read_authorisation("LIVE-X", path=db)
    assert original is not None and original.authorised_by == "o"
    history = live.verification_history("LIVE-X", path=db)
    assert [row["status"] for row in history] == [live.VOIDED]
    assert history[0]["confirmation"] == USER_WORDS


@pytest.mark.parametrize("missing", ["confirmed_by", "confirmation", "confirmation_source", "confirmed_at"])
def test_a_change_without_the_users_confirmation_is_refused_and_nothing_changes(db, missing):
    _record(db)
    with pytest.raises(live.LiveAuthorisationError):
        live.verify_authorisation("LIVE-X", path=db, **_confirm(**{missing: ""}))
    with pytest.raises(live.LiveAuthorisationError):
        live.void_authorisation("LIVE-X", path=db, **_confirm(**{missing: ""}))
    assert live.read_verification("LIVE-X", path=db).status == live.UNVERIFIED
    assert live.verification_history("LIVE-X", path=db) == []


def test_a_voided_record_cannot_be_verified_again(db):
    _record(db)
    live.void_authorisation("LIVE-X", path=db, **_confirm())
    with pytest.raises(live.LiveAuthorisationError):
        live.verify_authorisation("LIVE-X", path=db, **_confirm())
    assert not live.evaluate_live_switch(authorisation_ref="LIVE-X", path=db).opened


def test_a_record_that_does_not_exist_cannot_be_verified(db):
    with pytest.raises(live.LiveAuthorisationError):
        live.verify_authorisation("LIVE-NOPE", path=db, **_confirm())


def test_verification_never_overrides_expiry_or_scope(db):
    _record(db, valid_until=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
    live.verify_authorisation("LIVE-X", path=db, **_confirm())
    assert live.evaluate_live_switch(authorisation_ref="LIVE-X", path=db).reason == live.REASON_EXPIRED
    _record(db, reference="LIVE-Y")
    live.verify_authorisation("LIVE-Y", path=db, **_confirm())
    assert live.evaluate_live_switch(authorisation_ref="LIVE-Y", consumer_id="chief",
                                     path=db).reason == live.REASON_OUT_OF_SCOPE


def test_cli_gate_reports_unverified_and_then_voided(db, capsys):
    from ats.runtime import cli

    _record(db)
    assert cli.main(["live", "gate", "--reference", "LIVE-X", "--db", db]) == 2
    assert "授权来源未核实" in capsys.readouterr().out
    assert cli.main(["live", "void", "--reference", "LIVE-X", "--db", db,
                     "--confirmed-by", "user", "--confirmation", USER_WORDS,
                     "--confirmation-source", "chat 2026-10-21", "--confirmed-at", NOW]) == 0
    capsys.readouterr()
    assert cli.main(["live", "gate", "--reference", "LIVE-X", "--db", db]) == 2
    assert "已作废" in capsys.readouterr().out
    assert cli.main(["live", "show-auth", "--reference", "LIVE-X", "--db", db]) == 0
    assert USER_WORDS in capsys.readouterr().out
