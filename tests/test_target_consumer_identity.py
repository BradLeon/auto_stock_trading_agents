from datetime import datetime, timezone

import yaml

from ats.data.consumer_identity import canonical_consumer
from ats.data.contract_validation import validate_target_contract
from ats.data.products.base import DataProducts


def test_observer_alias_preserves_requested_identity_without_second_consumer():
    class Repository:
        def create_snapshot(self, **kwargs):
            return kwargs

    metadata = {"source": "original"}
    result = DataProducts(structured_repository=Repository()).snapshot_manifest(
        consumer="evidence_observer", purpose="retirement-replay",
        as_of=datetime(2026, 10, 3, tzinfo=timezone.utc),
        rows=[{"observation_id": "historical-observation", "source_id": "original-source"}],
        metadata=metadata,
    )
    assert result["consumer"] == "layer"
    assert result["metadata"]["legacy_consumer"] == "evidence_observer"
    assert result["items"][0]["observation_id"] == "historical-observation"
    assert result["items"][0]["selected_source"] == "original-source"
    assert metadata == {"source": "original"}
    assert canonical_consumer("macro") == "macro"


def test_missing_retirement_and_duplicate_active_role_fail_closed(tmp_path):
    from ats.config import REPO_ROOT

    raw = yaml.safe_load((REPO_ROOT / "config/data/target_dataflow_coverage.yaml").read_text())
    raw.pop("retired_consumers")
    raw["consumers"].append({"id": "evidence_observer"})
    path = tmp_path / "coverage.yaml"
    path.write_text(yaml.safe_dump(raw))
    report = validate_target_contract(path)
    assert not report["valid"]
    assert "observer_retirement_contract_missing_or_invalid" in report["errors"]
    assert "consumer_roles_must_match_target_ten_roles" in report["errors"]
