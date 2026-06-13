import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Make the src layout importable without installing.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ats.schemas.channel import ApprovalRequest, Notification, ReportBundle  # noqa: E402
from ats.schemas.decision import BossApproval  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path, monkeypatch):
    """Point Context Memory + checkpoints at throwaway DBs per test."""
    monkeypatch.setenv("ATS_DB_PATH", str(tmp_path / "mem.sqlite"))
    monkeypatch.setenv("ATS_CHECKPOINT_DB", str(tmp_path / "ckpt.sqlite"))
    from ats.memory import reset_store_cache

    reset_store_cache()
    yield
    reset_store_cache()


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
