import json

import pytest

from ats.data.sources.frontier_ai_capability import _parse_aa_flight_models


@pytest.mark.parametrize(
    ("benchmark_id", "field"),
    [
        ("automationbench_aa", "automationBenchPartialScore"),
        ("scicode", "scicode"),
        ("critpt", "critpt"),
        ("mmmu_pro", "mmmuPro"),
        ("humanitys_last_exam", "hle"),
    ],
)
def test_public_next_flight_model_scores_are_normalized(benchmark_id, field):
    model = {
        "slug": "claude-opus-5-5",
        "name": "Claude Opus 5.5 (Max Effort)",
        "releaseDate": "2026-09-22",
        "creator": {"slug": "anthropic", "name": "Anthropic"},
        field: 0.625,
    }
    flight = '1:{"initialModels":' + json.dumps([model]) + "}"
    payload = (
        "<script>self.__next_f.push([1,"
        + json.dumps(flight)
        + "]) </script>"
    ).encode()

    rows, meta = _parse_aa_flight_models(
        payload,
        source_url="https://artificialanalysis.ai/evaluations/example",
        fetched_date="2026-10-02",
        benchmark_id=benchmark_id,
    )

    assert len(rows) == 1
    assert rows[0]["benchmark_id"] == benchmark_id
    assert rows[0]["lab_id"] == "ANTHROPIC"
    assert rows[0]["score"] == 62.5
    assert rows[0]["source_transport"] == "public_html_next_flight"
    assert meta["parser_drift"] is False
    assert meta["initial_model_count"] == 1


def test_public_next_flight_missing_score_field_fails_closed():
    flight = '1:{"initialModels":[{"name":"Claude Opus 5.5","creator":{"name":"Anthropic"}}]}'
    payload = ("<script>self.__next_f.push([1," + json.dumps(flight) + "]) </script>").encode()

    rows, meta = _parse_aa_flight_models(
        payload,
        source_url="https://artificialanalysis.ai/evaluations/example",
        fetched_date="2026-10-02",
        benchmark_id="scicode",
    )

    assert rows == []
    assert meta["parser_drift"] is True
