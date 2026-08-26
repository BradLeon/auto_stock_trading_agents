"""Deprecated compatibility facade for governed structured research data.

New code should import structured catalog, stores, pipelines, and products from
``ats.data``. The legacy surface remains during staged consumer migration.
"""

__deprecated__ = True

from .artifacts import ArtifactStore, default_artifact_root
from .catalog import StructuredCatalog
from .models import (
    ArtifactDescriptor,
    CatalogStatus,
    DataSnapshot,
    DerivationDefinition,
    EvidenceCandidateInput,
    EvidenceLink,
    FetchRequest,
    IngestionStatus,
    MetricDefinition,
    NativeRecord,
    AdapterArtifact,
    AdapterBatch,
    AdapterFailure,
    ObservationInput,
    ObservationVintage,
    Persistence,
    ProviderMapping,
    QualityStatus,
    RawArtifact,
    SeriesIdentity,
    StructuredDataset,
    StructuredSource,
)
from .ingestion import CentralAdmission, IngestionPipeline, StructuredAdapter
from .evidence import EvidenceWorkbench
from .selection import SourceSelector
from .flags import READ_MODES, read_mode, source_mode
from .repository import (
    SQLiteStructuredRepository,
    StructuredRepository,
    default_db_path,
    get_repository,
    reset_repository_cache,
)
from .reporting import build_quality_report, render_quality_markdown
from .discovery import DataDiscovery, render_discovery_markdown
from .release import ReleaseManager, default_release_path, load_release_overlay
from .runtime_registry import (
    RuntimeSourceSpec,
    build_ingestion,
    ingest_source,
    register_runtime,
    validate_source_registration,
)

__all__ = [
    "AdapterArtifact", "AdapterBatch", "AdapterFailure", "ArtifactDescriptor",
    "ArtifactStore", "CatalogStatus", "CentralAdmission", "DataDiscovery", "DataSnapshot",
    "DerivationDefinition", "EvidenceCandidateInput", "EvidenceLink", "EvidenceWorkbench",
    "FetchRequest", "IngestionPipeline",
    "IngestionStatus", "MetricDefinition", "NativeRecord", "ObservationInput",
    "ObservationVintage", "Persistence", "ProviderMapping", "QualityStatus", "RawArtifact",
    "SeriesIdentity", "SQLiteStructuredRepository", "StructuredCatalog", "StructuredDataset",
    "READ_MODES", "SourceSelector", "StructuredAdapter", "StructuredRepository",
    "StructuredSource", "read_mode", "source_mode",
    "ReleaseManager", "RuntimeSourceSpec", "build_ingestion", "ingest_source",
    "default_release_path",
    "load_release_overlay", "register_runtime", "validate_source_registration",
    "build_quality_report", "default_artifact_root", "default_db_path",
    "get_repository", "render_discovery_markdown", "render_quality_markdown",
    "reset_repository_cache",
]
