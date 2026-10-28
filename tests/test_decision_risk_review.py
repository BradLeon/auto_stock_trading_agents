"""Phase B task group 4: deterministic whole-revision risk review.

The review never modifies the proposal; caps come back as structured
boundaries; the evaluation unit is the whole revision (cross-order layer and
cluster rules); and the recorded metrics are deterministic so a clerk can
quantify the risk the revision adds.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ats.memory.store import TradingMemory
from ats.risk import checks as risk_checks
from ats.risk import assess as risk_assess
from ats.schemas.decision import TradeDecision
from ats.schemas.portfolio import ExposureBreakdown, PortfolioSnapshot, Position
from ats.schemas.risk import RiskReview

NOW = datetime.now(timezone.utc)

DEMO_SECTOR = """name: demo
layers:
  - key: LA
    label: A层
    tickers: [{symbol: MU}, {symbol: SO}]
  - key: LB
    label: B层
    tickers: [{symbol: TSM}]
"""

DEMO_RISK = """limits:
  max_position_pct: 0.99
  max_single_order_usd: 100000000
sector_layer_caps:
  demo:
    LA: {weight_cap: 0.25}
    LB: {weight_cap: 0.99}
"""


@pytest.fixture
def demo(tmp_path, monkeypatch):
    """合成 sector：两层结构 + LA 层 25% 上限（不绑任何真实 sector 政策）。"""
    from ats.config import reset_config_cache

    (tmp_path / "sectors").mkdir()
    (tmp_path / "settings.yaml").write_text("environment: paper\n", encoding="utf-8")
    (tmp_path / "risk.yaml").write_text(DEMO_RISK, encoding="utf-8")
    (tmp_path / "sectors" / "demo.yaml").write_text(DEMO_SECTOR, encoding="utf-8")
    monkeypatch.setenv("ATS_CONFIG_DIR", str(tmp_path))
    reset_config_cache()
    monkeypatch.setattr(risk_assess, "_prices", lambda syms: {})  # 无网络聚类
    yield
    reset_config_cache()


def _pos(sym, weight, sector="semis"):
    mv = 1_000_000 * weight
    return Position(symbol=sym, sector=sector, sec_type="STK", qty=10, avg_cost=100.0,
                    market_price=mv / 10, market_value=mv, unrealized_pnl=0.0,
                    weight=weight, beta=1.0)


def _pf(positions):
    return PortfolioSnapshot(as_of=NOW, net_liquidation=1_000_000,
                             cash=1_000_000 - sum(p.market_value for p in positions),
                             gross_exposure=sum(p.market_value for p in positions),
                             daily_pnl=0.0, positions=positions,
                             exposure=ExposureBreakdown())


def _buy(sym, notional):
    return TradeDecision(symbol=sym, action="buy", notional_usd=notional)


# --- 4.1 read-only, whole-revision evaluation ---------------------------------- #

def test_review_does_not_modify_any_order_field(demo):
    pf = _pf([_pos("MU", 0.10)])
    orders = [_buy("MU", 80_000), _buy("SO", 80_000)]
    snapshot = [o.model_copy(deep=True) for o in orders]
    result = risk_checks.review_revision(orders, pf, sector="demo")
    assert [o.model_dump() for o in orders] == [o.model_dump() for o in snapshot]
    assert result.order_verdicts and len(result.order_verdicts) == len(orders)


def test_single_order_cap_becomes_boundary_not_clip(demo):
    """4.3: over-cap order is rejected with the acceptable ceiling, unchanged."""
    pf = _pf([_pos("TSM", 0.05)])
    big = _buy("TSM", 250_000)          # cap 1e8 here; override below instead
    from ats.config import get_config
    rc = get_config().app.risk
    original_cap = rc.max_single_order_usd
    rc.max_single_order_usd = 100_000
    try:
        result = risk_checks.review_revision([big], pf, sector="demo")
    finally:
        rc.max_single_order_usd = original_cap
    assert big.notional_usd == 250_000
    assert result.verdict == "rejected"
    violation = next(v for v in result.violations if v.entity == "TSM")
    assert violation.rule_id == "max_single_order_usd"
    assert violation.limit == 100_000 and violation.actual == 250_000
    assert result.allowed_boundary.max_additional_notional == 100_000


# --- 4.2 store binding quad ------------------------------------------------------ #

def test_review_without_full_binding_is_invalid(tmp_path):
    from ats.decision.repository import (DecisionAuditRepository,
                                         InvalidRiskReviewError)
    from ats.memory.store import TradingMemory

    repo = DecisionAuditRepository(TradingMemory(tmp_path / "b.sqlite"))
    quad = dict(cycle_id="c1", revision_no=1, decision_hash="h",
                ruleset_version="r1", portfolio_snapshot_id="ps1",
                market_as_of="2026-09-23")
    with pytest.raises(InvalidRiskReviewError):
        repo.record_review(review_id="rv1", verdict="approved", **{**quad, "decision_hash": ""})
    with pytest.raises(InvalidRiskReviewError):
        repo.record_review(review_id="rv1", verdict="approved", **{**quad, "ruleset_version": ""})
    with pytest.raises(InvalidRiskReviewError):
        repo.record_review(review_id="rv1", verdict="approved", **{**quad, "portfolio_snapshot_id": ""})
    with pytest.raises(InvalidRiskReviewError):
        repo.record_review(review_id="rv1", verdict="approved", **{**quad, "market_as_of": ""})
    row = repo.record_review(review_id="rv1", verdict="approved", **quad)
    assert row["decision_hash"] == "h"


# --- 4.6 LLM text never overrides hard rules -------------------------------------- #

def test_llm_comment_cannot_flip_a_rejection(demo):
    pf = _pf([_pos("MU", 0.10)])
    orders = [_buy("MU", 80_000), _buy("SO", 80_000)]  # 合并后 LA 26% > 25%
    result = risk_checks.review_revision(
        orders, pf, sector="demo",
        llm_comment="文本判断：风险可接受，建议全额通过")
    assert result.verdict == "rejected"
    assert result.llm_comment == "文本判断：风险可接受，建议全额通过"
    assert any("LLM_COMMENT" in n for n in result.notes)   # recorded as observable


# --- 4.8 decision-level vs portfolio-level isolation ------------------------------- #

def test_decision_reviews_do_not_touch_portfolio_risk_reviews(tmp_path):
    store = TradingMemory(tmp_path / "iso.sqlite")
    from ats.decision.repository import DecisionAuditRepository
    repo = DecisionAuditRepository(store)
    store.save_risk_review(RiskReview(as_of=NOW, risk_state="normal",
                                      notes="portfolio snapshot"))
    for i in range(2):
        repo.record_review(review_id=f"rv{i}", cycle_id="c1", revision_no=1,
                           decision_hash=f"h{i}", ruleset_version="r1",
                           portfolio_snapshot_id="ps1", market_as_of="2026-09-23",
                           verdict="approved")
    rows = store.conn.execute(
        "SELECT * FROM decision_risk_reviews").fetchall()
    assert len(rows) == 2                      # 决策级审查各自留痕
    portfolio = store.conn.execute("SELECT * FROM risk_reviews").fetchall()
    assert len(portfolio) == 1                 # 组合级读取不受影响
    assert portfolio[0]["risk_state"] == "normal"


# --- 4.9 legacy silent rewriter unreachable ----------------------------------------- #

def test_risk_validator_has_no_live_references():
    """`apply_guardrails` 的静默改写路径在默认流程不可达：src 内仅剩定义与墓碑注释。"""
    import subprocess
    result = subprocess.run(
        ["grep", "-rl", "apply_guardrails", "src/", "--include=*.py"],
        cwd="/Users/liuchao/Code/trading/auto_stock_trading_agents",
        capture_output=True, text=True)
    referencing = [p for p in result.stdout.splitlines()
                   if p and "agents/risk_validator.py" not in p]
    assert referencing == [], f"legacy rewriter referenced from {referencing}"


# --- 4.10 cross-order rules on the merged consequence -------------------------------- #

def test_same_layer_orders_judged_together_not_independently(demo):
    """两笔各自合规的买单，合并后果超层限 → 整条修订驳回 + 层边界。"""
    pf = _pf([_pos("MU", 0.10)])
    orders = [_buy("MU", 80_000), _buy("SO", 80_000)]
    # 每一笔单独审：都在 25% 层限内
    for order in orders:
        alone = risk_checks.review_revision([order], pf, sector="demo")
        assert alone.verdict == "approved", order.symbol
    # 合并审：MU 18%、SO 再 +8% = 26% > 25%
    result = risk_checks.review_revision(orders, pf, sector="demo")
    assert result.verdict == "rejected"
    by_symbol = {ov.symbol: ov for ov in result.order_verdicts}
    assert by_symbol["MU"].verdict == "approved"
    assert by_symbol["SO"].verdict == "rejected"
    layer_violation = next(v for v in result.violations
                           if v.rule_id.startswith("new_breach:layer:LA")
                           or v.rule_id.startswith("breach:L1-LA"))
    assert layer_violation.entity == "SO"
    # 边界给出该层的规则标识、上限值与可接受的最大增量
    assert by_symbol["SO"].max_allowed_notional is not None
    # 25% cap - MU 既有 10% - 本单 8% 前的层权重 18% → 剩 7% = $70,000
    assert by_symbol["SO"].max_allowed_notional == pytest.approx(70_000)


def test_cluster_concentration_judged_on_merged_consequence(demo, monkeypatch):
    """相关簇集中度：合并后果超过簇上限 → 驳回并给出簇标识与边界。"""
    from ats.config import get_config, reset_config_cache
    rc = get_config().app.risk
    original = rc.cluster_weight_cap
    rc.cluster_weight_cap = 0.20
    base = [100 + i for i in range(60)]
    monkeypatch.setattr(risk_assess, "_prices",
                        lambda syms: {"MU": base, "SO": [x * 1.01 for x in base]})
    try:
        pf = _pf([_pos("MU", 0.10)])
        orders = [_buy("MU", 80_000), _buy("SO", 80_000)]
        result = risk_checks.review_revision(orders, pf, sector="demo")
    finally:
        rc.cluster_weight_cap = original
    assert result.verdict == "rejected"
    cluster_violation = next(v for v in result.violations
                             if "相关簇" in v.rule_id or "cluster" in v.rule_id)
    assert cluster_violation.severity == "hard"


# --- 4.11 before/after metrics are deterministic and binding-sensitive -------------- #

def test_metrics_are_deterministic_and_move_with_the_revision(demo):
    pf = _pf([_pos("MU", 0.10), _pos("TSM", 0.10)])
    small = [_buy("MU", 10_000)]
    large = [_buy("MU", 10_000), _buy("TSM", 10_000)]
    first = risk_checks.review_revision(small, pf, sector="demo")
    again = risk_checks.review_revision(small, pf, sector="demo")
    assert first.verdict == "approved"
    assert first.before_metrics == again.before_metrics
    assert first.after_metrics == again.after_metrics
    assert first.after_metrics != first.before_metrics   # 修订确实改变了风险
    bigger = risk_checks.review_revision(large, pf, sector="demo")
    assert bigger.after_metrics != first.after_metrics   # 修订内容变化 → 指标变化


def test_metrics_quantify_rejected_revision_risk_change(demo):
    """仅凭审查记录即可说明驳回修订的风险变化幅度（§5.3 用途）。"""
    pf = _pf([_pos("MU", 0.10)])
    orders = [_buy("MU", 80_000), _buy("SO", 80_000)]
    result = risk_checks.review_revision(orders, pf, sector="demo")
    assert result.verdict == "rejected"
    assert result.after_metrics.get("layer:LA", 0) > result.before_metrics.get("layer:LA", 0)


# --- Phase D Group 6: 风控输入收口（6.1–6.4） ---------------------------------------- #

def test_review_records_complete_basis(demo):
    """6.4: 审查记录其依据的组合快照标识、市场 as-of 与规则集版本。"""
    pf = _pf([_pos("MU", 0.10)])
    result = risk_checks.review_revision([_buy("MU", 80_000)], pf, sector="demo")
    assert result.basis is not None
    assert result.basis.portfolio_snapshot_id == risk_checks.portfolio_snapshot_id(pf)
    assert result.basis.market_as_of
    assert result.basis.ruleset_version.startswith("risk-")
    ok, why = risk_checks.usable_for_release(result)
    assert ok and why == "ok"


def test_review_basis_missing_binding_is_not_usable(demo):
    """6.4: 三者缺一即判该审查不可用于放行。"""
    from ats.schemas.risk import ReviewBasis

    pf = _pf([_pos("MU", 0.10)])
    result = risk_checks.review_revision([_buy("MU", 80_000)], pf, sector="demo")

    broken = result.model_copy(update={"basis": ReviewBasis(
        portfolio_snapshot_id="pf:x", market_as_of="", ruleset_version="risk-1")})
    ok, why = risk_checks.usable_for_release(broken)
    assert not ok and "market_as_of" in why

    no_basis = result.model_copy(update={"basis": None})
    ok2, why2 = risk_checks.usable_for_release(no_basis)
    assert not ok2 and why2 == "no_basis_recorded"

    rejected = result.model_copy(update={"verdict": "rejected"})
    ok3, why3 = risk_checks.usable_for_release(rejected)
    assert not ok3 and why3 == "verdict_not_approved"


def test_review_inputs_are_a_closed_set_without_macro(demo):
    """6.2: 确定性审查输入只含提案、组合快照、市场快照与规则包，
    审查结果不引用任何宏观观点。"""
    import pathlib

    pf = _pf([_pos("MU", 0.10)])
    result = risk_checks.review_revision([_buy("MU", 80_000)], pf, sector="demo")
    blob = " ".join(result.notes
                    + [v.detail or "" for v in result.violations]
                    + [result.llm_comment])
    assert "宏观" not in blob and "regime" not in blob.lower()
    assert set(risk_checks.REVIEW_INPUT_KINDS) == {
        "orders", "portfolio_snapshot", "market_snapshot", "ruleset"}
    for py in pathlib.Path("src/ats/risk").rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        assert "latest_macro_review" not in text, py
        assert "agents.macro" not in text, py


def test_ruleset_version_is_shared_between_chief_and_review(demo):
    """6.4: 审查 basis 与 chief 审计行使用同一规则集版本函数，永不互相矛盾。"""
    from ats.graph.chief import _ruleset_version

    assert _ruleset_version() == risk_checks.ruleset_version()


def test_risk_officer_memo_context_has_no_macro(demo):
    """6.1: 风控 memo 上下文不再有宏观 regime / 象限注入。"""
    import pathlib

    from ats.agents.risk_officer import review as risk_review

    pf = _pf([_pos("MU", 0.10)])
    r = risk_assess.assess(pf, sector="demo")
    ctx = risk_review._context(r)
    assert "宏观" not in ctx and "象限" not in ctx and "regime" not in ctx.lower()
    src = pathlib.Path("src/ats/agents/risk_officer/review.py").read_text(encoding="utf-8")
    assert "latest_macro_review" not in src


def test_guard_risk_officer_has_zero_cross_role_reads():
    """6.3: risk_officer 无任何被允许的跨角色读取，模块内无投影读取调用。"""
    import ast
    import pathlib

    from ats.workflow import architecture_guards as guards

    assert guards.ALLOWED_CROSS_ROLE_READS.get("risk_officer") is None
    assert "risk_officer" not in guards.OWNED_PROJECTION_ROLES
    hits = [v for v in guards.scan_agents()
            if v.module.startswith("src/ats/agents/risk")]
    assert hits == []
    for py in pathlib.Path("src/ats/agents/risk").rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                assert name not in guards.PROJECTION_READ_CALLS, f"{py}:{node.lineno}"


def test_authorization_refuses_review_missing_basis_bindings(tmp_path):
    """6.4: 授权门对缺依据绑定的审查行 fail closed。

    record_review 本身已拒绝缺绑定的行（Phase B §5.3）；这里再验证授权门对
    绕过写入路径的残缺行同样拒绝（纵深防御）。
    """
    from ats.decision.repository import DecisionAuditRepository
    from ats.execution.authorization import AuthorizationError, build_authorization

    repo = DecisionAuditRepository(TradingMemory(tmp_path / "auth.sqlite"))
    repo.create_cycle(cycle_id="c1", trigger_source="test", created_at=NOW.isoformat())
    repo.append_revision(cycle_id="c1", orders=[{"symbol": "MU", "action": "buy"}],
                         rationale="", model_version="", created_at=NOW.isoformat())
    rev = repo.latest_revision("c1")
    base = dict(cycle_id="c1", revision_no=rev["revision_no"],
                decision_hash=rev["decision_hash"])
    repo.record_review(review_id="c1:r1:review:r1", verdict="approved",
                       ruleset_version="risk-1", portfolio_snapshot_id="pf:1",
                       market_as_of=NOW.isoformat(), **base)
    repo.record_approval(approval_id="c1:r1:approval:r3", **base,
                         decision="approved", reviewer="boss",
                         idempotency_key="k1", created_at=NOW.isoformat())
    auth = build_authorization(repo, "c1")            # 完整绑定 → 可授权
    assert auth.ruleset_version == "risk-1"

    # 绕过 record_review 的残缺行（created_at 更晚 → 成为 effective review）。
    repo.conn.execute(
        "INSERT INTO decision_risk_reviews (review_id, cycle_id, revision_no, "
        "decision_hash, ruleset_version, portfolio_snapshot_id, market_as_of, "
        "verdict, created_at) VALUES (?,?,?,?,?,?,?,?,'2099-01-01')",
        ("c1:r1:review:r2", "c1", rev["revision_no"], rev["decision_hash"],
         "", "pf:1", NOW.isoformat(), "approved"))
    repo.conn.commit()
    try:
        build_authorization(repo, "c1")
        raise AssertionError("expected AuthorizationError")
    except AuthorizationError as exc:
        assert "basis bindings" in str(exc)
