"""Pytest plugin that turns a missing optional dependency into an explicit report.

Registered project-wide via ``[tool.pytest.ini_options] addopts`` so it applies to
every run, including ad-hoc invocations. When a test module (or a fixture) fails to
import a module that belongs to a declared extras group, the failure text is prefixed
with a report naming the module and its group — instead of the operator seeing an
unrelated business assertion failure further down the stack.
"""

from __future__ import annotations

from ats.runtime.optional_deps import describe_missing_optional_dependency


def enrich_failure_text(text: str) -> str | None:
    """Return the explicit dependency report for ``text``, or ``None`` if it does not
    apply (so unrelated failures are reported unchanged)."""
    if not text:
        return None
    return describe_missing_optional_dependency(text)


def _enrich(report) -> None:
    if getattr(report, "longrepr", None) is None:
        return
    described = enrich_failure_text(str(report.longrepr))
    if described:
        report.longrepr = f"[{described}]\n{report.longrepr}"


def pytest_collectreport(report) -> None:
    if report.failed:
        _enrich(report)


def pytest_runtest_logreport(report) -> None:
    if report.failed:
        _enrich(report)
