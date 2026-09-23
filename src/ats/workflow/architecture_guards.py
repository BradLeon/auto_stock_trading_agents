"""Machine-checkable versions of three architecture rules.

The rules themselves are old news: analysts must not read each other's opinions, agents
must not reach past the data products into providers, and an opinionated conclusion must
not be laundered into a neutral shared fact. What has been missing is an executable
judgement — until now they were enforced by review, which is exactly the kind of
constraint that decays the moment a deadline lands.

Implementation is AST scanning, not runtime probing (see design D7): a runtime probe
only covers the branches that happen to execute, while the question "does role A read
role B's output anywhere?" is a question about the source.

Exceptions are allowed but must be *specific* — a module and a target, with a reason.
A wildcard exclusion would make the guard a decoration.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from ..config import REPO_ROOT

AGENT_ROOT = REPO_ROOT / "src" / "ats" / "agents"

# --- roles ----------------------------------------------------------------- #
# Directory → role. Deliberately explicit: inferring a role from a file name is how a
# renamed module silently changes what it is allowed to read.
ROLE_BY_PATH_PREFIX: tuple[tuple[str, str], ...] = (
    ("src/ats/agents/sector/layer_analyst", "layer_analyst"),
    ("src/ats/agents/sector/layer_review", "layer_analyst"),
    ("src/ats/agents/layer", "layer_analyst"),
    ("src/ats/agents/sector", "sector_analyst"),
    ("src/ats/agents/information", "information_analyst"),
    ("src/ats/agents/evidence", "evidence_observer"),
    ("src/ats/agents/pead", "fundamental_analyst"),
    ("src/ats/agents/macro", "macro_analyst"),
    ("src/ats/agents/technical", "technical_analyst"),
    ("src/ats/agents/chief", "chief"),
    ("src/ats/agents/risk", "risk_officer"),
)

# The two dependencies the design explicitly permits. Everything else is a violation.
# Key = the reader, value = the roles it may read.
ALLOWED_CROSS_ROLE_READS: dict[str, tuple[str, ...]] = {
    "sector_analyst": ("layer_analyst",),      # 行业分析师读层次分析师
    "fundamental_analyst": ("information_analyst",),  # 基本面分析师读信息分析师
}

# Projection readers: calls through which one role can consume another's output.
PROJECTION_READ_CALLS = frozenset({
    "task_projection_envelopes", "reusable_task_projection", "reuse_decision",
    "task_projections",
})

# --- providers -------------------------------------------------------------- #
PROVIDER_PREFIXES: tuple[str, ...] = ("ats.data.adapters",)

PROVIDER_MODULES: frozenset[str] = frozenset({
    "ats.data.defeatbeta", "ats.data.factset", "ats.data.news", "ats.data.websearch",
    "ats.data.sec", "ats.data.transcript", "ats.data.source_cache",
    "ats.data.documents", "ats.data.document_assets", "ats.data.research",
    "ats.data.consensus", "ats.data.fundamentals", "ats.data.industry",
    "ats.data.regional", "ats.data.base", "ats.data.market_data", "ats.data.options",
})

# --- shared-fact write targets ---------------------------------------------- #
SHARED_FACT_WRITE_CALLS: frozenset[str] = frozenset({
    "save_evidence_observation", "save_evidence_failure", "save_measurement_points",
    "save_document", "save_document_candidate", "save_document_alias",
    "save_document_chunks", "ingest",
})

# Shared-fact WRITE owners: the data-layer repository and Workflow memory's delegating
# methods. A call to one of these from an agent module is the laundering we forbid —
# except where a module is registered as an exception with a stated reason.
WRITE_OWNER_HINTS: frozenset[str] = frozenset({
    "document_assets", "data_store", "platform", "repository",
})


@dataclass(frozen=True)
class Violation:
    kind: str
    module: str
    target: str
    lineno: int
    detail: str = ""

    def __str__(self) -> str:
        place = f"{self.module}:{self.lineno}"
        return f"[{self.kind}] {place} — {self.target}" + (
            f" ({self.detail})" if self.detail else "")


@dataclass(frozen=True)
class ExceptionEntry:
    """A declared, reasoned exception. Module-level, never a pattern."""

    module: str
    target: str
    reason: str


# --- first batch ------------------------------------------------------------ #
# These are current-state violations, each declared with the reason it still exists and
# the phase that will remove it. They are NOT a tolerance list: adding a new violation
# fails the guard until it is declared here in the same change.
FIRST_BATCH_EXCEPTIONS: tuple[ExceptionEntry, ...] = (
    ExceptionEntry(
        module="src/ats/agents/evidence/observer.py", target="ats.data.defeatbeta",
        reason="证据观察器是采集侧确定性组件，直接取结构化纪要源；Phase D 把采集移出 "
               "agents/ 后改由数据产品入口读取。"),
    ExceptionEntry(
        module="src/ats/agents/evidence/observer.py", target="ats.data.transcript",
        reason="同上：transcript 取数是采集路径而非观点输入，Phase D 随采集侧一并迁出。"),
    ExceptionEntry(
        module="src/ats/agents/evidence/observer.py", target="ats.data.source_cache",
        reason="本地语料缓存用于保证前后两次取数可比（重取不幂等），Phase D 随采集侧迁出。"),
    ExceptionEntry(
        module="src/ats/agents/evidence/observer.py", target="ats.data.documents",
        reason="公开文档聚合属采集阶段，Phase D 随采集侧迁出。"),
    ExceptionEntry(
        module="src/ats/agents/evidence/observer.py", target="ats.data.document_assets",
        reason="写入的是取回的原始文档资产，不是分析结论；Phase D 改由采集侧写入后收紧。"),
    ExceptionEntry(
        module="src/ats/agents/evidence/observer.py", target="ats.data.sec",
        reason="SEC 披露是原始来源而非观点，Phase D 随采集侧迁出。"),
    ExceptionEntry(
        module="src/ats/agents/evidence/observer.py", target="ats.data.fiscal",
        reason="财年标签解析是纯函数工具，不访问 Provider；Phase D 归入数据产品工具层。"),
    ExceptionEntry(
        module="src/ats/agents/evidence/observer.py", target="ingest",
        reason="写入的是取回的原始文档资产（未含任何观点），不是把结论写成中性事实；"
               "Phase D 采集侧迁出 agents/ 后本例外撤销。"),
    ExceptionEntry(
        module="src/ats/agents/macro/assemble.py", target="ats.data.factset",
        reason="宏观指标取数尚未收敛到数据产品入口；Phase D 改为经 products 读取。"),
    ExceptionEntry(
        module="src/ats/agents/macro/assemble.py", target="ats.data.regional",
        reason="同上：区域口径属取数侧，Phase D 收敛。"),
    ExceptionEntry(
        module="src/ats/agents/macro/assemble.py", target="ats.data.websearch",
        reason="宏观事件检索尚直连检索 Provider；Phase D 改为经数据产品入口。"),
    ExceptionEntry(
        module="src/ats/agents/sector/assemble.py", target="ats.data.factset",
        reason="产业链行情取数未收敛；Phase D 改为经 products 读取。"),
    ExceptionEntry(
        module="src/ats/agents/sector/assemble.py", target="ats.data.consensus",
        reason="一致预期属共享数据而非观点，但取数入口未收敛；Phase D 改经 products。"),
    ExceptionEntry(
        module="src/ats/agents/sector/assemble.py", target="ats.data.fundamentals",
        reason="同上；Phase D 收敛。"),
    ExceptionEntry(
        module="src/ats/agents/sector/assemble.py", target="ats.data.industry",
        reason="同上；Phase D 收敛。"),
    ExceptionEntry(
        module="src/ats/agents/sector/assemble.py", target="ats.data.regional",
        reason="同上；Phase D 收敛。"),
    ExceptionEntry(
        module="src/ats/agents/sector/cross_section.py", target="ats.data.consensus",
        reason="同上；Phase D 收敛。"),
    ExceptionEntry(
        module="src/ats/agents/sector/cross_section.py", target="ats.data.fundamentals",
        reason="同上；Phase D 收敛。"),
    ExceptionEntry(
        module="src/ats/agents/sector/kb_perturb.py", target="ats.data.industry",
        reason="知识库扰动只用行业分类枚举，不取数；Phase D 归入数据产品工具层。"),
    ExceptionEntry(
        module="src/ats/agents/layer/layer_review.py", target="ats.data.industry",
        reason="同上；Phase D 收敛。"),
    ExceptionEntry(
        module="src/ats/agents/sector/structure.py", target="ats.data.industry",
        reason="产业链结构只取行业分类枚举，不取数；Phase D 归入数据产品工具层。"),
    ExceptionEntry(
        module="src/ats/agents/sector/review.py", target="ats.data.factset",
        reason="同上；Phase D 收敛。"),
    ExceptionEntry(
        module="src/ats/agents/pead/monitor.py", target="ats.data.news",
        reason="PEAD 监控的新闻取数未收敛；Phase D 改为经数据产品入口。"),
    ExceptionEntry(
        module="src/ats/agents/pead/triage.py", target="ats.data.news",
        reason="同上；Phase D 收敛。"),
    ExceptionEntry(
        module="src/ats/agents/pead/research.py", target="ats.data.research",
        reason="研究文章取数未收敛；Phase D 改为经数据产品入口。"),
    ExceptionEntry(
        module="src/ats/agents/pead/report.py", target="ats.data.fiscal",
        reason="财年标签解析是纯函数工具，不访问 Provider；Phase D 归入工具层。"),
    ExceptionEntry(
        module="src/ats/agents/technical/review.py", target="ats.data.base",
        reason="技术面读取的是行情基类接口而非特定 Provider；Phase D 改为经 products。"),
)


def declared_exception(module: str, target: str,
                       exceptions: Iterable[ExceptionEntry] = ()) -> ExceptionEntry | None:
    for entry in exceptions or FIRST_BATCH_EXCEPTIONS:
        if entry.module == module and entry.target == target:
            return entry
    return None


def role_for(relative_path: str) -> str | None:
    for prefix, role in ROLE_BY_PATH_PREFIX:
        if relative_path.startswith(prefix):
            return role
    return None


def _relative(path: Path, root: Path | None = None) -> str:
    return path.relative_to(root or REPO_ROOT).as_posix()


def _imported_modules(node: ast.ImportFrom) -> list[str]:
    """Absolute modules an `ImportFrom` actually binds, relative levels resolved.

    `from ...data import defeatbeta` binds `ats.data.defeatbeta`, not `ats.data` —
    resolving only the module part would let every provider import through the package's
    front door.
    """
    if not node.module:
        return []
    base = (node.module if node.level == 0
            else ("ats." + node.module) if node.level >= 3 else node.module)
    out = [base]
    out.extend(f"{base}.{alias.name}" for alias in node.names)
    return out


def _is_provider(module: str) -> bool:
    return (module in PROVIDER_MODULES
            or any(module.startswith(prefix) for prefix in PROVIDER_PREFIXES))


def _role_literal(node: ast.Call) -> str | None:
    for keyword in node.keywords:
        if keyword.arg in ("agent_role", "role") and isinstance(keyword.value, ast.Constant):
            return str(keyword.value.value)
    return None


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _write_owner(node: ast.Call) -> str:
    """Base name a write call hangs off — `data_store().save_x()` is `data_store`.

    Unwrapping through the call is required: the delegating accessor is itself a call,
    and stopping at the Attribute would miss every write made through it.
    """
    if not isinstance(node.func, ast.Attribute):
        return ""
    base: ast.expr = node.func.value
    while isinstance(base, ast.Attribute):
        base = base.value
    if isinstance(base, ast.Name):
        return base.id
    if isinstance(base, ast.Call):
        return _call_name(base)
    return ""


def scan_module(path: Path, *, root: Path | None = None,
                exceptions: Sequence[ExceptionEntry] = ()) -> list[Violation]:
    """All architecture violations in one module, after declared exceptions."""
    relative = _relative(path, root or REPO_ROOT)
    role = role_for(relative)
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    found: list[Violation] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            # Report one violation per import statement, naming the shortest matching
            # target: `from ats.data.adapters.x import y` matches on both the module and
            # the module+name form, and duplicating it would inflate the count.
            hits = [m for m in _imported_modules(node) if _is_provider(m)
                    and not declared_exception(relative, m, exceptions)]
            if hits:
                found.append(Violation(
                    "provider_import", relative, min(hits, key=len), node.lineno,
                    "agent module imports a source adapter or provider"))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if not alias.name.startswith("ats."):
                    continue
                if not _is_provider(alias.name):
                    continue
                if declared_exception(relative, alias.name, exceptions):
                    continue
                found.append(Violation("provider_import", relative, alias.name,
                                       node.lineno, "direct provider import"))
        elif isinstance(node, ast.Call):
            name = _call_name(node)
            if name in PROJECTION_READ_CALLS and role is not None:
                read_role = _role_literal(node)
                if read_role and read_role != role:
                    allowed = ALLOWED_CROSS_ROLE_READS.get(role, ())
                    if read_role not in allowed:
                        found.append(Violation(
                            "cross_role_read", relative, read_role, node.lineno,
                            f"{role} reads {read_role}'s projection"))
            if name in SHARED_FACT_WRITE_CALLS and role is not None:
                owner = _write_owner(node)
                if owner in WRITE_OWNER_HINTS:
                    if declared_exception(relative, name, exceptions):
                        continue
                    found.append(Violation(
                        "opinion_writeback", relative, name, node.lineno,
                        f"{role} writes a shared-fact record via {owner}"))
    return found


def scan_agents(base: Path | None = None, *, root: Path | None = None) -> list[Violation]:
    """Scan every agent module. `root` is only overridden by tests using a temp tree."""
    start = base or AGENT_ROOT
    out: list[Violation] = []
    for path in sorted(start.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        out.extend(scan_module(path, root=root))
    return out
