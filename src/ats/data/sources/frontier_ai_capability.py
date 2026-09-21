"""Frontier AI raw-capability source adapters.

The provider contract is intentionally small and stable.  The default runtime
uses free benchmark-maintainer Git/CSV/JSON/HTML artefacts.  Artificial
Analysis public evaluation pages are a low-frequency, policy-gated structured
page route; its paid API is never required.  Test fixtures are accepted only
when explicitly passed by tests and are marked test-only in metadata.

The adapter does not invent scores for missing cells.  Model and method metadata
are carried in dimensions/raw payloads and are versioned by a deterministic
methodology fingerprint.  A score is one immutable observation vintage; revised
values are appended by the normal structured ingestion pipeline.
"""

from __future__ import annotations

from datetime import datetime, timezone
import csv
import hashlib
import io
import json
import os
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener, urlopen

from ..core.structured_models import (
    AdapterArtifact, AdapterBatch, AdapterFailure, DiscoveryResult,
    DiscoveryStatus, FetchRequest, IngestionStatus, NativeRecord,
    ReferenceEntityInput, ReleaseCandidate,
)

SOURCE_ID = "frontier_ai_capability"
DATASET_ID = "frontier_ai_capability_benchmarks"
AA_MODELS_URL = "https://artificialanalysis.ai/api/v2/language/models"
AA_METHODOLOGY_URL = "https://artificialanalysis.ai/methodology/intelligence-benchmarking"
PUBLIC_SOURCE_VERSION = "frontier_public_benchmark/v1"
HARBOR_SUPABASE_URL = os.environ.get(
    "HARBOR_SUPABASE_URL", "https://ofhuhcpkvzjlejydnvyd.supabase.co"
)
# This is Harbor's public/anonymous publishable key, not a user credential.
# Operators may override it if Harbor rotates the public project key.
HARBOR_SUPABASE_PUBLISHABLE_KEY = os.environ.get(
    "HARBOR_SUPABASE_PUBLISHABLE_KEY",
    "sb_publishable_Z-vuQbpvpG-PStjbh4yE0Q_e-d3MTIH",
)
PUBLIC_ROUTES = {
    # LiveBench publishes dated CSV snapshots in a public Git repository.  The
    # date in this default route is replaced by the Git directory probe when
    # the repository exposes a newer table; operators may pin a URL in env.
    "livebench": {
        "transport": "git_html_directory",
        "url": "https://github.com/LiveBench/new-livebench/tree/main/public",
        "repo": "https://github.com/LiveBench/new-livebench",
    },
    "terminal_bench_4": {
        # Harbor Hub is the benchmark maintainer's canonical leaderboard.  Its
        # public leaderboard-read endpoint is anonymous and returns the exact
        # model, agent, effort, accuracy, confidence interval and trial count.
        # GitHub submission JSON remains an auditable fallback, not the primary
        # coverage route.
        "transport": "harbor_leaderboard_api",
        "url": f"{HARBOR_SUPABASE_URL}/functions/v1/leaderboard-read",
        "body": {"package": "terminal-bench/terminal-bench", "name": "4-0-0"},
        "repo": "https://github.com/harbor-framework/terminal-bench/tree/main/leaderboard/submissions",
    },
    "terminal_bench_science_01": {
        "transport": "public_json",
        "url": "https://www.terminal-bench-science.ai/api/leaderboard?package=terminal-bench-science%2Fterminal-bench-science&name=v0-1-eval",
    },
    "osworld_2": {
        "transport": "public_json",
        "url": "https://osworld-v2.xlang.ai/static/data/leaderboard/official-results.json?v=leaderboard-gpt56-cost-v1",
    },
    "automationbench_aa": {
        "transport": "public_html_structured",
        "url": "https://artificialanalysis.ai/evaluations/automationbench-aa",
        "repo": "https://github.com/zapier/AutomationBench",
    },
    "scicode": {
        "transport": "public_html_structured",
        "url": "https://artificialanalysis.ai/evaluations/scicode",
        "repo": "https://github.com/scicode-bench/SciCode",
    },
    "critpt": {
        "transport": "public_html_structured",
        "url": "https://artificialanalysis.ai/evaluations/critpt",
        "repo": "https://github.com/CritPt-Benchmark/CritPt",
    },
    "mmmu_pro": {
        "transport": "public_html_structured",
        "url": "https://artificialanalysis.ai/evaluations/mmmu-pro",
        "repo": "https://github.com/Zheng0428/MMMU-Pro",
    },
    "toolathlon_verified": {
        "transport": "official_html",
        "url": "https://toolathlon.xyz/docs/leaderboard",
        "repo": "https://toolathlon.xyz/docs/leaderboard",
    },
    "spreadsheetbench_2": {
        # The project page loads this first-party V2 leaderboard JSON directly.
        # Prefer the machine-readable payload over executing the page JavaScript.
        "transport": "official_json_event",
        "url": "https://spreadsheetbench.github.io/data/leaderboard-v2-full.json",
        "repo": "https://arxiv.org/abs/2606.29955",
    },
    "humanitys_last_exam": {
        "transport": "public_html_structured",
        "url": "https://artificialanalysis.ai/evaluations/humanitys-last-exam",
        "repo": "https://labs.scale.com/leaderboard/humanitys_last_exam",
    },
}
PARSER_VERSION = "frontier_ai_capability/v1"
COHORT_VERSION = "frontier_ai_flagship_cohort/v1"
PUBLIC_REQUEST_BUDGET = 64

LABS = (
    ("OPENAI", "OpenAI"), ("ANTHROPIC", "Anthropic"),
    ("GOOGLE", "Google / Google DeepMind"), ("XAI", "xAI"),
    ("DEEPSEEK", "DeepSeek"), ("MOONSHOT", "Moonshot / Kimi"),
    ("TENCENT", "Tencent"), ("ZAI", "Z.ai / GLM"),
    ("ALIBABA", "Alibaba / Qwen"),
)
BENCHMARKS = (
    "livebench", "automationbench_aa",
    "terminal_bench_4", "terminal_bench_science_01", "scicode",
    "critpt", "osworld_2", "mmmu_pro", "toolathlon_verified",
    "spreadsheetbench_2", "humanitys_last_exam",
)
DEFAULT_FLAGSHIP_MODEL_IDS = {
    "OPENAI": "gpt-6-astra",
    "ANTHROPIC": "claude-fable-5.1",
    "GOOGLE": "gemini-3.8-flash",
    "XAI": "grok-4.6",
    "DEEPSEEK": "deepseek-v4.1-flash",
    "MOONSHOT": "kimi-k3",
    "TENCENT": "hy4",
    "ZAI": "glm-5.3",
    "ALIBABA": "qwen3.8-max",
}
DEFAULT_FLAGSHIP_MODEL_NAMES = {
    "OPENAI": "GPT-6 Astra",
    "ANTHROPIC": "Claude Fable 5.1",
    "GOOGLE": "Gemini 3.8 Flash",
    "XAI": "Grok 4.6",
    "DEEPSEEK": "DeepSeek V4.1 Flash",
    "MOONSHOT": "Kimi K3",
    "TENCENT": "Tencent Hy4",
    "ZAI": "GLM-5.3",
    "ALIBABA": "Qwen3.8 Max",
}

# Exact source aliases for the reviewed flagship releases. These are an
# identity registry, not fuzzy matching rules: a nearby release such as
# ``glm-5.3-flash`` is deliberately absent and therefore cannot populate the
# ``glm-5.3`` column. Configuration suffixes are listed only when the public
# source explicitly evaluates that exact release under a declared effort.
FLAGSHIP_MODEL_SOURCE_ALIASES = {
    "OPENAI": {"gpt-6-astra", "gpt-6-astra-max", "openai/gpt-6-astra"},
    "ANTHROPIC": {
        "claude-fable-5.1", "claude-fable-5-1", "fable-5.1",
        "anthropic/claude-fable-5-1", "claude-fable-5-1-max-effort",
        "claude-fable-5.1-max-with-fallback",
    },
    "GOOGLE": {
        "gemini-3.8-flash", "gemini-3-8-flash", "gemini-3.8-flash-high",
        "gemini/gemini-3.8-flash",
    },
    "XAI": {"grok-4.6", "grok-4-6", "grok-4.6-high", "xai/grok-4.6"},
    "DEEPSEEK": {
        "deepseek-v4.1-flash", "deepseek-v4-1-flash", "deepseek-v4.1-flash-max",
    },
    "MOONSHOT": {"kimi-k3", "kimi-k3-max", "kimi-kimi-k3-max"},
    "TENCENT": {"hy4", "hy4-high", "tencent/hy4"},
    "ZAI": {"glm-5.3", "glm-5-3", "glm-5.3-max", "anthropic/glm-5.3"},
    "ALIBABA": {"qwen3.8-max", "qwen3-8-max", "qwen3.8-max-0902"},
}
BENCHMARK_METHODS: dict[str, dict[str, Any]] = {
    "livebench": {"label": "LiveBench", "method_version": "v1", "direction": "general dynamic reasoning",
                   "value_range": [0, 100], "score_semantics": "percent correct",
                   "b_eligible": False, "comparability_group": "livebench/v1"},
    "automationbench_aa": {"label": "AutomationBench-AA", "method_version": "aa-v1", "direction": "workflow automation",
                            "value_range": [0, 100], "score_semantics": "partial task objective share; guardrail violation scores zero",
                            "b_eligible": False, "comparability_group": "automationbench_aa/v1",
                            "event_ledger": True, "measurement_scope": "model_agent_stack_capability"},
    "terminal_bench_4": {"label": "Terminal-Bench 4.0", "method_version": "4.0", "direction": "agentic coding in terminal",
                          "value_range": [0, 100], "score_semantics": "strict pass@1 percent",
                          "b_eligible": True, "comparability_group": "terminal_bench/4.0"},
    "terminal_bench_science_01": {"label": "Terminal-Bench-Science 0.1", "method_version": "0.1", "direction": "scientific terminal tasks",
                                   "value_range": [0, 100], "score_semantics": "strict pass@1 percent",
                                   "b_eligible": True, "comparability_group": "terminal_bench_science/0.1"},
    "scicode": {"label": "SciCode", "method_version": "v1", "direction": "scientific coding and reasoning",
                 "value_range": [0, 100], "score_semantics": "task accuracy percent",
                 "b_eligible": True, "comparability_group": "scicode/v1"},
    "critpt": {"label": "CritPt", "method_version": "v1", "direction": "critical-point / research reasoning",
               "value_range": [0, 100], "score_semantics": "strict task success percent",
               "b_eligible": True, "comparability_group": "critpt/v1"},
    "osworld_2": {"label": "OSWorld 2.0", "method_version": "2.0-partial", "direction": "computer-use GUI tasks",
                  "value_range": [0, 100], "score_semantics": "partial completion percent",
                  "b_eligible": False, "comparability_group": "osworld_2/partial"},
    "mmmu_pro": {"label": "MMMU-Pro", "method_version": "v1", "direction": "multimodal knowledge and reasoning",
                 "value_range": [0, 100], "score_semantics": "accuracy percent",
                 "b_eligible": True, "comparability_group": "mmmu_pro/v1"},
    "toolathlon_verified": {"label": "Toolathlon Verified", "method_version": "verified-v1", "direction": "multi-tool and MCP workflow execution",
                             "value_range": [0, 100], "score_semantics": "verified task success percent",
                             "b_eligible": True, "comparability_group": "toolathlon_verified/v1",
                             "event_ledger": True, "measurement_scope": "model_agent_stack_capability"},
    "spreadsheetbench_2": {"label": "SpreadsheetBench 2", "method_version": "2", "direction": "spreadsheet modeling, debugging and visualization",
                            "value_range": [0, 100], "score_semantics": "task score; exact checks plus visualization judge where declared",
                            "b_eligible": True, "comparability_group": "spreadsheetbench_2/v1",
                            "event_ledger": True, "measurement_scope": "model_agent_stack_capability"},
    "humanitys_last_exam": {"label": "Humanity's Last Exam", "method_version": "v1", "direction": "frontier academic knowledge and reasoning",
                             "value_range": [0, 100], "score_semantics": "accuracy percent",
                             "b_eligible": True, "comparability_group": "humanitys_last_exam/v1",
                             "measurement_scope": "model_capability_proxy"},
}


class FrontierCapabilityError(ValueError):
    """The capability response cannot safely enter the governed store."""


def _configured_url_opener() -> Callable[..., Any]:
    """Build an opener from explicit HTTP(S) proxy settings when provided.

    Python's stdlib urllib has no SOCKS implementation.  Clash Verge should
    therefore expose its HTTP mixed-port (commonly ``127.0.0.1:7897``) via
    ``ATS_FRONTIER_AI_HTTP_PROXY``/``ATS_FRONTIER_AI_HTTPS_PROXY``.  If those
    variables are absent, the normal ``http_proxy``/``https_proxy`` environment
    handling remains in effect through ``urlopen``.
    """
    http_proxy = os.environ.get("ATS_FRONTIER_AI_HTTP_PROXY", "").strip()
    https_proxy = os.environ.get("ATS_FRONTIER_AI_HTTPS_PROXY", "").strip() or http_proxy
    proxies = {key: value for key, value in (("http", http_proxy), ("https", https_proxy)) if value}
    if not proxies:
        return urlopen
    return build_opener(ProxyHandler(proxies)).open


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    if isinstance(value, bytes):
        body = value
    elif isinstance(value, str):
        body = value.encode()
    else:
        body = _json(value).encode()
    return hashlib.sha256(body).hexdigest()


def _lab_id(value: Any) -> str:
    raw = str(value or "").strip().casefold()
    aliases = {
        "openai": "OPENAI", "anthropic": "ANTHROPIC", "google": "GOOGLE",
        "google deepmind": "GOOGLE", "gemini": "GOOGLE", "xai": "XAI",
        "grok": "XAI", "deepseek": "DEEPSEEK", "moonshot": "MOONSHOT",
        "kimi": "MOONSHOT", "tencent": "TENCENT", "z-ai": "ZAI", "zai": "ZAI",
        "glm": "ZAI", "alibaba": "ALIBABA", "qwen": "ALIBABA",
    }
    result = aliases.get(raw, str(value or "").strip().upper())
    if result not in {item[0] for item in LABS}:
        raise FrontierCapabilityError(f"unknown_lab:{value}")
    return result


def _benchmark_id(value: Any) -> str:
    raw = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    aliases = {"terminal_bench_4_0": "terminal_bench_4", "terminal_bench_4.0": "terminal_bench_4",
               "terminal_bench_science": "terminal_bench_science_01", "osworld": "osworld_2",
               "osworld_partial": "osworld_2", "osworld_strict": "osworld_2",
               "osworld_binary": "osworld_2"}
    result = aliases.get(raw, raw)
    if result not in BENCHMARKS:
        raise FrontierCapabilityError(f"unknown_benchmark:{value}")
    return result


def _score(value: Any, benchmark: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise FrontierCapabilityError(f"score_not_numeric:{value}") from exc
    low, high = BENCHMARK_METHODS[benchmark]["value_range"]
    if not low <= numeric <= high:
        raise FrontierCapabilityError(f"score_out_of_range:{benchmark}:{numeric}")
    return numeric


def _date(value: Any, *, fallback: str = "") -> str:
    text = str(value or fallback)[:10]
    if len(text) != 10:
        raise FrontierCapabilityError(f"invalid_date:{value}")
    try:
        datetime.fromisoformat(text)
    except ValueError as exc:
        raise FrontierCapabilityError(f"invalid_date:{value}") from exc
    return text


def _timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def method_fingerprint(method: dict[str, Any]) -> str:
    """Stable identity for task set/harness/grader/semantic revisions."""
    fields = {key: method.get(key) for key in (
        # A provider may bump a label for documentation without changing the
        # scoring protocol; actual protocol fields define comparability.
        "benchmark_id", "task_set", "tasks", "harness", "grader",
        "score_semantics", "inference_config", "reasoning_effort")}
    return _digest(fields)[:24]


def classify_method_change(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Conservatively classify a benchmark revision.

    Task-set, harness, grader or score semantic changes are breaking.  A version
    label alone is not enough to break comparability; sparse metadata is flagged
    for review instead of being silently joined.
    """
    breaking_fields = ("task_set", "tasks", "harness", "grader", "score_semantics", "inference_config")
    changed = [field for field in breaking_fields if old.get(field) != new.get(field)
               and (old.get(field) is not None or new.get(field) is not None)]
    if changed:
        classification = "breaking"
    elif method_fingerprint(old) == method_fingerprint(new):
        classification = "compatible"
    else:
        classification = "review_required"
    return {"classification": classification, "changed_fields": changed,
            "old_fingerprint": method_fingerprint(old), "new_fingerprint": method_fingerprint(new),
            "comparability_group": (new.get("comparability_group") or old.get("comparability_group") or "")}


def _default_payload() -> dict[str, Any]:
    """Return an empty, truthful no-login fallback.

    We must not manufacture benchmark scores when the paid AA endpoint is not
    entitled.  Operators can provide an allowed public snapshot through
    ``ATS_FRONTIER_AI_CAPABILITY_FIXTURE``; otherwise the source is reported as
    unavailable and the Observer renders NA cells.
    """
    return {"meta": {"version": "v1", "source": "no_login_fallback_unavailable"},
            "models": [], "methods": [], "scores": []}


def _infer_lab_id(model_name: Any, organization: Any = "") -> str | None:
    """Map public leaderboard labels to the governed nine-Lab universe."""
    text = f"{organization} {model_name}".casefold()
    for needles, lab in (
        (("openai", "gpt-", "gpt"), "OPENAI"),
        (("anthropic", "claude", "fable", "opus", "sonnet"), "ANTHROPIC"),
        (("google", "gemini"), "GOOGLE"),
        (("xai", "grok"), "XAI"),
        (("deepseek",), "DEEPSEEK"),
        (("moonshot", "kimi"), "MOONSHOT"),
        (("tencent", "hunyuan", "hy4", "hy3"), "TENCENT"),
        (("z.ai", "z-ai", "glm"), "ZAI"),
        (("alibaba", "qwen"), "ALIBABA"),
    ):
        if any(needle in text for needle in needles):
            return lab
    return None


def _clean_model_id(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9._/-]+", "-", text).strip("-")
    return text or "unknown-model"


def _display_model(value: Any) -> str:
    text = str(value or "").strip()
    return text or "Unknown model"


def _registered_model_release_id(lab: str, *, model_id: Any = "", model_name: Any = "",
                                 details_url: Any = "") -> str:
    """Resolve an evaluated configuration to an exact registered release.

    Artificial Analysis exposes the release slug in ``detailsUrl`` while its
    row label includes effort/configuration suffixes. Other public leaderboards
    expose stable but source-specific exact aliases. We preserve the evaluated
    variant as ``model_id`` and use this separate id only for the fixed flagship
    join. No prefix, family, or edit-distance matching is permitted.
    """
    candidates: list[str] = []
    if details_url:
        path = str(details_url).split("?", 1)[0].rstrip("/")
        candidates.append(path.rsplit("/", 1)[-1])
    candidates.extend((str(model_id or ""), str(model_name or "")))
    def release_base(candidate: Any) -> str:
        normalized = _clean_model_id(candidate)
        # These are evaluation settings, not model releases. Only this closed
        # suffix vocabulary is removed; product variants such as ``flash`` or
        # ``lite`` remain part of identity and therefore cannot be merged.
        return re.sub(
            r"-(?:xhigh|max|high|medium|low)(?:-effort)?(?:-with-fallback)?$",
            "", normalized,
        )

    normalized = {release_base(candidate) for candidate in candidates if candidate}
    aliases = {release_base(alias) for alias in FLAGSHIP_MODEL_SOURCE_ALIASES.get(lab, set())}
    if normalized & aliases:
        return DEFAULT_FLAGSHIP_MODEL_IDS[lab]
    if details_url and candidates:
        return _clean_model_id(candidates[0])
    return _clean_model_id(model_id or model_name)


def _reasoning_effort_from_label(value: Any) -> str:
    text = str(value or "").casefold()
    for effort in ("xhigh", "max", "high", "medium", "low"):
        if re.search(rf"(?:^|[-_ (]){effort}(?:$|[-_ )])", text):
            return effort
    return "default"


def _date_from_text(value: Any, fallback: str) -> str:
    match = re.search(r"(20\d{2})[-_](\d{2})[-_](\d{2})", str(value or ""))
    if match:
        return "-".join(match.groups())
    return fallback


def _public_model(models: dict[tuple[str, str], dict[str, Any]], *, lab: str,
                  model_id: str, display_name: str, score_date: str,
                  source_url: str, model_release_id: str = "") -> None:
    key = (lab, model_id)
    current = models.get(key)
    if current is None:
        models[key] = {"lab_id": lab, "model_id": model_id,
                       "display_name": display_name, "available": True,
                       "flagship": False, "lineage": model_id,
                       "model_release_id": model_release_id or model_id,
                       "release_date": score_date,
                       "source_url": source_url}
        return
    if score_date > str(current.get("release_date") or ""):
        current["release_date"] = score_date


def _parse_livebench_csv(payload: bytes, *, source_url: str, fetched_date: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Parse the official LiveBench dated CSV into model-level mean accuracy."""
    text = payload.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict[str, Any]] = []
    for row in reader:
        model_text = row.get("model") or row.get("model_name") or row.get("name")
        lab = _infer_lab_id(model_text)
        if not lab:
            continue
        scores = []
        for key, value in row.items():
            if key in {"model", "model_name", "name"} or value in (None, "", "NA", "N/A"):
                continue
            try:
                score = float(value)
            except (TypeError, ValueError):
                continue
            if 0 <= score <= 100:
                scores.append(score)
        if not scores:
            continue
        model_id = _clean_model_id(model_text)
        rows.append({"lab_id": lab, "model_id": model_id, "model_name": _display_model(model_text),
                     "benchmark_id": "livebench", "score": round(sum(scores) / len(scores), 4),
                     "score_as_of": _date_from_text(source_url, fetched_date),
                     "source_type": "third_party_evaluation", "measurement_scope": "model_capability_proxy",
                     "source_transport": "git_csv", "source_url": source_url,
                     "sample_size": None, "confidence_low": None, "confidence_high": None,
                     "method_version": "v1", "task_set": "dated_public_csv"})
    return rows, {"record_count": len(rows), "score_semantics": "mean category accuracy percent"}


def _parse_terminal_submission(obj: dict[str, Any], *, source_url: str, fetched_date: str,
                               benchmark_id: str = "terminal_bench_4",
                               source_transport: str = "git_json") -> dict[str, Any] | None:
    metadata = obj.get("metadata") or {}
    metrics = obj.get("metrics") or {}
    model_name = ((metadata.get("model_display") or {}).get("label")
                  or (metadata.get("model_org") or {}).get("label")
                  or (obj.get("source_filter") or {}).get("model_name"))
    organization = ((metadata.get("model_org") or {}).get("label")
                    or (metadata.get("agent_org") or {}).get("label"))
    lab = _infer_lab_id(model_name, organization)
    accuracy = metrics.get("accuracy")
    if not lab or accuracy is None:
        return None
    try:
        accuracy = float(accuracy)
    except (TypeError, ValueError):
        return None
    if not 0 <= accuracy <= 100:
        return None
    model_id = _clean_model_id((obj.get("source_filter") or {}).get("model_name") or model_name)
    return {"lab_id": lab, "model_id": model_id, "model_name": _display_model(model_name),
            "benchmark_id": benchmark_id, "score": accuracy,
            "score_as_of": _date_from_text(metadata.get("date") or metadata.get("display_date"), fetched_date),
            "source_type": "third_party_evaluation", "measurement_scope": "model_agent_stack_capability",
            "source_transport": source_transport, "source_url": source_url,
            "sample_size": metrics.get("n_trials"),
            "confidence_low": (round(accuracy - float(metrics["accuracy_ci95_half_width"]), 6)
                               if metrics.get("accuracy_ci95_half_width") is not None else None),
            "confidence_high": (round(accuracy + float(metrics["accuracy_ci95_half_width"]), 6)
                                if metrics.get("accuracy_ci95_half_width") is not None else None),
            "harness": (metadata.get("agent_display") or {}).get("label", ""),
            "reasoning_effort": metadata.get("reasoning_effort") or (obj.get("source_filter") or {}).get("reasoning_effort", "default"),
            "method_version": "4.0", "task_set": "terminal-bench-4.0"}


def _walk_dicts(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        found.append(value)
        for child in value.values():
            found.extend(_walk_dicts(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_walk_dicts(child))
    return found


def _parse_generic_public_json(payload: bytes, *, source_url: str, fetched_date: str,
                               benchmark_id: str, measurement_scope: str,
                               source_transport: str = "public_json",
                               source_type: str = "third_party_evaluation",
                               event_only: bool = False) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Best-effort parser for official JSON leaderboards with explicit NA fallback.

    It only emits rows when both a model label and a numeric accuracy/success
    field are present.  Unknown schemas therefore fail closed to coverage, never
    to invented values.
    """
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return [], {"record_count": 0, "parser_drift": True, "structured_only": True}
    rows: list[dict[str, Any]] = []
    for item in _walk_dicts(decoded):
        model_name = item.get("model") or item.get("model_name") or item.get("model_display") or item.get("name")
        score = item.get("accuracy")
        if score is None:
            score = item.get("success_rate") or item.get("success") or item.get("score")
        if isinstance(model_name, dict):
            model_name = model_name.get("label") or model_name.get("name")
        lab = _infer_lab_id(model_name, item.get("organization") or item.get("provider") or item.get("lab"))
        if not lab or score is None:
            continue
        try:
            score = float(score)
        except (TypeError, ValueError):
            continue
        if not 0 <= score <= 100:
            continue
        model_id = _clean_model_id(model_name)
        rows.append({"lab_id": lab, "model_id": model_id, "model_name": _display_model(model_name),
                     "benchmark_id": benchmark_id, "score": score,
                     "score_as_of": _date_from_text(item.get("date") or item.get("as_of"), fetched_date),
                     "source_type": source_type, "measurement_scope": measurement_scope,
                     "source_transport": source_transport, "source_url": source_url,
                     "sample_size": item.get("n_trials") or item.get("trials"),
                     "confidence_low": item.get("confidence_low"), "confidence_high": item.get("confidence_high"),
                     "verified_status": item.get("verified_status") or item.get("verification_status") or item.get("verified"),
                     "harness": item.get("harness") or item.get("agent_harness") or item.get("agent"),
                     "grader": item.get("grader") or item.get("judge") or item.get("judge_model"),
                     "task_set": item.get("task_set") or item.get("task_set_id") or item.get("tasks"),
                     "metric_semantic": item.get("metric_semantic") or item.get("metric") or item.get("score_semantics"),
                     "inference_config": item.get("inference_config") or item.get("config"),
                     "reasoning_effort": item.get("reasoning_effort") or item.get("effort"),
                     "method_version": BENCHMARK_METHODS[benchmark_id]["method_version"],
                     "event_only": event_only,
                     "uniform_matrix": not event_only})
    return rows, {"record_count": len(rows), "parser_drift": not bool(rows), "structured_only": True,
                  "score_semantics": BENCHMARK_METHODS[benchmark_id]["score_semantics"]}


def _parse_terminal_bench_science_json(payload: bytes, *, source_url: str,
                                       fetched_date: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Parse Terminal-Bench-Science's published nested leaderboard schema."""
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return [], {"record_count": 0, "parser_drift": True, "structured_only": True}
    rows: list[dict[str, Any]] = []
    for item in decoded.get("rows", []) if isinstance(decoded, dict) else []:
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata") or {}
        metrics = item.get("metrics") or {}
        model_name = ((metadata.get("model_display") or {}).get("label")
                      or metadata.get("model") or "")
        organization = ((metadata.get("model_org") or {}).get("label")
                        or metadata.get("organization") or "")
        score = metrics.get("accuracy")
        lab = _infer_lab_id(model_name, organization)
        if not lab or score is None:
            continue
        try:
            score = float(score)
        except (TypeError, ValueError):
            continue
        if not 0 <= score <= 100:
            continue
        half_width = metrics.get("accuracy_stderr")
        try:
            half_width = float(half_width) * 1.96 if half_width is not None else None
        except (TypeError, ValueError):
            half_width = None
        rows.append({
            "lab_id": lab,
            "model_id": _clean_model_id(model_name),
            "model_name": _display_model(model_name),
            "benchmark_id": "terminal_bench_science_01",
            "score": score,
            "score_as_of": _date_from_text(metadata.get("model_release_date") or metadata.get("date"), fetched_date),
            "source_type": "third_party_evaluation",
            "measurement_scope": "model_agent_stack_capability",
            "source_transport": "public_json_nested",
            "source_url": source_url,
            "sample_size": metrics.get("tasks") or metrics.get("n_trials"),
            "confidence_low": round(score - half_width, 6) if half_width is not None else None,
            "confidence_high": round(score + half_width, 6) if half_width is not None else None,
            "harness": ((metadata.get("agent_display") or {}).get("label") or metadata.get("agent")),
            "reasoning_effort": metadata.get("reasoning_effort"),
            "method_version": "0.1",
            "task_set": "terminal-bench-science/v0-1-eval",
            "metric_semantic": "strict pass@1 percent",
            "inference_config": metadata.get("inference_config"),
            "event_only": False,
            "uniform_matrix": True,
        })
    return rows, {"record_count": len(rows), "parser_drift": not bool(rows),
                  "structured_only": True,
                  "score_semantics": BENCHMARK_METHODS["terminal_bench_science_01"]["score_semantics"]}


def _parse_osworld_json(payload: bytes, *, source_url: str, fetched_date: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Parse OSWorld 2.0 official results using its partial-score column.

    OSWorld also publishes ``binaryAccuracy``.  The registered route is the
    partial-score series, so it is intentionally A-only; a future strict route
    can use the same parser with an explicit binary metric and comparability
    group rather than silently mixing the two semantics.
    """
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return [], {"record_count": 0, "parser_drift": True, "structured_only": True}
    if not isinstance(decoded, dict) or not isinstance(decoded.get("results"), list):
        return [], {"record_count": 0, "parser_drift": True, "structured_only": True}
    rows: list[dict[str, Any]] = []
    updated = _date_from_text(decoded.get("updatedAt"), fetched_date)
    for item in decoded["results"]:
        if not isinstance(item, dict):
            continue
        model_name = item.get("model") or ""
        lab = _infer_lab_id(model_name, item.get("modelFamily") or "")
        score = item.get("partialScore")
        if not lab or score is None:
            continue
        try:
            score = float(score)
        except (TypeError, ValueError):
            continue
        if not 0 <= score <= 100:
            continue
        rows.append({
            "lab_id": lab,
            "model_id": _clean_model_id(model_name),
            "model_name": _display_model(model_name),
            "benchmark_id": "osworld_2",
            "score": score,
            "score_as_of": updated,
            "source_type": "third_party_evaluation",
            "measurement_scope": "model_agent_stack_capability",
            "source_transport": "public_json_nested",
            "source_url": source_url,
            "sample_size": None,
            "confidence_low": None,
            "confidence_high": None,
            "harness": item.get("toolSetting"),
            "reasoning_effort": item.get("reasoning"),
            "method_version": str(decoded.get("benchmarkVersion") or "2.0-partial"),
            "task_set": str(item.get("datasetScope") or decoded.get("taskVersion") or "OSWorld 2.0"),
            "metric_semantic": "partial completion percent",
            "inference_config": {"step_budget": item.get("stepBudget"), "release_version": item.get("releaseVersion")},
            "event_only": False,
            "uniform_matrix": True,
        })
    return rows, {"record_count": len(rows), "parser_drift": not bool(rows),
                  "structured_only": True, "metric": "partialScore",
                  "score_semantics": BENCHMARK_METHODS["osworld_2"]["score_semantics"]}


def _parse_aa_dataset_documents(documents: list[Any], *, source_url: str, fetched_date: str,
                                benchmark_id: str, measurement_scope: str,
                                event_only: bool = False) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Parse Artificial Analysis' free JSON-LD dataset payloads.

    The public evaluation pages expose a ``Dataset`` JSON-LD object whose
    ``data`` array contains one row per model and a benchmark-specific score
    field (for example ``SciCode`` or ``MMMU-Pro``).  This is not the paid API
    and does not require a login.  We only accept the named score dataset; cost,
    token and latency datasets are deliberately ignored.
    """
    score_keys = {
        "automationbench_aa": ("automationBenchScore",),
        "scicode": ("SciCode",),
        "critpt": ("CritPt",),
        "mmmu_pro": ("MMMU-Pro",),
        "humanitys_last_exam": ("Humanity's Last Exam",),
    }.get(benchmark_id, ())
    rows: list[dict[str, Any]] = []
    dataset_count = 0
    for document in documents:
        if not isinstance(document, dict) or not isinstance(document.get("data"), list):
            continue
        name = str(document.get("name") or "")
        dataset_name_tokens = {
            "automationbench_aa": ("automationbench-aa",),
            "scicode": ("scicode",),
            "critpt": ("critpt",),
            "mmmu_pro": ("mmmu-pro",),
            "humanitys_last_exam": ("humanity's last exam",),
        }.get(benchmark_id, ())
        if not any(token in name.casefold() for token in dataset_name_tokens):
            continue
        dataset_count += 1
        for item in document["data"]:
            if not isinstance(item, dict):
                continue
            model_name = item.get("label") or item.get("model") or item.get("model_name")
            score_key = next((key for key in score_keys if item.get(key) is not None), None)
            if not model_name or score_key is None:
                continue
            try:
                score = float(item[score_key]) * 100.0
            except (TypeError, ValueError):
                continue
            if not 0 <= score <= 100:
                continue
            lab = _infer_lab_id(model_name)
            if not lab:
                continue
            details_url = item.get("detailsUrl") or ""
            model_id = _clean_model_id(model_name)
            rows.append({
                "lab_id": lab,
                "model_id": model_id,
                "model_release_id": _registered_model_release_id(
                    lab, model_id=model_id, model_name=model_name, details_url=details_url),
                "model_name": _display_model(model_name),
                "benchmark_id": benchmark_id,
                "score": round(score, 6),
                "score_as_of": fetched_date,
                "source_type": "third_party_evaluation",
                "measurement_scope": measurement_scope,
                "source_transport": "public_html_jsonld",
                "source_url": source_url,
                "sample_size": None,
                "confidence_low": None,
                "confidence_high": None,
                "method_version": BENCHMARK_METHODS[benchmark_id]["method_version"],
                "task_set": name,
                "metric_semantic": BENCHMARK_METHODS[benchmark_id]["score_semantics"],
                "details_url": details_url,
                "reasoning_effort": _reasoning_effort_from_label(model_name),
                "event_only": event_only,
                "uniform_matrix": not event_only,
            })
    unique: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        unique[(row["lab_id"], row["model_id"], row["benchmark_id"])] = row
    return list(unique.values()), {
        "record_count": len(unique), "score_dataset_count": dataset_count,
        "structured_only": True, "parser_drift": not bool(unique),
        "score_semantics": BENCHMARK_METHODS[benchmark_id]["score_semantics"],
    }


def _embedded_json_documents(payload: bytes) -> list[Any]:
    """Extract JSON-bearing script payloads from a public HTML page.

    This deliberately handles only explicit JSON script blocks and Next.js
    ``__NEXT_DATA__``.  It does not execute JavaScript or scrape rendered
    pixels, and returns an empty list when the page is not structurally
    inspectable.
    """
    text = payload.decode("utf-8", errors="replace")
    candidates = re.findall(
        r'<script[^>]*(?:type=["\'](?:application/json|application/ld\+json)["\']|id=["\']__NEXT_DATA__["\'])[^>]*>(.*?)</script>',
        text, flags=re.I | re.S,
    )
    documents: list[Any] = []
    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue
        try:
            documents.append(json.loads(candidate))
        except json.JSONDecodeError:
            continue
    return documents


def _parse_structured_html_scores(payload: bytes, *, source_url: str, fetched_date: str,
                                  benchmark_id: str, measurement_scope: str,
                                  source_type: str = "third_party_evaluation",
                                  event_only: bool = False) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Parse explicit structured score payloads embedded in public HTML."""
    documents = _embedded_json_documents(payload)
    rows: list[dict[str, Any]] = []
    for document in documents:
        encoded = json.dumps(document, ensure_ascii=False).encode("utf-8")
        parsed, _meta = _parse_generic_public_json(
            encoded, source_url=source_url, fetched_date=fetched_date,
            benchmark_id=benchmark_id, measurement_scope=measurement_scope,
            source_transport="public_html_structured", source_type=source_type,
            event_only=event_only,
        )
        rows.extend(parsed)
    # Artificial Analysis uses JSON-LD ``Dataset`` objects for its free public
    # evaluation pages.  Parse only the benchmark's score dataset, not the
    # adjacent cost/token/latency datasets.
    aa_rows, aa_meta = _parse_aa_dataset_documents(
        documents, source_url=source_url, fetched_date=fetched_date,
        benchmark_id=benchmark_id, measurement_scope=measurement_scope,
        event_only=event_only,
    )
    rows.extend(aa_rows)
    unique: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["lab_id"], row["model_id"], row["benchmark_id"], row["score_as_of"])
        unique[key] = row
    return list(unique.values()), {
        "record_count": len(unique), "embedded_document_count": len(documents),
        "score_semantics": BENCHMARK_METHODS[benchmark_id]["score_semantics"],
        "structured_only": True,
        "parser_drift": not bool(unique),
        "score_dataset_count": aa_meta.get("score_dataset_count", 0),
    }


class _ToolathlonTableParser(HTMLParser):
    """Small, fail-closed parser for the public Toolathlon leaderboard table."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[dict[str, str]] = []
        self._row: dict[str, str] | None = None
        self._cell_label = ""
        self._cell_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "tr":
            self._row = {}
        elif tag == "td" and self._row is not None:
            self._cell_label = str(attrs_dict.get("data-label") or "").strip()
            self._cell_parts = []

    def handle_data(self, data: str) -> None:
        if self._row is not None and self._cell_label:
            self._cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self._row is not None and self._cell_label:
            value = re.sub(r"\s+", " ", "".join(self._cell_parts)).strip()
            self._row[self._cell_label] = value
            self._cell_label = ""
            self._cell_parts = []
        elif tag == "tr" and self._row:
            self.rows.append(self._row)
            self._row = None


def _parse_toolathlon_verified(payload: bytes, *, source_url: str, fetched_date: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Parse the official Toolathlon Verified table when its HTML is explicit.

    The page may expose a JSON script or a Markdown-like table.  Unknown page
    revisions fail closed to zero rows and are represented as coverage rather
    than inferred scores.
    """
    rows, metadata = _parse_structured_html_scores(
        payload, source_url=source_url, fetched_date=fetched_date,
        benchmark_id="toolathlon_verified", measurement_scope="model_agent_stack_capability",
        source_type="third_party_evaluation", event_only=False,
    )
    if rows:
        return rows, metadata
    parser = _ToolathlonTableParser()
    parser.feed(payload.decode("utf-8", errors="replace"))
    parsed: list[dict[str, Any]] = []
    for item in parser.rows:
        model_name = item.get("Model") or item.get("model") or ""
        score_text = item.get("Pass@1") or item.get("Pass @ 1") or ""
        match = re.search(r"(\d+(?:\.\d+)?)", score_text)
        if not model_name or not match:
            continue
        score = float(match.group(1))
        lab = _infer_lab_id(model_name, item.get("Model Type") or item.get("Provider"))
        if not lab or not 0 <= score <= 100:
            continue
        ci_match = re.search(r"±\s*(\d+(?:\.\d+)?)", score_text)
        parsed.append({
            "lab_id": lab,
            "model_id": _clean_model_id(model_name),
            "model_name": _display_model(model_name),
            "benchmark_id": "toolathlon_verified",
            "score": score,
            "score_as_of": _date_from_text(item.get("Date"), fetched_date),
            "source_type": "third_party_evaluation",
            "measurement_scope": "model_agent_stack_capability",
            "source_transport": "official_html_table",
            "source_url": source_url,
            "sample_size": None,
            "confidence_low": round(score - float(ci_match.group(1)), 6) if ci_match else None,
            "confidence_high": round(score + float(ci_match.group(1)), 6) if ci_match else None,
            "verified_status": "verified" if "✓" in model_name or "verified" in payload.decode("utf-8", errors="replace").casefold() else None,
            "harness": item.get("Agent") or item.get("Harness"),
            "method_version": "verified-v1",
            "task_set": "Toolathlon Verified public leaderboard",
            "event_only": False,
            "uniform_matrix": True,
        })
    if parsed:
        return parsed, {"record_count": len(parsed), "structured_only": True,
                        "parser_drift": False,
                        "score_semantics": BENCHMARK_METHODS["toolathlon_verified"]["score_semantics"]}
    text = payload.decode("utf-8", errors="replace")
    parsed: list[dict[str, Any]] = []
    pattern = re.compile(r"(Kimi\s+K3|Hy3|GLM[- ]?5\.3(?:[- ]?Flash)?|Claude\s+Opus\s+4\.8)\D{0,100}(\d{1,3}(?:\.\d+)?)\s*%", re.I)
    for match in pattern.finditer(text):
        model_name, score_text = match.groups()
        score = float(score_text)
        lab = _infer_lab_id(model_name)
        if lab and 0 <= score <= 100:
            parsed.append({"lab_id": lab, "model_id": _clean_model_id(model_name),
                           "model_name": model_name, "benchmark_id": "toolathlon_verified",
                           "score": score, "score_as_of": fetched_date,
                           "source_type": "third_party_evaluation",
                           "measurement_scope": "model_agent_stack_capability",
                           "source_transport": "official_html", "source_url": source_url,
                           "method_version": "verified-v1", "event_only": False,
                           "uniform_matrix": True})
    return parsed, {"record_count": len(parsed), "structured_only": False,
                    "parser_drift": not bool(parsed),
                    "score_semantics": BENCHMARK_METHODS["toolathlon_verified"]["score_semantics"]}


def _parse_readme_scores(payload: bytes, *, source_url: str, fetched_date: str,
                         benchmark_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Parse an official Markdown leaderboard table when one is published."""
    text = payload.decode("utf-8", errors="replace")
    lines = text.splitlines()
    parsed: dict[str, dict[str, Any]] = {}
    header: list[str] | None = None
    for line in lines:
        if not line.strip().startswith("|"):
            if header is not None:
                # Stop at the next prose block; a later table is often a
                # public-split variant and should not be mixed into one series.
                header = None
            continue
        cells = [re.sub(r"[*`]", "", part.strip()) for part in line.strip().strip("|").split("|")]
        if not cells:
            continue
        lowered = [cell.casefold() for cell in cells]
        if "model" in lowered and any("pass rate" in cell or "avg" in cell for cell in lowered):
            header = cells
            continue
        if header is None or set(cells) <= {"-", ":", "--"}:
            continue
        if len(cells) != len(header):
            continue
        model_idx = next((i for i, cell in enumerate(header) if cell.casefold() == "model"), None)
        score_idx = next((i for i, cell in enumerate(header)
                          if "pass rate" in cell.casefold() or cell.casefold() in {"avg", "average", "avg."}), None)
        if model_idx is None or score_idx is None:
            continue
        model_name = cells[model_idx]
        if not model_name or model_name.casefold() in {"closed source", "open source", "model"}:
            continue
        score_text = cells[score_idx].replace("%", "").replace(",", "").strip()
        try:
            score = float(score_text)
        except ValueError:
            continue
        lab = _infer_lab_id(model_name)
        if not lab or not 0 <= score <= 100:
            continue
        model_id = _clean_model_id(model_name)
        scope = "model_agent_stack_capability" if benchmark_id in {
            "automationbench_aa", "osworld_2", "terminal_bench_4", "terminal_bench_science_01",
            "toolathlon_verified", "spreadsheetbench_2",
        } else "model_capability_proxy"
        parsed[model_id] = {"lab_id": lab, "model_id": model_id, "model_name": model_name,
                            "benchmark_id": benchmark_id, "score": score,
                            "score_as_of": _date_from_text(text, fetched_date),
                            "source_type": "third_party_evaluation", "measurement_scope": scope,
                            "source_transport": "git_readme", "source_url": source_url,
                            "method_version": BENCHMARK_METHODS[benchmark_id]["method_version"],
                            "task_set": "official_readme_table", "uniform_matrix": True}
    return list(parsed.values()), {"record_count": len(parsed), "score_semantics": "official leaderboard table"}


def normalize_payload(payload: bytes | str | dict[str, Any], *, fetched_at: datetime | None = None,
                      source_url: str = AA_MODELS_URL) -> tuple[list[NativeRecord], list[ReferenceEntityInput], dict[str, Any]]:
    fetched_at = fetched_at or _now()
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise FrontierCapabilityError("invalid_json") from exc
    if not isinstance(payload, dict):
        raise FrontierCapabilityError("payload_not_object")
    models = payload.get("models")
    methods = payload.get("methods") or payload.get("benchmark_methods") or []
    scores = payload.get("scores") or payload.get("benchmark_scores") or []
    data = payload.get("data")
    if isinstance(data, dict):
        models = models or data.get("models")
        methods = methods or data.get("methods") or data.get("benchmark_methods") or []
        scores = scores or data.get("scores") or data.get("benchmark_scores") or []
    elif isinstance(data, list) and not models:
        # Artificial Analysis commonly returns one model object per row with
        # benchmark scores nested under ``benchmarks``/``scores``.
        models = []
        flattened: list[dict[str, Any]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            model_id = item.get("model_id") or item.get("id") or item.get("slug")
            lab = item.get("lab_id") or item.get("lab") or item.get("provider") or item.get("organization") or item.get("creator")
            if model_id and lab:
                models.append({k: item.get(k) for k in ("lab_id", "lab", "provider", "organization", "creator", "model_id", "id", "slug", "display_name", "model_name", "name", "available", "flagship", "aliases", "release_date") if k in item})
                nested = item.get("benchmarks") or item.get("scores") or {}
                if isinstance(nested, dict):
                    for bid, value in nested.items():
                        flattened.append({"lab_id": lab, "model_id": model_id, "benchmark_id": bid,
                                          **(value if isinstance(value, dict) else {"score": value}),
                                          "source_type": item.get("source_type", "third_party_evaluation")})
                elif isinstance(nested, list):
                    flattened.extend({"lab_id": lab, "model_id": model_id, **entry} for entry in nested if isinstance(entry, dict))
        scores = scores or flattened
    if not isinstance(models, list) or not isinstance(scores, list):
        raise FrontierCapabilityError("schema_drift:models_or_scores_not_list")
    model_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    entities: list[ReferenceEntityInput] = []
    for model in models:
        if not isinstance(model, dict):
            raise FrontierCapabilityError("schema_drift:model_not_object")
        lab = _lab_id(model.get("lab_id") or model.get("lab") or model.get("provider") or
                      model.get("organization") or model.get("creator"))
        model_id = str(model.get("model_id") or model.get("id") or model.get("slug") or "").strip()
        if not model_id:
            raise FrontierCapabilityError("model_identity_missing")
        available = bool(model.get("available", True))
        model_copy = {**model, "lab_id": lab, "model_id": model_id, "available": available}
        model_by_key[(lab, model_id)] = model_copy
        entities.append(ReferenceEntityInput(entity_id=f"FRONTIER_MODEL:{lab}:{model_id}".upper(), kind="frontier_model",
                                             canonical_name=str(model.get("display_name") or model.get("model_name") or model.get("name") or model_id),
                                             aliases=list(model.get("aliases") or []), metadata=model_copy))
    # Method metadata is optional for API responses; use the governed registry as fallback.
    method_by_id: dict[str, dict[str, Any]] = {key: dict(value) for key, value in BENCHMARK_METHODS.items()}
    for method in methods:
        if not isinstance(method, dict):
            continue
        bid = _benchmark_id(method.get("benchmark_id") or method.get("id"))
        method_by_id[bid].update(method)
    records: list[NativeRecord] = []
    seen: set[tuple[str, str, str, str]] = set()
    for row in scores:
        if not isinstance(row, dict):
            raise FrontierCapabilityError("schema_drift:score_not_object")
        lab = _lab_id(row.get("lab_id") or row.get("lab") or row.get("provider") or
                      row.get("organization") or row.get("creator"))
        model_id = str(row.get("model_id") or row.get("model") or "").strip()
        benchmark_value = row.get("benchmark_id") or row.get("benchmark")
        benchmark_raw = str(benchmark_value or "").strip().casefold().replace("-", "_").replace(" ", "_")
        benchmark = _benchmark_id(benchmark_value)
        model = model_by_key.get((lab, model_id))
        if model is None:
            raise FrontierCapabilityError(f"model_identity_unresolved:{lab}:{model_id}")
        period = _date(row.get("score_as_of") or row.get("date"), fallback=fetched_at.date().isoformat())
        method = method_by_id[benchmark]
        method_version = str(row.get("method_version") or method.get("method_version") or "v1")
        group = str(row.get("comparability_group") or method.get("comparability_group") or f"{benchmark}/{method_version}")
        key = (lab, model_id, benchmark, period)
        if key in seen:
            raise FrontierCapabilityError(f"duplicate_score:{key}")
        seen.add(key)
        value = _score(row.get("score"), benchmark)
        model_release_id = str(
            row.get("model_release_id") or model.get("model_release_id")
            or _registered_model_release_id(
                lab, model_id=model_id,
                model_name=model.get("display_name") or model.get("model_name") or model.get("name"),
                details_url=row.get("details_url"))
        )
        score_unit = str(row.get("score_unit") or "percent")
        if score_unit not in {"percent", "%", "accuracy_percent"}:
            raise FrontierCapabilityError(f"unknown_score_unit:{score_unit}")
        score_semantics = str(row.get("score_semantics") or method.get("score_semantics", ""))
        b_eligible = bool(row.get("b_eligible", method.get("b_eligible", False)))
        source_type = str(row.get("source_type") or "third_party_evaluation")
        source_priority = str(row.get("source_priority") or {
            "third_party_evaluation": "benchmark_maintainer_or_independent_third_party",
            "competitor_reported": "competitor_reported",
            "lab_self_reported": "lab_self_reported",
        }.get(source_type, source_type))
        # ``event_ledger`` means that a benchmark supports an additional
        # heterogeneous evidence stream; it does not make its governed
        # maintainer/independent result non-uniform.  Only an explicit row
        # marker (or a parser-specific event route) is event-only.
        event_only = bool(row.get("event_only", False))
        uniform_matrix = bool(row.get("uniform_matrix", not event_only))
        # OSWorld exposes partial-completion and strict/binary reward scores
        # under the same benchmark family. Keep a distinct comparability group
        # and let only the strict/binary variant participate in B.
        osworld_strict_alias = benchmark == "osworld_2" and (
            benchmark_raw in {"osworld_strict", "osworld_binary", "osworld_2_strict", "osworld_2_binary"}
            or str(row.get("variant") or row.get("evaluation_variant") or "").casefold() in {
                "strict", "binary", "strict_reward", "binary_reward"
            }
        )
        if benchmark == "osworld_2" and (osworld_strict_alias or score_semantics.casefold() in {
            "strict success percent", "binary reward", "strict/binary", "accuracy percent"
        }):
            b_eligible = True
            group = str(row.get("comparability_group") or "osworld_2/strict")
        dims = {
            "lab_id": lab, "lab_label": dict(LABS)[lab], "model_id": model_id,
            "model_release_id": model_release_id, "evaluation_variant_id": model_id,
            "model_name": model.get("display_name") or model.get("model_name") or model.get("name") or model_id, "model_lineage": model.get("lineage") or model_id,
            "release_date": str(model.get("release_date") or ""),
            "available": bool(model.get("available", True)),
            "flagship": bool(model.get("flagship", True)),
            "flagship_source": str(model.get("flagship_source") or ""),
            "identity_state": str(model.get("identity_state") or "resolved"),
            "withdrawal_date": str(model.get("withdrawal_date") or ""),
            "benchmark_id": benchmark, "benchmark_label": method.get("label", benchmark),
            "method_version": method_version, "comparability_group": group,
            "direction": row.get("direction") or method.get("direction", ""), "score_semantics": score_semantics,
            "value_range": method.get("value_range", [0, 100]), "harness": row.get("harness") or method.get("harness", ""),
            "grader": row.get("grader") or method.get("grader", ""),
            "verified_status": row.get("verified_status"),
            "metric_semantic": row.get("metric_semantic") or score_semantics,
            "inference_config": row.get("inference_config") or method.get("inference_config", ""),
            "reasoning_effort": row.get("reasoning_effort") or "default",
            "source_type": source_type, "source_priority": source_priority,
            "measurement_scope": row.get("measurement_scope") or method.get(
                "measurement_scope", "model_capability_proxy"),
            "source_transport": row.get("source_transport") or ((payload.get("meta") or {}).get("source_transport") or "unknown"),
            "source_url": row.get("source_url") or source_url,
            "details_url": row.get("details_url") or "",
            "task_set": row.get("task_set") or method.get("task_set", ""),
            "coverage_state": "observed", "cohort_version": COHORT_VERSION,
            "b_eligible": b_eligible,
            "event_only": event_only, "uniform_matrix": uniform_matrix,
            "sample_size": row.get("sample_size"), "confidence_low": row.get("confidence_low"),
            "confidence_high": row.get("confidence_high"), "score_as_of": period,
        }
        raw = {"provider_row": row, "model": model, "method": method, "meta": payload.get("meta") or {},
               "source_url": source_url}
        records.append(NativeRecord(
            entity_id=f"FRONTIER_MODEL:{lab}:{model_id}".upper(), provider_field="benchmark_score",
            period=period, period_start=period, period_end=period, value=value,
            unit="percent", period_basis="benchmark_score_as_of",
            published_at=_timestamp(row.get("published_at")), dimensions=dims, raw=raw))
    observed_keys = {(r.dimensions.get("lab_id"), r.dimensions.get("model_id"),
                      r.dimensions.get("benchmark_id")) for r in records}
    coverage_hints = []
    for model in models:
        lab = _lab_id(model.get("lab_id") or model.get("lab") or model.get("provider") or
                      model.get("organization") or model.get("creator"))
        model_id = str(model.get("model_id") or model.get("id") or model.get("slug") or "").strip()
        if not model_id:
            continue
        source_type = str((payload.get("meta") or {}).get("source_type") or "third_party_evaluation")
        model_state = "withdrawn" if not bool(model.get("available", True)) else (
            "not_self_reported" if source_type == "lab_self_reported" else "not_evaluated")
        for benchmark, method in method_by_id.items():
            if (lab, model_id, benchmark) in observed_keys:
                continue
            coverage_hints.append({
                "lab_id": lab, "model_id": model_id, "benchmark_id": benchmark,
                "method_version": str(method.get("method_version") or "v1"),
                "comparability_group": str(method.get("comparability_group") or f"{benchmark}/v1"),
                "source_type": source_type,
                "coverage_state": model_state,
            })
    active_flagship_counts: dict[str, int] = {}
    for model in models:
        if bool(model.get("available", True)) and bool(model.get("flagship", True)):
            lab = _lab_id(model.get("lab_id") or model.get("lab") or model.get("provider"))
            active_flagship_counts[lab] = active_flagship_counts.get(lab, 0) + 1
    duplicate_active_flagships = sorted(lab for lab, count in active_flagship_counts.items() if count > 1)
    payload_meta = payload.get("meta") or {}
    diagnostics = {"as_of": payload_meta.get("as_of"), "version": payload_meta.get("version", "v1"),
                    "source_url": source_url, "model_count": len(models), "method_count": len(method_by_id),
                    "score_count": len(records), "payload_sha256": _digest(payload), "parser_version": PARSER_VERSION,
                    "cohort_version": COHORT_VERSION, "coverage_hints": coverage_hints,
                    "duplicate_active_flagships": duplicate_active_flagships,
                    "source_transport": payload_meta.get("source_transport"),
                    "sources": payload_meta.get("sources") or [],
                    "route_errors": payload_meta.get("route_errors") or [],
                    "request_count": payload_meta.get("request_count"),
                    "request_budget": payload_meta.get("request_budget")}
    return records, entities, diagnostics


class FrontierAICapabilityAdapter:
    source_id = SOURCE_ID
    dataset_id = DATASET_ID

    def __init__(self, *, api_key: str | None = None, fixture_path: str | Path | None = None,
                 opener: Callable[..., Any] | None = None, clock: Callable[[], datetime] | None = None,
                 public: bool | None = None):
        self.api_key = api_key if api_key is not None else os.environ.get("ARTIFICIAL_ANALYSIS_API_KEY", "")
        env_fixture = os.environ.get("ATS_FRONTIER_AI_CAPABILITY_FIXTURE", "")
        self.fixture_path = Path(fixture_path or env_fixture) if (fixture_path or env_fixture) else None
        self.opener = opener or _configured_url_opener()
        self.clock = clock or _now
        # Passing an explicit api_key (including an empty value in tests) keeps
        # the legacy AA-only behavior.  The runtime factory passes no key and
        # therefore defaults to public routes.
        self.public = (api_key is None) if public is None else bool(public)
        self._public_request_count = 0

    def _read_url(self, url: str, *, headers: dict[str, str] | None = None,
                  method: str = "GET", body: bytes | None = None) -> tuple[bytes, dict[str, str]]:
        if self.public:
            if self._public_request_count >= PUBLIC_REQUEST_BUDGET:
                raise RuntimeError(f"public_source_request_budget_exhausted:{PUBLIC_REQUEST_BUDGET}")
            self._public_request_count += 1
        request_headers = {"Accept": "application/json,text/plain,text/csv,*/*",
                           "User-Agent": "ATS-EvidenceObserver/1.0 (+public-benchmark-ingestion)"}
        request_headers.update(headers or {})
        request = Request(url, headers=request_headers, data=body, method=method)
        try:
            with self.opener(request, timeout=30) as response:
                raw_headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
                return response.read(), raw_headers
        except HTTPError as exc:
            if exc.code in {401, 403}:
                raise PermissionError(f"public_source_access_denied:{url}") from exc
            if exc.code == 429:
                raise RuntimeError(f"public_source_rate_limited:{url}") from exc
            raise ConnectionError(f"public_source_http_{exc.code}:{url}") from exc
        except URLError as exc:
            raise ConnectionError(f"public_source_unreachable:{url}:{exc.reason}") from exc

    def _public_route(self, benchmark: str) -> dict[str, Any]:
        route = dict(PUBLIC_ROUTES[benchmark])
        overrides = os.environ.get("ATS_FRONTIER_AI_PUBLIC_ROUTES", "")
        if overrides:
            try:
                configured = json.loads(overrides)
                value = configured.get(benchmark)
                if isinstance(value, str):
                    route["url"] = value
                elif isinstance(value, dict):
                    route.update(value)
            except json.JSONDecodeError as exc:
                raise FrontierCapabilityError("invalid_public_routes_json") from exc
        return route

    def _load_public(self, fetched_at: datetime) -> tuple[dict[str, Any], str, str, dict[str, Any]]:
        """Fetch registered public routes and normalize only observed scores."""
        self._public_request_count = 0
        fetched_date = fetched_at.date().isoformat()
        models: dict[tuple[str, str], dict[str, Any]] = {}
        scores: list[dict[str, Any]] = []
        score_keys: set[tuple[str, str, str, str]] = set()
        sources: list[dict[str, Any]] = []
        route_errors: list[str] = []
        for benchmark in BENCHMARKS:
            route = self._public_route(benchmark)
            url = str(route["url"])
            transport = str(route.get("transport") or "public_json")
            try:
                if transport in {"public_html_structured", "official_html", "official_html_event"}:
                    if os.environ.get("ATS_FRONTIER_AI_ALLOW_PUBLIC_STRUCTURED_PAGES", "1").strip().casefold() in {"0", "false", "no"}:
                        raise PermissionError(f"source_policy_blocked:{benchmark}")
                    if os.environ.get("ATS_FRONTIER_AI_PUBLIC_TERMS_APPROVED", "1").strip().casefold() in {"0", "false", "no"}:
                        raise PermissionError(f"terms_or_robots_not_approved:{benchmark}")
                if transport == "harbor_leaderboard_api":
                    request_body = json.dumps(route.get("body") or {}).encode("utf-8")
                    payload, headers = self._read_url(
                        url,
                        method="POST",
                        body=request_body,
                        headers={
                            "Accept": "application/json",
                            "Content-Type": "application/json",
                            "apikey": HARBOR_SUPABASE_PUBLISHABLE_KEY,
                        },
                    )
                else:
                    payload, headers = self._read_url(url)
                source_url = url
                candidate_payloads: list[tuple[bytes, str]] = [(payload, url)]
                # GitHub directory APIs return a list of files. Fetch only
                # dated LiveBench CSVs and JSON submissions; all data remains
                # from the public repository and is hash-bound below.
                try:
                    listing = json.loads(payload.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    listing = None
                if benchmark == "terminal_bench_4" and transport == "git_html_directory":
                    try:
                        directory_html = payload.decode("utf-8")
                    except UnicodeDecodeError as exc:
                        raise FrontierCapabilityError("terminal_bench_directory_not_utf8") from exc
                    paths = sorted(set(re.findall(
                        r"leaderboard/submissions/[^\"?#<]+\.json", directory_html)))
                    if not paths:
                        raise FrontierCapabilityError("terminal_bench_directory_parser_drift")
                    candidate_payloads = []
                    for path in paths:
                        child_url = f"https://raw.githubusercontent.com/harbor-framework/terminal-bench/main/{path}"
                        body, _ = self._read_url(child_url)
                        candidate_payloads.append((body, child_url))
                elif benchmark == "terminal_bench_4" and transport == "harbor_leaderboard_api":
                    try:
                        leaderboard_payload = json.loads(payload.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise FrontierCapabilityError("terminal_bench_hub_invalid_json") from exc
                    published_rows = leaderboard_payload.get("rows")
                    if not isinstance(published_rows, list):
                        raise FrontierCapabilityError("terminal_bench_hub_schema_drift")
                    candidate_payloads = [
                        (json.dumps(item, sort_keys=True).encode("utf-8"), url)
                        for item in published_rows if isinstance(item, dict)
                    ]
                elif benchmark == "livebench" and transport == "git_html_directory":
                    try:
                        directory_html = payload.decode("utf-8")
                    except UnicodeDecodeError as exc:
                        raise FrontierCapabilityError("livebench_directory_not_utf8") from exc
                    paths = sorted(set(re.findall(r"public/table_20[0-9_]+\.csv", directory_html)))
                    if not paths:
                        raise FrontierCapabilityError("livebench_directory_parser_drift")
                    child_url = f"https://raw.githubusercontent.com/LiveBench/new-livebench/main/{paths[-1]}"
                    body, headers = self._read_url(child_url)
                    candidate_payloads = [(body, child_url)]
                    source_url = child_url
                if isinstance(listing, list):
                    if benchmark == "livebench":
                        files = [item for item in listing if isinstance(item, dict)
                                 and re.match(r"table_20\d{2}_\d{2}_\d{2}\.csv$", str(item.get("name", "")))]
                        files = sorted(files, key=lambda item: str(item.get("name")))
                        if files:
                            latest = files[-1]
                            child_url = str(latest.get("download_url") or "")
                            if child_url:
                                body, child_headers = self._read_url(child_url)
                                candidate_payloads = [(body, child_url)]
                                headers = child_headers
                                source_url = child_url
                    elif benchmark == "terminal_bench_4":
                        files = [item for item in listing if isinstance(item, dict)
                                 and str(item.get("name", "")).endswith(".json")]
                        # The directory is the authoritative batch; select all
                        # published submissions, not a guessed model subset.
                        candidate_payloads = []
                        for item in files:
                            child_url = str(item.get("download_url") or "")
                            if child_url:
                                body, _ = self._read_url(child_url)
                                candidate_payloads.append((body, child_url))
                for body, body_url in candidate_payloads:
                    if benchmark == "livebench":
                        parsed, parser_meta = _parse_livebench_csv(body, source_url=body_url, fetched_date=fetched_date)
                    elif benchmark == "terminal_bench_4":
                        try:
                            item = json.loads(body.decode("utf-8"))
                        except (UnicodeDecodeError, json.JSONDecodeError):
                            item = {}
                        row = _parse_terminal_submission(
                            item,
                            source_url=body_url,
                            fetched_date=fetched_date,
                            source_transport=("harbor_leaderboard_api"
                                              if transport == "harbor_leaderboard_api"
                                              else "git_json"),
                        )
                        parsed, parser_meta = ([row] if row else []), {"record_count": 1 if row else 0}
                    elif benchmark == "terminal_bench_science_01":
                        parsed, parser_meta = _parse_terminal_bench_science_json(
                            body, source_url=body_url, fetched_date=fetched_date)
                    elif benchmark == "osworld_2":
                        parsed, parser_meta = _parse_osworld_json(
                            body, source_url=body_url, fetched_date=fetched_date)
                    elif benchmark == "spreadsheetbench_2" and transport == "official_json_event":
                        parsed, parser_meta = _parse_generic_public_json(
                            body, source_url=body_url, fetched_date=fetched_date,
                            benchmark_id=benchmark,
                            measurement_scope="model_agent_stack_capability",
                            source_transport=transport,
                            source_type="third_party_evaluation",
                            event_only=True,
                        )
                    elif transport == "public_html_structured":
                        parsed, parser_meta = _parse_structured_html_scores(
                            body, source_url=body_url, fetched_date=fetched_date,
                            benchmark_id=benchmark,
                            measurement_scope=str(BENCHMARK_METHODS[benchmark].get("measurement_scope") or "model_capability_proxy"),
                            source_type="third_party_evaluation",
                            # The public AA page is the governed uniform
                            # matrix route.  Its observations may also be
                            # copied into the event ledger, but the
                            # event_ledger registry flag must not make every
                            # uniform score non-comparable.
                            event_only=False,
                        )
                    elif transport == "official_html":
                        parsed, parser_meta = _parse_toolathlon_verified(body, source_url=body_url, fetched_date=fetched_date)
                    elif transport == "official_html_event":
                        parsed, parser_meta = _parse_structured_html_scores(
                            body, source_url=body_url, fetched_date=fetched_date,
                            benchmark_id=benchmark,
                            measurement_scope=str(BENCHMARK_METHODS[benchmark].get("measurement_scope") or "model_agent_stack_capability"),
                            source_type="third_party_evaluation", event_only=True,
                        )
                    else:
                        parsed, parser_meta = _parse_readme_scores(body, source_url=body_url, fetched_date=fetched_date,
                                                                   benchmark_id=benchmark)
                    for row in parsed:
                        lab = row["lab_id"]
                        model_id = row["model_id"]
                        row.setdefault("model_release_id", _registered_model_release_id(
                            lab, model_id=model_id, model_name=row.get("model_name"),
                            details_url=row.get("details_url")))
                        score_key = (lab, model_id, row["benchmark_id"], str(row.get("score_as_of") or fetched_date))
                        if score_key in score_keys:
                            continue
                        score_keys.add(score_key)
                        _public_model(models, lab=lab, model_id=model_id,
                                      display_name=row["model_name"], score_date=row.get("score_as_of", fetched_date),
                                      source_url=body_url,
                                      model_release_id=str(row.get("model_release_id") or model_id))
                        scores.append(row)
                    source_record = {"benchmark_id": benchmark, "transport": transport,
                                     "url": source_url, "etag": headers.get("etag"),
                                     "last_modified": headers.get("last-modified"),
                                     "payload_sha256": _digest(body), "parser": parser_meta}
                    # A structured page that still fetched successfully but
                    # yielded no explicit score document is a parser drift,
                    # not a clean no-change observation.  Keep the raw
                    # lineage auditable and fail closed for data selection.
                    if parser_meta.get("parser_drift"):
                        source_record["status"] = "parser_drift"
                        route_errors.append(f"{benchmark}:parser_drift")
                    sources.append(source_record)
            except PermissionError as exc:
                route_errors.append(f"{benchmark}:{exc}")
                sources.append({"benchmark_id": benchmark, "transport": transport, "url": url,
                                "status": "source_policy_blocked", "error": str(exc)})
            except (ConnectionError, RuntimeError, FrontierCapabilityError) as exc:
                route_errors.append(f"{benchmark}:{exc}")
                sources.append({"benchmark_id": benchmark, "transport": transport, "url": url,
                                "status": "source_unavailable", "error": str(exc)})
        # Benchmark recency is not a valid flagship rule: a newly evaluated old
        # model must not displace a newer official release.  Public benchmark
        # rows can confirm the configured release identity or an explicit
        # operator override; official release discovery is handled by the
        # separate official-Lab adapter.
        overrides = {}
        try:
            overrides = json.loads(os.environ.get("ATS_FRONTIER_AI_FLAGSHIP_OVERRIDES", "{}"))
        except json.JSONDecodeError as exc:
            raise FrontierCapabilityError("invalid_flagship_overrides_json") from exc
        for lab, _label in LABS:
            candidates = [m for (candidate_lab, _), m in models.items() if candidate_lab == lab]
            if not candidates:
                continue
            chosen_id = str(overrides.get(lab) or "")
            chosen = next((m for m in candidates
                           if m["model_id"] == chosen_id or m.get("model_release_id") == chosen_id), None)
            chosen_source = "operator_override" if chosen is not None else ""
            if chosen is None:
                default_id = DEFAULT_FLAGSHIP_MODEL_IDS.get(lab, "")
                chosen = next((m for m in candidates
                               if m["model_id"] == default_id or m.get("model_release_id") == default_id), None)
                chosen_source = "configured_registry" if chosen is not None else ""
            for item in candidates:
                item["flagship"] = item is chosen
                if item is chosen:
                    item["flagship_source"] = chosen_source
        payload = {"meta": {"version": PUBLIC_SOURCE_VERSION, "source": "official_public_benchmark_routes",
                             "as_of": fetched_at.isoformat(), "source_type": "third_party_evaluation",
                             "sources": sources, "route_errors": route_errors},
                   "models": list(models.values()), "methods": [], "scores": scores}
        access_path = "public_routes" if sources else "public_routes_unavailable"
        payload["meta"]["request_count"] = self._public_request_count
        payload["meta"]["request_budget"] = PUBLIC_REQUEST_BUDGET
        return payload, "public://frontier-benchmarks", access_path, {"sources": sources, "route_errors": route_errors,
                                                                        "request_count": self._public_request_count,
                                                                        "request_budget": PUBLIC_REQUEST_BUDGET}

    def _load(self) -> tuple[bytes | dict[str, Any], str, str]:
        if self.fixture_path:
            return self.fixture_path.read_bytes(), str(self.fixture_path), "fixture"
        # Only the explicit value ``1`` opts into Artificial Analysis.  Values
        # such as ``0``/``false`` must not accidentally disable the free public
        # route because environment variables are strings.
        if self.public and os.environ.get("ATS_FRONTIER_AI_USE_AA", "").strip() != "1":
            payload, source_url, access_path, _meta = self._load_public(self.clock().astimezone(timezone.utc))
            return payload, source_url, access_path
        if not self.api_key:
            return _default_payload(), AA_METHODOLOGY_URL, "aa_not_entitled_or_disabled"
        # Artificial Analysis documents API keys via the ``x-api-key`` header.
        # Keeping the provider-specific header here matters: a Bearer header
        # yields a misleading 401 even when the key is valid but the account
        # is on the Free plan (which returns a more useful 403 entitlement
        # response with the documented header).
        req = Request(AA_MODELS_URL, headers={"x-api-key": self.api_key,
                                              "Accept": "application/json", "User-Agent": "ATS-EvidenceObserver/1.0"})
        try:
            with self.opener(req, timeout=30) as response:
                return response.read(), AA_MODELS_URL, "artificial_analysis_api"
        except HTTPError as exc:
            if exc.code in {401, 403}:
                raise PermissionError(f"artificial_analysis_http_{exc.code}") from exc
            if exc.code == 429:
                raise RuntimeError("artificial_analysis_rate_limited") from exc
            raise ConnectionError(f"artificial_analysis_http_{exc.code}") from exc
        except URLError as exc:
            raise ConnectionError(f"artificial_analysis_unreachable:{exc.reason}") from exc

    def fetch(self, request: FetchRequest) -> AdapterBatch:
        fetched = self.clock().astimezone(timezone.utc)
        try:
            payload, source_url, access_path = self._load()
            records, entities, diagnostics = normalize_payload(payload, fetched_at=fetched, source_url=source_url)
            if not records and not entities:
                return AdapterBatch(source_id=request.source_id, dataset_id=request.dataset_id,
                                    status=IngestionStatus.NO_COVERAGE, fetched_at=fetched,
                                    failures=[AdapterFailure(status=IngestionStatus.NO_COVERAGE,
                                                             message="no_entitled_or_allowed_capability_slice")],
                                    provider_metadata={**diagnostics, "access_path": access_path,
                                                       "source_url": source_url})
            digest = diagnostics["payload_sha256"]
            artifact_key = f"frontier-capability:{digest[:16]}"
            records = [record.model_copy(update={"slice_key": artifact_key}) for record in records]
            artifact = AdapterArtifact(artifact_key=artifact_key, payload=payload, source_url=source_url,
                                       source_version=f"v1:{digest}", retention="query_slice", storage_mode="full",
                                       pointer=source_url, metadata={**diagnostics, "access_path": access_path,
                                                                      "test_only": access_path == "fixture",
                                                                      "credential_redacted": True,
                                                                      "methodology_url": AA_METHODOLOGY_URL})
            return AdapterBatch(source_id=request.source_id, dataset_id=request.dataset_id, status=IngestionStatus.SUCCEEDED,
                                fetched_at=fetched, records=records, artifacts=[artifact], entities=entities,
                                provider_metadata={**diagnostics, "access_path": access_path,
                                                   "source_url": source_url, "api_key_configured": bool(self.api_key),
                                                   "methodology_url": AA_METHODOLOGY_URL,
                                                   "request_budget": {"bulk_requests_per_run": PUBLIC_REQUEST_BUDGET,
                                                                       "requests_used": diagnostics.get("request_count")},
                                                   "test_only": access_path == "fixture"})
        except PermissionError as exc:
            return AdapterBatch(source_id=request.source_id, dataset_id=request.dataset_id, status=IngestionStatus.UNAUTHORIZED,
                                fetched_at=fetched, failures=[AdapterFailure(status=IngestionStatus.UNAUTHORIZED, message=str(exc))])
        except RuntimeError as exc:
            return AdapterBatch(source_id=request.source_id, dataset_id=request.dataset_id, status=IngestionStatus.STALE,
                                fetched_at=fetched, failures=[AdapterFailure(status=IngestionStatus.STALE, message=str(exc))])
        except (ConnectionError, FrontierCapabilityError) as exc:
            status = IngestionStatus.METHODOLOGY_DRIFT if "schema" in str(exc) or "version" in str(exc) else IngestionStatus.PARSE_FAILED
            return AdapterBatch(source_id=request.source_id, dataset_id=request.dataset_id, status=status,
                                fetched_at=fetched, failures=[AdapterFailure(status=status, message=str(exc))])

    def discover(self, request: FetchRequest) -> DiscoveryResult:
        checked = self.clock().astimezone(timezone.utc)
        batch = self.fetch(request)
        if not batch.records:
            status = DiscoveryStatus.ACCESS_REQUIRED if batch.status == IngestionStatus.UNAUTHORIZED else DiscoveryStatus.UNREACHABLE
            return DiscoveryResult(source_id=request.source_id, dataset_id=request.dataset_id, checked_at=checked,
                                   status=status, diagnostics={"failures": [f.message for f in batch.failures]})
        latest = max(row.period for row in batch.records)
        identity = str(batch.provider_metadata.get("payload_sha256") or _digest([row.model_dump(mode="json") for row in batch.records]))
        candidate = ReleaseCandidate(identity=f"FRONTIER_CAPABILITY:{latest}:{identity}", period=latest,
                                     urls=[str(batch.provider_metadata.get("source_url") or AA_MODELS_URL)],
                                     methodology_fingerprint=COHORT_VERSION,
                                     metadata={"record_count": len(batch.records), "model_count": len(batch.entities)})
        if str((request.query_scope or {}).get("known_upstream_identity") or "") == candidate.identity:
            return DiscoveryResult(source_id=request.source_id, dataset_id=request.dataset_id, checked_at=checked,
                                   status=DiscoveryStatus.NO_CHANGE, latest_upstream_identity=candidate.identity,
                                   latest_available_period=latest, diagnostics={**batch.provider_metadata,
                                                                                "change_events": []})
        return DiscoveryResult(source_id=request.source_id, dataset_id=request.dataset_id, checked_at=checked,
                               status=DiscoveryStatus.NEW_RELEASE, latest_upstream_identity=candidate.identity,
                               latest_available_period=latest, candidates=[candidate], diagnostics={**batch.provider_metadata,
                                                                                                     "change_events": ["score_added_or_revised", "model_directory_checked"]})


ArtificialAnalysisCapabilityAdapter = FrontierAICapabilityAdapter


class PublicBenchmarkAdapter(FrontierAICapabilityAdapter):
    """Named public-route adapter used by the runtime registry."""

    def __init__(self, **kwargs: Any):
        kwargs["public"] = True
        super().__init__(**kwargs)


class OfficialLabReleaseAdapter(FrontierAICapabilityAdapter):
    """Parse an allowed Lab release/model-card slice with identical semantics.

    This adapter is intentionally opt-in (``ATS_FRONTIER_AI_CAPABILITY_LAB_FIXTURE``)
    and never replaces a third-party score.  Its dimensions retain
    ``source_type=lab_self_reported`` so source priority remains auditable.
    """

    def __init__(self, *, fixture_path: str | Path | None = None, **kwargs: Any):
        resolved_fixture = fixture_path or os.environ.get("ATS_FRONTIER_AI_CAPABILITY_LAB_FIXTURE")
        kwargs.setdefault("public", False)
        # This is a separate official-release supplement, not an Artificial
        # Analysis adapter.  Never let a configured AA key silently turn a
        # missing release fixture into an AA request.
        if resolved_fixture is None:
            kwargs.setdefault("api_key", "")
        super().__init__(fixture_path=resolved_fixture, **kwargs)

    def _load(self) -> tuple[bytes | dict[str, Any], str, str]:
        """Convert release/model-card rows into the shared model/score contract.

        Official fixtures intentionally contain identity-only rows (aliases,
        withdrawals and missing self-reports). They still become model entities;
        only rows with a numeric benchmark score become observations.
        """
        if self.fixture_path is None:
            return ({"meta": {"version": "v1", "source": "official_lab_public_release_unavailable",
                               "source_type": "lab_self_reported"},
                     "models": [], "scores": []},
                    "public://frontier-lab-releases", "official_lab_no_coverage")
        payload, source_url, access_path = super()._load()
        if isinstance(payload, bytes):
            try:
                decoded = json.loads(payload.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise FrontierCapabilityError("invalid_official_release_json") from exc
        elif isinstance(payload, dict):
            decoded = payload
        else:
            decoded = {}
        if not isinstance(decoded, dict) or not isinstance(decoded.get("records"), list):
            return payload, source_url, access_path
        models: list[dict[str, Any]] = []
        scores: list[dict[str, Any]] = []
        for row in decoded["records"]:
            if not isinstance(row, dict):
                continue
            model_id = str(row.get("model_id") or "").strip()
            lab_id = row.get("lab_id") or row.get("lab") or row.get("provider")
            if not model_id or not lab_id:
                continue
            available = bool(row.get("available", True))
            models.append({"lab_id": lab_id, "model_id": model_id,
                           "display_name": row.get("display_name") or model_id,
                           "aliases": [row["alias"]] if row.get("alias") else list(row.get("aliases") or []),
                           "available": available, "flagship": bool(row.get("flagship", True)),
                           "flagship_source": row.get("flagship_source") or "official_lab_release",
                           "model_release_id": row.get("model_release_id") or model_id,
                           "release_date": row.get("release_date") or row.get("available_date") or "",
                           "identity_state": row.get("identity_state", "resolved"),
                           "withdrawal_date": row.get("withdrawal_date", ""),
                           "lineage": row.get("lineage") or model_id})
            if row.get("benchmark_id") and row.get("score") is not None and available:
                score = {"lab_id": lab_id, "model_id": model_id,
                         "benchmark_id": row["benchmark_id"], "score": row["score"],
                         "score_as_of": row.get("score_as_of") or decoded.get("as_of"),
                         "source_type": "lab_self_reported",
                         "score_semantics": row.get("score_semantics"),
                         "b_eligible": row.get("b_eligible"),
                         "comparability_group": row.get("comparability_group"),
                         "event_only": bool(row.get("event_only", False)),
                         "uniform_matrix": bool(row.get("uniform_matrix", not row.get("event_only", False))),
                         "harness": row.get("harness"), "grader": row.get("grader"),
                         "task_set": row.get("task_set"),
                         "metric_semantic": row.get("metric_semantic") or row.get("score_semantics"),
                         "sample_size": row.get("sample_size") or row.get("n_trials"),
                         "confidence_low": row.get("confidence_low"),
                         "confidence_high": row.get("confidence_high")}
                if row.get("methodology_regime") == "non_comparable_lab_harness":
                    score["comparability_group"] = f"{row['benchmark_id']}/official_non_comparable"
                    score["score_semantics"] = "non-comparable official score"
                scores.append({k: v for k, v in score.items() if v is not None})
        return {"meta": {"source": "official_lab_release", "source_type": "lab_self_reported",
                          "as_of": decoded.get("as_of")},
                "models": models, "scores": scores}, source_url, "official_lab_release"
