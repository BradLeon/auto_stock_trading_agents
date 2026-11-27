"""`ats data model-release-*`: look at release candidates and decide on them."""
from __future__ import annotations

import json
from typing import Any

ACTIONS = ("model-release-candidates", "model-release-confirm", "model-release-dismiss", "model-release-add")
_COMMIT_HINT = "已写入配置文件，尚未提交 git"


def markdown(report: dict[str, Any]) -> str:
    from ..data.model_release_discovery import describe_reason

    if report["status"] != "ok":
        return f"无法判断新型号：{describe_reason(report['reason_code'])}"
    head = (f"清单快照截至 {report['latest_snapshot']}（基线 {report['baseline_date']}），"
            f"待确认 {len(report['candidates'])} 个。")
    lines = [head]
    for item in report["candidates"]:
        lines.append(f"\n- `{item['candidate_id']}` · {item['lab_id']} · {item['name'] or item['model_id']}")
        lines.append(f"  - 上架 {item['listed_date'] or '日期未知'}（只是线索，发布日期以官方公告为准）")
        if item["variants"] != [item["candidate_id"]]:
            lines.append(f"  - 清单条目：{', '.join(item['variants'])}")
    return "\n".join(lines)


def run(action: str, *, candidate_ids: list[str] | None = None, tier: str = "", release_date: str = "",
        url: str = "", lab: str = "", model: str = "", name: str = "", aliases: list[str] | None = None,
        status: str = "", note: str = "", output_format: str = "json") -> int:
    from ..data import model_release_discovery as discovery

    ids = [item.strip().casefold() for item in candidate_ids or [] if item.strip()]
    try:
        if action == "model-release-candidates":
            report = discovery.current_report()
            if output_format == "markdown":
                print(markdown(report))
                return 0 if report["status"] == "ok" else 1
            result = {**report, "status": "succeeded" if report["status"] == "ok" else "failed"}
        elif action == "model-release-add":
            status = status or "confirmed"
            missing = [flag for flag, value in (("--lab", lab), ("--model", model), ("--tier", tier)) if not value]
            if status == "confirmed":
                missing += [flag for flag, value in (("--release-date", release_date), ("--url", url))
                            if not value]
            if missing:
                raise ValueError(f"{action} 缺少 {' '.join(missing)}")
            record = discovery.register(
                lab_id=lab.upper(), model_id=model, display_name=name, tier=tier, release_date=release_date,
                announcement_url=url, status=status, source_aliases=aliases or [])
            result = {"status": "succeeded", "record": record, "note": _COMMIT_HINT}
        else:
            if not ids:
                raise ValueError(f"{action} 需要 --candidate")
            report = discovery.current_report()
            if report["status"] != "ok":
                raise ValueError(f"candidates_unavailable:{report['reason_code']}")
            pending = {item["candidate_id"]: item for item in report["candidates"]}
            unknown = [value for value in ids if value not in pending]
            if unknown:
                raise ValueError(f"not_a_pending_candidate:{', '.join(unknown)}")
            if action == "model-release-dismiss":
                result = {"status": "succeeded", "dismissed": discovery.dismiss(ids, reason=note),
                          "note": _COMMIT_HINT}
            else:
                if len(ids) != 1:
                    raise ValueError("model-release-confirm 一次只确认一个候选；同一型号的其他叫法用 --alias")
                missing = [flag for flag, value in (
                    ("--tier", tier), ("--release-date", release_date), ("--url", url)) if not value]
                if missing:
                    raise ValueError(f"{action} 缺少 {' '.join(missing)}")
                item = pending[ids[0]]
                if lab and lab.upper() != item["lab_id"]:
                    raise ValueError(f"lab_mismatch:{item['candidate_id']} 属于 {item['lab_id']}")
                authors = discovery.load_settings().authors
                # An alias given as a list id is stored the way the registry names models: without the author.
                names = [value.partition("/")[2] if value.partition("/")[0].casefold() in authors else value
                         for value in aliases or []]
                record = discovery.register(
                    lab_id=item["lab_id"], model_id=model or item["model_id"],
                    display_name=name or item["name"].partition(": ")[2] or item["name"], tier=tier,
                    release_date=release_date, announcement_url=url,
                    source_aliases=[item["model_id"], *names])
                result = {"status": "succeeded", "record": record, "listed_date": item["listed_date"],
                          "note": _COMMIT_HINT}
    except (OSError, ValueError) as exc:
        result = {"status": "failed", "reason_code": str(exc) or type(exc).__name__}
    except Exception as exc:  # noqa: BLE001 - e.g. the store cannot be opened; report it, never traceback
        result = {"status": "failed", "reason_code": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result["status"] == "succeeded" else 1
