import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Make the src layout importable without installing.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ats.schemas.channel import ApprovalRequest, Notification, ReportBundle  # noqa: E402
from ats.schemas.decision import BossApproval  # noqa: E402
from ats.schemas.memory import TradeLogEntry  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch):
    """Point Context Memory + checkpoints at throwaway DBs per test."""
    monkeypatch.setenv("ATS_DB_PATH", str(tmp_path / "mem.sqlite"))
    monkeypatch.delenv("ATS_STRUCTURED_DB_PATH", raising=False)
    monkeypatch.setenv("ATS_STRUCTURED_ARTIFACT_ROOT", str(tmp_path / "structured_artifacts"))
    monkeypatch.setenv("ATS_CHECKPOINT_DB", str(tmp_path / "ckpt.sqlite"))
    from ats.memory import reset_store_cache
    from ats.data.structured import reset_repository_cache

    reset_store_cache()
    reset_repository_cache()
    yield
    reset_store_cache()
    reset_repository_cache()


@pytest.fixture(autouse=True)
def _isolate_report_dir(tmp_path, monkeypatch):
    """Reports must never land in the real Obsidian vault during tests.

    Writers resolve the vault via a config loader's `output_dir` at call time (imports
    are inside functions), so patching the module attribute covers them all.

    BOTH loaders are redirected. Relying on each test to stub whatever happens to write
    has now failed twice: 2026-07-15 a chief test overwrote real vault documents, and
    2026-08-06 the chain-evidence weekly report landed in the vault because it reads
    `load_sector_config().output_dir` — not covered here at the time — from a scheduler
    test that had no reason to know a report writer sat downstream. Isolation belongs
    in this fixture, not in each caller's memory.
    """
    import ats.config as config

    real_macro = config.load_macro_config
    real_sector = config.load_sector_config

    def _macro(name: str = "macro"):
        return real_macro(name).model_copy(update={"output_dir": str(tmp_path)})

    def _sector(name: str = "ai_hardware"):
        return real_sector(name).model_copy(update={"output_dir": str(tmp_path)})

    monkeypatch.setattr(config, "load_macro_config", _macro)
    monkeypatch.setattr(config, "load_sector_config", _sector)
    # Primary-source cache (data.source_cache) reads AND WRITES under docs_root, which
    # in production is the same Obsidian vault. Without this a test that fetches a
    # document would deposit it in the user's notes — and, worse, would read whatever
    # is already there, so a test's outcome would depend on the vault's contents.
    monkeypatch.setenv("ATS_DOCS_ROOT", str(tmp_path / "sources"))


@pytest.fixture(autouse=True)
def _no_transcript_dataset(monkeypatch):
    """Tier-1 transcript lookup is off unless a test asks for it.

    It reads a 2.2GB remote parquet; left live, every test touching fetch_document
    would spend ~40s on the network. Tests that exercise this path monkeypatch
    `ats.data.defeatbeta.fetch` themselves.
    """
    from ats.data import defeatbeta

    monkeypatch.setattr(defeatbeta, "fetch", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _no_live_layer_analyst(monkeypatch):
    """No test may reach the live layer analyst or the rotation pass.

    Autouse because the failure mode is silent in the same way the adjudicator's is:
    `layer_review.run` and `rotation.run` both swallow exceptions and degrade (to a
    carried-forward verdict / to None), so a test that accidentally called out to a
    model would not error — it would spend money, take a minute, and still look green.
    That is exactly what happened on 2026-08-20: three tests written against the old
    single-synthesis path kept patching only `review.run_structured`, and when `run()`
    started defaulting to the layered path they silently began making real calls.

    Raising (rather than returning a stub) is deliberate: a test that needs these must
    say so by patching them, and one that does not should never be shaped by whatever a
    stub happened to return.
    """
    def _blocked(role, schema, context, **kw):
        raise AssertionError(
            f"test reached the live model for role {role!r} — patch "
            f"`layer_review.run_structured` / `rotation.run_structured` in this test")

    from ats.agents.sector import layer_review, rotation

    monkeypatch.setattr(layer_review, "run_structured", _blocked)
    monkeypatch.setattr(rotation, "run_structured", _blocked)


@pytest.fixture(autouse=True)
def _stub_adjudicator(monkeypatch):
    """No test may reach the live cluster adjudicator.

    Patched at `run_structured` rather than at `judge`, so the adjudicator's own glue
    still runs — echoing cluster ids back, dropping invented ones, defaulting the
    unjudged to neutral. Polarity is expressed in the fixture data: a cluster whose
    evidence spans carry [[REFUTE]] or [[NEUTRAL]] gets that, everything else supports.

    Autouse because the failure mode is silent: `judge()` swallows exceptions and
    degrades to all-neutral, so a test that accidentally called out to a model would
    not error — it would just quietly assert against `unknown` and look green.
    """
    from ats.agents.evidence import adjudicator
    from ats.agents.evidence.outputs import (AdjudicationView, ClusterJudgementView,
                                          CrossSectionView, EntityReadingView)

    def _fake(role, schema, context, **kw):
        blocks: list[tuple[str, list[str]]] = []
        for line in context.splitlines():
            if " id=" in line:
                blocks.append((line.split(" id=", 1)[1].strip(), []))
            elif blocks:
                blocks[-1][1].append(line)
        out = []
        for key, body in blocks:
            text = "\n".join(body)
            polarity = ("refute" if "[[REFUTE]]" in text
                        else "neutral" if "[[NEUTRAL]]" in text else "support")
            out.append(ClusterJudgementView(cluster_key=key, polarity=polarity,
                                            reason=f"stub:{polarity}"))
        return AdjudicationView(judgements=out)

    def _fake_cross(role, schema, context, **kw):
        blocks: list[tuple[str, list[str]]] = []
        for line in context.splitlines():
            if line.startswith("### "):
                blocks.append((line[4:].strip(), []))
            elif blocks:
                blocks[-1][1].append(line)
        out = []
        for entity, body in blocks:
            text = "\n".join(body)
            standing = ("unknown" if "（本期无可用读数）" in text
                        else "weak" if "[[WEAK]]" in text
                        else "neutral" if "[[NEUTRAL]]" in text else "strong")
            # Cite the clusters whose direction matches the standing — the real
            # adjudicator is asked to name what it relied on, and a stub that cited
            # everything would hide the very defect `key_clusters` exists to catch.
            want = "down" if standing == "weak" else "up"
            cited = [ln.split("id=", 1)[1].strip()
                     for ln in body if " id=" in ln or ln.strip().startswith("· id=")]
            keys = [k for k in cited if f"|{want}|" in k] or cited
            out.append(EntityReadingView(entity=entity, standing=standing,
                                         reason=f"stub:{standing}", key_clusters=keys))
        return CrossSectionView(readings=out)

    def _dispatch(role, schema, context, **kw):
        return (_fake_cross if schema is CrossSectionView else _fake)(
            role, schema, context, **kw)

    monkeypatch.setattr(adjudicator, "run_structured", _dispatch)


class FakeBroker:
    """Records placed orders; fills everything at $100."""

    placed: list = []

    def __init__(self, *a, **k):
        pass

    def place_orders(self, items, cycle_id, wait=3.0):
        now = datetime.now(timezone.utc)
        FakeBroker.placed = list(items)
        return [TradeLogEntry(order_id="1", cycle_id=cycle_id, symbol=d.symbol, action=d.action,
                              qty=q, status="filled", submitted_at=now, filled_at=now,
                              avg_fill_price=100.0, rationale=d.rationale) for d, q in items]

    def get_fills(self):
        return [{"exec_id": "e1", "symbol": "NVDA", "side": "BOT", "shares": 5, "price": 100,
                 "time": datetime.now(timezone.utc).isoformat(), "realized_pnl": None,
                 "commission": 1.0, "order_id": "1"}]


@pytest.fixture
def broker(monkeypatch):
    """Hermetic broker stack: FakeBroker, $100 last price, no live portfolio
    (the risk gate degrades to 'risk checks skipped')."""
    from ats.trader import execute as texec

    FakeBroker.placed = []
    monkeypatch.setattr(texec, "IBKRBroker", FakeBroker)
    monkeypatch.setattr(texec, "_last_price", lambda s: 100.0)
    monkeypatch.setattr("ats.trader.portfolio.snapshot", lambda: None)
    return FakeBroker


class FakeAsyncChannel:
    """Async BossChannel stub: captures the approval request instead of sending."""

    is_async = True

    def __init__(self):
        self.thread_id = None
        self.request = None
        self.notifications = []

    def push(self, msg):
        self.notifications.append(msg)

    def send_approval_request(self, req, thread_id):
        self.request = req
        self.thread_id = thread_id

    def fetch_report_context(self, query):
        from ats.channel.context import build_report_bundle

        return build_report_bundle(query)


@pytest.fixture
def async_channel():
    return FakeAsyncChannel()


class FakeChannel:
    """Programmable BossChannel for tests: replays a scripted verdict."""

    def __init__(self, verdict: BossApproval):
        self.verdict = verdict
        self.requests: list[ApprovalRequest] = []
        self.notifications: list[Notification] = []

    def push(self, msg: Notification) -> None:
        self.notifications.append(msg)

    def request_approval(self, req: ApprovalRequest) -> BossApproval:
        self.requests.append(req)
        return self.verdict

    def fetch_report_context(self, query: str) -> ReportBundle:
        return ReportBundle(query=query)


@pytest.fixture
def approve_all():
    return FakeChannel(BossApproval(status="approved", reviewer="test",
                                    reviewed_at=datetime.now(timezone.utc)))


@pytest.fixture
def reject_all():
    return FakeChannel(BossApproval(status="rejected", reviewer="test",
                                    reviewed_at=datetime.now(timezone.utc)))
