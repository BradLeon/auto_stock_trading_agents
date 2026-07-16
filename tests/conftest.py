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
    monkeypatch.setenv("ATS_CHECKPOINT_DB", str(tmp_path / "ckpt.sqlite"))
    from ats.memory import reset_store_cache

    reset_store_cache()
    yield
    reset_store_cache()


@pytest.fixture(autouse=True)
def _isolate_report_dir(tmp_path, monkeypatch):
    """Reports must never land in the real Obsidian vault during tests.

    Every writer resolves the vault via ats.config.load_macro_config().output_dir
    at call time (imports are inside functions), so patching the module attribute
    covers them all. 2026-07-15: a chief test overwrote real vault documents.
    """
    import ats.config as config

    real = config.load_macro_config

    def _redirected(name: str = "macro"):
        cfg = real(name)
        return cfg.model_copy(update={"output_dir": str(tmp_path)})

    monkeypatch.setattr(config, "load_macro_config", _redirected)


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
