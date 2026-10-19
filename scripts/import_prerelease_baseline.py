"""Re-file a pre-release PEAD baseline under the right fiscal period (parent 7.5, 2026-10-19).

Why this exists: until 2026-10-16 the hand-written `fiscal_label` in config/pead/<SYM>.yaml went stale every
quarter, so the baselines built just BEFORE the October prints were filed under the wrong period (ASML's under
"Q2 FY2026", TSM's under "Q2 FY2026" and "Q4 FY2026"). Their content is a genuine pre-release baseline, but the
event review looked for it under the right label, found nothing and reported "no comparison basis".

What it does, and what it refuses:
- copies the baseline (expectation set, background, market set-up, signal chain, scorecard dimensions) from the
  source dossier into the target period, with the period label rewritten;
- REFUSES when the baseline was built at or after the print: a baseline made after the numbers are known is the
  look-ahead the freeze rule exists to prevent. `--printed-at` is the release instant (UTC);
- does NOT carry the source's actuals or scorecard: those were scored against the other period's numbers;
- backs up both dossiers first, publishes a `[基线 v1]` projection that states where the baseline came from, and
  changes nothing without `--apply`.

Usage: import_prerelease_baseline.py SYMBOL --from "Q2 FY2026" --to "Q3 FY2026" --printed-at 2026-10-15T06:00:00+00:00 [--apply]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol")
    ap.add_argument("--from", dest="source", required=True, help="fiscal label the baseline was filed under")
    ap.add_argument("--to", dest="target", required=True, help="fiscal label of the print it was built for")
    ap.add_argument("--printed-at", required=True, help="release instant of the print, ISO 8601 with timezone")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    from ats.agents.fundamental import routine
    from ats.memory import get_store
    from ats.schemas.pead import PeadDossier

    symbol = args.symbol.upper()
    printed_at = datetime.fromisoformat(args.printed_at.replace("Z", "+00:00"))
    if printed_at.tzinfo is None:
        raise SystemExit("--printed-at needs a timezone")
    store = get_store()
    source = store.get_dossier(symbol, args.source)
    if source is None or source.expectation_set is None or not source.expectation_set.expectations:
        raise SystemExit(f"no baseline under {symbol} {args.source}")
    built = source.expectation_set.as_of
    built = built if built.tzinfo else built.replace(tzinfo=timezone.utc)
    if built >= printed_at:
        raise SystemExit(f"REFUSED: the baseline was built {built.isoformat()}, not before the print "
                         f"{printed_at.isoformat()}")
    target = store.get_dossier(symbol, args.target)
    existing = [row for row in store.task_projection_envelopes(
        agent_role="fundamental_expectation_update", scope_kind="entity", scope_id=symbol, limit=500)
        if (row.get("payload") or {}).get("period") == args.target
        and str((row.get("payload") or {}).get("driver", "")).startswith("[基线")]
    es = source.expectation_set.model_copy(update={"fiscal_label": args.target})
    now = datetime.now(timezone.utc)
    new = PeadDossier(
        symbol=symbol, fiscal_label=args.target, phase="prep", updated_at=now,
        earnings_date=printed_at.date().isoformat(),
        fundamental_background=source.fundamental_background, expectation_set=es,
        market_setup=source.market_setup, signal_chain=source.signal_chain,
        signal_chain_summary=source.signal_chain_summary, fundamentals_context=source.fundamentals_context,
        scorecard_dims=source.scorecard_dims, scorecard_weights=source.scorecard_weights,
        long_threshold=source.long_threshold, run_up_warn_pct=source.run_up_warn_pct)
    provenance = (f"事后改标期次：这份基线在 {built.isoformat(timespec='minutes')} 建立（早于 {args.target} 财报发布 "
                  f"{printed_at.isoformat(timespec='minutes')}），当时因配置里的财季标签过期，被归在 {args.source} 名下；"
                  f"{now.date().isoformat()} 按用户指示改归 {args.target}。内容未改，不含 {args.source} 名下的实际值与打分。")
    print(json.dumps({"symbol": symbol, "from": args.source, "to": args.target, "baseline_built_at": built.isoformat(),
                      "printed_at": printed_at.isoformat(), "dimensions": [e.dim_key for e in es.expectations],
                      "consensus_revenue": es.consensus_revenue, "has_market_setup": bool(source.market_setup),
                      "signal_chain": len(source.signal_chain), "existing_baseline_projections": len(existing),
                      "target_dossier_exists": target is not None, "apply": args.apply}, ensure_ascii=False, indent=1))
    if not args.apply:
        print("dry-run: nothing written")
        return 0
    if existing:
        raise SystemExit("a [基线] projection already exists for the target period; refusing to add another")
    backups = REPO / "var" / "dossier_backups"
    backups.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%dT%H%M%S")
    for label, dossier in ((args.source, source), (args.target, target)):
        if dossier is not None:
            (backups / f"{symbol}_{label.replace(' ', '_')}.{stamp}.json").write_text(
                dossier.model_dump_json(indent=1), encoding="utf-8")
    store.save_dossier(new)
    projection_id = routine._publish_baseline(
        store, symbol, args.target, new, reason="财报前建立，事后改标期次", input_refs=[], use_llm=False,
        provenance=provenance)
    print(json.dumps({"saved_dossier": f"{symbol} {args.target}", "baseline_projection": projection_id}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
