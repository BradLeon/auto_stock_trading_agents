"""Per-consumer routing keeps Workflow memory writes out of data read cutover.

Post-cutover the router is platform-only: `legacy_repository` was removed from
`UnstructuredReadRouter.__init__` (it remains an ignored keyword on
`get_unstructured_read_router` solely so existing caller sites stay readable).
"shadow" mode therefore no longer falls back to a legacy repository — it reads the
platform like every other mode. Tests that asserted a legacy return were asserting
the pre-cutover contract.
"""

from __future__ import annotations

from ats.data.products.routing import UnstructuredReadRouter


class _Platform:
    def __init__(self, rows):
        self.rows = rows
        self.closed = False

    def documents(self, **_kwargs):
        return self.rows

    def observation_failures(self, **_kwargs):
        return [{"document_id": "platform-failure"}]

    def close(self):
        self.closed = True


def test_every_mode_reads_the_platform_repository(caplog) -> None:
    """Both declared modes resolve to the released platform repository."""
    platform = _Platform([{"document_id": "doc-2"}])
    router = UnstructuredReadRouter(
        consumer="pead_research", platform_repository=platform, mode="shadow")
    assert router.documents() == [{"document_id": "doc-2"}]
    router.close()
    assert platform.closed

    platform_mode = UnstructuredReadRouter(
        consumer="pead_research", platform_repository=_Platform([{"document_id": "doc-2"}]),
        mode="platform")
    assert platform_mode.documents() == [{"document_id": "doc-2"}]


def test_read_router_rejects_names_that_belong_to_workflow_memory() -> None:
    """`save_insights` is a Workflow-memory write; it must not be routable."""
    router = UnstructuredReadRouter(
        consumer="evidence_chain", platform_repository=_Platform([]), mode="platform")
    try:
        router.save_insights()
    except AttributeError as exc:
        assert "belongs to Workflow memory" in str(exc)
    else:  # pragma: no cover - the guard is the point of this test
        raise AssertionError("save_insights should not be routed to the data layer")


def test_platform_routes_persisted_evidence_failures() -> None:
    router = UnstructuredReadRouter(
        consumer="evidence_chain", platform_repository=_Platform([]), mode="platform")

    assert router.observation_failures() == [{"document_id": "platform-failure"}]
