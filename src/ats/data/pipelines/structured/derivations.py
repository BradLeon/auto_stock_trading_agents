"""Versioned, query-time calculations over governed observation rows."""

from __future__ import annotations

from collections import defaultdict
import re
from statistics import mean

from ...core.structured_models import DerivationDefinition


_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
_FISCAL_QUARTER = re.compile(r"^FY(\d{4})Q([1-4])$")
_FISCAL_YEAR = re.compile(r"^FY(\d{4})$")


def _previous_period(period: str, operation: str) -> str | None:
    month = _MONTH.match(period)
    if month:
        year, number = int(month.group(1)), int(month.group(2))
        if operation == "yoy":
            return f"{year - 1:04d}-{number:02d}"
        return f"{year - 1:04d}-12" if number == 1 else f"{year:04d}-{number - 1:02d}"
    quarter = _FISCAL_QUARTER.match(period)
    if quarter:
        year, number = int(quarter.group(1)), int(quarter.group(2))
        if operation == "yoy":
            return f"FY{year - 1:04d}Q{number}"
        return f"FY{year - 1:04d}Q4" if number == 1 else f"FY{year:04d}Q{number - 1}"
    annual = _FISCAL_YEAR.match(period)
    if annual and operation == "yoy":
        return f"FY{int(annual.group(1)) - 1:04d}"
    return None


def _group_key(row: dict) -> tuple:
    return (
        row.get("entity_id"), row.get("metric_id"), row.get("source_id"),
        row.get("unit"), row.get("currency"), row.get("period_basis"),
        row.get("adjustment"), row.get("dimensions_json", "{}"),
    )


def _base_derived(row: dict, definition: DerivationDefinition,
                  *, value, status: str, reason: str,
                  inputs: list[dict]) -> dict:
    result = dict(row)
    result.update({
        "value": value,
        "derivation_status": status,
        "derivation_reason": reason,
        "derivation_id": definition.id,
        "derivation_version": definition.version,
        "derived_metric_id": definition.output_metric_id or row.get("metric_id", ""),
        "lineage_observation_ids": [item["observation_id"] for item in inputs
                                    if item.get("observation_id")],
        "input_values": [item.get("value") for item in inputs],
    })
    return result


def period_change(rows: list[dict], definition: DerivationDefinition) -> list[dict]:
    operation = definition.operation
    if operation not in {"yoy", "mom"}:
        raise ValueError(f"unsupported period change: {operation}")
    output: list[dict] = []
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        groups[_group_key(row)].append(row)
    for group in groups.values():
        indexed = {row["period"]: row for row in group}
        for row in sorted(group, key=lambda item: (item.get("period_end") or item["period"])):
            previous_period = _previous_period(row["period"], operation)
            previous = indexed.get(previous_period) if previous_period else None
            if previous is None:
                output.append(_base_derived(
                    row, definition, value=None, status="insufficient_history",
                    reason=f"missing_{operation}_comparison_period",
                    inputs=[row]))
                continue
            denominator = previous.get("value")
            if denominator == 0:
                output.append(_base_derived(
                    row, definition, value=None, status="undefined",
                    reason="comparison_value_is_zero", inputs=[previous, row]))
                continue
            value = (row["value"] / denominator) - 1.0
            output.append(_base_derived(
                row, definition, value=value, status="ok", reason="",
                inputs=[previous, row]))
    return output


def rolling_statistic(rows: list[dict], definition: DerivationDefinition) -> list[dict]:
    window = int(definition.parameters.get("window", 0))
    minimum = int(definition.parameters.get("min_periods", window))
    if window <= 0 or minimum <= 0 or minimum > window:
        raise ValueError("rolling window requires 0 < min_periods <= window")
    statistic = definition.parameters.get("statistic", "mean")
    if statistic not in {"mean", "sum", "min", "max"}:
        raise ValueError(f"unsupported rolling statistic: {statistic}")
    output: list[dict] = []
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        groups[_group_key(row)].append(row)
    functions = {"mean": mean, "sum": sum, "min": min, "max": max}
    for group in groups.values():
        ordered = sorted(group, key=lambda item: (item.get("period_end") or item["period"]))
        for index, row in enumerate(ordered):
            inputs = ordered[max(0, index - window + 1):index + 1]
            if len(inputs) < minimum:
                output.append(_base_derived(
                    row, definition, value=None, status="insufficient_history",
                    reason="rolling_window_incomplete", inputs=inputs))
                continue
            value = float(functions[statistic]([item["value"] for item in inputs]))
            output.append(_base_derived(
                row, definition, value=value, status="ok", reason="", inputs=inputs))
    return output


def fx_convert(rows: list[dict], fx_rows: list[dict], definition: DerivationDefinition,
               *, target_currency: str, convention: str = "multiply") -> list[dict]:
    """Convert using explicit, period-matched FX observations supplied by the caller."""
    if convention not in {"multiply", "divide"}:
        raise ValueError("FX convention must be multiply or divide")
    rates = {row["period"]: row for row in fx_rows if row.get("value") is not None}
    output = []
    for row in rows:
        rate = rates.get(row["period"])
        if rate is None:
            output.append(_base_derived(
                row, definition, value=None, status="missing_fx",
                reason="explicit_fx_observation_missing", inputs=[row]))
            continue
        if convention == "divide" and rate["value"] == 0:
            output.append(_base_derived(
                row, definition, value=None, status="undefined",
                reason="fx_rate_is_zero", inputs=[row, rate]))
            continue
        value = (row["value"] * rate["value"] if convention == "multiply"
                 else row["value"] / rate["value"])
        converted = _base_derived(
            row, definition, value=value, status="ok", reason="", inputs=[row, rate])
        converted.update({
            "original_value": row["value"],
            "original_currency": row.get("currency", ""),
            "currency": target_currency,
            "fx_observation_id": rate.get("observation_id", ""),
            "fx_rate": rate["value"],
            "fx_convention": convention,
        })
        output.append(converted)
    return output


def binary_operation(left_rows: list[dict], right_rows: list[dict],
                     definition: DerivationDefinition) -> list[dict]:
    """Period/source/basis matched subtraction or division with full input lineage."""
    operation = definition.operation
    if operation not in {"subtract", "divide"}:
        raise ValueError(f"unsupported binary operation: {operation}")
    keys = ("entity_id", "period", "source_id", "currency", "period_basis", "adjustment")
    right = {tuple(row.get(key) for key in keys): row for row in right_rows}
    output = []
    for left in left_rows:
        peer = right.get(tuple(left.get(key) for key in keys))
        if peer is None:
            output.append(_base_derived(
                left, definition, value=None, status="insufficient_inputs",
                reason="matching_right_input_missing", inputs=[left]))
            continue
        if operation == "divide" and peer["value"] == 0:
            output.append(_base_derived(
                left, definition, value=None, status="undefined",
                reason="division_by_zero", inputs=[left, peer]))
            continue
        value = (left["value"] - peer["value"] if operation == "subtract"
                 else left["value"] / peer["value"])
        row = _base_derived(
            left, definition, value=value, status="ok", reason="", inputs=[left, peer])
        if operation == "divide":
            row.update({"unit": "ratio", "currency": ""})
        output.append(row)
    return output


def calculate(rows: list[dict], definition: DerivationDefinition,
              *, fx_rows: list[dict] | None = None,
              right_rows: list[dict] | None = None,
              target_currency: str = "", convention: str = "multiply") -> list[dict]:
    if definition.operation in {"yoy", "mom"}:
        return period_change(rows, definition)
    if definition.operation == "rolling":
        return rolling_statistic(rows, definition)
    if definition.operation == "fx_convert":
        if not fx_rows or not target_currency:
            raise ValueError("FX conversion requires explicit fx_rows and target_currency")
        return fx_convert(rows, fx_rows, definition,
                          target_currency=target_currency, convention=convention)
    if definition.operation in {"subtract", "divide"}:
        if right_rows is None:
            raise ValueError("binary derivation requires explicit right_rows")
        return binary_operation(rows, right_rows, definition)
    raise ValueError(f"unsupported derivation operation: {definition.operation}")
