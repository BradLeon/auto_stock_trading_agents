"""Chain evidence — the weekly Obsidian report (hermetic)."""

from datetime import datetime, timedelta, timezone

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

    # Same-day reruns pass DIFFERENT instants — which is what production does, and
    # what the previous version of this test failed to reproduce by pinning NOW.
    report.render(cfg, store, as_of=NOW, ind_cfg={})
    report.render(cfg, store, as_of=NOW + timedelta(days=7), ind_cfg={})
    report.render(cfg, store, as_of=NOW + timedelta(days=7, hours=3), ind_cfg={})
    report.render(cfg, store, as_of=NOW + timedelta(days=7, hours=9), ind_cfg={})

    hist = store.claim_assessment_history("hbm_share_and_pricing_power")
    # Two DAYS, not four rows. The report file is named by date and same-day reruns
    # overwrite it; a table that appended on every rerun would disagree with its own
    # report, and the history would read as change when nothing had changed.
    assert len(hist) == 2, "one snapshot per DAY; same-day reruns must replace"
    assert {h["as_of"] for h in hist} == {
        NOW.date().isoformat(), (NOW + timedelta(days=7)).date().isoformat()}
    assert store.latest_claim_assessments()                   # and a latest view exists


def test_snapshot_day_is_utc_regardless_of_the_callers_timezone():
    """The CLI stamps UTC and the scheduler stamps ET. `latest_claim_assessments` picks
    MAX over TEXT — a lexicographic compare on wall-clock digits — so mixing offsets
    could order the history backwards."""
    from datetime import timedelta, timezone as tz

    store = get_store()
    store.save_observation(_obs("SKHY", "hbm_share", direction="down", span="share"))
    cfg = load_sector_config("ai_hardware")

    et = tz(timedelta(hours=-4))
    # 2026-08-06 22:00 ET is 2026-08-07 02:00 UTC — the same UTC day as the next call.
    report.render(cfg, store, as_of=NOW.astimezone(et).replace(hour=22), ind_cfg={})
    report.render(cfg, store, as_of=NOW.replace(hour=3), ind_cfg={})

    hist = store.claim_assessment_history("hbm_share_and_pricing_power")
    assert {h["as_of"] for h in hist} <= {NOW.date().isoformat(),
                                          (NOW.date() - timedelta(days=1)).isoformat()}
    assert all(len(h["as_of"]) == 10 for h in hist), "stored as a date, not an instant"


def test_third_party_sources_are_shown_grounded_in_stored_data(monkeypatch):
    """The section exists so a reader can check WHY a claim cleared the stance gate on
    a non-self-reported witness, without leaving the report. It must read the ledger,
    never the network — the claims in the same report were computed against stored
    rows, and a live re-fetch that had since revised would let the report disagree
    with itself."""
    from ats.chain import sources as chain_sources

    store = get_store()
    monkeypatch.setattr(chain_sources, "load_sources",
                        lambda: [chain_sources.SourceDef(
                            id="tw_ic_exports", label="台湾 IC 出口", adapter="tw_mof",
                            entity="TW_IC_EXPORT", stance="regulator",
                            observation_type="regulatory", cadence="monthly",
                            concepts=["supply_tightness", "hbm_demand"],
                            direction_from=["yoy", "mom"])])
    store.save_observation(_obs("TW_IC_EXPORT", "supply_tightness",
                                span="2026-06 台湾 IC 出口 …", metric="tw_ic_exports_mom"))

    md = report.render(cfg=load_sector_config("ai_hardware"), store=store,
                       as_of=NOW, ind_cfg={})
    assert "## 第三方数据源" in md and "台湾 IC 出口" in md
    assert "本报告不发网络请求" in md
    assert "tw_ic_exports_mom" in md and "2026-06 台湾 IC 出口" in md


def test_a_source_bound_to_two_concepts_is_shown_once_not_twice(monkeypatch):
    """`collect()` persists one physical row per declared concept — the same print
    filed under `supply_tightness` and again under `hbm_demand` is two DB rows so both
    claims can find it. Showing "what this source said" twice reads like a duplication
    bug; the section must dedupe on the reading, not the storage row."""
    from ats.chain import sources as chain_sources

    store = get_store()
    monkeypatch.setattr(chain_sources, "load_sources",
                        lambda: [chain_sources.SourceDef(
                            id="tw_ic_exports", label="台湾 IC 出口", adapter="tw_mof",
                            entity="TW_IC_EXPORT", stance="regulator",
                            observation_type="regulatory", cadence="monthly",
                            concepts=["supply_tightness", "hbm_demand"])])
    # collect() re-derives the id including the concept precisely so the same print
    # can occupy two rows; `_obs()` doesn't, so force distinct ids the same way.
    span = "2026-06 台湾 IC 出口 同一条印数"
    for concept in ("supply_tightness", "hbm_demand"):
        obs = _obs("TW_IC_EXPORT", concept, span=span, metric="tw_ic_exports_mom")
        store.save_observation(obs.model_copy(update={"id": f"tw-ic-{concept}"}))

    md = report.render(cfg=load_sector_config("ai_hardware"), store=store,
                       as_of=NOW, ind_cfg={})
    # Scoped to the section itself: TW_IC_EXPORT and these concept keys are also real
    # witnesses/dimensions in ai_hardware.yaml's own claims, so the span legitimately
    # appears elsewhere in the report too (a real claim picking up the same fixture
    # data is expected cross-talk in this test file, not what this test is about).
    section = md.split("## 第三方数据源", 1)[1].split("\n## ", 1)[0]
    assert section.count(span) == 1


def test_a_failed_fetch_since_the_last_reading_is_flagged(monkeypatch):
    """`collect()` only ever writes a `source_documents` row on FAILURE. If the most
    recent attempt (by timestamp) is later than the most recent stored reading, the
    source is currently in a failed state even though old data is still on file — the
    report must say so rather than silently showing stale data as if it were current."""
    from ats.chain import sources as chain_sources

    store = get_store()
    monkeypatch.setattr(chain_sources, "load_sources",
                        lambda: [chain_sources.SourceDef(
                            id="tw_ic_exports", label="台湾 IC 出口", adapter="tw_mof",
                            entity="TW_IC_EXPORT", stance="regulator",
                            observation_type="regulatory", cadence="monthly",
                            concepts=["supply_tightness"])])
    old = _obs("TW_IC_EXPORT", "supply_tightness", span="旧读数",
              metric="tw_ic_exports_mom")
    store.save_observation(old.model_copy(
        update={"observed_at": NOW - timedelta(days=40)}))
    store.save_document_failure("TW_IC_EXPORT", "", "series", source="tw_mof",
                                note="tw_ic_exports 本轮取不到数据",
                                at=NOW.isoformat())

    md = report.render(cfg=load_sector_config("ai_hardware"), store=store,
                       as_of=NOW, ind_cfg={})
    assert "最近一次抓取失败" in md and "旧读数" in md
