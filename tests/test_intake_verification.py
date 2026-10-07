"""Phase F 7.1–7.6 — isolated intake verification for the ten roles.

Most of these tests are about things that must FAIL or must NOT be claimable,
because the failure mode this module exists to prevent is a verification that
passes while proving nothing:

- an entry point that was never reached reported as "no bypass found";
- an isolated run quoted as proof that the production ledger is complete;
- a summary of "everything checked out" from a run that checked nothing;
- a restart that silently continues with an empty projection.

The one place a PASS is the interesting result is the real scan of the ten roles,
which is pinned here so a future change cannot quietly widen the forbidden set
into meaninglessness or narrow it into ignoring a real bypass.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from ats.agent.task_projection import ProjectionScope, build_envelope
from ats.execution import broker_write_guard as guard
from ats.workflow import intake_verification as iv

NOW = datetime(2026, 10, 6, 8, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clean_guard():
    guard.reset_for_tests()
    yield
    guard.reset_for_tests()


# --------------------------------------------------------------------------- #
# 7.1 — the isolated acceptance entry
# --------------------------------------------------------------------------- #

def test_verification_starts_without_production_qualification(tmp_path):
    """The whole reason this module exists.

    `F.0.2` measured 0/10 eligible. If the entry point that produces the missing
    evidence required that evidence, qualification would be unobtainable rather
    than merely un-obtained.
    """
    with iv.isolated_verification("intake-no-qual", root=tmp_path) as attestation:
        assert attestation.qualification_required is False
        assert attestation.production_ledger_integrity_proven is False


def test_the_entry_point_does_not_ask_assurance(tmp_path, monkeypatch):
    """A verification that called `qualification()` to start would deadlock.

    Patched to raise so that any call from inside the context manager is a hard
    failure rather than a passing test.
    """
    from ats.data import assurance

    def _refuse(**_kwargs):
        raise AssertionError("intake verification must not gate on qualification")

    monkeypatch.setattr(assurance, "qualification", _refuse)
    with iv.isolated_verification("intake-no-gate", root=tmp_path):
        pass


def test_verification_runs_under_the_process_write_prohibition(tmp_path):
    """Redirecting ledgers does not stop a real order — the broker is not a DB."""
    with iv.isolated_verification("intake-guard", root=tmp_path):
        assert guard.is_prohibited() is True
        with pytest.raises(guard.BrokerWriteProhibited):
            guard.check_broker_write(operation="place_orders", caller="intake")


def test_every_persistence_surface_is_redirected_during_verification(tmp_path):
    """A surface the verification can still write to is a surface it can corrupt."""
    from ats.workflow import isolation

    with iv.isolated_verification("intake-surfaces", root=tmp_path) as attestation:
        for var in isolation.PERSISTENCE_ENV_VARS:
            import os

            assert os.environ[var].startswith(str(tmp_path)), var
        assert set(attestation.surfaces_redirected) == set(
            isolation.PERSISTENCE_ENV_VARS)


def test_verification_refuses_to_start_without_the_capability(tmp_path, monkeypatch):
    """A run that cannot prove writes are prohibited must not proceed."""
    monkeypatch.setattr(
        guard, "assert_broker_writes_prohibited",
        lambda **_kwargs: (_ for _ in ()).throw(guard.BrokerWriteProhibited(
            "capability missing", reason_code="guard_missing", refusal_id="")))
    with pytest.raises(guard.BrokerWriteProhibited):
        with iv.isolated_verification("intake-noguard", root=tmp_path):
            pytest.fail("the verification body must not execute")


def test_a_production_side_effect_invalidates_the_run(tmp_path, monkeypatch):
    """`7.1` second case, and the half that is easy to get wrong.

    A production write must make the acceptance INVALID, not merely be logged. A
    verification whose entire value is "I changed nothing" cannot be cited as
    evidence if it changed something — and a leak discovered at the end of a long
    run is exactly the one nobody goes looking for.
    """
    reads = iter([{"ats.sqlite:trades": 4}, {"ats.sqlite:trades": 5}])
    monkeypatch.setattr(iv, "_production_fingerprint", lambda: next(reads))

    with pytest.raises(iv.ProductionSideEffect) as excinfo:
        with iv.isolated_verification("intake-leak", root=tmp_path):
            pass

    assert "ats.sqlite:trades" in str(excinfo.value)
    assert "invalid" in str(excinfo.value)


def test_no_production_approval_is_produced_by_a_verification(tmp_path):
    """An approval is the input to a real order; a verification must not mint one."""
    from ats.memory import get_store

    with iv.isolated_verification("intake-approval", root=tmp_path):
        store = get_store()
        assert store.conn.execute(
            "SELECT COUNT(*) FROM boss_approvals").fetchone()[0] == 0
        assert store.conn.execute(
            "SELECT COUNT(*) FROM decision_revisions").fetchone()[0] == 0


def test_clearing_a_side_effect_requires_explicit_confirmation():
    """A cleanup that deletes ledger rows on its own initiative is a second way
    to corrupt the ledger it exists to protect."""
    with pytest.raises(iv.IntakeVerificationError, match="explicit"):
        iv.clear_production_side_effect("ats.sqlite:trades")


def test_a_confirmed_clear_reports_the_target_without_deleting():
    plan = iv.clear_production_side_effect("ats.sqlite:trades", confirm=True)
    assert plan["table"] == "trades"
    assert plan["cleared"] is False, "deletion is an operator action, not a side effect"


def test_an_isolated_result_cannot_authorise_a_trade(tmp_path):
    """`7.1` third case / `7.6`: the path working is not ledger integrity."""
    with iv.isolated_verification("intake-not-tradable", root=tmp_path) as att:
        with pytest.raises(iv.IntakeVerificationError) as excinfo:
            iv.assert_isolated_result_not_tradable(att, consumer_id="trader")

    message = str(excinfo.value)
    assert "trader" in message
    assert "production reading qualification" in message


def test_the_attestation_states_what_it_does_not_prove(tmp_path):
    with iv.isolated_verification("intake-attest", root=tmp_path) as att:
        pass
    row = att.as_row()
    assert row["production_ledger_integrity_proven"] is False
    assert row["qualification_required"] is False
    assert row["broker_write_prohibited"] is True
    assert row["production_side_effects"] == []


# --------------------------------------------------------------------------- #
# 7.2 — the ten roles' access, scanned from their real modules
# --------------------------------------------------------------------------- #

def test_every_consumer_has_a_declared_entry_point():
    assert set(iv.CONSUMER_ENTRY_POINTS) == set(iv.TEN_CONSUMERS)


def test_every_declared_entry_point_resolves_to_a_real_module():
    """An entry point that does not exist cannot be verified through, and saying
    so is the difference between "no bypass" and "no check"."""
    missing = [dotted for dotted in
               (entry for entries in iv.CONSUMER_ENTRY_POINTS.values()
                for entry in entries)
               if iv._module_path(dotted) is None]
    assert not missing, f"declared entry points that do not resolve: {missing}"


def test_all_ten_roles_are_reached_by_the_scan():
    """A record with an empty call path is a failure to verify, not a pass."""
    for consumer_id in iv.TEN_CONSUMERS:
        record = iv.scan_consumer_access(consumer_id)
        assert record.reached_entry_point is True, consumer_id


def test_a_role_that_never_reaches_the_governed_surface_is_registered_not_passed():
    """The contract names `consumer_api.read_input` as every consumer's read API.

    Four roles (`fundamental`, `risk`, `clerk`, `trader`) plus chief do not reach
    it from their own module, so their actual access is undescribed by the
    contract. The spec requires a not-yet-wired consumer to have its dependency
    REGISTERED — so this must be a finding, and it must be a distinct kind from a
    bypass: "you read the wrong table" and "you have not been wired to the
    governed API at all" call for different work.
    """
    unwired = []
    for consumer_id in iv.TEN_CONSUMERS:
        record = iv.scan_consumer_access(consumer_id)
        kinds = {v.kind for v in record.violations}
        reaches = any(m.startswith(iv.SANCTIONED_READ_SURFACE)
                      for m in record.call_path)
        if not reaches:
            unwired.append(consumer_id)
            assert "governed_read_surface_not_reached" in kinds, consumer_id
            # The two findings are independent, not alternatives: `trader` is
            # both unwired AND bypassing, and reporting only one would hide the
            # other. So the assertion is that the wiring gap is present, not that
            # it is the only thing wrong.
        else:
            assert "governed_read_surface_not_reached" not in kinds, consumer_id

    # Pinned so a future migration shows up as a change in THIS list rather than
    # as a silently passing suite.
    assert set(unwired) == {"fundamental", "chief", "risk", "trader", "clerk"}


def test_the_ten_analysis_and_decision_roles_are_the_declared_ten():
    assert set(iv.ANALYSIS_CONSUMERS) == {
        "layer", "information", "sector", "fundamental", "macro", "technical"}
    assert set(iv.DECISION_CONSUMERS) == {"chief", "risk", "trader", "clerk"}
    assert len(iv.TEN_CONSUMERS) == 10


def test_the_reference_price_read_is_not_live_but_is_still_reported():
    """Task 7.9 moved this from a live provider read to a retained one.

    Superseded by `test_the_real_scan_reports_the_stopped_read_as_disabled_not_live`
    below, which also pins the location. Kept as a second assertion on the
    behaviour rather than deleted, because "the provider is no longer reached" is
    the load-bearing claim and it should be stated twice in different terms.
    """
    record = iv.scan_consumer_access("trader")
    assert not [v for v in record.violations if v.kind == "provider_bypass"]
    assert record.compliant is False


def test_the_live_bypass_check_now_covers_all_ten_roles():
    """After task 7.9 no role makes a LIVE provider / raw-table / vendor read.

    The scan's bypass check still has to run over all ten, otherwise a new bypass
    in a role nobody happened to look at would pass silently. The retained
    (disabled) path is asserted separately by
    `test_only_trader_carries_a_provider_read_at_all`.
    """
    live = {}
    for consumer_id in iv.TEN_CONSUMERS:
        record = iv.scan_consumer_access(consumer_id)
        found = [v for v in record.violations
                 if v.kind in {"provider_bypass", "base_table_bypass",
                               "third_party_client_bypass"}]
        if found:
            live[consumer_id] = [f"{v.kind}@{v.location}" for v in found]

    assert live == {}, f"a live data-read bypass reappeared: {live}"


def test_the_real_scan_reports_the_stopped_read_as_disabled_not_live():
    """Pinned because it is the change task 7.9 made.

    `trader` used to reach the yfinance provider for its reference price. Auto
    execution is now disabled and that read is retained only for the plan-A
    restore, so the scan must report `disabled_bypass` — the code is still there,
    which still blocks, but the role no longer currently reads through it.

    If this ever returns to `provider_bypass`, something started calling the
    retained helper again.
    """
    record = iv.scan_consumer_access("trader")
    kinds = {v.kind for v in record.violations}
    assert "provider_bypass" not in kinds, (
        "the reference-price read is live again — the plan-A restore is the only "
        "thing that should make it reachable, and that needs MARKET_DATA declared")
    assert "disabled_bypass" in kinds
    assert record.compliant is False, (
        "a retained implementation still blocks until it is removed or authorised")


def test_only_trader_carries_a_provider_read_at_all():
    """Every other role is clean of provider / raw-table / vendor-client reads.

    Counts the retained (disabled) read too — it is still a provider import in the
    source, and pretending otherwise would hide the very thing plan A has to fix.
    """
    offenders: dict[str, list[str]] = {}
    for consumer_id in iv.TEN_CONSUMERS:
        record = iv.scan_consumer_access(consumer_id)
        read = [v for v in record.violations
                if v.kind in {"provider_bypass", "base_table_bypass",
                              "third_party_client_bypass", "disabled_bypass"}]
        if read:
            offenders[consumer_id] = sorted(f"{v.kind}@{v.location}" for v in read)

    assert set(offenders) == {"trader"}
    assert all(kind.startswith("disabled_bypass@ats.trader.execute:")
               for kind in offenders["trader"])


def test_a_disabled_path_is_reported_without_claiming_a_live_read(tmp_path):
    """A retained helper must be distinguished from a live one by something
    checkable, not by assertion — otherwise the scan cries wolf on a deliberately
    closed path and its reader learns to ignore it."""
    package = tmp_path / "ats" / "role_x"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "entry.py").write_text(
        "def kept_for_later():\n"
        '    """DISABLED — retained. TODO(方案 A): restore."""\n'
        "    from ats.data import sources\n"
        "    return sources\n")

    original_root, original_path = iv.PACKAGE_ROOT, iv._module_path
    original_calls = iv._PACKAGE_CALLS
    iv.PACKAGE_ROOT = tmp_path / "ats"
    iv._module_path = lambda dotted: _resolve_under(tmp_path, dotted)
    iv._PACKAGE_CALLS = None
    try:
        record = iv.scan_consumer_access("macro",
                                         extra_entry_points=("ats.role_x.entry",))
    finally:
        iv.PACKAGE_ROOT, iv._module_path = original_root, original_path
        iv._PACKAGE_CALLS = original_calls

    kinds = {v.kind for v in record.violations}
    assert "disabled_bypass" in kinds
    assert "provider_bypass" not in kinds


def test_a_called_helper_is_treated_as_live_even_if_marked_disabled(tmp_path):
    """The marker is not a licence: if anything calls it, it is live again."""
    package = tmp_path / "ats" / "role_y"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "entry.py").write_text(
        "def kept_for_later():\n"
        '    """DISABLED — retained. TODO(方案 A): restore."""\n'
        "    from ats.data import sources\n"
        "    return sources\n"
        "\n\ndef caller():\n"
        "    return kept_for_later()\n")

    original_root, original_path = iv.PACKAGE_ROOT, iv._module_path
    original_calls = iv._PACKAGE_CALLS
    iv.PACKAGE_ROOT = tmp_path / "ats"
    iv._module_path = lambda dotted: _resolve_under(tmp_path, dotted)
    iv._PACKAGE_CALLS = None
    try:
        record = iv.scan_consumer_access("macro",
                                         extra_entry_points=("ats.role_y.entry",))
    finally:
        iv.PACKAGE_ROOT, iv._module_path = original_root, original_path
        iv._PACKAGE_CALLS = original_calls

    kinds = {v.kind for v in record.violations}
    assert "provider_bypass" in kinds, (
        "a DISABLED marker on a function something calls is just a comment")
    assert "disabled_bypass" not in kinds


def test_a_violation_names_a_location_a_reader_can_act_on():
    """'Non-compliant' without a location cannot be fixed.

    A read violation names a line, because that is what someone has to edit. A
    wiring gap names its module, which is the right granularity for "this role was
    never pointed at the governed API" — there is no line to fix there.
    """
    record = iv.scan_consumer_access("trader")
    by_kind = {v.kind: v for v in record.violations}

    read = by_kind["disabled_bypass"]
    assert read.location.startswith("ats.trader.execute:")
    assert read.detail

    unwired = by_kind["governed_read_surface_not_reached"]
    assert unwired.location == "ats.trader.execute"
    assert unwired.detail


def test_a_provider_module_is_flagged_with_its_reason(tmp_path):
    """The check must work on a module that does bypass, not only on real code
    that happens to be clean today."""
    package = tmp_path / "ats" / "fake_role"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "entry.py").write_text(
        "from ats.data import sources\n\n\ndef run():\n    return sources.fetch()\n")

    original_root, original_path = iv.PACKAGE_ROOT, iv._module_path
    iv.PACKAGE_ROOT = tmp_path / "ats"
    iv._module_path = lambda dotted: _resolve_under(tmp_path, dotted)
    try:
        record = iv.scan_consumer_access(
            "layer", extra_entry_points=("ats.fake_role.entry",))
    finally:
        iv.PACKAGE_ROOT, iv._module_path = original_root, original_path

    kinds = {v.kind for v in record.violations}
    assert "provider_bypass" in kinds
    assert record.compliant is False


def test_a_base_table_read_is_flagged_separately_from_a_provider(tmp_path):
    """The two are different defects: one skips admission, the other has no
    vintage. Collapsing them loses the reason."""
    package = tmp_path / "ats" / "fake_role2"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "entry.py").write_text(
        "from ats.data.stores import structured\n\n\ndef run():\n"
        "    return structured.get_platform_repository()\n")

    original_root, original_path = iv.PACKAGE_ROOT, iv._module_path
    iv.PACKAGE_ROOT = tmp_path / "ats"
    iv._module_path = lambda dotted: _resolve_under(tmp_path, dotted)
    try:
        record = iv.scan_consumer_access(
            "macro", extra_entry_points=("ats.fake_role2.entry",))
    finally:
        iv.PACKAGE_ROOT, iv._module_path = original_root, original_path

    assert any(v.kind == "base_table_bypass" for v in record.violations)
    assert not any(v.kind == "provider_bypass" for v in record.violations)


def test_a_third_party_client_is_flagged(tmp_path):
    package = tmp_path / "ats" / "fake_role3"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "entry.py").write_text("import yfinance\n\n\ndef run():\n    return 1\n")

    original_root, original_path = iv.PACKAGE_ROOT, iv._module_path
    iv.PACKAGE_ROOT = tmp_path / "ats"
    iv._module_path = lambda dotted: _resolve_under(tmp_path, dotted)
    try:
        record = iv.scan_consumer_access(
            "sector", extra_entry_points=("ats.fake_role3.entry",))
    finally:
        iv.PACKAGE_ROOT, iv._module_path = original_root, original_path

    assert any(v.kind == "third_party_client_bypass" for v in record.violations)


def test_workflow_memory_is_not_a_data_plane_bypass():
    """`ats.memory` is the projection store — the opinion channel governed by
    7.3, not the raw repository governed here. Treating it as a base-table read
    flagged nearly every role and would have taught readers to ignore the check.
    """
    assert "ats.memory" not in iv.BASE_TABLE_MODULES


def test_the_product_layer_is_not_treated_as_a_bypass():
    """It owns the raw repositories legitimately; its internals are its business."""
    assert iv.PRODUCT_LAYER not in iv.PROVIDER_MODULES
    assert iv.PRODUCT_LAYER not in iv.BASE_TABLE_MODULES
    assert iv.PRODUCT_LAYER in iv.SANCTIONED_READ_SURFACE


def test_an_undeclared_consumer_is_refused_rather_than_reported_clean():
    with pytest.raises(iv.IntakeVerificationError, match="no declared entry point"):
        iv.scan_consumer_access("not_a_consumer")


def test_an_entry_point_that_does_not_resolve_is_a_violation():
    """An extra entry point that cannot be resolved is recorded as missing, and
    the role is still reached through its declared one — the two are separate
    facts and conflating them hides either."""
    record = iv.scan_consumer_access(
        "layer", extra_entry_points=("ats.does.not.exist",))
    assert any(v.kind == "entry_point_missing" for v in record.violations)
    assert record.reached_entry_point is True


def test_a_role_whose_only_entry_point_is_missing_is_not_reached():
    """With no resolvable entry point there is nothing to have verified, and an
    empty call path must never read as a clean pass."""
    record = iv.AccessRecord(consumer_id="ghost", entry_point="ats.does.not.exist")
    iv.scan_consumer_access  # the scanner is exercised above; this asserts the property
    assert record.reached_entry_point is False
    assert record.compliant is False


def _resolve_under(root, dotted: str):
    import pathlib

    parts = dotted.split(".")
    base = pathlib.Path(root) / pathlib.Path(*parts)
    candidate = base.with_suffix(".py")
    if candidate.is_file():
        return candidate
    package = base / "__init__.py"
    return package if package.is_file() else None


# --------------------------------------------------------------------------- #
# 7.3 — the opinion whitelist, candidate material and writeback
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("source,consumer", sorted(iv.ALLOWED_OPINION_EDGES))
def test_the_two_declared_opinion_edges_are_allowed(source, consumer):
    """`ALLOWED_OPINION_EDGES` is oriented (source, consumer): layer→sector means
    SECTOR reads LAYER. Reversing the pair would report the two permitted edges
    as leakage — the one failure a whitelist check cannot afford."""
    assert iv.ALLOWED_OPINION_EDGES == frozenset({("layer", "sector"),
                                                  ("information", "fundamental")})
    record = iv.AccessRecord(consumer_id=consumer, entry_point="x",
                             call_path=("ats.data.products",),
                             opinion_inputs=(source,))
    iv._check_opinion_dependencies(record)
    assert record.violations == [], record.violations


@pytest.mark.parametrize("consumer,source", [
    ("layer", "macro"), ("layer", "technical"), ("sector", "fundamental"),
    ("macro", "layer"), ("technical", "information"),
    ("fundamental", "layer"), ("information", "sector"),
    ("sector", "chief"), ("layer", "chief"),
])
def test_every_other_opinion_edge_is_leakage(consumer, source):
    record = iv.AccessRecord(consumer_id=consumer, entry_point="x",
                             call_path=("ats.data.products",),
                             opinion_inputs=(source,))
    iv._check_opinion_dependencies(record)
    assert any(v.kind == "opinion_leakage" for v in record.violations), (
        f"{source}->{consumer} should be leakage")


def test_chief_is_the_declared_aggregator_and_is_exempt():
    """Chief reads every analyst's view by design. Flagging it would make the
    check something a reader learns to skip."""
    record = iv.AccessRecord(
        consumer_id="chief", entry_point="x", call_path=("ats.data.products",),
        opinion_inputs=("layer", "information", "sector", "fundamental",
                        "macro", "technical"))
    iv._check_opinion_dependencies(record)
    assert record.violations == []


def test_a_role_reading_its_own_opinion_is_not_leakage():
    record = iv.AccessRecord(consumer_id="sector", entry_point="x",
                             call_path=("ats.data.products",),
                             opinion_inputs=("sector", "layer"))
    iv._check_opinion_dependencies(record)
    assert record.violations == []


def test_an_admitted_document_is_not_a_candidate():
    assert iv.check_candidate_material(
        consumer_id="information", document_refs=["doc-1@v3"],
        admitted_refs=["doc-1@v3", "doc-2@v1"]) == []


def test_a_candidate_presented_as_published_is_flagged():
    """The check is by REFERENCE, not by a status flag the caller passes — the
    refs are what the analysis actually used."""
    violations = iv.check_candidate_material(
        consumer_id="information", document_refs=["doc-1@v3", "doc-9"],
        admitted_refs=["doc-1@v3"])
    assert len(violations) == 1
    assert violations[0].kind == "candidate_material_as_published"
    assert violations[0].location == "doc-9"


def test_an_opinion_written_as_a_shared_fact_is_flagged():
    """Once an opinion is a fact row, later readers cannot tell it apart from
    evidence and the reasoning that produced it is gone."""
    violations = iv.check_opinion_writeback(
        consumer_id="layer",
        statements=[{"table": "evidence_facts", "values": {"layer": "expanding"}}])
    assert len(violations) == 1
    assert violations[0].kind == "opinion_writeback"


def test_writing_a_projection_is_not_writeback():
    assert iv.check_opinion_writeback(
        consumer_id="layer",
        statements=[{"table": "task_projection_envelopes"}]) == []


def test_every_neutral_fact_table_named_in_the_project_exists_as_a_table_name():
    """The writeback check is only as good as the table list behind it."""
    from ats.memory.store import _SCHEMA

    known = set()
    for line in _SCHEMA.splitlines():
        stripped = line.strip()
        if stripped.startswith("CREATE TABLE IF NOT EXISTS "):
            # The trailing name carries a space before the opening paren, so a
            # plain split left "evidence_facts " and matched nothing.
            known.add(stripped.split("CREATE TABLE IF NOT EXISTS ")[1]
                      .split("(")[0].strip())
    # `data_evidence_facts` and friends live in the data layer, not in Workflow
    # memory; the rest must be real Workflow-memory tables.
    memory_tables = {t for t in iv.NEUTRAL_FACT_TABLES if t in known}
    assert memory_tables, "no neutral-fact table name matches a real table"
    assert {"evidence_facts", "measurement_points"} <= memory_tables


# --------------------------------------------------------------------------- #
# 7.4 — required-input blocking and restart resolvability
# --------------------------------------------------------------------------- #

# A payload per agent role. `build_envelope` validates against the role's own
# schema, so a generic dict is rejected — which is the point of the contract, and
# why these cannot all share one fixture.
ROLE_PAYLOADS: dict[str, dict] = {
    "layer_analysis": {"layer": "L4_interconnect", "status": "expanding",
                       "summary": "orders up", "findings": ["b2b 1.4"],
                       "confidence": 0.8},
    "information_brief": {"entity": "MU", "headline": "指引上修",
                          "summary": "上游订单走强", "relevance": "high",
                          "sources": ["rss:test"]},
    "sector_allocation": {"sector": "ai_hardware", "stance": "overweight",
                          "target_weight": 0.2, "rationale": "景气上行"},
    "fundamental_expectation_update": {"entity": "MU", "metric": "HBM4 ASP",
                                       "period": "FY27", "new_value": 210.0,
                                       "driver": "供给收缩"},
    "fundamental_event_review": {"entity": "MU", "event": "earnings",
                                 "period": "FY26Q3", "direction": 1,
                                 "magnitude": 0.05,
                                 "falsifiable_conditions": ["下季低于区间即证伪"]},
    "macro_review": {"regime": "risk_on", "summary": "流动性宽松",
                     "indicators": ["10y real yield"]},
    "technical_review": {"entity": "MU", "signal": "neutral", "summary": "区间震荡",
                         "levels": {"support": 300.0}},
}


def _envelope(role, scope, **extra):
    payload = ROLE_PAYLOADS.get(role)
    if payload is None:
        raise AssertionError(f"no schema-valid payload declared for role {role!r}")
    return build_envelope(
        role=role, payload=payload, scope=scope,
        as_of=NOW.isoformat(timespec="seconds"),
        valid_until=(NOW + timedelta(hours=48)).isoformat(timespec="seconds"),
        data_vintage_refs=["dataset@2026-10-05"], **extra)


def _complete_projections(registry):
    from ats.workflow.run_contracts import ROLE_TO_CATEGORY

    out = {}
    for task_id in registry.task_ids():
        role = registry.spec(task_id).agent_role
        out[task_id] = _envelope(role, ProjectionScope(kind="portfolio"))
    return out


def test_a_complete_run_may_enter_the_decision_cycle():
    from ats.workflow.run_contracts import default_registry

    registry = default_registry()
    result = iv.verify_required_input_blocking(
        registry=registry, projections=_complete_projections(registry),
        scope={"kind": "portfolio"})
    assert result["complete"] is True
    assert result["decision_cycle_entered"] is True
    assert result["verified"] is True


def test_a_missing_required_analysis_blocks_the_cycle():
    """`7.4`: the requirement is a refusal that NAMES the gap, not a gap report
    that exists. An incomplete snapshot must never open a cycle."""
    from ats.workflow.run_contracts import default_registry

    registry = default_registry()
    projections = _complete_projections(registry)
    projections.pop("technical_review")

    result = iv.verify_required_input_blocking(
        registry=registry, projections=projections, scope={"kind": "portfolio"})

    assert result["complete"] is False
    assert result["decision_cycle_entered"] is False
    assert result["verified"] is False
    assert any(gap["task_id"] == "technical_review" for gap in result["gaps"])
    assert "technical_review" in result["refusal_message"]


def test_an_empty_projection_set_blocks_and_names_every_category():
    from ats.workflow.run_contracts import default_registry

    registry = default_registry()
    result = iv.verify_required_input_blocking(
        registry=registry, projections={}, scope={"kind": "portfolio"})
    assert result["complete"] is False
    assert result["decision_cycle_entered"] is False
    assert {gap["category"] for gap in result["gaps"]} >= {
        "layer_analysis", "information_brief", "sector_allocation",
        "fundamental_analysis", "macro_review", "technical_review"}


def test_a_missing_analysis_is_reported_by_name_not_as_a_count():
    from ats.workflow.run_contracts import default_registry

    registry = default_registry()
    projections = _complete_projections(registry)
    # BOTH fundamental modes absent. Nulling only one leaves the category
    # satisfied — the contract judges per CATEGORY, since the fundamental analyst
    # runs in exactly one mode per cycle and demanding both would leave every
    # cycle permanently incomplete. That is why this test removes both.
    projections["fundamental_expectation_update"] = None
    projections["fundamental_event_review"] = None

    outcome = iv.verify_required_input_blocking(
        registry=registry, projections=projections, scope={"kind": "portfolio"})

    assert outcome["complete"] is False
    categories = {gap["category"] for gap in outcome["gaps"]}
    assert "fundamental_analysis" in categories
    # Every gap names the task it is about — a count would leave the reader
    # nothing to act on.
    assert all(gap["task_id"] for gap in outcome["gaps"])
    assert "fundamental_expectation_update" in outcome["refusal_message"]


def test_a_disagreement_between_the_two_contracts_is_reported_not_hidden():
    """The snapshot judges per CATEGORY; `build_run_result` judges per TASK.

    Production's `open_decision_cycle` gates on the snapshot alone, so with one
    fundamental mode missing the cycle WOULD open — while the run contract says
    incomplete. Adopting either verdict silently would hide a real conflict
    between two contracts that both claim to govern entry, so it is reported and
    the run is not marked verified.
    """
    from ats.workflow.run_contracts import default_registry

    registry = default_registry()
    projections = _complete_projections(registry)
    projections["fundamental_event_review"] = None

    outcome = iv.verify_required_input_blocking(
        registry=registry, projections=projections, scope={"kind": "portfolio"})

    assert outcome["complete"] is True
    assert outcome["run_contract_complete"] is False
    assert outcome["decision_cycle_entered"] is False
    assert len(outcome["contract_disagreements"]) == 1
    assert "fundamental_event_review" in outcome["contract_disagreements"][0]
    assert outcome["verified"] is False, (
        "a run whose two governing contracts disagree is not a verified run")


def test_a_fully_consistent_run_reports_no_disagreement():
    from ats.workflow.run_contracts import default_registry

    registry = default_registry()
    outcome = iv.verify_required_input_blocking(
        registry=registry, projections=_complete_projections(registry),
        scope={"kind": "portfolio"})
    assert outcome["contract_disagreements"] == []
    assert outcome["verified"] is True


# --- restart ---------------------------------------------------------------- #

def _store_with_projections(tmp_path):
    from ats.memory.store import TradingMemory

    store = TradingMemory(str(tmp_path / "restart.sqlite"))
    envelope = _envelope("macro_review", ProjectionScope(kind="portfolio"))
    store.save_task_projection_envelope(envelope)
    store.conn.commit()
    return str(tmp_path / "restart.sqlite"), envelope


def test_a_projection_resolves_in_a_fresh_store(tmp_path):
    path, envelope = _store_with_projections(tmp_path)

    verdict = iv.verify_restart_resolvability(
        lambda: _open_store(path), [envelope.projection_id])

    assert verdict.resolvable is True
    assert verdict.resolved == [envelope.projection_id]
    assert verdict.unresolved == []


def test_an_unresolvable_projection_fails_rather_than_defaulting(tmp_path):
    """The quiet failure this catches: after a restart the projection is gone,
    and a caller that reads 'no opinion yet' continues with a default snapshot."""
    path, _ = _store_with_projections(tmp_path)
    verdict = iv.verify_restart_resolvability(lambda: _open_store(path), ["no-such-id"])

    assert verdict.resolvable is False
    assert verdict.unresolved[0]["projection_id"] == "no-such-id"
    assert "not found" in verdict.unresolved[0]["reason"]


def test_a_projection_whose_content_hash_moved_is_not_the_same_projection(tmp_path):
    """A projection that resolves to different content IS a different
    projection, even though the id still matches."""
    path, envelope = _store_with_projections(tmp_path)
    verdict = iv.verify_restart_resolvability(
        lambda: _open_store(path), [envelope.projection_id],
        expect_hash={envelope.projection_id: "0" * 64})

    assert verdict.resolvable is False
    assert "content hash drifted" in verdict.unresolved[0]["reason"]


def test_an_unopenable_store_is_unresolvable_not_an_error(tmp_path):
    """'I could not check' and 'the check found nothing' must never read alike."""
    def _explode():
        raise sqlite3.OperationalError("database is locked")

    verdict = iv.verify_restart_resolvability(_explode, ["p1"])
    assert verdict.resolvable is False
    assert "could not be reopened" in verdict.unresolved[0]["reason"]


def test_an_unpublished_projection_is_not_resolvable(tmp_path):
    from ats.memory.store import TradingMemory

    path = str(tmp_path / "failed.sqlite")
    store = TradingMemory(path)
    envelope = _envelope("macro_review", ProjectionScope(kind="portfolio"))
    envelope = envelope.model_copy(update={"status": "failed"})
    store.save_task_projection_envelope(envelope)
    store.conn.commit()

    verdict = iv.verify_restart_resolvability(lambda: _open_store(path),
                                              [envelope.projection_id])
    assert verdict.resolvable is False
    assert "not published" in verdict.unresolved[0]["reason"]


def _open_store(path: str):
    from ats.memory.store import TradingMemory

    return TradingMemory(path)


# --------------------------------------------------------------------------- #
# 7.5 — the decision and execution chain
# --------------------------------------------------------------------------- #

@pytest.fixture
def chain_repo(tmp_path):
    """A cycle with two review rounds and an approval on the latest revision."""
    from ats.decision.repository import DecisionAuditRepository
    from ats.decision.snapshot import build_research_snapshot
    from ats.memory.store import TradingMemory
    from ats.workflow.run_contracts import default_registry

    store = TradingMemory(str(tmp_path / "chain.sqlite"))
    repo = DecisionAuditRepository(store)
    registry = default_registry()
    projections = _complete_projections(registry)
    snapshot = build_research_snapshot(
        registry=registry, projections=projections,
        scope=ProjectionScope(kind="portfolio"))

    cycle_id = "chain-cycle"
    repo.create_cycle(cycle_id=cycle_id, trigger_source="intake",
                      research_snapshot=snapshot.to_payload())
    revision = repo.append_revision(
        cycle_id=cycle_id,
        orders=[{"symbol": "MU", "action": "buy", "qty": 100}],
        rationale="chain verification")
    binding = dict(decision_hash=revision["decision_hash"],
                   ruleset_version="risk-v1", portfolio_snapshot_id="pf-1",
                   market_as_of=NOW.isoformat(timespec="seconds"))
    repo.record_review(review_id="rr-1:r1", cycle_id=cycle_id,
                       revision_no=revision["revision_no"],
                       verdict="revise", **binding)
    repo.record_review(review_id="rr-2:r2", cycle_id=cycle_id,
                       revision_no=revision["revision_no"],
                       verdict="approved", **binding)
    repo.record_approval(
        approval_id="ap-1:r2", cycle_id=cycle_id,
        revision_no=revision["revision_no"],
        decision_hash=revision["decision_hash"], decision="approved",
        reviewer="tester", idempotency_key="k-1")
    return repo, store, cycle_id, snapshot


def test_every_chain_link_is_reported_individually(chain_repo):
    """A cutover decision needs to know WHICH link is missing; 'the chain is not
    bound' sends the reader back to the start."""
    repo, store, cycle_id, snapshot = chain_repo
    result = iv.verify_decision_chain(repo=repo, cycle_id=cycle_id,
                                      snapshot=snapshot, store=store)

    names = {binding.name for binding in result.bindings}
    assert names == {"research_snapshot", "decision_revision", "risk_review",
                     "boss_approval", "execution_authorization",
                     "internal_state", "order_fill_channel"}
    for binding in result.bindings:
        assert binding.detail, binding.name


def test_the_chain_is_complete_when_every_link_is_bound(chain_repo):
    repo, store, cycle_id, snapshot = chain_repo
    result = iv.verify_decision_chain(repo=repo, cycle_id=cycle_id,
                                      snapshot=snapshot, store=store)
    bound = {b.name: b.bound for b in result.bindings}
    for name in ("research_snapshot", "decision_revision", "risk_review",
                 "boss_approval"):
        assert bound[name] is True, name


def test_more_than_one_review_round_is_detected(chain_repo):
    """A single review is the easy path; the loop exists because a revision after
    review invalidates it."""
    repo, store, cycle_id, snapshot = chain_repo
    result = iv.verify_decision_chain(repo=repo, cycle_id=cycle_id,
                                      snapshot=snapshot, store=store)
    assert result.rounds == 2
    assert result.multi_round is True


def test_an_unbound_chain_reports_the_missing_link_rather_than_a_boolean(chain_repo):
    repo, store, cycle_id, snapshot = chain_repo
    result = iv.verify_decision_chain(repo=repo, cycle_id="no-such-cycle",
                                      snapshot=snapshot, store=store)
    unbound = [b.name for b in result.bindings if not b.bound]
    assert "decision_revision" in unbound
    assert "boss_approval" in unbound
    assert result.complete is False


def test_a_broker_write_attempt_is_recorded_as_a_refusal(tmp_path):
    """`7.5`: the chain is verified without broker write permission, so the
    evidence that the prohibition held is the refusal."""
    from ats.decision.repository import DecisionAuditRepository
    from ats.memory.store import TradingMemory

    store = TradingMemory(str(tmp_path / "refusal.sqlite"))
    repo = DecisionAuditRepository(store)
    with iv.isolated_verification("chain-refusal", root=tmp_path):
        with pytest.raises(guard.BrokerWriteProhibited):
            guard.check_broker_write(operation="place_orders", caller="chain")
        result = iv.verify_decision_chain(
            repo=repo, cycle_id="none", snapshot=None, store=store,
            broker_write_attempted=True)

    assert result.broker_write_attempts == 1
    assert result.broker_refusals >= 1


def test_repeated_recovery_must_not_duplicate_the_ledger(tmp_path):
    """A recovery is exactly what gets re-run, because nobody watches a process
    crash and resume. Comparing the LEDGER, not the return value, because the
    ledger is what downstream readers see."""
    rows = [("fill-1",)]
    calls = {"count": 0}

    def _recover():
        calls["count"] += 1
        if calls["count"] > 1:
            rows.append(("fill-2",))       # a non-idempotent recovery

    outcome = iv.verify_recovery_idempotence(action=_recover,
                                             ledger_reader=lambda: list(rows))
    assert outcome["idempotent"] is False
    assert outcome["rows_after_first"] == 1
    assert outcome["rows_after_second"] == 2
    assert "double-count" in outcome["reason"]


def test_an_idempotent_recovery_is_reported_as_such():
    rows = [("fill-1",)]

    outcome = iv.verify_recovery_idempotence(action=lambda: None,
                                             ledger_reader=lambda: list(rows))
    assert outcome["idempotent"] is True
    assert outcome["reason"] == ""


def test_a_late_fill_stops_a_switch(tmp_path):
    """The asymmetry: absorbing a late fill is always correct, letting a switch
    continue past one is not — it proves the broker was still working after local
    state looked settled."""
    from ats.execution import order_disposition

    outcome = order_disposition.late_fill_disposition(
        {"order_id": "o1", "status": "submitted",
         "first_submitted_at": "2026-10-01T13:30:00+00:00", "symbol": "MU"},
        {"exec_id": "e1", "symbol": "MU", "side": "BOT", "shares": 10,
         "price": 100.0, "time": "2026-10-06T14:00:00+00:00", "order_id": "o1"})
    assert outcome.late is True
    assert outcome.stop_switch is True


def test_performance_rebuild_is_verified_when_a_period_is_supplied(chain_repo):
    repo, store, cycle_id, snapshot = chain_repo
    result = iv.verify_decision_chain(repo=repo, cycle_id=cycle_id,
                                      snapshot=snapshot, store=store,
                                      rebuild_period="2026-10")
    assert result.performance_rebuilt is True


def test_performance_rebuild_is_skipped_without_a_period(chain_repo):
    repo, store, cycle_id, snapshot = chain_repo
    result = iv.verify_decision_chain(repo=repo, cycle_id=cycle_id,
                                      snapshot=snapshot, store=store)
    assert result.performance_rebuilt is False


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #

def test_the_report_counts_per_consumer_rather_than_saying_ok():
    records = [iv.scan_consumer_access(c) for c in iv.TEN_CONSUMERS]
    report = iv.render_report(records)

    assert "# 十角色接入核验" in report
    for consumer_id in iv.TEN_CONSUMERS:
        assert f"`{consumer_id}`" in report
    assert "否" in report, "a report that only says pass is the failure mode"


def test_the_report_states_that_isolation_is_not_ledger_integrity(tmp_path):
    records = [iv.scan_consumer_access("macro")]
    with iv.isolated_verification("report-attest", root=tmp_path) as attestation:
        pass
    report = iv.render_report(records, attestation=attestation)
    assert "隔离证明不是生产账本完整性证明" in report


def test_the_digest_is_stable_across_identical_runs():
    """Two runs that verified the same thing must digest the same, or 'the
    evidence matches' becomes a claim nobody can check."""
    first = [iv.scan_consumer_access(c) for c in iv.TEN_CONSUMERS]
    second = [iv.scan_consumer_access(c) for c in iv.TEN_CONSUMERS]
    assert iv.verification_digest(first) == iv.verification_digest(second)


def test_the_digest_changes_when_a_finding_appears():
    clean = [iv.scan_consumer_access("macro")]
    dirty = [iv.scan_consumer_access("macro"),
             iv.AccessRecord(consumer_id="macro", entry_point="x",
                             call_path=("ats.data.products",))]
    assert iv.verification_digest(clean) != iv.verification_digest(dirty)


def test_a_role_the_scan_never_reached_is_called_out_in_the_report():
    record = iv.AccessRecord(consumer_id="ghost", entry_point="ats.nope")
    report = iv.render_report([record])
    assert "未到达入口" in report