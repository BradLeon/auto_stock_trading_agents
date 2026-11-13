"""Config lineage follows a YAML file's parsed content, not its bytes (2026-11-13).

A comment-only edit to sectors/ai_hardware.yaml (c0c5ce1) made every Layer review and the sector
review look built from different rules, so chief-daily refused to assemble its context.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ats.workflow import planning

BODY = "layers:\n  L1_app:\n    members: [GOOG, MSFT]\n  L5_chip_design:\n    members: [NVDA, AMD]\n"


def _hash_text(tmp_path: Path, text: str, rel: str = "sectors/x.yaml") -> str:
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return planning._file_hash(path)


def test_comment_and_whitespace_edits_do_not_change_the_lineage_hash(tmp_path):
    before = _hash_text(tmp_path, BODY)
    assert _hash_text(tmp_path, "# Ramp v2 note\n" + BODY.replace("NVDA, AMD]", "NVDA, AMD]  # chip")) == before
    reordered = "layers:\n  L5_chip_design:\n    members: [NVDA, AMD]\n\n  L1_app:\n    members: [GOOG, MSFT]\n"
    assert _hash_text(tmp_path, reordered) == before


def test_a_value_edit_does_change_it(tmp_path):
    before = _hash_text(tmp_path, BODY)
    assert _hash_text(tmp_path, BODY.replace("AMD]", "AMD, AVGO]")) != before
    # List order is data, not formatting.
    assert _hash_text(tmp_path, BODY.replace("[NVDA, AMD]", "[AMD, NVDA]")) != before


def test_per_ticker_pead_files_keep_the_byte_hash_because_their_comments_are_data(tmp_path):
    text = "signal_chain:\n  - {symbol: MU, role: upstream}  # HBM 主供\n"
    before = _hash_text(tmp_path, text, "pead/NVDA.yaml")
    assert _hash_text(tmp_path, text.replace("HBM 主供", "HBM 次供"), "pead/NVDA.yaml") != before


def test_unparseable_yaml_falls_back_to_bytes(tmp_path):
    text = "a: [unclosed\n"
    path = tmp_path / "sectors" / "bad.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    import hashlib
    assert planning._file_hash(path) == hashlib.sha256(text.encode()).hexdigest()


@pytest.mark.parametrize("rel", ["sectors/ai_hardware.yaml", "technical.yaml", "macro.yaml"])
def test_repo_files_answer_with_the_digest_their_projections_recorded(rel):
    path = planning.REPO_ROOT / "config" / rel
    content = planning._hash(planning.yaml.safe_load(path.read_text(encoding="utf-8")))
    assert content in planning.CONFIG_LEGACY_DIGESTS, f"{rel} content changed: verify and re-record or let it recompute"
    assert planning._file_hash(path) == planning.CONFIG_LEGACY_DIGESTS[content]


def test_the_legacy_table_never_hides_a_real_change(tmp_path):
    path = planning.REPO_ROOT / "config" / "sectors" / "ai_hardware.yaml"
    doc = planning.yaml.safe_load(path.read_text(encoding="utf-8"))
    doc["__probe__"] = 1
    changed = _hash_text(tmp_path, planning.yaml.safe_dump(doc, allow_unicode=True), "sectors/ai_hardware.yaml")
    assert changed not in planning.CONFIG_LEGACY_DIGESTS.values()
    assert changed == planning._hash(doc)
