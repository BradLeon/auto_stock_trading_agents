"""Consumer-facing research data products.

Callers ask domain questions here instead of knowing which compatibility table, file,
or index currently serves them. Storage can evolve without changing agent contracts.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json


class DataProducts:
    def __init__(self, store=None, structured_repository=None):
        if store is None:
            from ..memory import get_store

            store = get_store()
        self.store = store
        self._structured_repository = structured_repository

    @property
    def structured(self):
        if self._structured_repository is None:
            from ..structured import get_repository

            self._structured_repository = get_repository()
            self._structured_repository.bootstrap_catalog()
        return self._structured_repository

    def indicator_series(self, *, source_id: str | None = None,
                         series: str | None = None, entity: str | None = None,
                         since: str | None = None, as_of: datetime | None = None,
                         include_vintages: bool = False, as_frame: bool = False):
        rows = self.store.measurements(
            source_id=source_id, series=series, entity=entity, since=since,
            as_of=as_of, latest_only=not include_vintages)
        if not as_frame:
            return rows
        try:
            import pandas as pd
        except ImportError as exc:  # optional dependency, explicit only when requested
            raise RuntimeError("pandas is required for as_frame=True") from exc
        return pd.DataFrame(rows)

    def _gap_status(self, *, dataset_id: str | None, source_id: str | None,
                    entity_id: str, metric_id: str,
                    as_of: datetime | None) -> dict:
        unbounded = self.structured.observations(
            dataset_id=dataset_id, source_id=source_id, entity_id=entity_id,
            metric_id=metric_id, latest_only=True, accepted_only=False, limit=1)
        if as_of and unbounded:
            return {"status": "not_yet_known", "reason": "no_visible_vintage_at_as_of"}
        history = self.structured.ingestion_history(
            source_id=source_id, dataset_id=dataset_id, limit=1)
        if history:
            state = history[0]["status"]
            if state in {
                "zero_match", "not_yet_published", "no_coverage", "stale",
                "unreachable", "unauthorized", "parse_failed", "validation_failed",
            }:
                return {"status": state, "reason": "latest_ingestion_state"}
        return {"status": "no_coverage", "reason": "no_accepted_observation"}

    @staticmethod
    def _as_frame(result: dict):
        try:
            import pandas as pd
        except ImportError as exc:
            raise RuntimeError("pandas is required for as_frame=True") from exc
        frame = pd.DataFrame(result["rows"])
        frame.attrs["structured_query"] = {
            key: value for key, value in result.items() if key not in {"rows", "rejected"}
        }
        return frame

    def metric_series(self, *, metric: str, entity: str,
                      dataset: str | None = None, source_id: str | None = None,
                      since: str | None = None, as_of: datetime | None = None,
                      include_vintages: bool = False,
                      source_strategy: str = "selected", quality: str = "strict",
                      max_age_hours: float | None = None,
                      snapshot_consumer: str = "", snapshot_purpose: str = "",
                      as_frame: bool = False):
        """Query a governed metric with explicit source, quality and gap semantics."""
        if source_strategy not in {"selected", "all"}:
            raise ValueError("source_strategy must be selected or all")
        if quality not in {"strict", "loose"}:
            raise ValueError("quality must be strict or loose")
        rows = self.structured.observations(
            dataset_id=dataset, metric_id=metric, entity_id=entity,
            source_id=source_id, since=since, as_of=as_of,
            latest_only=not include_vintages, accepted_only=True)
        selected: list[dict] = []
        rejected: list[dict] = []
        conflicts: list[dict] = []
        from ..structured import SourceSelector

        if source_id or source_strategy == "all" or not rows:
            for row in rows:
                selected.append(dict(row, selected_source=row["source_id"],
                                     selection_reason="explicit_source" if source_id
                                     else "all_sources_requested", conflict=False))
        else:
            selector = SourceSelector(self.structured)
            group_fields = (
                "period", "period_basis", "adjustment", "unit", "currency")
            groups = sorted({tuple(row.get(field, "") for field in group_fields)
                             for row in rows})
            for group in groups:
                period_rows = [row for row in rows if tuple(
                    row.get(field, "") for field in group_fields) == group]
                latest_by_source: dict[str, dict] = {}
                for row in period_rows:
                    current = latest_by_source.get(row["source_id"])
                    if current is None or (row["known_at"], row["fetched_at"]) > (
                            current["known_at"], current["fetched_at"]):
                        latest_by_source[row["source_id"]] = row
                choice = selector.select(dataset or period_rows[0]["dataset_id"],
                                         list(latest_by_source.values()))
                if choice.selected is None:
                    continue
                chosen_rows = ([choice.selected] if not include_vintages else [
                    row for row in period_rows
                    if row["source_id"] == choice.selected_source])
                for row in chosen_rows:
                    selected.append(dict(
                        row, selected_source=choice.selected_source,
                        selection_reason=choice.selection_reason,
                        conflict=choice.conflict,
                        alternative_sources=[item["source_id"]
                                             for item in choice.alternatives]))
                if choice.conflict:
                    conflicts.append({
                        "period": group[0], "period_basis": group[1],
                        "selected_source": choice.selected_source,
                        "sources": sorted(latest_by_source),
                        "values": {key: value["value"]
                                   for key, value in latest_by_source.items()},
                    })

        reference = as_of or datetime.now(timezone.utc)
        usable = []
        for row in selected:
            known = datetime.fromisoformat(row["known_at"])
            row["age_hours"] = max(0.0, (reference - known).total_seconds() / 3600)
            row["lineage"] = {
                "observation_id": row["observation_id"],
                "artifact_id": row["artifact_id"],
                "content_hash": row["content_hash"],
            }
            strict_reasons = []
            if row.get("conflict") or row.get("quality_status") == "conflict":
                strict_reasons.append("source_conflict")
            if max_age_hours is not None and row["age_hours"] > max_age_hours:
                strict_reasons.append("stale")
            if quality == "strict" and strict_reasons:
                rejected.append(dict(row, strict_reasons=strict_reasons))
            else:
                row["quality_warnings"] = strict_reasons
                usable.append(row)

        gap = None
        if not usable:
            if rejected:
                status = "stale" if all("stale" in row["strict_reasons"]
                                        for row in rejected) else "quality_rejected"
                gap = {"status": status, "reason": "strict_quality_gate"}
            else:
                gap = self._gap_status(
                    dataset_id=dataset, source_id=source_id,
                    entity_id=entity.upper(), metric_id=metric, as_of=as_of)
        result = {
            "status": "ok" if usable else gap["status"],
            "metric_id": metric,
            "entity_id": entity.upper(),
            "dataset_id": dataset or (rows[0]["dataset_id"] if rows else ""),
            "as_of": as_of.isoformat() if as_of else None,
            "include_vintages": include_vintages,
            "source_strategy": source_strategy,
            "quality_mode": quality,
            "rows": usable,
            "rejected": rejected,
            "conflicts": conflicts,
            "missing": gap,
        }
        if snapshot_consumer and usable:
            result["snapshot"] = self.snapshot_manifest(
                consumer=snapshot_consumer,
                purpose=snapshot_purpose or f"metric_series:{metric}",
                as_of=reference, rows=usable,
                metadata={"metric_id": metric, "entity_id": entity.upper(),
                          "dataset_id": result["dataset_id"]})
        return self._as_frame(result) if as_frame else result

    def cross_section(self, *, metric: str, entities: list[str], period: str,
                      dataset: str | None = None, as_of: datetime | None = None,
                      quality: str = "strict", as_frame: bool = False):
        """Return partial coverage and mark, rather than hide, incomparable rows."""
        rows: list[dict] = []
        missing: list[dict] = []
        for entity in entities:
            result = self.metric_series(
                metric=metric, entity=entity, dataset=dataset, as_of=as_of,
                quality=quality)
            matches = [row for row in result["rows"] if row["period"] == period]
            if not matches:
                missing.append({
                    "entity_id": entity.upper(),
                    "status": (result["missing"] or {}).get("status", "period_missing"),
                    "reason": (result["missing"] or {}).get(
                        "reason", f"no_observation_for_{period}"),
                })
                continue
            rows.append(matches[-1])
        baseline = rows[0] if rows else None
        comparable_fields = (
            "period_start", "period_end", "unit", "currency", "period_basis", "adjustment")
        for row in rows:
            differences = [field for field in comparable_fields
                           if baseline and row.get(field) != baseline.get(field)]
            row["comparability"] = "comparable" if not differences else "incomparable"
            row["comparability_reasons"] = [f"{field}_differs" for field in differences]
        result = {
            "status": "ok" if rows else "no_coverage",
            "metric_id": metric, "period": period,
            "rows": rows, "missing": missing,
            "all_comparable": bool(rows) and all(
                row["comparability"] == "comparable" for row in rows),
        }
        return self._as_frame({**result, "rejected": []}) if as_frame else result

    def derive(self, *, operation: str, query_result: dict,
               version: str = "v1", output_metric: str = "",
               window: int | None = None, min_periods: int | None = None,
               statistic: str = "mean", fx_result: dict | None = None,
               right_result: dict | None = None,
               target_currency: str = "", convention: str = "multiply") -> dict:
        from ..structured import DerivationDefinition
        from ..structured.derivations import calculate

        parameters = {}
        if operation == "rolling":
            parameters = {
                "window": window,
                "min_periods": min_periods if min_periods is not None else window,
                "statistic": statistic,
            }
        if operation == "fx_convert":
            parameters = {"target_currency": target_currency, "convention": convention}
        input_metric = query_result.get("metric_id", "")
        input_metrics = [input_metric]
        if right_result and right_result.get("metric_id"):
            input_metrics.append(right_result["metric_id"])
        derivation_id = f"{operation}:{input_metric}:{output_metric or input_metric}"
        definition = DerivationDefinition(
            id=derivation_id, version=version, operation=operation,
            inputs=input_metrics, parameters=parameters,
            output_metric_id=output_metric or input_metric)
        self.structured.register_derivation(definition)
        rows = calculate(
            query_result.get("rows", []), definition,
            fx_rows=(fx_result or {}).get("rows"), target_currency=target_currency,
            right_rows=(right_result or {}).get("rows"), convention=convention)
        return {
            "status": "ok" if any(row["derivation_status"] == "ok" for row in rows)
            else "insufficient_inputs",
            "derivation": definition.model_dump(mode="json"),
            "rows": rows,
        }

    def financial_derived(self, *, metric: str, entity: str,
                          dataset: str = "company_financials",
                          as_of: datetime | None = None,
                          quality: str = "strict") -> dict:
        formulas = {
            "financial.free_cash_flow": (
                "subtract", "financial.cash_from_operations.gaap", "financial.capex.gaap"),
            "financial.gross_margin.gaap": (
                "divide", "financial.gross_profit.gaap", "financial.revenue.gaap"),
            "financial.operating_margin.gaap": (
                "divide", "financial.operating_income.gaap", "financial.revenue.gaap"),
        }
        if metric not in formulas:
            raise ValueError(f"no registered financial formula for {metric}")
        operation, left_metric, right_metric = formulas[metric]
        left = self.metric_series(
            metric=left_metric, entity=entity, dataset=dataset,
            as_of=as_of, quality=quality)
        right = self.metric_series(
            metric=right_metric, entity=entity, dataset=dataset,
            as_of=as_of, quality=quality)
        return self.derive(
            operation=operation, query_result=left, right_result=right,
            output_metric=metric, version="v1")

    def structured_sources(self) -> list[dict]:
        return self.structured.sources()

    def structured_datasets(self) -> list[dict]:
        return self.structured.datasets()

    def structured_metrics(self) -> list[dict]:
        return self.structured.metrics()

    def structured_catalog(self) -> dict:
        from ..structured import DataDiscovery

        return DataDiscovery(self.structured).catalog_view()

    def describe_structured(self, value: str, *, kind: str = "") -> dict:
        from ..structured import DataDiscovery

        return DataDiscovery(self.structured).describe(value, kind=kind)

    def structured_availability(self, *, entity: str = "", dataset: str = "") -> dict:
        from ..structured import DataDiscovery

        return DataDiscovery(self.structured).availability(entity=entity, dataset=dataset)

    def structured_examples(self, *, dataset: str = "") -> dict:
        from ..structured import DataDiscovery

        return DataDiscovery(self.structured).examples(dataset=dataset)

    # Stable discovery aliases for users and autonomous agents.
    def catalog(self) -> dict:
        return self.structured_catalog()

    def describe(self, value: str, *, kind: str = "") -> dict:
        return self.describe_structured(value, kind=kind)

    def availability(self, *, entity: str = "", dataset: str = "") -> dict:
        return self.structured_availability(entity=entity, dataset=dataset)

    def examples(self, *, dataset: str = "") -> dict:
        return self.structured_examples(dataset=dataset)

    # Stable discovery names; structured_* aliases remain explicit during migration.
    def sources(self) -> list[dict]:
        return self.structured_sources()

    def datasets(self) -> list[dict]:
        return self.structured_datasets()

    def metrics(self) -> list[dict]:
        return self.structured_metrics()

    def structured_health(self) -> list[dict]:
        return self.structured.source_health()

    def financial_quality(self, *, entity: str | None = None,
                          as_of: datetime | None = None) -> dict:
        from ..structured.quality import financial_quality

        rows = self.structured.observations(
            dataset_id="company_financials", entity_id=entity, as_of=as_of,
            latest_only=True, accepted_only=True, limit=100_000)
        return financial_quality(rows)

    def consensus_snapshot(self, *, entity: str,
                           as_of: datetime | None = None) -> dict:
        """Return the newest whole consensus snapshot visible at ``as_of``."""
        rows = self.structured.observations(
            dataset_id="market_consensus", source_id="yfinance_consensus",
            entity_id=entity, as_of=as_of, latest_only=False,
            accepted_only=True, limit=100_000)
        if not rows:
            gap = self._gap_status(
                dataset_id="market_consensus", source_id="yfinance_consensus",
                entity_id=entity.upper(), metric_id="consensus.eps.mean", as_of=as_of)
            return {"status": gap["status"], "entity_id": entity.upper(),
                    "known_at": None, "rows": [], "missing": gap}
        known_at = max(row["known_at"] for row in rows)
        snapshot_rows = [row for row in rows if row["known_at"] == known_at]
        return {
            "status": "ok", "entity_id": entity.upper(), "known_at": known_at,
            "rows": snapshot_rows, "missing": None,
            "target_periods": sorted({row["period"] for row in snapshot_rows
                                      if row["period_basis"] == "target_quarter"}),
        }

    def consensus_legacy_dict(self, *, entity: str,
                              as_of: datetime | None = None) -> dict:
        """Assemble the historical ``consensus.fetch`` dict from one governed vintage."""
        scalar_defaults = {
            "eps": None, "revenue": None, "eps_low": None, "eps_high": None,
            "revenue_low": None, "revenue_high": None,
            "target_mean": None, "target_median": None, "target_low": None,
            "target_high": None, "target_current": None,
            "rating_strong_buy": None, "rating_buy": None, "rating_hold": None,
            "rating_sell": None, "rating_strong_sell": None,
        }
        output = {**scalar_defaults, "rating_trend": [], "upgrades_downgrades": []}
        snapshot = self.consensus_snapshot(entity=entity, as_of=as_of)
        if snapshot["status"] != "ok":
            return output
        metric_to_key = {
            "consensus.eps.mean": "eps", "consensus.eps.low": "eps_low",
            "consensus.eps.high": "eps_high",
            "consensus.revenue.mean": "revenue",
            "consensus.revenue.low": "revenue_low",
            "consensus.revenue.high": "revenue_high",
            "consensus.price_target.mean": "target_mean",
            "consensus.price_target.median": "target_median",
            "consensus.price_target.low": "target_low",
            "consensus.price_target.high": "target_high",
            "consensus.rating.strong_buy_count": "strong_buy",
            "consensus.rating.buy_count": "buy",
            "consensus.rating.hold_count": "hold",
            "consensus.rating.sell_count": "sell",
            "consensus.rating.strong_sell_count": "strong_sell",
        }
        rating: dict[str, dict] = {}
        actions = []
        for row in snapshot["rows"]:
            metric = row["metric_id"]
            dimensions = json.loads(row.get("dimensions_json") or "{}")
            if metric.startswith("consensus.rating.") and metric.endswith("_count"):
                relative = dimensions.get("provider_relative_period", "0m")
                item = rating.setdefault(relative, {"period": relative})
                item[metric_to_key[metric]] = int(row["value"])
                if relative == "0m":
                    output[f"rating_{metric_to_key[metric]}"] = int(row["value"])
            elif metric == "consensus.rating.change":
                actions.append({
                    "date": row["period"], "firm": dimensions.get("firm") or None,
                    "to_grade": dimensions.get("to_grade") or None,
                    "from_grade": dimensions.get("from_grade") or None,
                    "action": dimensions.get("action") or None,
                })
            elif metric in metric_to_key:
                output[metric_to_key[metric]] = row["value"]
        order = lambda item: int(str(item[0]).removesuffix("m") or 0)
        output["rating_trend"] = [item for _, item in sorted(rating.items(), key=order,
                                                              reverse=True)]
        output["upgrades_downgrades"] = sorted(
            actions, key=lambda item: item["date"], reverse=True)[:8]
        return output

    def consensus_quality(self, *, entity: str | None = None,
                          as_of: datetime | None = None,
                          now: datetime | None = None) -> dict:
        from ..structured.quality import consensus_quality

        rows = self.structured.observations(
            dataset_id="market_consensus", source_id="yfinance_consensus",
            entity_id=entity, as_of=as_of, latest_only=False,
            accepted_only=True, limit=100_000)
        history = self.structured.ingestion_history(
            source_id="yfinance_consensus", dataset_id="market_consensus", limit=1)
        dataset = self.structured.dataset("market_consensus") or {}
        settings = json.loads(dataset.get("quality_json") or "{}")
        return consensus_quality(
            rows, now=now,
            freshness_hours_max=float(settings.get("freshness_hours_max", 168)),
            latest_ingestion_status=history[0]["status"] if history else "")

    def structured_conflicts(self, **filters) -> list[dict]:
        return self.structured.conflicts(**filters)

    def structured_pending_mappings(self, **filters) -> list[dict]:
        return self.structured.pending_mappings(**filters)

    def structured_ingestion_history(self, **filters) -> list[dict]:
        return self.structured.ingestion_history(**filters)

    def structured_quality_report(self, *, dataset: str | None = None,
                                  now: datetime | None = None) -> dict:
        from ..structured import build_quality_report

        return build_quality_report(self.structured, dataset_id=dataset, now=now)

    def structured_artifact_usage(self, *, source: str | None = None) -> dict:
        return self.structured.artifact_usage(source_id=source)

    def read_only_sql(self):
        return self.structured.open_read_only()

    def snapshot_manifest(self, *, consumer: str, purpose: str,
                          as_of: datetime, rows: list[dict],
                          metadata: dict | None = None) -> dict:
        items = []
        for row in rows:
            lineage_ids = row.get("lineage_observation_ids") or [
                row.get("observation_id", "")]
            for observation_id in lineage_ids:
                items.append({
                    "observation_id": observation_id,
                    "selected_source": row.get("selected_source") or row.get("source_id", ""),
                    "selection_reason": row.get("selection_reason", "explicit_input"),
                    "derivation_id": row.get("derivation_id", ""),
                    "derivation_version": row.get("derivation_version", ""),
                    "input_mode": row.get("input_mode", "persistent"),
                })
        return self.structured.create_snapshot(
            consumer=consumer, purpose=purpose, as_of=as_of,
            items=items, metadata=metadata)

    def replay_snapshot(self, snapshot_id: str) -> dict | None:
        return self.structured.replay_snapshot(snapshot_id)

    @staticmethod
    def compose_inputs(*, persistent: list[dict], runtime: list[dict]) -> dict:
        """Keep replayable facts and ephemeral market inputs visibly separated."""
        persistent_rows = [dict(row, input_mode="persistent") for row in persistent]
        runtime_rows = [dict(row, input_mode="runtime") for row in runtime]
        return {
            "persistent": persistent_rows,
            "runtime": runtime_rows,
            "snapshot_eligible": persistent_rows,
            "runtime_replayable": False,
        }

    def company_research_package(self, entity: str, *,
                                 since: datetime | None = None) -> dict:
        """Shared facts plus task-specific views for one economic entity."""
        key = entity.upper()
        return {
            "entity": key,
            "documents": self.store.documents(
                entity=key, published_since=since.isoformat() if since else None, limit=1000),
            "measurements": self.store.measurements(entity=key, limit=2000),
            "facts": self.store.facts(entity=key, since=since, limit=1000),
            "pead_projections": self.store.task_projections(
                profile="pead_research", target_type="entity", target_id=key, limit=500),
        }

    def claim_evidence_package(self, concept: str, *, limit: int = 500) -> dict:
        projections = self.store.fact_projections(concept=concept, limit=limit)
        fact_ids = {p["fact_id"] for p in projections}
        facts = {f["fact_id"]: f for f in self.store.facts(
            include_superseded=False, limit=max(limit * 4, 500)) if f["fact_id"] in fact_ids}
        return {
            "concept": concept,
            "evidence": [dict(p, fact=facts.get(p["fact_id"])) for p in projections],
            "missing_facts": sorted(fact_ids - set(facts)),
        }

    def search_documents(self, query: str, *, entity: str | None = None,
                         source_contains: str | None = None,
                         published_since: str | None = None,
                         limit: int = 20) -> list[dict]:
        return self.store.search_document_chunks(
            query, entity=entity, source_contains=source_contains,
            published_since=published_since, limit=limit)

    def health(self) -> dict:
        processing = self.store.document_processing(limit=5000)
        return {
            "structured_sources": self.store.data_source_health(),
            "document_sources": self.store.document_source_health(),
            "candidate_admission": self.store.document_candidate_health(),
            "processing": {
                "total": len(processing),
                "running": sum(r["status"] == "running" for r in processing),
                "failed": sum(r["status"] == "failed" for r in processing),
                "succeeded": sum(r["status"] == "succeeded" for r in processing),
            },
        }

    def quality(self) -> dict:
        """Queryable release-gate metrics for accepted and quarantined documents."""
        from ..data import document_assets

        candidates = self.store.document_candidates(limit=100_000)
        check_counts = {
            "identity": {"checked": 0, "passed": 0},
            "period": {"checked": 0, "passed": 0},
        }
        candidate_check_counts = {
            "identity": {"checked": 0, "passed": 0},
            "period": {"checked": 0, "passed": 0},
        }
        reasons: Counter[str] = Counter()
        for row in candidates:
            try:
                validation = json.loads(row.get("validation_json") or "{}")
            except json.JSONDecodeError:
                validation = {}
                reasons["invalid_validation_json"] += 1
            checks = validation.get("checks") or {}
            for name in candidate_check_counts:
                if name in checks:
                    candidate_check_counts[name]["checked"] += 1
                    candidate_check_counts[name]["passed"] += int(bool(checks[name]))
                    if row.get("status") == "accepted":
                        check_counts[name]["checked"] += 1
                        check_counts[name]["passed"] += int(bool(checks[name]))
            for issue in validation.get("issues") or ():
                reasons[str(issue.get("code") or "unknown_reason")] += 1

        inventory = self.store.document_quality_inventory()
        total_docs = sum(int(row["documents"] or 0) for row in inventory)
        full_docs = sum(int(row["documents"] or 0) for row in inventory
                        if row["completeness"] == "full")
        docs = self.store.documents(limit=100_000)
        consistency_issues: list[dict] = []
        checked = 0
        for row in docs:
            checked += 1
            version = self.store.latest_document_version(row["document_id"])
            if version is None:
                consistency_issues.append({
                    "document_id": row["document_id"], "reason": "version_missing"})
                continue
            body = document_assets.read_document(row["document_id"], store=self.store)
            if not body:
                consistency_issues.append({
                    "document_id": row["document_id"], "reason": "read_mismatch"})

        accepted_candidates = sum(row.get("status") == "accepted" for row in candidates)
        quarantined_candidates = sum(row.get("status") == "quarantined"
                                     for row in candidates)

        def ratio(passed: int, denominator: int) -> float | None:
            return round(passed / denominator, 6) if denominator else None

        return {
            "coverage": {
                "accepted_documents": total_docs,
                "candidates": len(candidates),
                "accepted_candidates": accepted_candidates,
                "quarantined_candidates": quarantined_candidates,
                "admission_rate": ratio(accepted_candidates, len(candidates)),
            },
            "correctness": {
                name: {**counts, "rate": ratio(counts["passed"], counts["checked"])}
                for name, counts in check_counts.items()
            },
            "candidate_checks": {
                name: {**counts, "rate": ratio(counts["passed"], counts["checked"])}
                for name, counts in candidate_check_counts.items()
            },
            "completeness": {
                "documents": total_docs, "full": full_docs,
                "rate": ratio(full_docs, total_docs),
            },
            "source_lag": [
                {"source_id": row["source_id"], "status": row.get("status"),
                 "snapshot_updated_at": row.get("snapshot_updated_at"),
                 "snapshot_lag_hours": row.get("snapshot_lag_hours")}
                for row in self.store.data_source_health()
            ],
            "read_consistency": {
                "checked": checked, "passed": checked - len(consistency_issues),
                "rate": ratio(checked - len(consistency_issues), checked),
                "issues": consistency_issues,
            },
            "reason_codes": dict(sorted(reasons.items())),
            "inventory": inventory,
        }

    def lineage(self, identifier: str) -> dict | None:
        structured = self.structured.lineage(identifier)
        return structured if structured is not None else self.store.projection_lineage(identifier)


def get_data_products() -> DataProducts:
    return DataProducts()
