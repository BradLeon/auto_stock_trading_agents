"""Per-consumer routing keeps Workflow memory writes out of data read cutover."""

from __future__ import annotations

from ats.data.products.routing import UnstructuredReadRouter


class _Legacy:
    def documents(self, **_kwargs):
        return [{"document_id": "doc-1"}]

    def save_insights(self, *_args):
        return "legacy-write"


class _Platform:
    def __init__(self, rows):
        self.rows = rows
        self.closed = False

    def documents(self, **_kwargs):
        return self.rows

    def close(self):
        self.closed = True


def test_shadow_returns_legacy_and_platform_returns_migrated_documents(caplog) -> None:
    legacy, platform = _Legacy(), _Platform([{"document_id": "doc-2"}])
    shadow = UnstructuredReadRouter(
        consumer="pead_research", legacy_repository=legacy,
        platform_repository=platform, mode="shadow")
    assert shadow.documents() == [{"document_id": "doc-1"}]
    assert "unstructured shadow mismatch" in caplog.text
    assert shadow.save_insights() == "legacy-write"
    shadow.close()
    assert platform.closed

    platform_mode = UnstructuredReadRouter(
        consumer="pead_research", legacy_repository=legacy,
        platform_repository=_Platform([{"document_id": "doc-2"}]), mode="platform")
    assert platform_mode.documents() == [{"document_id": "doc-2"}]


def test_router_without_a_provisioned_platform_keeps_the_declared_legacy_path() -> None:
    legacy = _Legacy()
    router = UnstructuredReadRouter(
        consumer="evidence_chain", legacy_repository=legacy,
        platform_repository=None, mode="shadow")
    assert router.documents() == [{"document_id": "doc-1"}]
