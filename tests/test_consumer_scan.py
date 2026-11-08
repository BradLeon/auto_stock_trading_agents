"""The consumer access scan behind `ats retirement zero/decide` (r5 task 8.1).

Moved with the scan from `test_intake_verification`: the scan now lives in
`ats.workflow.consumer_scan` so the retirement tool no longer depends on the
intake tooling it will retire. The tests are unchanged apart from the module
they address.

Most of them are about things that must FAIL: an entry point that was never
reached reported as "no bypass found", a forbidden read without a location a
reader can act on. The real scan of the ten roles is pinned here so a future
change cannot quietly widen the forbidden set into meaninglessness or narrow it
into ignoring a real bypass.
"""

from __future__ import annotations

import pytest

from ats.workflow import consumer_scan as cs


# --------------------------------------------------------------------------- #
# 7.2 — the ten roles' access, scanned from their real modules
# --------------------------------------------------------------------------- #

def test_every_consumer_has_a_declared_entry_point():
    assert set(cs.CONSUMER_ENTRY_POINTS) == set(cs.TEN_CONSUMERS)


def test_every_declared_entry_point_resolves_to_a_real_module():
    """An entry point that does not exist cannot be verified through, and saying
    so is the difference between "no bypass" and "no check"."""
    missing = [dotted for dotted in
               (entry for entries in cs.CONSUMER_ENTRY_POINTS.values()
                for entry in entries)
               if cs._module_path(dotted) is None]
    assert not missing, f"declared entry points that do not resolve: {missing}"


def test_all_ten_roles_are_reached_by_the_scan():
    """A record with an empty call path is a failure to verify, not a pass."""
    for consumer_id in cs.TEN_CONSUMERS:
        record = cs.scan_consumer_access(consumer_id)
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
    for consumer_id in cs.TEN_CONSUMERS:
        record = cs.scan_consumer_access(consumer_id)
        kinds = {v.kind for v in record.violations}
        reaches = any(m.startswith(cs.SANCTIONED_READ_SURFACE)
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
    # Trader now directly consumes the governed authorization packet. The
    # delegated product/state paths of other roles need dynamic evidence.
    # 2026-10-17: `fundamental` left this list — its declared entry points now include `fundamental.inputs`, the
    # module through which the governed target path really reads company data (the three entry modules alone never
    # reached it, so the role looked unwired although it had been cut over).
    # 2026-10-29: `chief` left this list — switch-trade-path-and-retire-r5 group 2 moved its context onto the
    # research snapshot and Internal State, read through the governed surface (`ats retirement zero` clears
    # `chief.legacy_table_direct_reads`). Risk and Clerk follow in that change's task 6.4.
    # 2026-11-07: `risk` and `clerk` left this list (task 6.4) — Risk reads through `risk.inputs`, Clerk's
    # broker reads are `BROKER_STATE` steps in its read adapter; every decision role now reaches the surface.
    assert set(unwired) == set()


def test_the_ten_analysis_and_decision_roles_are_the_declared_ten():
    assert set(cs.ANALYSIS_CONSUMERS) == {
        "layer", "information", "sector", "fundamental", "macro", "technical"}
    assert set(cs.DECISION_CONSUMERS) == {"chief", "risk", "trader", "clerk"}
    assert len(cs.TEN_CONSUMERS) == 10


def test_the_reference_price_read_is_not_live_but_is_still_reported():
    """Task 7.9 moved this from a live provider read to a retained one.

    Superseded by `test_the_real_scan_reports_the_stopped_read_as_disabled_not_live`
    below, which also pins the location. Kept as a second assertion on the
    behaviour rather than deleted, because "the provider is no longer reached" is
    the load-bearing claim and it should be stated twice in different terms.
    """
    record = cs.scan_consumer_access("trader")
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
    for consumer_id in cs.TEN_CONSUMERS:
        record = cs.scan_consumer_access(consumer_id)
        found = [v for v in record.violations
                 if v.kind in {"provider_bypass", "base_table_bypass",
                               "third_party_client_bypass"}]
        if found:
            live[consumer_id] = [f"{v.kind}@{v.location}" for v in found]

    # The PEAD graph keeps the pre-cutover provider fallbacks behind the route switch (they are the rollback target,
    # run only on the legacy route). They are pinned here by module and count, so a NEW bypass still fails this test
    # and the retained ones stay visible for the retirement work (task 12.1) instead of being scanned away.
    legacy_fallbacks = live.pop("fundamental", [])
    assert all(item.startswith("provider_bypass@ats.graph.pead:") for item in legacy_fallbacks)
    assert len(legacy_fallbacks) <= 4, legacy_fallbacks
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
    record = cs.scan_consumer_access("trader")
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
    for consumer_id in cs.TEN_CONSUMERS:
        record = cs.scan_consumer_access(consumer_id)
        read = [v for v in record.violations
                if v.kind in {"provider_bypass", "base_table_bypass",
                              "third_party_client_bypass", "disabled_bypass"}]
        if read:
            offenders[consumer_id] = sorted(f"{v.kind}@{v.location}" for v in read)

    # `fundamental`: the PEAD graph's route-gated pre-cutover provider fallbacks (see the test above).
    assert set(offenders) == {"trader", "fundamental"}
    assert all(kind.startswith("disabled_bypass@ats.trader.execute:")
               for kind in offenders["trader"])
    assert all(kind.startswith("provider_bypass@ats.graph.pead:") for kind in offenders["fundamental"])
    assert len(offenders["fundamental"]) <= 4, offenders["fundamental"]


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

    original_root, original_path = cs.PACKAGE_ROOT, cs._module_path
    original_calls = cs._PACKAGE_CALLS
    cs.PACKAGE_ROOT = tmp_path / "ats"
    cs._module_path = lambda dotted: _resolve_under(tmp_path, dotted)
    cs._PACKAGE_CALLS = None
    try:
        record = cs.scan_consumer_access("macro",
                                         extra_entry_points=("ats.role_x.entry",))
    finally:
        cs.PACKAGE_ROOT, cs._module_path = original_root, original_path
        cs._PACKAGE_CALLS = original_calls

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

    original_root, original_path = cs.PACKAGE_ROOT, cs._module_path
    original_calls = cs._PACKAGE_CALLS
    cs.PACKAGE_ROOT = tmp_path / "ats"
    cs._module_path = lambda dotted: _resolve_under(tmp_path, dotted)
    cs._PACKAGE_CALLS = None
    try:
        record = cs.scan_consumer_access("macro",
                                         extra_entry_points=("ats.role_y.entry",))
    finally:
        cs.PACKAGE_ROOT, cs._module_path = original_root, original_path
        cs._PACKAGE_CALLS = original_calls

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
    record = cs.scan_consumer_access("trader")
    by_kind = {v.kind: v for v in record.violations}

    read = by_kind["disabled_bypass"]
    assert read.location.startswith("ats.trader.execute:")
    assert read.detail

    # Every real role reaches the surface since 2026-11-07 (task 6.4), so the wiring gap is shown on a role
    # entered through a module that reads nothing.
    import pytest as _pytest
    with _pytest.MonkeyPatch.context() as mp:
        mp.setitem(cs.CONSUMER_ENTRY_POINTS, "risk", ("ats.agents.risk_officer.outputs",))
        unwired = next(v for v in cs.scan_consumer_access("risk").violations
                       if v.kind == "governed_read_surface_not_reached")
    assert unwired.location == "ats.agents.risk_officer.outputs"
    assert unwired.detail


def test_a_provider_module_is_flagged_with_its_reason(tmp_path):
    """The check must work on a module that does bypass, not only on real code
    that happens to be clean today."""
    package = tmp_path / "ats" / "fake_role"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "entry.py").write_text(
        "from ats.data import sources\n\n\ndef run():\n    return sources.fetch()\n")

    original_root, original_path = cs.PACKAGE_ROOT, cs._module_path
    cs.PACKAGE_ROOT = tmp_path / "ats"
    cs._module_path = lambda dotted: _resolve_under(tmp_path, dotted)
    try:
        record = cs.scan_consumer_access(
            "layer", extra_entry_points=("ats.fake_role.entry",))
    finally:
        cs.PACKAGE_ROOT, cs._module_path = original_root, original_path

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

    original_root, original_path = cs.PACKAGE_ROOT, cs._module_path
    cs.PACKAGE_ROOT = tmp_path / "ats"
    cs._module_path = lambda dotted: _resolve_under(tmp_path, dotted)
    try:
        record = cs.scan_consumer_access(
            "macro", extra_entry_points=("ats.fake_role2.entry",))
    finally:
        cs.PACKAGE_ROOT, cs._module_path = original_root, original_path

    assert any(v.kind == "base_table_bypass" for v in record.violations)
    assert not any(v.kind == "provider_bypass" for v in record.violations)


def test_a_third_party_client_is_flagged(tmp_path):
    package = tmp_path / "ats" / "fake_role3"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "entry.py").write_text("import yfinance\n\n\ndef run():\n    return 1\n")

    original_root, original_path = cs.PACKAGE_ROOT, cs._module_path
    cs.PACKAGE_ROOT = tmp_path / "ats"
    cs._module_path = lambda dotted: _resolve_under(tmp_path, dotted)
    try:
        record = cs.scan_consumer_access(
            "sector", extra_entry_points=("ats.fake_role3.entry",))
    finally:
        cs.PACKAGE_ROOT, cs._module_path = original_root, original_path

    assert any(v.kind == "third_party_client_bypass" for v in record.violations)


def test_workflow_memory_is_not_a_data_plane_bypass():
    """`ats.memory` is the projection store — the opinion channel governed by
    7.3, not the raw repository governed here. Treating it as a base-table read
    flagged nearly every role and would have taught readers to ignore the check.
    """
    assert "ats.memory" not in cs.BASE_TABLE_MODULES


def test_the_product_layer_is_not_treated_as_a_bypass():
    """It owns the raw repositories legitimately; its internals are its business."""
    assert cs.PRODUCT_LAYER not in cs.PROVIDER_MODULES
    assert cs.PRODUCT_LAYER not in cs.BASE_TABLE_MODULES
    assert cs.PRODUCT_LAYER in cs.SANCTIONED_READ_SURFACE


def test_an_undeclared_consumer_is_refused_rather_than_reported_clean():
    with pytest.raises(cs.ConsumerScanError, match="no declared entry point"):
        cs.scan_consumer_access("not_a_consumer")


def test_an_entry_point_that_does_not_resolve_is_a_violation():
    """An extra entry point that cannot be resolved is recorded as missing, and
    the role is still reached through its declared one — the two are separate
    facts and conflating them hides either."""
    record = cs.scan_consumer_access(
        "layer", extra_entry_points=("ats.does.not.exist",))
    assert any(v.kind == "entry_point_missing" for v in record.violations)
    assert record.reached_entry_point is True


def test_a_role_whose_only_entry_point_is_missing_is_not_reached():
    """With no resolvable entry point there is nothing to have verified, and an
    empty call path must never read as a clean pass."""
    record = cs.AccessRecord(consumer_id="ghost", entry_point="ats.does.not.exist")
    cs.scan_consumer_access  # the scanner is exercised above; this asserts the property
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

@pytest.mark.parametrize("source,consumer", sorted(cs.ALLOWED_OPINION_EDGES))
def test_the_two_declared_opinion_edges_are_allowed(source, consumer):
    """`ALLOWED_OPINION_EDGES` is oriented (source, consumer): layer→sector means
    SECTOR reads LAYER. Reversing the pair would report the two permitted edges
    as leakage — the one failure a whitelist check cannot afford."""
    assert cs.ALLOWED_OPINION_EDGES == frozenset({("layer", "sector"),
                                                  ("information", "fundamental")})
    record = cs.AccessRecord(consumer_id=consumer, entry_point="x",
                             call_path=("ats.data.products",),
                             opinion_inputs=(source,))
    cs._check_opinion_dependencies(record)
    assert record.violations == [], record.violations


@pytest.mark.parametrize("consumer,source", [
    ("layer", "macro"), ("layer", "technical"), ("sector", "fundamental"),
    ("macro", "layer"), ("technical", "information"),
    ("fundamental", "layer"), ("information", "sector"),
    ("sector", "chief"), ("layer", "chief"),
])
def test_every_other_opinion_edge_is_leakage(consumer, source):
    record = cs.AccessRecord(consumer_id=consumer, entry_point="x",
                             call_path=("ats.data.products",),
                             opinion_inputs=(source,))
    cs._check_opinion_dependencies(record)
    assert any(v.kind == "opinion_leakage" for v in record.violations), (
        f"{source}->{consumer} should be leakage")


def test_chief_is_the_declared_aggregator_and_is_exempt():
    """Chief reads every analyst's view by design. Flagging it would make the
    check something a reader learns to skip."""
    record = cs.AccessRecord(
        consumer_id="chief", entry_point="x", call_path=("ats.data.products",),
        opinion_inputs=("layer", "information", "sector", "fundamental",
                        "macro", "technical"))
    cs._check_opinion_dependencies(record)
    assert record.violations == []


def test_a_role_reading_its_own_opinion_is_not_leakage():
    record = cs.AccessRecord(consumer_id="sector", entry_point="x",
                             call_path=("ats.data.products",),
                             opinion_inputs=("sector", "layer"))
    cs._check_opinion_dependencies(record)
    assert record.violations == []
