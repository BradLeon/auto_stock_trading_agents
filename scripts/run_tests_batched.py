"""Run the suite in batches and aggregate the results — for restricted environments.

Some execution environments interrupt a whole-suite run (deletion quotas, temp-root
restrictions). Batching keeps each pytest process small enough to complete, so the
failures can still be *enumerated*. It is evidence collection, not a baseline: the
aggregated numbers are comparable to a full run only for counting purposes, because
batches do not share process state, temp-directory lifetimes or global caches.

    uv run python scripts/run_tests_batched.py --batch-size 8

Output: per-batch counts, then an attributed summary with the explicit limitation note
required by `workflow/test-baseline`.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from ats.workflow.test_baseline import (  # noqa: E402
    BaselineRecord,
    TestRunReport,
    batched_evidence_note,
    iter_batches,
    merge_reports,
    parse_junit_xml,
    parse_pytest_text,
    render_summary,
    summarize,
)


def _test_files() -> list[str]:
    return sorted(str(p) for p in (REPO_ROOT / "tests").glob("test_*.py"))


def _run_batch(files: list[str], basetemp: Path, junit: Path) -> tuple[TestRunReport, str]:
    # pytest only mkdir()s the basetemp itself — a missing parent makes every test error.
    basetemp.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--tb=no",
        "-rA",
        "-p",
        "no:cacheprovider",
        "--basetemp",
        str(basetemp),
        f"--junitxml={junit}",
        *files,
    ]
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
    output = (proc.stdout or "") + (proc.stderr or "")
    # JUnit carries the failure message; `--tb=no` strips it from the text output.
    if junit.exists():
        return parse_junit_xml(junit.read_text(encoding="utf-8")), output
    return parse_pytest_text(output), output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=8, help="test files per batch")
    parser.add_argument(
        "--basetemp-root",
        default=str(REPO_ROOT / "tmp" / "pytest-basetemp"),
        help="parent directory for per-batch temp roots",
    )
    parser.add_argument("--limit", type=int, default=0, help="only first N test files (debugging)")
    args = parser.parse_args(argv)

    files = _test_files()
    if args.limit:
        files = files[: args.limit]
    batches = list(iter_batches(files, max(1, args.batch_size)))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    root = Path(args.basetemp_root) / f"batched-{stamp}"

    reports: list[TestRunReport] = []
    started = time.time()
    for index, batch in enumerate(batches, start=1):
        report, _output = _run_batch(
            batch, root / f"batch-{index}", root / f"batch-{index}.xml"
        )
        reports.append(report)
        print(
            f"[batch {index}/{len(batches)}] "
            f"passed={report.totals.get('passed', 0)} "
            f"failed={report.totals.get('failed', 0)} "
            f"errors={report.totals.get('errors', 0)}",
            flush=True,
        )

    merged = merge_reports(reports)
    summary = summarize(merged)
    record = BaselineRecord(
        command=f"uv run python scripts/run_tests_batched.py --batch-size {args.batch_size}",
        dependency_scope="uv sync --all-extras（全部可选分组）",
        environment_conditions=[
            f"分批运行：{len(batches)} 批，每批 ≤{args.batch_size} 个测试文件",
            f"临时目录根：{root}（仓库内，规避执行环境对默认临时目录的限制）",
            f"耗时 {time.time() - started:.0f}s",
        ],
        limitations=[batched_evidence_note(len(batches), args.batch_size)],
    )
    print()
    print(render_summary(summary, record=record))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
