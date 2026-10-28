"""Governed Sector CLI views; legacy rich reports remain on the legacy route."""
import json
from html import escape
from pathlib import Path

from ...agent.task_projection import ProjectionScope
from ...workflow.consumer_reads import read_projection
from ...workflow.cutover_routing import RouteUnavailable
from ...workflow.runtime_reads import current_read_context


def governed():
    from ...workflow.isolation import verified_isolation_root

    context = current_read_context()
    return context is not None and (context.route == "target" or verified_isolation_root() is not None)


def allocation(store, name, *, date=""):
    context = current_read_context()
    selected = None
    if date:
        rows = store.task_projection_envelopes(
            agent_role="sector_allocation", scope_kind="sector", scope_id=name, limit=10_000)
        selected = next((row for row in rows if str(row.get("as_of", ""))[:10] == date), None)
        if selected is None:
            raise RouteUnavailable("required Sector projection missing for date: " + date)
    row = read_projection(store, consumer="sector", role="sector_allocation",
        scope=ProjectionScope(kind="sector", id=name), at=context.cutoff if context else None,
        projection_id=selected["projection_id"] if selected else "",
        content_hash=selected["content_hash"] if selected else "")
    if row is None:
        raise RouteUnavailable("required Sector allocation projection missing or unusable")
    return row


def layer_inputs(store, cfg, overrides=None):
    from ..chief.assemble import _envelope_from_row

    result = {}
    for layer in cfg.layers:
        scope = ProjectionScope(kind="layer", id=layer.key)
        selected = overrides.get(layer.key) if overrides is not None else None
        if overrides is not None and selected is None:
            raise RouteUnavailable("required Sector LayerAnalysis missing: " + layer.key)
        row = read_projection(store, consumer="sector", role="layer_analysis", scope=scope,
            projection_id=selected.projection_id if selected else "",
            content_hash=selected.content_hash if selected else "")
        if row is None:
            raise RouteUnavailable("required Sector LayerAnalysis unavailable: " + layer.key)
        result[layer.key] = _envelope_from_row(row, scope)
    return result


def write_html(row, output_dir):
    root = Path(output_dir) if output_dir else None
    if root is None or not root.is_dir():
        return None
    payload = row["payload"]
    body = (f"<h1>{escape(payload['sector'])}</h1>"
            f"<p>{escape(row['as_of'])} · {escape(payload['stance'])} · "
            f"target weight {payload['target_weight']:.1%}</p>"
            f"<p>{escape(payload['rationale'])}</p>"
            f"<pre>{escape(json.dumps(payload, ensure_ascii=False, indent=2))}</pre>"
            f"<p>Projection {escape(row['projection_id'])}<br>SHA256 {escape(row['content_hash'])}</p>"
            f"<pre>{escape(json.dumps(row.get('input_refs') or [], ensure_ascii=False))}</pre>")
    path = root / f"sector-{payload['sector']}-{row['as_of'][:10]}.html"
    path.write_text('<!doctype html><html lang="zh"><meta charset="utf-8"><title>Sector allocation</title>'
                    + body + '</html>', encoding="utf-8")
    return path
