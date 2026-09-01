from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "config" / "data" / "structured.yaml"
ARCHITECTURE_PATH = ROOT / "docs" / "DATA_ARCHITECTURE.md"
DEVELOPER_PATH = ROOT / "docs" / "STRUCTURED_DATA_DEVELOPER.md"
OPERATIONS_PATH = ROOT / "docs" / "STRUCTURED_DATA_OPERATIONS.md"
USER_GUIDE_PATH = ROOT / "docs" / "STRUCTURED_DATA_USER_GUIDE.md"
CONSUMER_SKILL = ROOT / ".agents" / "skills" / "structured-data-consumer" / "SKILL.md"
CONSUMER_SKILL_UI = CONSUMER_SKILL.parent / "agents" / "openai.yaml"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _catalog() -> dict:
    return yaml.safe_load(_read(CATALOG_PATH))


def test_architecture_is_the_role_document_entry_point() -> None:
    architecture = _read(ARCHITECTURE_PATH)
    for filename in (
        "STRUCTURED_DATA_DEVELOPER.md",
        "STRUCTURED_DATA_OPERATIONS.md",
        "STRUCTURED_DATA_USER_GUIDE.md",
    ):
        assert f"]({filename})" in architecture
    assert "../config/data/structured.yaml" in architecture


def test_architecture_links_every_structured_validation_report() -> None:
    architecture = _read(ARCHITECTURE_PATH)
    expected = (
        "STRUCTURED_DATA_BASELINE_2026-08-25.md",
        "STRUCTURED_DATA_BENCHMARK_2026-08-25.md",
        "STRUCTURED_DATA_REGIONAL_VALIDATION_2026-08-25.md",
        "STRUCTURED_DATA_FINANCIAL_VALIDATION_2026-08-25.md",
        "STRUCTURED_DATA_CONSENSUS_VALIDATION_2026-08-25.md",
        "STRUCTURED_DATA_EVIDENCE_VALIDATION_2026-08-25.md",
        "STRUCTURED_DATA_QUALITY_VALIDATION_2026-08-25.md",
        "STRUCTURED_DATA_CONSUMER_VALIDATION_2026-08-25.md",
        "STRUCTURED_DATA_FINAL_ACCEPTANCE_2026-08-25.md",
    )
    for filename in expected:
        assert f"]({filename})" in architecture
        assert (ROOT / "docs" / filename).is_file()


def test_operations_source_matrix_matches_machine_catalog() -> None:
    operations = _read(OPERATIONS_PATH)
    catalog = _catalog()
    for source_id, row in catalog["sources"].items():
        persistence = row.get("persistence", "persistent")
        datasets = ", ".join(row.get("datasets") or []) or "—"
        datasets_cell = datasets if datasets == "—" else f"`{datasets}`"
        expected = (
            f"| `{source_id}` | `{row['catalog_status']}` | "
            f"`{persistence}` | {datasets_cell} |"
        )
        assert expected in operations


def test_operations_dataset_matrix_matches_machine_catalog() -> None:
    operations = _read(OPERATIONS_PATH)
    for dataset_id, row in _catalog()["datasets"].items():
        assert f"| `{dataset_id}` | `{row['catalog_status']}` |" in operations


def test_role_docs_share_catalog_and_command_vocabulary() -> None:
    developer = _read(DEVELOPER_PATH)
    operations = _read(OPERATIONS_PATH)
    user_guide = _read(USER_GUIDE_PATH)

    assert "config/data/structured.yaml" in developer
    assert "config/data/structured.yaml" in operations
    assert "data health" in operations and "data health" in user_guide
    assert "data quality" in operations and "data quality" in user_guide
    assert "data series" in operations and "data series" in user_guide
    for action in (
        "data sources", "data datasets", "data metrics", "data coverage",
        "data conflicts", "data pending-mappings", "data ingestion-history",
        "data artifacts",
    ):
        assert action in operations


def test_role_docs_expose_executable_lifecycle_and_dynamic_discovery() -> None:
    developer = _read(DEVELOPER_PATH)
    operations = _read(OPERATIONS_PATH)
    user_guide = _read(USER_GUIDE_PATH)
    architecture = _read(ARCHITECTURE_PATH)

    for token in (
        "src/ats/structured/runtime_registry.py", "data validate-source",
        "data ingest", "data financial-package-check", "data release-check", "data publish", "data rollback",
        "data release-assessment", "config/data/consumer_release.yaml",
        "var/structured_data/releases.yaml", "--apply",
    ):
        assert token in operations
    for action in ("data catalog", "data describe", "data availability", "data examples"):
        assert action in user_guide
        assert action in architecture or action.replace("data ", "") in architecture
    assert "Dynamic Discovery" in developer
    assert "structured-data-consumer" in developer or "structured-data-consumer" in user_guide


def test_structured_consumer_skill_is_valid_and_references_live_discovery() -> None:
    assert CONSUMER_SKILL.is_file()
    assert CONSUMER_SKILL_UI.is_file()
    body = _read(CONSUMER_SKILL)
    for action in ("data catalog", "data describe", "data availability", "data examples",
                   "data series", "data derive", "data cross-section", "data lineage"):
        assert action in body
    assert "Deterministic Workflows" in body
    assert "runtime" in body and "structured snapshot" in body


def test_cli_help_lists_documented_structured_actions(capsys) -> None:
    from ats.runtime.cli import main

    with pytest.raises(SystemExit) as exc:
        main(["data", "--help"])
    assert exc.value.code == 0
    output = capsys.readouterr().out
    for action in (
        "catalog", "describe", "availability", "examples", "validate-source",
        "ingest", "financial-package-check", "release-check", "release-assessment", "publish", "rollback", "derive", "cross-section",
    ):
        assert action in output
