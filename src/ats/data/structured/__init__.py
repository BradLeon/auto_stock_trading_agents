"""Public structured-data surface owned by :mod:`ats.data`.

This module is the single internal aggregation point for structured contracts.
It replaces the retired ``ats.structured`` package; all implementation imports
below stay inside the unified data-layer tree.
"""

from ats.data.core.structured_models import *  # noqa: F401,F403
from ats.data.core.structured_models import __all__ as _model_all
from ats.data.catalog import StructuredCatalog
from ats.data.adapters.structured.registry import (
    RuntimeSourceSpec,
    build_ingestion,
    ingest_source,
    register_runtime,
    validate_source_registration,
)
from ats.data.stores.structured.repository import (
    SQLiteStructuredRepository,
    StructuredRepository,
    default_db_path,
    get_repository,
    reset_repository_cache,
)
from ats.data.stores.structured.artifacts import ArtifactStore, default_artifact_root
from ats.data.pipelines.structured.ingestion import (
    CentralAdmission, IngestionPipeline, StructuredAdapter,
)
from ats.data.stores.unstructured.workbench import EvidenceWorkbench
from ats.data.products.selection import SourceSelector
from ats.data.rollout_modes import READ_MODES, read_mode, source_mode
from ats.data.release import ReleaseManager, default_release_path, load_release_overlay
from ats.data.products.reporting import build_quality_report, render_quality_markdown
from ats.data.products.discovery import DataDiscovery, render_discovery_markdown

__all__ = list(dict.fromkeys([
    *_model_all,
    "StructuredCatalog",
    "RuntimeSourceSpec",
    "build_ingestion",
    "ingest_source",
    "register_runtime",
    "validate_source_registration",
    "SQLiteStructuredRepository",
    "StructuredRepository",
    "default_db_path",
    "get_repository",
    "reset_repository_cache",
]))
