"""Score the falsifiable claims the system already makes.

Every PEAD score emits numbers that can be checked against reality — the scorecard
total and band (predicted drift), the option-implied expected move, the consensus target
price. None of them was ever compared to an outcome.

This is the fast feedback loop, and the reason it matters is arithmetic: calibration
accrues per SCORE (13 targets x 4 quarters ~= 52/yr, whether or not a trade was taken),
while P&L accrues per FILL (3 so far). A prediction with `entry_id = NULL` — predicted
but not traded — is not missing data, it is precisely the sample that keeps the
calibration free of survivorship bias.

Each prediction is scored at every horizon and ALL horizons are kept. Right at T+1 and
wrong at T+20 means the entry was right and the holding period wrong; collapsing that
into one verdict throws away the finding.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from . import prices

log = logging.getLogger("ats.journal")


def _forward_close(symbol: str, start: date, horizon: int) -> tuple[date, float] | None:
    """Close `horizon` TRADING days after the first bar on/after `start`."""
    bars = prices.bars(symbol)
    idx = next((i for i, b in enumerate(bars) if b.date >= start), None)
    if idx is None or idx + horizon >= len(bars):
        return None
    b = bars[idx + horizon]
    return (b.date, b.close)


def _pct(a: float, b: float) -> float | None:
    return round((b / a - 1) * 100, 3) if a else None


def _as_date(v) -> date | None:
    """PeadDossier.earnings_date is an ISO STRING, MarketSetup's is a date — accept both."""
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str) and v.strip():
        try:
            return date.fromisoformat(v.strip()[:10])
        except ValueError:
            return None
    return None


def record_pead_prediction(*, store, symbol: str, fiscal_label: str, scorecard,
                           market_setup=None, expectation_set=None,
                           earnings_date=None, scored_at=None,
                           sector_etf: str = "SMH", benchmark: str = "QQQ",
                           entry_id: str | None = None) -> list[str]:
    """Register this quarter's falsifiable claims. Idempotent per (symbol, quarter, kind).

    The reference point is when the score was MADE, not the print date. Measuring from
    the pre-announcement close would fold in the earnings gap, which is not capturable —
    you cannot enter at that price after seeing the result. PEAD is the post-announcement
    drift, so the honest reference is the first close we could actually have traded.
    """
    made = datetime.now(timezone.utc)
    print_date = _as_date(earnings_date)
    ref_date = _as_date(scored_at) or print_date or made.date()
    got = prices.close_on_or_after(symbol, ref_date)
    ref_price = got[1] if got else None
    ref_key = f"{symbol}:{fiscal_label}"
    ids: list[str] = []

    claims: list[tuple[str, str, float | None, str]] = []
    if scorecard is not None and scorecard.total is not None:
        claims.append(("pead_score", "drift_direction", scorecard.total, scorecard.band or ""))
    em = getattr(market_setup, "expected_move_pct", None) if market_setup else None
    if em:
        claims.append(("expected_move", "abs_move_pct", em, ""))
    pt = getattr(expectation_set, "consensus_target_price", None) if expectation_set else None
    if pt and ref_price:
        claims.append(("consensus_pt", "target_pct", _pct(ref_price, pt), ""))

    from ..schemas.journal import Prediction

    for source, kind, value, band in claims:
        pid = f"{ref_key}:{source}"
        store.save_prediction(Prediction(
            prediction_id=pid, made_at=made, symbol=symbol, source=source,
            ref_key=ref_key, kind=kind, predicted_value=value, predicted_band=band,
            ref_price=ref_price, ref_date=(got[0] if got else ref_date),
            print_date=print_date, sector_etf=sector_etf, benchmark=benchmark,
            entry_id=entry_id))
        ids.append(pid)
    return ids


def score_open_predictions(*, store=None, horizons=None) -> dict:
    """Fill in outcomes for every horizon that has now elapsed. Idempotent."""
    from ..config import get_config
    from ..memory import get_store
    from ..schemas.journal import PredictionOutcome

    store = store or get_store()
    horizons = horizons or get_config().app.journal.horizons
    summary = {"scored": 0, "pending": 0, "no_price": 0}

    for p in store.open_predictions(horizons):
        pid, sym = p.prediction_id, p.symbol
        ref_date, ref_px = p.ref_date, p.ref_price
        if ref_date is None:
            summary["no_price"] += 1
            continue
        for h in horizons:
            if store.has_outcome(pid, h):
                continue
            fwd = _forward_close(sym, ref_date, h)
            if fwd is None:
                summary["pending"] += 1
                continue
            if not ref_px:
                summary["no_price"] += 1
                continue
            realized = _pct(ref_px, fwd[1])
            excess_sector = excess_bench = None
            for etf, key in ((p.sector_etf, "sector"), (p.benchmark, "bench")):
                if not etf:
                    continue
                base = prices.close_on_or_after(etf, ref_date)
                fwd_b = _forward_close(etf, ref_date, h)
                if base and fwd_b:
                    bench_ret = _pct(base[1], fwd_b[1])
                    if realized is not None and bench_ret is not None:
                        val = round(realized - bench_ret, 3)
                        if key == "sector":
                            excess_sector = val
                        else:
                            excess_bench = val
            store.save_prediction_outcome(PredictionOutcome(
                prediction_id=pid, horizon_days=h, as_of=fwd[0],
                realized_pct=realized, excess_vs_sector_pct=excess_sector,
                excess_vs_bench_pct=excess_bench))
            summary["scored"] += 1
    return summary


def backfill_from_dossiers(*, store=None) -> dict:
    """Register predictions for every already-scored dossier.

    The scorecard totals, expected moves and target prices are already stored and price
    history is retroactive, so the calibration series does not have to start from zero.
    """
    import json

    from ..config import load_pead_config
    from ..memory import get_store
    from ..schemas.pead import PeadDossier

    store = store or get_store()
    out = {"dossiers": 0, "predictions": 0}
    for row in store.conn.execute("SELECT symbol, fiscal_label, payload FROM pead_dossier"):
        try:
            payload = json.loads(row["payload"])
        except (TypeError, ValueError):
            continue
        if not (payload.get("scorecard") or {}).get("total") is not None:
            continue
        try:
            d = PeadDossier.model_validate(payload)
        except Exception:  # noqa: BLE001 - skip unparseable history
            continue
        if d.scorecard is None:
            continue
        try:
            cfg = load_pead_config(row["symbol"])
            etf, bench = cfg.sector_etf, cfg.benchmark
        except Exception:  # noqa: BLE001
            etf, bench = "SMH", "QQQ"
        try:
            ids = record_pead_prediction(
                store=store, symbol=row["symbol"], fiscal_label=row["fiscal_label"],
                scorecard=d.scorecard, market_setup=d.market_setup,
                expectation_set=d.expectation_set,
                # dossier.earnings_date holds the NEXT print (ASML: 2026-10-14 while the
                # scored quarter was July), so it is context only — the scorecard's own
                # as_of is what dates the claim.
                earnings_date=d.earnings_date, scored_at=d.scorecard.as_of,
                sector_etf=etf, benchmark=bench)
        except Exception as exc:  # noqa: BLE001
            log.warning("backfill failed for %s: %s", row["symbol"], exc)
            continue
        out["dossiers"] += 1
        out["predictions"] += len(ids)
    return out


def render(summary: dict) -> str:
    return ("=== 预测打分 ===\n"
            f"  新打分 {summary['scored']} · 未到期 {summary['pending']} "
            f"· 缺参考价 {summary['no_price']}")


def run(*, backfill: bool = False) -> int:
    if backfill:
        b = backfill_from_dossiers()
        print(f"回填：{b['dossiers']} 份已打分 dossier → {b['predictions']} 条预测")
    print(render(score_open_predictions()))
    return 0
