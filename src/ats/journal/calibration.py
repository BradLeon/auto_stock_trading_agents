"""Stage D — the calibration report: `系统校准-<YYYY-MM>.md` / `系统校准-<YYYY>Q<n>.md`.

Fully deterministic statistics, no LLM. Produces a list of `EvidenceBlock`s (the
schema's report-layer contract) — pre-aggregated tables plus the strongest
counterexamples, never raw rows, because an LLM handed raw rows finds patterns in
noise (this is exactly what the critic agent in Stage E consumes downstream).

The main reason this stage matters more than P&L reporting: calibration accrues per
SCORE (~13 targets x 4 quarters ~= 52/yr) regardless of whether a trade followed,
while P&L accrues per FILL (a handful so far). It is the fast feedback loop.

Every block carries n and a `sufficient` gate; the renderer prints "样本不足，不作
结论" below it rather than a bare, possibly-noise-driven number. Nothing here is
auto-applied — block 2's threshold sweep and blocks 6/7's gate audits are advisory
only, same standing rule as everywhere else in this system.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from ..schemas.journal import EvidenceBlock


def _counterexamples(pairs: list[tuple[str, float, float]], k: int = 3) -> list[str]:
    """pairs = (id, expected_signed_value, actual_signed_value). Returns bare IDs
    (episode_id / prediction_id — see EvidenceBlock.counterexamples' own field
    comment) for the strongest disagreements — expected and actual point opposite
    ways — largest |actual| first. Only given the mean, an LLM (or a human skimming)
    will read n=14 as a rule; a forced counterexample is what surfaces "but twice it
    went the other way". Formatting into a human-readable line happens at render
    time (`_fmt_counterexample`), not here — a bare ID is what lets a downstream
    consumer (Stage E's critic) resolve it back to the full record, not just read it."""
    bad = [(abs(a), i, e, a) for i, e, a in pairs if e * a < 0]
    bad.sort(key=lambda t: -t[0])
    return [i for _, i, _, _ in bad[:k]]


def _group_outlier_counterexamples(groups: dict[str, list[tuple[str, float]]],
                                   k: int = 3) -> list[str]:
    """groups = {label: [(id, value), ...]}. Bare IDs of whichever items deviate
    most from THEIR OWN group's mean — the case(s) that most contradict that
    group's story, largest deviation first. Needs >=2 items in a group to have a
    mean worth deviating from."""
    scored = []
    for items in groups.values():
        if len(items) < 2:
            continue
        mean = sum(v for _, v in items) / len(items)
        scored += [(abs(v - mean), id_) for id_, v in items]
    scored.sort(key=lambda t: -t[0])
    return [id_ for _, id_ in scored[:k]]


# --------------------------------------------------------------------------- #
# 1. 打分 band → 后续超额收益（按打分次数累积，不需要有交易）
# --------------------------------------------------------------------------- #
def _band_calibration(store) -> EvidenceBlock:
    from ..config import get_config

    horizons = get_config().app.journal.horizons or [1, 5, 20, 60]
    primary_h = horizons[0]
    preds = store.all_predictions(source="pead_score")
    per_band_excess: dict[str, dict[int, list[float]]] = {}
    per_band_n: dict[str, int] = {}
    primary_pairs: list[tuple[str, float, float]] = []
    n_open = n_closed = 0
    for p in preds:
        outs = {o.horizon_days: o for o in store.prediction_outcomes(p.prediction_id)}
        if not outs:
            n_open += 1
            continue
        n_closed += 1
        band = p.predicted_band or "（未分档）"
        per_band_n[band] = per_band_n.get(band, 0) + 1
        for h, o in outs.items():
            if o.excess_vs_sector_pct is not None:
                per_band_excess.setdefault(band, {}).setdefault(h, []).append(o.excess_vs_sector_pct)
        o1 = outs.get(primary_h)
        if o1 is not None and o1.excess_vs_sector_pct is not None and p.predicted_value is not None:
            primary_pairs.append((p.prediction_id, p.predicted_value, o1.excess_vs_sector_pct))

    rows = []
    for band, by_h in per_band_excess.items():
        row = {"band": band, "n": per_band_n[band]}
        for h in horizons:
            vals = by_h.get(h, [])
            row[f"T+{h}均值超额%"] = round(sum(vals) / len(vals), 2) if vals else None
        rows.append(row)
    key0 = f"T+{primary_h}均值超额%"
    rows.sort(key=lambda r: r[key0] if r[key0] is not None else -999, reverse=True)

    return EvidenceBlock(
        question=f"打分卡 band 能否单调预测 T+{primary_h} 相对板块的超额收益？"
                f"（按打分次数累积，未到期的不计入均值）",
        table=rows, n_closed=n_closed, n_open=n_open, n_min=10,
        counterexamples=_counterexamples(primary_pairs))


# --------------------------------------------------------------------------- #
# 2. 门槛扫描（反事实，只作建议）
# --------------------------------------------------------------------------- #
def _threshold_sweep(store) -> EvidenceBlock:
    from ..config import get_config

    horizons = get_config().app.journal.horizons or [1, 5, 20, 60]
    primary_h = horizons[0]
    preds = [p for p in store.all_predictions(source="pead_score") if p.predicted_value is not None]
    rows = []
    for thr in (0.8, 1.0, 1.2, 1.5):
        cleared = [p for p in preds if p.predicted_value >= thr]
        vals = []
        for p in cleared:
            outs = {o.horizon_days: o for o in store.prediction_outcomes(p.prediction_id)}
            o = outs.get(primary_h)
            if o is not None and o.excess_vs_sector_pct is not None:
                vals.append(o.excess_vs_sector_pct)
        rows.append({
            "long_threshold": thr, "会放行次数": len(cleared),
            "其中已到期": len(vals),
            f"T+{primary_h}均值超额%": round(sum(vals) / len(vals), 2) if vals else None,
        })
    return EvidenceBlock(
        question="long_threshold 取 {0.8, 1.0, 1.2, 1.5} 时各自放行多少次、"
                "后续表现如何？—— 仅供参考，不自动调参",
        table=rows, n_closed=len(preds), n_open=0, n_min=20)


# --------------------------------------------------------------------------- #
# 3. expected_move 校准（→ 直接调 L6 事件闸）
# --------------------------------------------------------------------------- #
def _expected_move_calibration(store) -> EvidenceBlock:
    preds = store.all_predictions(source="expected_move")
    diffs: list[tuple[str, float, float]] = []
    n_open = n_closed = 0
    for p in preds:
        outs = {o.horizon_days: o for o in store.prediction_outcomes(p.prediction_id)}
        o = outs.get(1)
        if o is None or o.realized_pct is None or p.predicted_value is None:
            n_open += 1
            continue
        n_closed += 1
        diffs.append((p.prediction_id, p.predicted_value, abs(o.realized_pct)))
    avg_pred = round(sum(d[1] for d in diffs) / len(diffs), 2) if diffs else None
    avg_real = round(sum(d[2] for d in diffs) / len(diffs), 2) if diffs else None
    ratio = round(avg_real / avg_pred, 2) if avg_pred else None
    table = [{"预测隐含波幅均值%": avg_pred, "实现|T+1|均值%": avg_real, "实现/预测比值": ratio,
             "n": len(diffs)}]
    # counterexamples here are UNDER-priced surprises (realized far > predicted) —
    # events the L6 gate should have sized larger, not the ratio's sign.
    diffs.sort(key=lambda d: d[1] - d[2])
    counterexamples = [pid for pid, pv, rv in diffs[:3] if rv > pv]
    return EvidenceBlock(
        question="期权隐含预期波幅 vs 实现 |T+1| 涨跌幅的比值 —— 决定 L6 事件闸松紧",
        table=table, n_closed=n_closed, n_open=n_open, n_min=20,
        counterexamples=counterexamples)


# --------------------------------------------------------------------------- #
# 4. consensus 目标价校准
# --------------------------------------------------------------------------- #
def _consensus_pt_calibration(store) -> EvidenceBlock:
    from ..config import get_config

    horizons = get_config().app.journal.horizons or [1, 5, 20, 60]
    primary_h = max(horizons)
    preds = store.all_predictions(source="consensus_pt")
    pairs: list[tuple[str, float, float]] = []
    n_open = n_closed = 0
    for p in preds:
        outs = {o.horizon_days: o for o in store.prediction_outcomes(p.prediction_id)}
        o = outs.get(primary_h)
        if o is None or o.realized_pct is None or p.predicted_value is None:
            n_open += 1
            continue
        n_closed += 1
        pairs.append((p.prediction_id, p.predicted_value, o.realized_pct))
    avg_gap = round(sum(pv for _, pv, _ in pairs) / len(pairs), 2) if pairs else None
    avg_real = round(sum(rv for _, _, rv in pairs) / len(pairs), 2) if pairs else None
    capture = round(avg_real / avg_gap, 2) if avg_gap else None
    table = [{"共识PT隐含涨幅均值%": avg_gap, f"T+{primary_h}实现涨幅均值%": avg_real,
             "捕获率(实现/隐含)": capture, "n": len(pairs)}]
    return EvidenceBlock(
        question=f"卖方共识目标价隐含涨幅 vs T+{primary_h} 实际涨幅 —— 共识有没有系统性偏差？",
        table=table, n_closed=n_closed, n_open=n_open, n_min=20,
        counterexamples=_counterexamples(pairs))


# --------------------------------------------------------------------------- #
# 5. 按 setup 的期望值 / MFE:|MAE|（依赖 Stage B）
# --------------------------------------------------------------------------- #
def _setup_expectancy(store) -> EvidenceBlock:
    episodes = store.list_episodes(limit=100_000)
    gradeable = [e for e in episodes if e.decision_gradeable]
    closed = [e for e in gradeable if e.status == "closed"]
    open_ = [e for e in gradeable if e.status == "open"]
    by_setup: dict[str, list] = {}
    for e in closed:
        by_setup.setdefault(e.setup, []).append(e)
    rows = []
    pnl_groups: dict[str, list[tuple[str, float]]] = {}
    for setup, eps in by_setup.items():
        pnls = [e.realized_pnl for e in eps if e.realized_pnl is not None]
        wins = [x for x in pnls if x > 0]
        mfe_mae = [(e.mfe_pct, abs(e.mae_pct)) for e in eps
                  if e.mfe_pct is not None and e.mae_pct]
        mae_sum = sum(a for _, a in mfe_mae)
        rows.append({
            "setup": setup, "n": len(eps),
            "win_rate": round(len(wins) / len(pnls), 2) if pnls else None,
            "均值盈亏$": round(sum(pnls) / len(pnls), 0) if pnls else None,
            "MFE:|MAE|": round(sum(m for m, _ in mfe_mae) / mae_sum, 2) if mae_sum else None,
        })
        pnl_groups[setup] = [(e.episode_id, e.realized_pnl) for e in eps if e.realized_pnl is not None]
    rows.sort(key=lambda r: -(r["n"] or 0))
    return EvidenceBlock(
        question="按 setup 分类的胜率 / 均值盈亏 / MFE:|MAE| —— 哪类值得加码，哪类该收紧？",
        table=rows, n_closed=len(closed), n_open=len(open_), n_min=20,
        counterexamples=_group_outlier_counterexamples(pnl_groups))


# --------------------------------------------------------------------------- #
# 6. 风控闸审计：被削减/拦下的意图，事后走成什么
# --------------------------------------------------------------------------- #
def _risk_gate_audit(store) -> EvidenceBlock:
    episodes_by_entry = {e.primary_entry_id: e for e in store.list_episodes(limit=100_000)
                         if e.decision_gradeable and e.status == "closed"}
    entries = store.journal_entries(limit=5000)
    gated: list[tuple[str, float]] = []
    ungated: list[tuple[str, float]] = []
    for entry in entries:
        ep = episodes_by_entry.get(entry.entry_id)
        if ep is None or ep.realized_pnl is None:
            continue
        (gated if entry.risk_notes else ungated).append((ep.episode_id, ep.realized_pnl))

    def _stats(label: str, pairs: list[tuple[str, float]]) -> dict:
        if not pairs:
            return {"分组": label, "n": 0, "win_rate": None, "均值盈亏$": None}
        pnls = [p for _, p in pairs]
        wins = [p for p in pnls if p > 0]
        return {"分组": label, "n": len(pnls), "win_rate": round(len(wins) / len(pnls), 2),
                "均值盈亏$": round(sum(pnls) / len(pnls), 0)}

    table = [_stats("风控介入过（削减/预警）", gated), _stats("风控未介入", ungated)]
    counterexamples = _group_outlier_counterexamples({"gated": gated, "ungated": ungated})
    return EvidenceBlock(
        question="风控闸介入过的意图，事后走成什么样——保护了下行，还是错杀了盈利单？",
        table=table, n_closed=len(gated) + len(ungated), n_open=0, n_min=10,
        counterexamples=counterexamples)


# --------------------------------------------------------------------------- #
# 7. 人审闸审计：被否决的提议，事后走成什么（中性呈现）
# --------------------------------------------------------------------------- #
def _human_gate_audit(store) -> EvidenceBlock:
    from ..config import get_config
    from . import prices

    horizons = get_config().app.journal.horizons or [1, 5, 20, 60]
    h = max(horizons)
    entries = store.journal_entries(limit=5000)
    rows_data: list[tuple[str, str, float]] = []
    for entry in entries:
        ap = entry.approval
        if ap is None or not ap.diverged or entry.symbol not in ap.dropped_symbols:
            continue
        start = entry.as_of.date()
        bars = prices.bars(entry.symbol)
        idx = next((i for i, b in enumerate(bars) if b.date >= start), None)
        if idx is None or idx + h >= len(bars):
            continue
        entry_px, fwd_px = bars[idx].close, bars[idx + h].close
        if not entry_px:
            continue
        move_pct = round((fwd_px / entry_px - 1) * 100, 2)
        direction = 1 if entry.action in ("buy", "add") else -1
        rows_data.append((entry.entry_id, entry.symbol, round(move_pct * direction, 2)))

    n = len(rows_data)
    would_win = [r for r in rows_data if r[2] > 0]
    table = [{
        "n": n,
        "若按提议方向持有会赢的比例": round(len(would_win) / n, 2) if n else None,
        f"若按提议方向持有T+{h}均值涨跌%": round(sum(r[2] for r in rows_data) / n, 2) if n else None,
    }]
    strongest = sorted(rows_data, key=lambda r: -r[2])[:3]
    counterexamples = [eid for eid, _sym, pct in strongest if pct > 0]
    return EvidenceBlock(
        question="人审否决的提议，若仍按提议方向持有，事后是赢是亏？"
                "（中性呈现——否决可能对，也可能是防住了这里没测到的别的风险）",
        table=table, n_closed=n, n_open=0, n_min=10, counterexamples=counterexamples)


# --------------------------------------------------------------------------- #
# assemble + render + write
# --------------------------------------------------------------------------- #
def build_evidence_blocks(store=None) -> list[EvidenceBlock]:
    from ..memory import get_store

    store = store or get_store()
    return [
        _band_calibration(store),
        _threshold_sweep(store),
        _expected_move_calibration(store),
        _consensus_pt_calibration(store),
        _setup_expectancy(store),
        _risk_gate_audit(store),
        _human_gate_audit(store),
    ]


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def _render_table(rows: list[dict]) -> str:
    if not rows:
        return "（无数据）"
    cols = list(rows[0].keys())
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        lines.append("| " + " | ".join(_fmt(r.get(c)) for c in cols) + " |")
    return "\n".join(lines)


def _fmt_counterexample(store, id_: str) -> str:
    """Resolve a bare counterexample ID (episode_id / prediction_id / entry_id) back
    to a human-readable line for the report. Tries each store in turn — the ID's own
    shape doesn't say which kind it is, so this just asks each lookup and keeps
    whichever answers. Falls back to the bare ID if nothing resolves (store rebuilt
    since the block was computed, etc.) rather than failing the whole render."""
    ep = store.get_episode(id_)
    if ep is not None:
        perf = (f"{ep.r_multiple:+.2f}R" if ep.r_multiple is not None
               else (f"${ep.realized_pnl:,.0f}" if ep.realized_pnl is not None else "—"))
        return f"{ep.symbol} 回合 `{id_}`（{perf}）"
    pred = store.get_prediction(id_)
    if pred is not None:
        val = f"{pred.predicted_value:+.2f}" if pred.predicted_value is not None else "—"
        band = f"（{pred.predicted_band}）" if pred.predicted_band else ""
        return f"{pred.symbol} {pred.source} 预测 `{id_}`：预测值 {val}{band}"
    entry = store.get_journal_entry(id_)
    if entry is not None:
        return f"{entry.symbol} {entry.action} 意图 `{id_}`"
    return f"`{id_}`"


def render_calibration(blocks: list[EvidenceBlock], period_label: str, *, store=None) -> str:
    from ..memory import get_store

    store = store or get_store()
    lines = [f"# 系统校准 — {period_label}", "",
            f"> 生成于 {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC ｜ "
            "纯确定性统计，无 LLM ｜ 主指标是校准而非盈亏", ""]
    for i, b in enumerate(blocks, 1):
        lines += [f"## {i}. {b.question}", ""]
        n_note = f"n_closed={b.n_closed} · n_open={b.n_open}"
        lines.append(f"> {n_note}" + ("" if b.sufficient else f" — **样本不足（<{b.n_min}），不作结论**"))
        lines += ["", _render_table(b.table)]
        if b.counterexamples:
            fmted = [_fmt_counterexample(store, cx) for cx in b.counterexamples]
            lines += ["", "**最强反例**：" + "；".join(fmted)]
        lines.append("")
    return "\n".join(lines)


def _period_label(as_of: date, quarterly: bool) -> str:
    if quarterly:
        q = (as_of.month - 1) // 3 + 1
        return f"{as_of.year}Q{q}"
    return f"{as_of:%Y-%m}"


def write_calibration(*, store=None, as_of: date | None = None,
                      quarterly: bool = False) -> Path | None:
    from ..memory import get_store
    from ..runtime.digest import _write_md

    store = store or get_store()
    today = as_of or datetime.now(timezone.utc).date()
    label = _period_label(today, quarterly)
    blocks = build_evidence_blocks(store)
    return _write_md(f"系统校准-{label}.md", render_calibration(blocks, label, store=store))


def run(*, quarterly: bool = False) -> int:
    path = write_calibration(quarterly=quarterly)
    print(f"系统校准 → {path}" if path else "（未配置 Obsidian 输出目录）")
    return 0
