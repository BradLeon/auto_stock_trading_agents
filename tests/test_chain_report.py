"""Chain evidence — the weekly Obsidian report (hermetic)."""

from datetime import datetime, timezone

from ats.chain import report
from ats.config import load_sector_config
from ats.memory import get_store
from ats.schemas.chain import ClaimProposal, Observation, ObservationFailure

NOW = datetime(2026, 8, 6, tzinfo=timezone.utc)


def _obs(speaker, concept, *, about=None, direction="up", span="…", metric=""):
    return Observation(
        document_id=f"{speaker}-d", entity=(about or speaker).upper(),
        source_entity=speaker.upper(), metric=metric or concept or "misc",
        concept=concept, period="FY26Q3", observation_type="guidance",
        stance="incumbent", direction=direction, evidence_span=span, observed_at=NOW)


def test_report_shows_verdict_coverage_silence_and_source_text():
    """A verdict you cannot trace back to a filing is not usable, and a claim resting
    on one witness must not look like one resting on four."""
    store = get_store()
    store.save_observation(_obs("SKHY", "hbm_share", direction="down",
                                span="we expect our HBM share to decline modestly"))
    store.save_observation(_obs("NVDA", "hbm_share", about="SKHY", direction="down",
                                span="allocation to our largest memory partner will step down"))
    cfg = load_sector_config("ai_hardware")

    md = report.render(cfg, store, as_of=NOW, ind_cfg={})
    assert "# 🤖 产业链证据" in md
    assert "证人覆盖" in md and "本期未发声" in md          # coverage AND silence
    assert "decline modestly" in md                        # verbatim source text
    assert "说话人" in md and "关于" in md                  # speaker vs subject kept apart
    assert "竞争者扩产不等于我方失份额" in md               # the load-bearing rule is stated


def test_report_surfaces_pool_pending_proposals_and_failures():
    store = get_store()
    store.save_observation(_obs("COHR", "", metric="optical_lead_time",
                                span="lead times extended to 26 weeks"))
    store.save_claim_proposal(ClaimProposal(
        statement="瓶颈正从存储转向光互联", signature="sig", created_at=NOW,
        layer_hint="L5→L3"))
    store.save_observation_failure(ObservationFailure(
        document_id="d1", entity="ORCL", reason="指标未单独披露", at=NOW))

    md = report.render(cfg=load_sector_config("ai_hardware"), store=store,
                       as_of=NOW, ind_cfg={})
    assert "未映射池" in md and "optical_lead_time" in md
    assert "待确认命题" in md and "瓶颈正从存储转向光互联" in md
    assert "只有你能把它写进配置" in md                     # who may extend the axes
    assert "抽取失败" in md and "指标未单独披露" in md
    assert "「读不到」和「没说」是两种状态" in md


def test_write_is_idempotent_and_degrades_without_output_dir(tmp_path):
    store = get_store()
    cfg = load_sector_config("ai_hardware")

    assert report.write(cfg.model_copy(update={"output_dir": ""}), store,
                        as_of=NOW) is None
    p1 = report.write(cfg.model_copy(update={"output_dir": str(tmp_path)}), store,
                      as_of=NOW)
    p2 = report.write(cfg.model_copy(update={"output_dir": str(tmp_path)}), store,
                      as_of=NOW)
    assert p1 == p2 and p1.exists()                        # same-day rerun overwrites
    assert len(list(tmp_path.glob("*.md"))) == 1
    assert "产业链证据-AI硬件-2026-08-06.md" == p1.name


def test_report_writer_is_isolated_from_the_real_vault(tmp_path):
    """Guard the guard.

    The weekly report resolves its folder from load_sector_config().output_dir, which
    points at the real Obsidian vault in production. conftest._isolate_report_dir must
    redirect it — this asserts the redirection is live, so a future writer added
    downstream of some unrelated scheduler test cannot quietly reach the vault again.
    """
    from ats.config import load_sector_config

    out = load_sector_config("ai_hardware").output_dir
    assert "Obsidian" not in out, f"sector output_dir leaked to the real vault: {out}"
    assert out.startswith(str(tmp_path.parent.parent)) or "pytest" in out


def test_render_persists_verdict_history():
    """The verdict table exists to answer "when did this change" — so each render
    snapshots, versioned by (claim_id, as_of), rather than overwriting."""
    from datetime import timedelta

    store = get_store()
    store.save_observation(_obs("SKHY", "hbm_share", direction="down", span="share to decline"))
    cfg = load_sector_config("ai_hardware")

    report.render(cfg, store, as_of=NOW, ind_cfg={})
    report.render(cfg, store, as_of=NOW + timedelta(days=7), ind_cfg={})
    report.render(cfg, store, as_of=NOW + timedelta(days=7), ind_cfg={})   # rerun

    hist = store.claim_assessment_history("hbm_share_and_pricing_power")
    # Two dates, not three rows: the snapshot is stamped with the REPORT's as_of, so a
    # rerun replaces rather than appends — otherwise the history reads as change when
    # nothing changed.
    assert len(hist) == 2, "one snapshot per report date; reruns must not append"
    assert {h["verdict"] for h in hist}                      # verdict recorded
    assert store.latest_claim_assessments()                   # and a latest view exists
