"""Replayable shadow input packets and the per-batch applicability matrix.

Task 3.1's premise is that same data vintage is not same inputs: `read_input`
returns runtime values for `MARKET_DATA` and `BROKER_STATE`, and `assemble.build`
stamps `datetime.now()`. So a risk-verdict difference cannot be attributed to the
path change unless those were held fixed.

Task 3.2 then has to avoid the opposite mistake — demanding broker snapshots from
a Macro run that never reads them, which would mean the comparison silently never
happens.
"""

import pytest

from ats.workflow import shadow_matrix as mx
from ats.workflow import shadow_inputs as si

CLOCK = "2026-10-05T09:00:00+00:00"


class _Input:
    """Stand-in for `ConsumerInput` — the packet describes reads that happened."""

    def __init__(self, refs, as_of):
        self.input_refs = list(refs)
        self.source_as_of = list(as_of)


# --------------------------------------------------------------------------- #
# 3.1 — the packet fixes what a comparison needs fixed
# --------------------------------------------------------------------------- #

def test_a_packet_hashes_each_surface_separately():
    """One combined hash cannot answer "which surface moved?".

    That question is the whole point: a difference in account state invalidates a
    risk comparison but says nothing about an input-snapshot comparison, and a
    single hash would report both as merely "different".
    """
    packet = si.build_packet(
        run_id="r1", consumer_id="trader", batch_class="trading",
        logical_eval_time=CLOCK, persistent_refs="refs-hash",
        projection_hash="proj-hash", account_state="acct-hash")

    fixed = packet.fixed_surfaces()
    assert fixed["persistent_refs"] == "refs-hash"
    assert fixed["account_state"] == "acct-hash"
    assert set(fixed) == set(si.ALL_SURFACES) - set(packet.unset_surfaces())


def test_the_same_vintage_with_a_different_account_state_is_a_different_packet():
    """The case the requirement names explicitly.

    Published snapshots identical, account state different — so the two runs were
    not given the same inputs and a verdict difference between them proves nothing.
    """
    left = si.build_packet(
        run_id="l", consumer_id="trader", batch_class="trading",
        logical_eval_time=CLOCK, persistent_refs="refs", projection_hash="proj",
        account_state=si.capture_account_surface({"cash": 100_000, "positions": []}))
    right = si.build_packet(
        run_id="r", consumer_id="trader", batch_class="trading",
        logical_eval_time=CLOCK, persistent_refs="refs", projection_hash="proj",
        account_state=si.capture_account_surface({"cash": 250_000, "positions": []}))

    diff = si.diff_packets(left, right)
    assert diff["identical"] is False
    # Only the account surface moved — which is the diagnosis, not just the fact.
    assert diff["differing"] == [si.ACCOUNT_STATE]


def test_an_unfixed_logical_evaluation_time_is_refused_as_evidence():
    """The one surface every batch class must fix.

    Everything else can be marked not-applicable per batch class; a missing clock
    cannot, because without it every read drifts and no comparison is repeatable.
    """
    packet = si.build_packet(
        run_id="r1", consumer_id="macro", batch_class="research_read",
        logical_eval_time=None, persistent_refs="refs", projection_hash="proj")

    assert any(si.LOGICAL_EVAL_TIME in problem
               for problem in packet.evidence_problems())
    with pytest.raises(si.IncompletePacketError):
        packet.assert_usable_as_evidence()


def test_marking_the_clock_not_applicable_is_still_refused():
    """Otherwise "not applicable" becomes a way to skip the one required surface."""
    packet = si.mark_not_applicable(
        si.build_packet(run_id="r1", consumer_id="macro",
                        batch_class="research_read",
                        persistent_refs="refs", projection_hash="proj"),
        si.LOGICAL_EVAL_TIME)

    with pytest.raises(si.IncompletePacketError):
        packet.assert_usable_as_evidence()


def test_a_fixed_surface_cannot_be_declared_away_afterwards():
    """The declaration is a statement about the run, not an editing tool.

    Marking a captured snapshot "not applicable" would discard real observation
    data and make the packet claim it never touched runtime data at all.
    """
    fixed = si.build_packet(
        run_id="r1", consumer_id="macro", batch_class="research_read",
        logical_eval_time=CLOCK, persistent_refs="refs", projection_hash="proj",
        market_runtime="captured-snapshot")

    with pytest.raises(ValueError, match="discard captured data"):
        si.mark_not_applicable(fixed, si.MARKET_RUNTIME)


def test_an_unparseable_clock_is_refused():
    """A packet whose clock cannot be parsed cannot be replayed."""
    packet = si.build_packet(
        run_id="r1", consumer_id="macro", batch_class="research_read",
        logical_eval_time="yesterday-ish", persistent_refs="refs",
        projection_hash="proj")

    assert any("not a timestamp" in problem for problem in packet.evidence_problems())


def test_a_persistent_surface_hash_is_derived_from_real_refs():
    """The packet describes reads that happened, not a second set of them."""
    left = si.capture_persistent_surface([
        _Input(["doc:1", "fact:2"], ["2026-10-04T00:00:00+00:00"]),
        _Input(["doc:3"], ["2026-10-05T00:00:00+00:00"]),
    ])
    right = si.capture_persistent_surface([
        _Input(["doc:3"], ["2026-10-05T00:00:00+00:00"]),
        _Input(["doc:1", "fact:2"], ["2026-10-04T00:00:00+00:00"]),
    ])
    assert left == right, "order must not change the content hash"

    changed = si.capture_persistent_surface([_Input(["doc:1"], [])])
    assert left != changed


def test_marking_a_surface_not_applicable_returns_a_new_packet():
    """A declared packet is evidence, and evidence does not change after the fact.

    Same append-only reasoning the shadow report applies to conclusions.
    """
    original = si.build_packet(
        run_id="r1", consumer_id="macro", batch_class="research_read",
        logical_eval_time=CLOCK, persistent_refs="refs", projection_hash="proj")
    marked = si.mark_not_applicable(original, si.ACCOUNT_STATE)

    assert si.ACCOUNT_STATE not in original.surfaces
    assert marked.not_applicable() == (si.ACCOUNT_STATE,)
    assert marked.packet_hash() != original.packet_hash()


def test_an_unknown_surface_is_rejected_rather_than_stored():
    """A typo'd surface name would read as a real, satisfied one."""
    with pytest.raises(ValueError, match="unknown"):
        si.mark_not_applicable(
            si.build_packet(run_id="r", consumer_id="macro",
                            batch_class="research_read", logical_eval_time=CLOCK),
            "accountstate")


def test_the_wrapper_does_not_modify_consumer_api():
    """A hard constraint, not a preference.

    `consumer_api.py` is on the fingerprint surface for all ten consumers, so
    editing it invalidates every recorded evidence row. The wrapper is the only
    place that can add replay semantics without retiring evidence.
    """
    from ats.workflow.assurance_surface import load_surface

    assert "src/ats/data/consumer_api.py" in load_surface().all_paths()


# --------------------------------------------------------------------------- #
# 3.2 — the matrix
# --------------------------------------------------------------------------- #

def _research_packet(*, declared_na: tuple[str, ...] = (), **overrides
                     ) -> si.ShadowInputPacket:
    """A research packet whose runtime surfaces are explicitly not-applicable.

    `declared_na` narrows the default set, and any surface passed in `overrides`
    is fixed rather than declared — so a caller can build a packet that (wrongly)
    carries runtime data and see what the matrix says about it.
    """
    not_applicable = tuple(
        s for s in mx.expected_not_applicable("research_read")
        if s not in overrides and s not in declared_na)
    packet = si.build_packet(
        run_id="r1", consumer_id="macro", batch_class="research_read",
        logical_eval_time=CLOCK, persistent_refs="refs", projection_hash="proj",
        **overrides)
    return si.mark_not_applicable(packet, *not_applicable)


def _trading_packet(**overrides) -> si.ShadowInputPacket:
    return si.build_packet(
        run_id="t1", consumer_id="trader", batch_class="trading",
        logical_eval_time=CLOCK, persistent_refs="refs", projection_hash="proj",
        market_runtime="mkt", account_state="acct", history_state="hist",
        ruleset_version="rs", model_config="mc", **overrides)


def test_a_research_batch_is_satisfied_without_any_runtime_surface():
    """The requirement's own scenario: a research batch must not be made to
    produce surfaces it never reads, or the comparison silently never happens."""
    verdict = mx.check_packet(_research_packet())
    assert verdict.satisfied, verdict.problems
    assert mx.expected_not_applicable("research_read")


def test_a_research_batch_marking_a_runtime_surface_not_applicable_is_fine():
    """That is the explicit declaration the spec asks for."""
    verdict = mx.check_packet(_research_packet())
    assert si.MARKET_RUNTIME in verdict.not_applicable


def test_a_full_trading_batch_with_every_surface_fixed_is_satisfied():
    assert mx.check_packet(_trading_packet()).satisfied


def test_a_trading_batch_missing_a_required_surface_is_incomplete():
    """The under-demanding direction: a clean result for the wrong reason."""
    packet = _trading_packet()
    broken = si.build_packet(
        run_id="t1", consumer_id="trader", batch_class="trading",
        logical_eval_time=CLOCK, persistent_refs="refs", projection_hash="proj",
        market_runtime="mkt", account_state="acct", history_state="hist",
        ruleset_version="rs")

    verdict = mx.check_packet(broken)
    assert not verdict.satisfied
    assert si.MODEL_CONFIG in verdict.missing
    assert any(si.MODEL_CONFIG in problem for problem in verdict.problems)
    with pytest.raises(mx.MatrixError):
        mx.assert_satisfies_matrix(broken)


def test_marking_a_required_trading_surface_not_applicable_is_rejected():
    """Not-applicable must not be an escape hatch for a surface that IS required."""
    without_account = si.build_packet(
        run_id="t1", consumer_id="trader", batch_class="trading",
        logical_eval_time=CLOCK, persistent_refs="refs", projection_hash="proj",
        market_runtime="mkt", history_state="hist", ruleset_version="rs",
        model_config="mc")
    broken = si.mark_not_applicable(without_account, si.ACCOUNT_STATE)

    verdict = mx.check_packet(broken)
    assert not verdict.satisfied
    assert any("not-applicable" in problem for problem in verdict.problems)
    with pytest.raises(mx.MatrixError):
        mx.assert_satisfies_matrix(broken)


def test_a_research_batch_cannot_declare_away_a_surface_it_captured():
    """The over-demanding direction, refused at the point of the claim.

    The matrix says a research batch fixes only persistent refs, projection and
    the clock. Marking a captured market snapshot "not applicable" would discard
    real observation data and make the packet claim it never touched runtime data
    — the opposite of what the declaration is for.
    """
    packet = _research_packet(market_runtime="mkt")
    with pytest.raises(ValueError, match="discard captured data"):
        si.mark_not_applicable(packet, si.MARKET_RUNTIME)


def test_a_runtime_surface_a_research_batch_should_have_declared_is_reported():
    """Same problem from the other end: the matrix view still calls it out.

    Even when the packet is assembled by hand and skips the declaration, the
    matrix check names the surface. Tolerating an extra fixed surface would let a
    mislabelled batch pass as a clean research comparison — and it DID touch
    runtime data, which its own scope then denies.
    """
    packet = _research_packet(market_runtime="mkt")
    verdict = mx.check_packet(packet)

    assert not verdict.satisfied
    assert any("mislabelled or the matrix is wrong" in problem
               for problem in verdict.problems)


def test_an_undeclared_surface_is_reported_as_an_oversight():
    """An unset surface is not the same statement as a deliberate not-applicable."""
    packet = si.build_packet(
        run_id="r1", consumer_id="macro", batch_class="research_read",
        logical_eval_time=CLOCK, persistent_refs="refs", projection_hash="proj")
    verdict = mx.check_packet(packet)

    assert not verdict.satisfied
    assert any("neither fixed nor declared" in problem for problem in verdict.problems)


def test_the_batch_class_must_match_the_consumers_role():
    """A Macro run labelled `trading` would fail as a matrix problem, not a
    labelling one — which sends the reader looking in the wrong place."""
    mislabelled = _research_packet()
    mislabelled.batch_class = "trading"

    with pytest.raises(mx.MatrixError, match="belongs to the"):
        mx.assert_class_matches_consumer(mislabelled)


def test_every_declared_consumer_has_a_batch_class():
    """A consumer added later without a class would silently default to `decision`."""
    assert mx.RESEARCH_CONSUMERS & mx.DECISION_CONSUMERS == frozenset()
    assert mx.DECISION_CONSUMERS & mx.TRADING_CONSUMERS == frozenset()
    for consumer in (mx.RESEARCH_CONSUMERS | mx.DECISION_CONSUMERS
                     | mx.TRADING_CONSUMERS):
        assert mx.class_for_consumer(consumer) in mx.BATCH_CLASSES


def test_a_required_surface_that_one_packet_of_a_batch_fixes_is_still_a_batch_gap():
    """Batch requirements are the union: satisfied per packet is not enough."""
    fixed = _trading_packet()
    short = si.build_packet(
        run_id="t2", consumer_id="trader", batch_class="trading",
        logical_eval_time=CLOCK, persistent_refs="refs", projection_hash="proj",
        market_runtime="mkt", account_state="acct", history_state="hist")

    assert mx.check_packet(fixed).satisfied
    gaps = mx.unmet_surfaces([fixed, short])
    assert gaps["trading"] == (si.RULESET_VERSION, si.MODEL_CONFIG)
