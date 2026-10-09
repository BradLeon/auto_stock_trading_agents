"""Reproduce real isolated Trader + Chief publication with local fixture inputs.

Run: UV_CACHE_DIR=/private/tmp/phase-f-audit-uv-cache uv run --offline --no-sync python docs/validation/phase_f_shadow_ledger_20261008/verify_business.py
This is a single-chain foundation sample, not task 7.5/3.9 acceptance.
"""
import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from pytest import MonkeyPatch

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "tests"))
from test_phase_f_shadow_business import external_inputs, ready
from ats.graph import chief
from ats.workflow import intake_verification as iv
from ats.workflow import shadow_ledger as ledger
from ats.workflow.isolated_entry import run_isolated_entry


def main():
    root = Path(tempfile.mkdtemp(prefix="phase-f-shadow-business-"))
    with MonkeyPatch.context() as patch:
        fixtures = external_inputs.__wrapped__(patch)
        next(fixtures)
        try:
            with iv.isolated_verification("prepare-validation", root=root):
                state = ready(cycle="phase-f-shadow-ledger-validation")
            trade = run_isolated_entry(run_id="validation-shadow", entry=chief.trader,
                                      root=root, shadow=True, state=state)
            assert trade.output["gate_outcome"] == "shadow_refused"
            state = state.model_copy(update=trade.output)
            published = run_isolated_entry(run_id="validation-shadow", entry=chief.persist,
                                          root=root, shadow=True, state=state)
            path = root / "shadow_orders.sqlite"
            rows = ledger.intents(path=path)
            attempts = ledger.submit_attempts(path=path)
            before = rows, attempts
            command = "from ats.workflow.shadow_ledger import rebuild_attribution;import json,sys;print(json.dumps(rebuild_attribution(sys.argv[1],path=sys.argv[2])))"
            child = subprocess.run([sys.executable, "-c", command, state.cycle_id, str(path)],
                                   capture_output=True, text=True, check=True)
            attribution = json.loads(child.stdout)
            assert attribution["order_count"] == attribution["refused_count"] == 1
            assert attribution["submitted_count"] == 0
            assert (ledger.intents(path=path), ledger.submit_attempts(path=path)) == before
            with sqlite3.connect(root / "memory.sqlite") as conn:
                counts = {name: conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                          for name in ("trades", "fills", "decision_revisions", "decision_risk_reviews", "boss_approvals")}
            assert counts["trades"] == counts["fills"] == 0
            with sqlite3.connect(path) as conn:
                (OUTPUT / "shadow-ledger.sql").write_text("\n".join(conn.iterdump()) + "\n")
            evidence = {"run_id": "validation-shadow", "cycle_id": state.cycle_id,
                        "entry_ids": [trade.entry_id, published.entry_id], "isolated_root": str(root),
                        "attestations": [trade.attestation, published.attestation], "isolated_counts": counts,
                        "intents": rows, "attempts": attempts, "fresh_process_attribution": attribution,
                        "original_rows_unchanged_by_rebuild": True, "tradable": False,
                        "limits": ["synthetic external inputs", "single chain", "no actual new/legacy paired runner"]}
            (OUTPUT / "business-evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps({"output": str(OUTPUT / "business-evidence.json"), "intents": len(rows),
                              "refusals": len(attempts), "tradable": False}))
        finally:
            try:
                next(fixtures)
            except StopIteration:
                pass


if __name__ == "__main__":
    main()
