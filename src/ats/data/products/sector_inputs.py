"""Sector/layer analyst read entry (Phase D 8.3).

行业与层级分析的取数只经这里：产业链知识库、FactSet 行业背景、一致预期、
成分股财务、区域月度与价格快照。Provider 模块（industry / factset / consensus /
fundamentals / regional / sector_snapshot）不再是 agents 的合法导入面。
"""

from __future__ import annotations

from pathlib import Path
import yaml


def curated_knowledge_packet(paths: list[str], *, cap: int = 16000) -> dict:
    """Read accepted Layer criteria by registered member identity, never by file I/O.

    ``missing`` remains explicit so an Agent cannot silently treat an unpublished
    repository file as a data product.  Each returned note carries its immutable
    document-version reference for Task Projection vintage binding.
    """
    from ...config import REPO_ROOT
    from ..document_assets import read_document
    from ..stores.unstructured import get_platform_unstructured_repository

    requested = [Path(raw).as_posix() for raw in paths]
    registry = yaml.safe_load(
        (REPO_ROOT / "config/data/unstructured.yaml").read_text(encoding="utf-8")) or {}
    source = (registry.get("sources") or {}).get("ai_hardware_knowledge_corpus") or {}
    allowed = set((source.get("policy") or {}).get("members") or [])
    notes: list[dict] = []
    missing: list[dict] = []
    store = get_platform_unstructured_repository()
    try:
        for path in requested:
            if path not in allowed:
                missing.append({"path": path, "reason": "not_registered"})
                continue
            row = store.document_by_external_id(f"knowledge:{path}")
            if not row or row.get("source") != "ai_hardware_knowledge_corpus":
                missing.append({"path": path, "reason": "not_published"})
                continue
            version = store.latest_document_version(row["document_id"])
            body = read_document(row["document_id"], store=store)
            if not version or not body:
                missing.append({"path": path, "reason": "version_unreadable"})
                continue
            notes.append({"path": path, "name": Path(path).stem,
                          "text": body[:cap], "document_id": row["document_id"],
                          "version_id": version["version_id"],
                          "content_hash": version["content_hash"],
                          "fetched_at": version["fetched_at"]})
    finally:
        store.close()
    return {"source_id": "ai_hardware_knowledge_corpus", "consumer": "layer_analyst",
            "notes": notes, "missing": missing,
            "status": "complete" if not missing else "partial" if notes else "missing"}


def fetch_named(paths: list[str], cap: int = 16000) -> list[tuple[str, str]]:
    """Compatibility shape for Layer context, backed only by the governed corpus."""
    packet = curated_knowledge_packet(paths, cap=cap)
    return [(row["name"], row["text"]) for row in packet["notes"]]


def as_context(notes: list[tuple[str, str]]) -> str:
    from ..industry import as_context as render

    return render(notes)


# --- 产业链知识库（策展笔记，只读） ---------------------------------------- #

def industry_notes() -> list[tuple[str, str]]:
    from .. import industry

    return industry.fetch_notes()


def industry_named(paths: list[str], cap: int = 16000) -> list[tuple[str, str]]:
    from .. import industry

    return industry.fetch_named(paths, cap=cap)


def industry_context(notes: list[tuple[str, str]]) -> str:
    from .. import industry

    return industry.as_context(notes)


def industry_criteria_spans(text: str) -> list[tuple[int, int]]:
    """知识库评分标准段落的定位（kb_perturb 用，纯文本工具）。"""
    from .. import industry

    return industry.criteria_spans(text)


# --- FactSet 行业材料（本地 DataProducts 快照） ----------------------------- #

def factset_sector_context() -> str:
    from .. import factset

    return factset.fetch_sector_context()


def factset_sector_material() -> dict:
    from .. import factset

    return factset.fetch_sector_material()


# --- 区域月度与一致预期/财务 ------------------------------------------------ #

def regional_monthly(consumer: str):
    from .. import regional

    return regional.fetch(consumer=consumer)


def consensus_for(symbol: str, *, consumer: str = "sector_consensus") -> dict:
    from .. import consensus

    return consensus.fetch(symbol, consumer=consumer)


def constituent_financials(symbol: str, **kwargs):
    from .. import fundamentals

    return fundamentals.fetch_constituent_financials(symbol, **kwargs)


# --- 价格快照（行情） -------------------------------------------------------- #

def sector_prices(symbols: list[str], period: str = "1y") -> dict[str, list[float]]:
    from .. import sector_snapshot

    return sector_snapshot.fetch_prices(symbols, period=period)


def sector_price_history(symbols: list[str], period: str = "1y"):
    """Runtime market read including per-symbol status and source bar date."""
    from .. import sector_snapshot

    return sector_snapshot.fetch_close_history(symbols, period=period)


def price_momentum(closes: list[float], days: int) -> float | None:
    from .. import sector_snapshot

    return sector_snapshot.momentum(closes, days)


def distance_to_high(closes: list[float]) -> float | None:
    from .. import sector_snapshot

    return sector_snapshot.dist_to_high(closes)
