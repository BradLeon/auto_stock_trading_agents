"""Readable material-review report (2026-11-24)."""
from ats.workflow import material_report as mr

BRIEF = {"projection_id": "b1", "as_of": "2026-11-24T00:00:00+00:00", "payload": {
    "event_components": [{"component_id": "c1", "content": "CEO 辞职", "materiality": "critical",
                          "direction": "negative", "novelty": "escalation", "fact_indices": [0]}],
    "source_evidence": [{"fact_index": 0, "source_actor": "WSJ"}]}}
REVIEW = {"projection_id": "r1", "agent_role": "fundamental_material_review", "scope_id": "NVDA",
          "as_of": "2026-11-24T07:35:00+00:00", "payload": {
              "entity": "NVDA", "event": "nvda-ceo", "component_refs": ["b1#c1"], "thesis_status": "falsified",
              "expectation_effect": "risk_premium", "direction": -1, "magnitude": 2.0, "confidence": 0.7,
              "narrative": "治理断裂", "hit_items": ["falsifier: 传票"], "materiality_review": "agree",
              "unverified": ["继任人选"], "valid_until": "2026-12-08T07:35:00+00:00"}}


class Store:
    def get_task_projection(self, pid):
        return {"b1": BRIEF, "r1": REVIEW}.get(pid)


def test_report_is_written_and_never_overwrites(tmp_path):
    first = mr.write(Store(), ["r1"], output_dir=tmp_path)
    second = mr.write(Store(), ["r1"], output_dir=tmp_path)
    assert first[0].endswith("重大事件评审-NVDA-2026-11-24.md") and second[0].endswith("-v2.md")
    text = (tmp_path / "重大事件评审-NVDA-2026-11-24.md").read_text(encoding="utf-8")
    assert "**证伪**" in text and "风险溢价" in text and "CEO 辞职" in text and "WSJ" in text and "继任人选" in text
