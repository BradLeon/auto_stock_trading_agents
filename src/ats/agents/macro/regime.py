"""Deterministic regime classification: growth × inflation quadrant + alerts.

Everything in this module is arithmetic over already-fetched series. No LLM, no
I/O — the same discipline the 6-layer risk gate follows, and for the same reason:
a classification that drives portfolio tilts has to be auditable and reproducible,
not re-derived by a model each week.

Design notes that are easy to get wrong later (docs/MACRO_ANALYST.md):
  §6.3  `transition` is mandatory. A classifier forced to pick one of four will
        emit a confident wrong answer exactly at the turning points.
  §4.3  Warsh ended forward guidance, so the front end whipsaws. Entering a
        quadrant needs a wider band than staying in one (hysteresis), and a flip
        is `provisional` until a second consecutive review agrees.
  §5.1  Nominal = real + breakeven is an identity (FRED defines T10YIE that way),
        so the decomposition is exact and free — it is not the model's opinion.
"""

from __future__ import annotations

from ...schemas.macro_strategy import AxisInput, RateDecomposition
from . import indicators as ind

# Fallback calibration only. The live values live in config/macro.yaml `regime:`
# and are merged over this dict key-by-key — TUNE THEM THERE, not here, or your
# edit will be silently overridden by the explicit YAML value.
# They are starting points to be reviewed against real trigger frequency, not
# settled law (flipping every week = too sensitive; never moving = too blunt).
DEFAULTS: dict = {
    "neutral_band": 0.25,      # |axis| below this ⇒ transition (entering)
    "stay_band": 0.15,         # looser band for REMAINING in a confirmed quadrant
    "min_inputs_per_axis": 2,  # fewer live inputs ⇒ insufficient, not a guess
    "confirm_weeks": 2,        # consecutive periods before provisional→confirmed
    "growth": {
        "sahm_bad": 0.50,          # 失业率 3m均值 − 12m最低 ≥ 0.50pp（Sahm 规则）
        "sahm_good": 0.0,
        "nfp_3m_bad_k": 50.0,
        "nfp_3m_good_k": 200.0,
        "initial_claims_bad_pct": 10.0,    # 初请 4 周均值 vs 3 个月前
        "continuing_claims_bad_pct": 8.0,
        "cfnai_bad": -0.35,        # CFNAI-MA3
        "cfnai_good": 0.20,
    },
    "inflation": {
        "pce_momentum_pp": 0.3,    # 核心 PCE 3m年化 − 12m YoY
        # Saturation point, not a trigger: at 0.5 a z of -0.51 and a z of -3.0
        # both scored -1.0, making the input effectively binary and letting one
        # ordinary reading swing the whole axis. 1.5σ is a real move.
        "breakeven_z": 1.5,        # 10y 通胀补偿 z(3y)
        "energy_3m_pct": 15.0,     # 汽油/原油 3 个月变化
    },
    "decomposition": {
        # A ±4bp leg is noise, but the 4-way table has no dead zone, so it was
        # reporting "通胀补偿↑ = 最差组合" off a flat breakeven.
        "flat_bp": 5.0,
    },
    "alerts": {
        "credit_widen_bp": 50.0,       # HY OAS 1 个月走阔
        "equity_near_high_pct": 3.0,   # SPX 距 3 个月高点
        "worst_combo_real_bp": 20.0,
        "worst_combo_breakeven_bp": 15.0,
        "bond_vol_z": 1.5,
    },
    "shock_vs_trend": {"shock_z": 2.0, "trend_z": 1.0},
}

# Which indicators deserve attention in each quadrant (§6.4). The 1-8 priority
# the framework starts from is a DEFAULT attention budget, not a fixed weight —
# credit spreads dominate everything during a credit event and carry almost no
# information in a calm one.
FOCUS_BY_QUADRANT: dict[str, list[str]] = {
    "goldilocks":  ["real_10y", "hy_oas"],
    "reflation":   ["breakeven_10y", "oil_wti", "ust_2y"],
    "stagflation": ["oil_wti", "real_10y", "hy_oas"],
    "deflation":   ["hy_oas", "ust_2y", "real_10y"],
    "transition":  [],
}


def _cfg(overrides: dict | None) -> dict:
    """Shallow-merge user config over DEFAULTS, one level into each sub-dict."""
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    for key, val in (overrides or {}).items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key].update(val)
        else:
            out[key] = val
    return out


def _scaled(value: float, at_minus1: float, at_plus1: float) -> float:
    """Linear map with clipping: `at_minus1`→-1, `at_plus1`→+1.

    Handles inverted mappings (at_plus1 < at_minus1) so "higher is worse" inputs
    like jobless claims use the same helper as "higher is better" ones.
    """
    if at_plus1 == at_minus1:
        return 0.0
    t = (value - at_minus1) / (at_plus1 - at_minus1)
    return max(-1.0, min(1.0, -1.0 + 2.0 * t))


def _pct_change_vs(points, days: int) -> float | None:
    from datetime import timedelta

    if not points:
        return None
    last_date, level = points[-1]
    prior = ind.value_asof(points, last_date - timedelta(days=days))
    if prior in (None, 0):
        return None
    return (level / prior - 1) * 100


# --------------------------------------------------------------------------- #
# Growth axis
# --------------------------------------------------------------------------- #
def growth_axis(series: dict, cfg: dict) -> tuple[float, list[AxisInput]]:
    g = cfg["growth"]
    inputs: list[AxisInput] = []

    # Sahm rule: unemployment 3-month mean minus its trailing 12-month low. A
    # published, widely-used recession rule — preferred over inventing a threshold.
    un = ind.as_points(series.get("unemployment"))
    if len(un) >= 12:
        recent3 = ind.moving_average(un, 3)
        low12 = min(v for _d, v in un[-12:])
        if recent3 is not None:
            sahm = recent3 - low12
            # ONE-SIDED on purpose. Sahm is current-minus-trailing-min, so it can
            # never go below 0 — a two-sided map would score every ordinary month
            # (gap 0.1-0.2) as "growth improving", quietly biasing the axis up.
            # It detects deterioration; absence of deterioration is 0, not good news.
            score = -min(1.0, max(0.0, sahm / g["sahm_bad"])) if g["sahm_bad"] else 0.0
            inputs.append(AxisInput(
                key="sahm", label="Sahm 规则（失业率3m均值−12m最低）",
                value=round(sahm, 2),
                threshold=f"≥ +{g['sahm_bad']:.2f}pp 判恶化（单边，0=无信号）",
                score=round(score, 3), note="公认的衰退识别规则，只报警不报喜"))

    # A single payroll print is revision-prone.  Use the latest three monthly
    # PAYEMS changes so a later catch-up run sees the new release but does not
    # let one noisy month flip the growth call by itself.
    payrolls = ind.as_points(series.get("payrolls"))
    if len(payrolls) >= 4:
        monthly_changes = [payrolls[i][1] - payrolls[i - 1][1]
                           for i in range(len(payrolls) - 3, len(payrolls))]
        avg3 = sum(monthly_changes) / len(monthly_changes)
        inputs.append(AxisInput(
            key="nfp_3m_avg", label="非农新增 3 个月均值",
            value=round(avg3, 1),
            threshold=(f"<{g['nfp_3m_bad_k']:.0f}k 判恶化 / "
                       f">{g['nfp_3m_good_k']:.0f}k 判改善"),
            score=_scaled(avg3, g["nfp_3m_bad_k"], g["nfp_3m_good_k"]),
            note="PAYEMS 月差；3 个月均值降低单月发布与修订噪音"))

    # Claims: weekly and timely, but unusable raw — 4-week mean vs 3 months ago.
    ic = ind.as_points(series.get("initial_claims"))
    if len(ic) >= 4:
        ma_now = ind.moving_average(ic, 4)
        from datetime import timedelta
        prior = ind.value_asof(ic, ic[-1][0] - timedelta(days=90))
        if ma_now is not None and prior:
            chg = (ma_now / prior - 1) * 100
            inputs.append(AxisInput(
                key="initial_claims", label="初请失业金 4 周均值 vs 3 个月前",
                value=round(chg, 1), threshold=f"↑>{g['initial_claims_bad_pct']:.0f}% 判恶化",
                score=_scaled(chg, g["initial_claims_bad_pct"],
                              -g["initial_claims_bad_pct"])))

    # Continuing claims: how hard it is to get re-hired — turns before the
    # unemployment rate does.
    cc = ind.as_points(series.get("continuing_claims"))
    chg_cc = _pct_change_vs(cc, 90)
    if chg_cc is not None:
        inputs.append(AxisInput(
            key="continuing_claims", label="续请失业金 vs 3 个月前",
            value=round(chg_cc, 1), threshold=f"↑>{g['continuing_claims_bad_pct']:.0f}% 判恶化",
            score=_scaled(chg_cc, g["continuing_claims_bad_pct"],
                          -g["continuing_claims_bad_pct"]),
            note="再就业难度，劳动力市场转弱的早期信号"))

    cf = ind.as_points(series.get("cfnai"))
    ma3 = ind.moving_average(cf, 3)
    if ma3 is not None:
        inputs.append(AxisInput(
            key="cfnai", label="CFNAI-MA3（广义活动）", value=round(ma3, 2),
            threshold=f"<{g['cfnai_bad']} 判恶化 / >{g['cfnai_good']} 判改善",
            score=_scaled(ma3, g["cfnai_bad"], g["cfnai_good"])))

    return _axis_score(inputs), inputs


# --------------------------------------------------------------------------- #
# Inflation axis
# --------------------------------------------------------------------------- #
def inflation_axis(series: dict, readings_by_key: dict, cfg: dict
                   ) -> tuple[float, list[AxisInput]]:
    f = cfg["inflation"]
    inputs: list[AxisInput] = []

    # Core PCE momentum: 3-month annualised vs 12-month YoY. YoY alone is far too
    # slow to show a turn — the Fed watches the短期 annualised run rate.
    pce = ind.as_points(series.get("core_pce"))
    if len(pce) >= 13:
        ann3 = ((pce[-1][1] / pce[-4][1]) ** 4 - 1) * 100
        yoy = (pce[-1][1] / pce[-13][1] - 1) * 100
        gap = ann3 - yoy
        inputs.append(AxisInput(
            key="pce_momentum", label="核心PCE 3m年化 − 12m YoY",
            value=round(gap, 2), threshold=f"±{f['pce_momentum_pp']}pp",
            score=_scaled(gap, -f["pce_momentum_pp"], f["pce_momentum_pp"]),
            note=f"3m年化 {ann3:.2f}% vs YoY {yoy:.2f}%"))

    be = readings_by_key.get("breakeven_10y")
    if be is not None and be.z_3y is not None:
        inputs.append(AxisInput(
            key="breakeven_z", label="10y 通胀补偿 z(3y)", value=be.z_3y,
            threshold=f"±{f['breakeven_z']}",
            score=_scaled(be.z_3y, -f["breakeven_z"], f["breakeven_z"]),
            note="市场定价的长期通胀"))

    # Energy: gasoline is closer to what households and politics feel than crude.
    energy = ind.as_points(series.get("gasoline")) or ind.as_points(series.get("oil_wti"))
    chg = _pct_change_vs(energy, 90)
    if chg is not None:
        inputs.append(AxisInput(
            key="energy_3m", label="能源价格 3 个月变化", value=round(chg, 1),
            threshold=f"±{f['energy_3m_pct']:.0f}%",
            score=_scaled(chg, -f["energy_3m_pct"], f["energy_3m_pct"])))

    return _axis_score(inputs), inputs


def _axis_score(inputs: list[AxisInput]) -> float:
    if not inputs:
        return 0.0
    return round(sum(i.score for i in inputs) / len(inputs), 3)


def _contradictory(inputs: list[AxisInput]) -> bool:
    """True when an axis's own inputs point opposite ways with conviction.

    Averaging these to ~0 and calling it "neutral" would hide a real
    disagreement; §6.3 says that case is `transition`, not a confident middle.
    """
    return any(i.score <= -0.5 for i in inputs) and any(i.score >= 0.5 for i in inputs)


# --------------------------------------------------------------------------- #
# Quadrant
# --------------------------------------------------------------------------- #
def _quadrant_from_axes(growth: float, inflation: float, band: float) -> str:
    if abs(growth) < band or abs(inflation) < band:
        return "transition"
    if growth >= 0:
        return "reflation" if inflation > 0 else "goldilocks"
    return "stagflation" if inflation > 0 else "deflation"


def classify(growth: float, inflation: float, g_inputs: list[AxisInput],
             f_inputs: list[AxisInput], cfg: dict, *,
             prior_quadrant: str | None = None, prior_state: str = "insufficient",
             prior_weeks: int = 0) -> tuple[str, str, int, str]:
    """→ (quadrant, quadrant_state, weeks_in_quadrant, reason).

    Hysteresis: entering a quadrant takes `neutral_band`, staying in an already
    confirmed one only takes `stay_band`. Without it the front end's post-forward-
    guidance noise would flip the call every other week (§4.3).

    `reason` separates the three ways we end up with no call. They are not
    interchangeable: "the inputs are thin" and "the inputs are complete and
    genuinely say nothing" ask different things of the reader.
    """
    min_n = cfg["min_inputs_per_axis"]
    if len(g_inputs) < min_n or len(f_inputs) < min_n:
        return ("transition", "insufficient", 0,
                f"输入不足（增长 {len(g_inputs)} 项 / 通胀 {len(f_inputs)} 项，"
                f"每轴至少需 {min_n} 项）")
    if _contradictory(g_inputs) or _contradictory(f_inputs):
        axis = "增长" if _contradictory(g_inputs) else "通胀"
        return ("transition", "insufficient", 0,
                f"{axis}轴内部输入互相矛盾（同时存在 ≤-0.5 与 ≥+0.5 的输入），"
                "取均值会掩盖真实分歧")

    cand = _quadrant_from_axes(growth, inflation, cfg["neutral_band"])
    if (cand == "transition" and prior_state == "confirmed"
            and prior_quadrant not in (None, "transition")
            and _quadrant_from_axes(growth, inflation, cfg["stay_band"]) == prior_quadrant):
        return (prior_quadrant, "confirmed", prior_weeks + 1,
                f"信号回落至进入门槛以下但仍高于维持门槛 {cfg['stay_band']}，"
                "按迟滞规则维持原判定")

    if cand == "transition":
        weak = "增长" if abs(growth) < cfg["neutral_band"] else "通胀"
        return ("transition", "insufficient", 0,
                f"信号中性：{weak}轴 |分数| 低于进入门槛 {cfg['neutral_band']}"
                f"（增长 {growth:+.2f} / 通胀 {inflation:+.2f}）—— 数据齐全但确实看不清")

    weeks = prior_weeks + 1 if cand == prior_quadrant else 1
    state = "confirmed" if weeks >= cfg["confirm_weeks"] else "provisional"
    reason = "连续同向，已确认" if state == "confirmed" else "首次成立，需下期复核后确认"
    return cand, state, weeks, reason


# --------------------------------------------------------------------------- #
# Rate decomposition (§5.1 / §5.2)
# --------------------------------------------------------------------------- #
_DECOMP_TABLE = {
    (True, True):   ("实际↑ + 通胀补偿↑ —— 最差组合",
                     "估值与盈利同时承压：真实资本成本上升，同时通胀风险抬头"),
    (True, False):  ("实际↑ + 通胀补偿↓ —— 实际利率驱动的紧缩",
                     "纯估值压缩，长久期成长股最受伤（本组合的主要风险形态）"),
    (False, True):  ("实际↓ + 通胀补偿↑ —— 通胀预期驱动",
                     "名义宽松但通胀风险上升，需观察是否传导到成本端"),
    (False, False): ("实际↓ + 通胀补偿↓ —— 通缩/衰退担忧",
                     "估值受益，但盈利端有风险，不可单独视为利好"),
}


def decompose(readings_by_key: dict, growth: float, inflation: float,
              cfg: dict, *, window_days: int = 30) -> RateDecomposition | None:
    real = readings_by_key.get("real_10y")
    be = readings_by_key.get("breakeven_10y")
    nom = readings_by_key.get("ust_10y")
    if real is None or be is None or real.d_1m is None or be.d_1m is None:
        return None

    d_real, d_be = real.d_1m, be.d_1m
    flat = cfg.get("decomposition", {}).get("flat_bp", 5.0)
    # Only call a leg directional if it actually moved — otherwise a +4bp
    # breakeven gets read as rising inflation expectations and drags the whole
    # classification into "worst combination".
    if abs(d_real) < flat and abs(d_be) < flat:
        label = f"实际与通胀补偿均基本持平（|Δ| < {flat:.0f}bp）"
        equity = "利率端本期无实质变化，不构成估值层面的新信息"
    elif abs(d_be) < flat:
        label = f"实际{'↑' if d_real > 0 else '↓'} + 通胀补偿持平 —— 纯实际利率变动"
        equity = ("真实资本成本上升，长久期成长股估值承压" if d_real > 0
                  else "真实资本成本下降，长久期成长股估值受益")
    elif abs(d_real) < flat:
        label = f"实际持平 + 通胀补偿{'↑' if d_be > 0 else '↓'} —— 纯通胀预期变动"
        equity = ("通胀预期抬头但真实成本未变，关注是否传导到成本端" if d_be > 0
                  else "通胀预期回落，真实成本未变")
    else:
        label, equity = _DECOMP_TABLE[(d_real > 0, d_be > 0)]

    # §5.2 — a falling real yield is benign disinflation or a recession bid, and
    # the two have opposite implications. Cross-reference the growth axis rather
    # than letting the model pick a story.
    cause = ""
    if d_real < 0:
        band = cfg["neutral_band"]
        if growth >= -band and inflation < 0:
            cause = "良性反通胀（增长未恶化 + 通胀下行）—— 估值扩张有基础"
        elif growth < -band:
            cause = "衰退驱动（增长轴恶化）—— 估值受益但盈利下修在路上，不构成买入理由"
        else:
            cause = "成因未定（增长/通胀信号不一致）—— 不做二选一"

    return RateDecomposition(
        d_nominal_bp=nom.d_1m if nom else (round(d_real + d_be, 1)),
        d_real_bp=d_real, d_breakeven_bp=d_be, window_days=window_days,
        classification=label, equity_read=equity, real_yield_cause=cause)


# --------------------------------------------------------------------------- #
# Shock vs trend (§5.3)
# --------------------------------------------------------------------------- #
def shock_vs_trend(series: dict, readings_by_key: dict, cfg: dict,
                   growth: float) -> list[str]:
    """Which moves look like one-off shocks vs. persistent trends.

    This is the Warsh lens made explicit: the Fed looks through shocks and
    responds to trends, so we have to make the same distinction ourselves now
    that forward guidance no longer does it for us.
    """
    s = cfg["shock_vs_trend"]
    out: list[str] = []
    for key in ("real_10y", "ust_2y", "hy_oas", "breakeven_10y", "oil_wti"):
        points = ind.as_points(series.get(key))
        if not points:
            continue
        z1m, z3m = ind.change_z(points, 30), ind.change_z(points, 90)
        if z1m is None:
            continue
        label = (readings_by_key.get(key).label if readings_by_key.get(key) else key) or key
        if abs(z1m) > s["shock_z"] and (z3m is None or abs(z3m) < s["trend_z"]):
            out.append(f"{label}: 冲击特征（1m z={z1m:+.1f}，3m 未成形）—— 美联储可能看穿")
        elif z3m is not None and abs(z3m) > s["trend_z"] and (z1m > 0) == (z3m > 0):
            out.append(f"{label}: 趋势特征（1m z={z1m:+.1f} / 3m z={z3m:+.1f}）—— 美联储会反应")

    # Oil's cause matters more than its direction: a supply shock is stagflationary,
    # demand-pull is not.
    oil, dxy = readings_by_key.get("oil_wti"), readings_by_key.get("dxy")
    if oil is not None and oil.d_3m is not None and oil.d_3m > 0:
        if dxy is not None and dxy.d_3m is not None and dxy.d_3m > 0 and growth <= 0:
            out.append("油价↑ + 美元↑ + 增长未改善 → 供给冲击（滞胀方向，消费/航空承压）")
        elif growth > 0:
            out.append("油价↑ + 增长改善 → 需求拉动（再通胀方向，含义温和）")
    return out


# --------------------------------------------------------------------------- #
# Deterministic alerts (§6.5)
# --------------------------------------------------------------------------- #
def alerts(series: dict, readings_by_key: dict, cfg: dict) -> list[str]:
    a = cfg["alerts"]
    out: list[str] = []

    # Credit widening while equities sit near highs — credit has historically
    # priced this kind of risk before equities did.
    hy = readings_by_key.get("hy_oas")
    spx = ind.as_points(series.get("spx"))
    if hy is not None and hy.d_1m is not None and hy.d_1m > a["credit_widen_bp"] and spx:
        from datetime import timedelta

        last_date, level = spx[-1]
        window = [v for d, v in spx if d >= last_date - timedelta(days=90)]
        if window:
            high = max(window)
            if high > 0 and (high - level) / high * 100 < a["equity_near_high_pct"]:
                out.append(f"信用-股票背离：HY 利差 1m 走阔 {hy.d_1m:+.0f}bp，"
                           f"而 SPX 距 3 个月高点仅 {(high - level) / high * 100:.1f}%")

    real, be = readings_by_key.get("real_10y"), readings_by_key.get("breakeven_10y")
    if (real is not None and be is not None
            and real.d_1m is not None and be.d_1m is not None
            and real.d_1m > a["worst_combo_real_bp"]
            and be.d_1m > a["worst_combo_breakeven_bp"]):
        out.append(f"最差组合成形：实际 {real.d_1m:+.0f}bp + 通胀补偿 {be.d_1m:+.0f}bp"
                   f"（估值与盈利同时承压）")

    # Rates flat but bond vol spiking still tightens financing conditions.
    move = readings_by_key.get("move")
    if (move is not None and move.z_3y is not None and move.z_3y > a["bond_vol_z"]
            and real is not None and real.d_1m is not None and abs(real.d_1m) < 20):
        out.append(f"债市波动骤升：MOVE z={move.z_3y:+.1f} 而实际收益率基本未动"
                   f"—— 利率水平没变但融资环境仍在收紧")
    return out


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def assess(series: dict, readings: list, *, overrides: dict | None = None,
           prior_quadrant: str | None = None, prior_state: str = "insufficient",
           prior_weeks: int = 0) -> dict:
    """Run the whole deterministic layer. Returns MacroReview-shaped kwargs."""
    cfg = _cfg(overrides)
    by_key = {r.key: r for r in readings}

    growth, g_inputs = growth_axis(series, cfg)
    inflation, f_inputs = inflation_axis(series, by_key, cfg)
    quadrant, state, weeks, reason = classify(
        growth, inflation, g_inputs, f_inputs, cfg,
        prior_quadrant=prior_quadrant, prior_state=prior_state, prior_weeks=prior_weeks)

    return {
        "quadrant": quadrant, "quadrant_state": state, "quadrant_weeks": weeks,
        "quadrant_reason": reason,
        "growth_axis": growth, "inflation_axis": inflation,
        "axis_inputs": g_inputs + f_inputs,
        "indicators": readings,
        "decomposition": decompose(by_key, growth, inflation, cfg),
        "shock_vs_trend": shock_vs_trend(series, by_key, cfg, growth),
        "alerts": alerts(series, by_key, cfg),
        "focus_keys": FOCUS_BY_QUADRANT.get(quadrant, []),
    }
