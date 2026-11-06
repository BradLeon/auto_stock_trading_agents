"""Layer/sector review lineage follows only the risk.yaml sections they read."""

from __future__ import annotations

from pathlib import Path

import yaml

from ats.workflow import planning

BASE = {"limits": {"max_position_pct": 0.25}, "instruments": {"X": {"layer_key": "L1"}},
        "sector_layer_caps": {"s": {"L1": {"weight_cap": 0.1}}}, "layer_utilization": {"超配": 1.0}}


def _hash(tmp_path: Path, doc: dict) -> str:
    path = tmp_path / "risk.yaml"
    path.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
    return planning._file_hash(path)


def test_editing_limits_or_instruments_does_not_change_the_lineage_hash(tmp_path):
    before = _hash(tmp_path, BASE)
    assert _hash(tmp_path, {**BASE, "limits": {"max_position_pct": 0.35}}) == before
    assert _hash(tmp_path, {**BASE, "instruments": {"Y": {"layer_key": "L2"}}}) == before


def test_editing_a_layer_ceiling_or_the_utilization_table_does_change_it(tmp_path):
    before = _hash(tmp_path, BASE)
    assert _hash(tmp_path, {**BASE, "sector_layer_caps": {"s": {"L1": {"weight_cap": 0.2}}}}) != before
    assert _hash(tmp_path, {**BASE, "layer_utilization": {"超配": 0.9}}) != before


def test_the_current_repo_file_keeps_the_digest_recorded_before_tiering():
    live = planning._file_hash(planning.REPO_ROOT / "config" / "risk.yaml")
    assert live == next(iter(planning.RISK_LEGACY_DIGESTS.values()))
