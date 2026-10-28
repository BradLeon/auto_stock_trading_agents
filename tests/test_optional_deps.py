"""Optional dependency reporting: a missing extra must be named, not disguised.

Without this, an un-installed extras group surfaces as a business assertion failure
deep in a workflow ("scheduler produced no events"), and the operator debugs the wrong
layer. These tests pin the mapping module -> extras group and the report wording.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from ats.runtime.optional_deps import (
    ENVIRONMENT_PROBE_MODULES,
    INSTALL_COMMAND,
    MissingOptionalDependencyError,
    describe_missing_optional_dependency,
    group_for_module,
    missing_module_from_message,
    missing_optional_modules,
    require_module,
)


def test_group_for_module_covers_declared_extras():
    assert group_for_module("apscheduler") == "schedule"
    assert group_for_module("pandas_market_calendars") == "schedule"
    assert group_for_module("ib_async") == "broker"
    assert group_for_module("langgraph.checkpoint.sqlite") == "memory_persist"
    assert group_for_module("fastapi") == "channel"
    assert group_for_module("chromadb") == "memory"
    assert group_for_module("yfinance") == "data"


def test_group_for_module_returns_none_for_unknown():
    assert group_for_module("sqlite3") is None
    assert group_for_module("json") is None


def test_missing_module_from_message():
    text = "ModuleNotFoundError: No module named 'apscheduler'"
    assert missing_module_from_message(text) == "apscheduler"
    assert missing_module_from_message("boom") is None


def test_describe_missing_optional_dependency_names_module_and_group():
    report = describe_missing_optional_dependency(
        "ModuleNotFoundError: No module named 'apscheduler'"
    )
    assert report is not None
    assert "apscheduler" in report
    assert "schedule" in report
    assert INSTALL_COMMAND in report


def test_describe_missing_optional_dependency_ignores_other_import_errors():
    # A genuine bug inside an installed package is not a missing optional dependency.
    assert (
        describe_missing_optional_dependency(
            "ImportError: cannot import name 'Foo' from 'ats.broker.ibkr'"
        )
        is None
    )


def test_require_module_raises_missing_optional_dependency(monkeypatch):
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "apscheduler":
            raise ModuleNotFoundError("No module named 'apscheduler'", name="apscheduler")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("importlib.import_module", fake_import)
    with pytest.raises(MissingOptionalDependencyError) as excinfo:
        require_module("apscheduler", purpose="scheduler jobs")
    err = excinfo.value
    assert err.module == "apscheduler"
    assert err.group == "schedule"
    assert "apscheduler" in str(err)
    assert "schedule" in str(err)
    assert "scheduler jobs" in str(err)


def test_require_module_reraises_plain_import_error_for_unknown_module(monkeypatch):
    def fake_import(name, *args, **kwargs):
        raise ModuleNotFoundError("No module named 'totally_unknown_pkg'")

    monkeypatch.setattr("importlib.import_module", fake_import)
    with pytest.raises(ImportError) as excinfo:
        require_module("totally_unknown_pkg")
    assert not isinstance(excinfo.value, MissingOptionalDependencyError)


def test_missing_optional_modules_reports_module_and_group(monkeypatch):
    def fake_import(name, *args, **kwargs):
        raise ModuleNotFoundError(f"No module named '{name}'")

    monkeypatch.setattr("importlib.import_module", fake_import)
    missing = missing_optional_modules(["apscheduler", "ib_async"])
    assert ("apscheduler", "schedule") in missing
    assert ("ib_async", "broker") in missing


def test_environment_probe_modules_match_install_command():
    """The probe list used by `scripts/run_tests.sh --check` must stay in the registry."""
    for module in ENVIRONMENT_PROBE_MODULES:
        assert group_for_module(module), f"{module} has no extras group"


def test_scheduler_tests_report_missing_group_instead_of_business_failure(tmp_path):
    """End-to-end: with the `schedule` extra unavailable, the failure names it.

    A throwaway package simulates an environment without the `schedule` extra by
    blocking `apscheduler` at import time; pytest must then surface the module *and*
    its extras group rather than a scheduler assertion failure.
    """
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "conftest.py").write_text(
        textwrap.dedent(
            """
            import sys
            from importlib.abc import MetaPathFinder


            class _Blocker(MetaPathFinder):
                def find_module(self, fullname, path=None):
                    return None

                def find_spec(self, fullname, path=None, target=None):
                    if fullname == "apscheduler" or fullname.startswith("apscheduler."):
                        raise ModuleNotFoundError("No module named 'apscheduler'")
                    return None


            sys.meta_path.insert(0, _Blocker())
            """
        ),
        encoding="utf-8",
    )
    (pkg / "test_scheduler_job.py").write_text(
        textwrap.dedent(
            """
            import apscheduler  # noqa: F401  (import must fail: extra not installed)


            def test_job_registered():
                assert True
            """
        ),
        encoding="utf-8",
    )

    repo_root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(pkg),
            "-q",
            "--tb=line",
            "-p",
            "no:cacheprovider",
            # Load the project config so the optional-dependency plugin is active.
            "-c",
            str(repo_root / "pyproject.toml"),
            "--basetemp",
            str(tmp_path / "bt"),
        ],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )
    output = proc.stdout + proc.stderr
    assert "apscheduler" in output
    assert "schedule" in output, (
        "the failure must name the owning extras group, got:\n" + output[-2000:]
    )
