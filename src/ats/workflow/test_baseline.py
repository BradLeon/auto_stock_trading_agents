"""Baseline measurement: parse a pytest run, attribute failures to clusters, and
separate environmental failures from real regressions.

Why this exists: a single environmental restriction (a sandbox that refuses to create
or delete pytest's temp directories) produced ~1500 errors in one run, while a real
regression produced 1. Counting the two together makes the suite useless as a
gate — every later phase needs "did *this* change break something?", which requires
the two to be distinguishable and the environmental part to be explained once, with
its amplification mechanism and affected scope, instead of being listed as 1500
individual business failures.

Two responsibilities:

  * :func:`parse_pytest_text` / :func:`parse_junit_xml` turn a run into outcomes.
  * :func:`summarize` groups outcomes into clusters (root cause + size + criterion),
    declares a whole-run environmental interruption when one dominates, and leaves an
    explicit "unattributed" bucket so no failure is silently absorbed.

  * :class:`BaselineRecord` renders the measurement conditions (command, dependency
    scope, environment limitations) that must accompany every cited baseline number.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from ats.runtime.optional_deps import describe_missing_optional_dependency

# --------------------------------------------------------------------------- #
# Environmental signatures. A failure matching one of these is an environment
# restriction, not a regression: the code under test never ran.
# --------------------------------------------------------------------------- #

_ENVIRONMENT_SIGNATURES: tuple[tuple[str, str], ...] = (
    ("SAFE_DELETE_BULK_CONFIRM_REQUIRED", "执行环境的文件删除配额被触发"),
    ("deletion quota", "执行环境的文件删除配额被触发"),
    ("pytest-of-", "pytest 临时目录根无法创建（沙箱拒绝 mkdir）"),
    ("PermissionError: EEXIST", "pytest 临时目录根已存在且沙箱拒绝 mkdir"),
    ("SystemExit", "守卫以 SystemExit 打断 fixture setup"),
    # JUnit's own boundary: an <error> means setup/teardown never completed, so the
    # assertion was never evaluated. A whole batch of these is the deletion-quota guard
    # tripping once and vetoing every fixture after it — not 413 separate regressions.
    ("failed on setup with", "fixture setup 未完成，测试体未执行（JUnit <error>）"),
    ("Read-only file system", "临时目录所在文件系统只读"),
    ("No space left on device", "临时目录所在设备无剩余空间"),
)

# Autouse fixture that turns an environment restriction into a suite-wide failure.
_AMPLIFICATION_FIXTURE = "tests/conftest.py::`_isolate_db`（autouse，依赖 pytest tmp_path）"

# Any single environmental cluster covering at least this share of outcomes is
# treated as a whole-run interruption rather than a set of per-test failures.
_WHOLE_RUN_ENV_THRESHOLD = 0.25

_OUTCOME_LINE_RE = re.compile(
    r"^(?P<kind>PASSED|FAILED|ERROR|XFAIL|XPASS|SKIPPED)\s+"
    r"(?P<nodeid>[^\s]+)(?:\s+-\s+(?P<msg>.*))?$"
)
_TOTALS_LINE_RE = re.compile(
    r"^(?P<counts>(?:\d+\s+\w+,\s+)*\d+\s+\w+)\s+in\s+(?P<duration>[\d.,:]+s?)"
)
_COUNT_RE = re.compile(r"(\d+)\s+(passed|failed|errors?|error|skipped|xfailed|xpassed|warnings?)")

# Root-cause signatures used to cluster business failures. Each entry maps a regex to
# (cluster label template, criterion). Order matters: first match wins.
_BUSINESS_CLUSTERS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(r"no such table:\s*(?P<table>\w+)"),
        "missing table: {table}",
        "失败信息指向同一张缺失表；该表被边界归类判定不属于写入目标库",
    ),
    (
        re.compile(r"unexpected keyword argument '(?P<arg>\w+)'"),
        "interface signature drift: {arg}",
        "调用方传入了被调方不接受的关键字参数，属接口签名漂移",
    ),
    (
        re.compile(r"missing \d+ required positional argument"),
        "interface signature drift: positional",
        "调用方缺少被调方要求的位置参数，属接口签名漂移",
    ),
)


@dataclass(frozen=True)
class TestOutcome:
    """One pytest result line."""

    kind: str            # PASSED | FAILED | ERROR | SKIPPED | XFAIL | XPASS
    nodeid: str
    message: str = ""
    phase: str = ""      # "call" for FAILED, "setup"/"teardown" for ERROR (junit only)

    @property
    def is_failure(self) -> bool:
        return self.kind in {"FAILED", "ERROR"}


@dataclass
class TestRunReport:
    """Parsed result of one pytest run."""

    outcomes: list[TestOutcome] = field(default_factory=list)
    totals: dict[str, int] = field(default_factory=dict)
    duration: str = ""
    raw_notes: list[str] = field(default_factory=list)  # environment guard output, etc.

    @property
    def failures(self) -> list[TestOutcome]:
        return [o for o in self.outcomes if o.is_failure]


@dataclass
class FailureCluster:
    """A group of failures sharing one root cause."""

    label: str
    kind: str            # "environmental" | "business"
    size: int
    criterion: str       # 判据：为什么这些失败算同一簇
    disposition: str     # 处置归属：由谁/哪一步消解
    samples: list[str] = field(default_factory=list)


@dataclass
class BaselineSummary:
    """Attributed summary of one run."""

    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    environmental: int = 0
    business: int = 0
    unattributed: int = 0
    clusters: list[FailureCluster] = field(default_factory=list)
    whole_run_environmental: bool = False
    amplification: str = ""
    affected_scope: int = 0

    def dominant_environmental_cluster(self) -> FailureCluster | None:
        env = [c for c in self.clusters if c.kind == "environmental"]
        return max(env, key=lambda c: c.size) if env else None


@dataclass
class BaselineRecord:
    """The measurement conditions that must accompany every cited baseline number."""

    command: str
    dependency_scope: str
    environment_conditions: Sequence[str] = ()
    limitations: Sequence[str] = ()
    counts: str = ""

    def render(self) -> str:
        lines = [
            "### 测量记录",
            "",
            f"- **命令**：`{self.command}`",
            f"- **依赖范围**：{self.dependency_scope}",
        ]
        if self.counts:
            lines.append(f"- **结果**：{self.counts}")
        if self.environment_conditions:
            lines.append("- **执行环境条件**：")
            lines.extend(f"  - {c}" for c in self.environment_conditions)
        if self.limitations:
            lines.append("- **限制与影响范围**：")
            lines.extend(f"  - {c}" for c in self.limitations)
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

def _classify_environment(message: str) -> str | None:
    for needle, reason in _ENVIRONMENT_SIGNATURES:
        if needle in message:
            return reason
    return None


def parse_pytest_text(text: str) -> TestRunReport:
    """Parse pytest's short summary output (`-rA --tb=no`) into a :class:`TestRunReport`."""
    report = TestRunReport()
    for raw in text.splitlines():
        line = raw.strip()
        m = _OUTCOME_LINE_RE.match(line)
        if m:
            kind = m.group("kind")
            phase = {"FAILED": "call", "ERROR": "setup"}.get(kind, "")
            report.outcomes.append(
                TestOutcome(
                    kind=kind,
                    nodeid=m.group("nodeid"),
                    message=(m.group("msg") or "").strip(),
                    phase=phase,
                )
            )
            continue
        m = _TOTALS_LINE_RE.match(line)
        if m:
            report.duration = m.group("duration")
            for count, name in _COUNT_RE.findall(m.group("counts")):
                key = "errors" if name.startswith("error") else name
                report.totals[key] = int(count)
            continue
        # Free-form guard/agent output (e.g. SAFE_DELETE_BULK_CONFIRM_REQUIRED).
        if "SAFE_DELETE_BULK_CONFIRM_REQUIRED" in line or "deletion quota" in line:
            report.raw_notes.append(line)
        elif "PermissionError" in line and "pytest-of-" in line:
            report.raw_notes.append(line)
    return report


def parse_junit_xml(xml_text: str) -> TestRunReport:
    """Parse a JUnit XML report (`pytest --junitxml=...`) into a :class:`TestRunReport`.

    JUnit distinguishes an error (setup/teardown did not complete — the test never ran)
    from a failure (the test ran and its assertion failed), which is exactly the
    environmental/business boundary.
    """
    import xml.etree.ElementTree as ET

    report = TestRunReport()
    root = ET.fromstring(xml_text)
    for suite in root.iter("testsuite"):
        for key in ("tests", "failures", "errors", "skipped"):
            if suite.get(key) is not None:
                report.totals[key] = int(suite.get(key))
    for case in root.iter("testcase"):
        nodeid = f"{case.get('classname', '').replace('.', '/')}.py::{case.get('name', '')}"
        failure = case.find("failure")
        error = case.find("error")
        skipped = case.find("skipped")
        if error is not None:
            report.outcomes.append(
                TestOutcome("ERROR", nodeid, (error.get("message") or "").strip(), "setup")
            )
        elif failure is not None:
            report.outcomes.append(
                TestOutcome("FAILED", nodeid, (failure.get("message") or "").strip(), "call")
            )
        elif skipped is not None:
            report.outcomes.append(
                TestOutcome("SKIPPED", nodeid, (skipped.get("message") or "").strip())
            )
        else:
            report.outcomes.append(TestOutcome("PASSED", nodeid))
    return report


# --------------------------------------------------------------------------- #
# Attribution
# --------------------------------------------------------------------------- #

def _cluster_key(outcome: TestOutcome) -> tuple[str, str, str] | None:
    """Return ``(kind, label, criterion)`` for a failure, or ``None`` if unattributed."""
    message = outcome.message or ""
    env_reason = _classify_environment(message)
    if env_reason:
        return ("environmental", f"environmental: {env_reason}", env_reason)
    dep = describe_missing_optional_dependency(message)
    if dep:
        return ("environmental", "environmental: missing optional dependency", dep)
    for pattern, label, criterion in _BUSINESS_CLUSTERS:
        m = pattern.search(message)
        if m:
            return ("business", label.format(**m.groupdict()), criterion)
    return None


def summarize(report: TestRunReport) -> BaselineSummary:
    """Group a run's failures into clusters and separate environmental from business."""
    summary = BaselineSummary()
    summary.passed = report.totals.get("passed", sum(o.kind == "PASSED" for o in report.outcomes))
    summary.failed = report.totals.get("failed", sum(o.kind == "FAILED" for o in report.outcomes))
    summary.errors = report.totals.get("errors", sum(o.kind == "ERROR" for o in report.outcomes))
    summary.skipped = report.totals.get(
        "skipped", sum(o.kind in {"SKIPPED", "XFAIL"} for o in report.outcomes)
    )

    buckets: dict[tuple[str, str], list[TestOutcome]] = {}
    criteria: dict[tuple[str, str], str] = {}
    unattributed: list[TestOutcome] = []
    for outcome in report.failures:
        key = _cluster_key(outcome)
        if key is None:
            unattributed.append(outcome)
            continue
        kind, label, criterion = key
        bucket_key = (kind, label)
        buckets.setdefault(bucket_key, []).append(outcome)
        criteria.setdefault(bucket_key, criterion)

    for (kind, label), items in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        crit = criteria.get((kind, label), "")
        if kind == "environmental":
            crit = items[0].message or label
            disposition = "环境性：修复执行环境后整体消解，不计入业务回归"
        else:
            disposition = "业务性：需逐簇修复并登记归属"
        summary.clusters.append(
            FailureCluster(
                label=label,
                kind=kind,
                size=len(items),
                criterion=crit,
                disposition=disposition,
                samples=[i.nodeid for i in items[:3]],
            )
        )

    if unattributed:
        summary.clusters.append(
            FailureCluster(
                label="unattributed",
                kind="business",
                size=len(unattributed),
                criterion="未匹配任何已知根因签名，需逐项归因",
                disposition="业务性：逐项归因后登记（本阶段不要求清零）",
                samples=[i.nodeid for i in unattributed[:5]],
            )
        )

    total_outcomes = max(
        summary.passed + summary.failed + summary.errors,
        len(report.outcomes),
    )
    for cluster in summary.clusters:
        if cluster.kind == "environmental":
            summary.environmental += cluster.size
        else:
            summary.business += cluster.size
    summary.unattributed = len(unattributed)

    dominant = summary.dominant_environmental_cluster()
    if dominant and total_outcomes and dominant.size / total_outcomes >= _WHOLE_RUN_ENV_THRESHOLD:
        summary.whole_run_environmental = True
        summary.affected_scope = dominant.size
        summary.amplification = (
            f"整库级 setup 中断：{dominant.label}。放大机制——"
            f"{_AMPLIFICATION_FIXTURE} 使每个测试都依赖 pytest 临时目录，"
            f"单一环境限制因此被放大为 {dominant.size} 项 setup 失败。"
        )
    return summary


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

def render_summary(
    summary: BaselineSummary,
    *,
    record: BaselineRecord | None = None,
) -> str:
    """Render a human-readable attributed summary (counts, clusters, unattributed)."""
    lines: list[str] = []
    if record is not None:
        lines.append(record.render())
        lines.append("")
    lines.append("### 计数")
    lines.append("")
    lines.append(
        f"- 通过：{summary.passed}｜断言失败（业务性）：{summary.business}｜"
        f"环境性失败：{summary.environmental}"
    )
    lines.append(f"- 原始计数：failed={summary.failed}，errors={summary.errors}，skipped={summary.skipped}")
    if summary.whole_run_environmental:
        lines.append("")
        lines.append("### 整库级环境性中断")
        lines.append("")
        lines.append(f"- {summary.amplification}")
        lines.append(f"- 受影响范围：{summary.affected_scope} 项（不计入业务回归）")
    lines.append("")
    lines.append("### 失败簇")
    lines.append("")
    lines.append("| 簇 | 类型 | 规模 | 判据 | 处置归属 |")
    lines.append("|---|---|---:|---|---|")
    for c in summary.clusters:
        lines.append(
            f"| `{c.label}` | {c.kind} | {c.size} | {c.criterion} | {c.disposition} |"
        )
    if summary.unattributed:
        lines.append("")
        lines.append(
            f"- 未归因：{summary.unattributed} 项，归因工作归属：本 change 的验收清单 9.1"
            "（业务性失败登记为待处理项，不要求清零）"
        )
    return "\n".join(lines)


def summarize_text(text: str) -> str:
    """Convenience: parse pytest text and render its attributed summary."""
    return render_summary(summarize(parse_pytest_text(text)))


def batched_evidence_note(batch_count: int, per_batch: int) -> str:
    """Limitation note that must accompany any batched (non-whole-suite) measurement."""
    return (
        f"分批取证：共 {batch_count} 批、每批约 {per_batch} 个测试文件。"
        "局限：分批运行不共享进程内状态与临时目录生命周期，"
        "跨批次的顺序依赖、全局缓存与资源竞争不会显现；"
        "汇总计数可与全量结果核对，但**不得**用分批结果替代全量基线判定回归。"
    )


def merge_reports(reports: Iterable[TestRunReport]) -> TestRunReport:
    """Merge several runs (batched evidence) into one report."""
    merged = TestRunReport()
    for report in reports:
        merged.outcomes.extend(report.outcomes)
        merged.raw_notes.extend(report.raw_notes)
        for key, value in report.totals.items():
            merged.totals[key] = merged.totals.get(key, 0) + value
    return merged


def iter_batches(items: Sequence[str], size: int) -> Iterable[list[str]]:
    """Split ``items`` into consecutive batches of at most ``size``."""
    for i in range(0, len(items), size):
        yield list(items[i : i + size])
