# G0 核验用的只读查看函数。用法：在仓库根目录的终端里 `source` 本文件，见 g0-runbook.md 0.3。
# 全部只读（sqlite 连接开 PRAGMA query_only=ON；不用 -readonly，WAL 库在没有 -shm 文件时只读打开会失败）：不改 var/ 下的数据、不加载/卸载服务、不发模型请求。
cd /Users/liuchao/Code/trading/auto_stock_trading_agents && source .venv/bin/activate
export G0_TZ=Asia/Taipei
G0_TOOLS_DIR="${${(%):-%x}:A:h}"   # 本文件所在目录（source 时确定），供 g0_show 找到 g0_show.py
export G0_REPO="${G0_TOOLS_DIR:h:h:h}"   # 仓库根目录；所有函数都用它定位 var/、config/，不依赖当前所在目录

# 任务名 → 来源名 的对照（读 config/data/schedules.yaml），供下面几个函数用
g0_map() {
  python -c '
import json, os, yaml
d = yaml.safe_load(open(os.path.join(os.environ["G0_REPO"], "config/data/schedules.yaml")))
out = {}
for dom in ("structured", "unstructured", "derived"):
    for k, v in d[dom]["jobs"].items():
        src = v.get("sources") or v.get("source") or []
        src = [src] if isinstance(src, str) else list(src)
        for e in (v.get("entities") or [None]):
            out[f"{k}.{e}" if e else k] = {"domain": dom, "sources": src}
print(json.dumps(out))
'
}

# 刷新任务：每个任务最近一次的窗口、结果、原因、完成时间，以及近 7 天失败次数。
# 参数（可选）：任务名或来源名的片段，例如 ibkr_news、defeatbeta_earnings_transcript、calendar_
g0_refresh() {
  local status_json; status_json="$(python -m ats.data.refresh status)" || return 1
  printf '%s' "$status_json" | G0_MAP="$(g0_map)" python -c '
import json, os, sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
tz = ZoneInfo(os.environ.get("G0_TZ", "Asia/Taipei"))
jobmap = json.loads(os.environ["G0_MAP"])
d = json.load(sys.stdin)
want = sys.argv[1] if len(sys.argv) > 1 else ""
def local(s):
    return datetime.fromisoformat(s).astimezone(tz).strftime("%m-%d %H:%M") if s else "-"
week = datetime.now(timezone.utc) - timedelta(days=7)
shown = [j for j, m in jobmap.items() if want in j or any(want in s for s in m["sources"])]
print("控制器最近一次定时运行:", local(d.get("last_tick", "")), "| launchd 已加载:", d.get("launchd_loaded"))
if not shown:
    print(f"没有任务或来源名里含“{want}”。任务名示例: ibkr_news_daily、consensus_、calendar_；来源名示例: ibkr_news、yfinance_consensus")
    sys.exit(0)
print(f"{"任务":50} {"来源":30} {"窗口":10} {"最近结果":14} {"原因":26} {"完成(台北)":11} 近7天失败")
for job in shown:
    src = ",".join(jobmap[job]["sources"])[:30]
    rows = [r for r in d["runs"] if r["job_id"] == job]
    if not rows:
        print(f"{job:50} {src:30} {"-":10} {"从未运行":14}")
        continue
    last = max(rows, key=lambda r: r["claimed_at"])
    fails = sum(1 for r in rows if r["status"] == "failed" and datetime.fromisoformat(r["claimed_at"]) >= week)
    print(f"{job:50} {src:30} {last["window_date"]:10} {last["status"]:14} {last["reason_code"][:26]:26} {local(last["completed_at"]):11} {fails}")
' "$@"
}

# 来源采集与文档准入。三段：①最近一次运行 ②候选条目累计去向 ③文档库存量。
# 参数（可选）：来源名或任务名的片段，例如 ibkr_news、sec_edgar、trendforce、defeatbeta_transcript_daily
g0_sources() {
  ats data health | G0_MAP="$(g0_map)" python -c '
import collections, json, os, sys
from datetime import datetime
from zoneinfo import ZoneInfo
tz = ZoneInfo(os.environ.get("G0_TZ", "Asia/Taipei"))
jobmap = json.loads(os.environ["G0_MAP"])
def local(s):
    try: return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(tz).strftime("%m-%d %H:%M")
    except ValueError: return s[:16] or "-"
d = json.load(sys.stdin)
want = sys.argv[1] if len(sys.argv) > 1 else ""
terms = {want}
for job, m in jobmap.items():
    if want and want in job:
        terms |= set(m["sources"])
def hit(name):
    return not want or any(t in name for t in terms)
print("① 最近一次运行（这次来源列出了多少候选、新放行多少、被质量门拒绝多少；时间为台北）")
for r in d["structured_sources"]:
    if hit(r["source_id"]):
        why = r["reason_codes"] if r["reason_codes"] not in ("{}", "[]") else ""
        note = ""
        if "source_unavailable" in why or "unreachable" in r["status"]:
            note = "  ← 来源连不上，正文没拿到：不算准入也不算隔离"
        try:
            age = (datetime.now(tz) - datetime.fromisoformat(r["updated_at"].replace("Z", "+00:00")).astimezone(tz)).days
        except ValueError:
            age = 0
        if age >= 2 and r["status"] not in ("succeeded", "no_change"):
            note += f"  ⏱ 这是 {age} 天前的旧结果，之后没有重新探测（sec_official:*、defeatbeta_transcript:<票> 这类行只在某次财报评审里顺带探测一次）"
        print(f"  {r["source_id"][:42]:42} {r["entity"][:6]:6} {r["status"]:17} 更新 {local(r["updated_at"])}  发现 {r["discovered"]} 准入 {r["accepted"]} 隔离 {r["quarantined"]}  {why[:50]}{note}")
print("② 候选条目累计去向（历次运行合计；一条候选只有一个去向）")
agg = collections.defaultdict(lambda: collections.Counter())
reasons = collections.defaultdict(set)
for r in d["candidate_admission"]:
    if r["source"].startswith(("file:", "tavily:", "fmp:")) or not hit(r["source"]):
        continue
    agg[r["source"]][r["status"]] += r["candidates"]
    reasons[(r["source"], r["status"])] |= set(r["reason_codes"])
for src, c in sorted(agg.items()):
    parts = [f"{k} {v}" + (f"（{"、".join(sorted(reasons[(src, k)]))}）" if reasons[(src, k)] else "") for k, v in c.items()]
    print(f"  {src[:34]:34} " + "；".join(parts))
print("③ 文档库存量（已存进库的文档；“存库失败”=已入库但标记抓取失败的行，拿不到的候选不会入库，所以不计在内）")
for r in d["document_sources"]:
    if hit(r["source"]) and not r["source"].startswith(("file:", "tavily:")):
        print(f"  {r["source"][:42]:42} 文档 {r["documents"]:4}  存库失败 {r["failures"]:3}  库内最新一篇存入 {local(r["latest_fetch"])}")
' "$@"
}

# 结构化采集记录：最近的入库批次，以及“上游有没有新一期”的检查。
# 参数（可选）：来源名或任务名的片段（如 yfinance_consensus、consensus_tsm_daily、macro、exports）；不带参数 = 全部结构化来源。
# 注意：入库批次只记来源和数据集，不记标的；共识预期要按标的看，用 g0_consensus。
g0_ingest() {
  local rows="${2:-2}"
  G0_MAP="$(g0_map)" python -c '
import json, os, sys
jobmap = json.loads(os.environ["G0_MAP"])
want = sys.argv[1] if len(sys.argv) > 1 else ""
structured = sorted({s for m in jobmap.values() if m["domain"] == "structured" for s in m["sources"]})
terms = {want} if want else set()
for job, m in jobmap.items():
    if want and want in job:
        terms |= set(m["sources"])
picked = [s for s in structured if not want or any(t in s for t in terms)]
if not picked:
    sys.exit(f"没有结构化来源或任务名里含“{want}”。来源示例: " + ", ".join(structured[:6]))
print("\n".join(picked))
' "${1:-}" > /private/tmp/g0_ingest_sources.$$ || { rm -f /private/tmp/g0_ingest_sources.$$; return 1; }
  local src
  while read -r src; do
    echo "== $src"
    ats data ingestion-history --source "$src" --limit "$rows" | python -c '
import json, os, sys
from datetime import datetime
from zoneinfo import ZoneInfo
tz = ZoneInfo(os.environ.get("G0_TZ", "Asia/Taipei"))
rows = json.load(sys.stdin) or []
if not rows: print("  （没有入库记录）")
def when(r): return r.get("started_at") or r.get("checked_at") or ""
for r in sorted(rows, key=when, reverse=True):
    at = datetime.fromisoformat(when(r)).astimezone(tz).strftime("%m-%d %H:%M")
    if r.get("record_type") == "source_check":
        same = "已入库" if r.get("latest_upstream_identity") == r.get("latest_ingested_identity") else "上游有未入库版本"
        print(f"  {at}  {r.get("dataset_id", "")[:34]:34} 检查 {r["status"]:10} 上游最新期 {r.get("latest_available_period") or "-"}（{same}）")
    else:
        reasons = r.get("reason_codes_json") or ""
        note = (r.get("note") or "")[:70]
        print(f"  {at}  {r.get("dataset_id", "")[:34]:34} 入库 {r["status"]:12} 发现 {r["discovered"]:>4} 准入 {r["accepted"]:>4} 隔离 {r["quarantined"]:>3} 未变 {r["unchanged"]:>4}  {reasons if reasons not in ("{}", "[]") else ""} {note}"[:230])
'
  done < /private/tmp/g0_ingest_sources.$$
  rm -f /private/tmp/g0_ingest_sources.$$
}

# 共识预期按标的看：每只最近一次入库时间、估计针对哪个季度、EPS/营收/目标价均值，并标出异常。
# 参数（可选）：标的名片段。异常含义：
#   过旧   最近入库超过 36 小时
#   已发布 估计针对的季度已经发完财报（下次财报日离该季度末 > 60 天，或财报日已过），下一季度的预期没有采集（只存 0q）
g0_consensus() {
  python - "${1:-}" <<'PY'
import json, os, sqlite3, sys
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
tz = ZoneInfo(os.environ.get("G0_TZ", "Asia/Taipei"))
want = sys.argv[1].upper()
c = sqlite3.connect(os.path.join(os.environ["G0_REPO"], "var/data.sqlite"), timeout=10)
c.execute("PRAGMA query_only=ON")
rows = c.execute("""SELECT s.entity_id, s.metric_id, s.dimensions_json, o.period, o.value, o.known_at
    FROM structured_observations o JOIN structured_series s USING(series_id)
    WHERE s.dataset_id='market_consensus' AND s.metric_id IN
      ('consensus.eps.mean','consensus.revenue.mean','consensus.price_target.mean')""").fetchall()
latest = {}
for ent, metric, dims, period, value, known in rows:
    if want and want not in ent:
        continue
    key = (ent, metric)
    if key not in latest or known > latest[key][3]:
        latest[key] = (period, value, dims, known)
ents = sorted({k[0] for k in latest})
now = datetime.now(timezone.utc)
print(f"{'标的':10} {'最近入库(台北)':14} {'估计季度':11} {'下次财报日':11} {'EPS均值':>9} {'营收均值':>12} {'目标价均值':>9}  提示")
for ent in ents:
    eps = latest.get((ent, "consensus.eps.mean"))
    rev = latest.get((ent, "consensus.revenue.mean"))
    tgt = latest.get((ent, "consensus.price_target.mean"))
    known = max(v[3] for k, v in latest.items() if k[0] == ent)
    age_h = (now - datetime.fromisoformat(known)).total_seconds() / 3600
    dims = json.loads(eps[2]) if eps else {}
    event = dims.get("target_event_date", "")
    flags = []
    if age_h > 36: flags.append(f"过旧({age_h/24:.0f}天)")
    if eps and event:
        gap = (date.fromisoformat(event) - date.fromisoformat(eps[0])).days
        if gap > 60 or date.fromisoformat(event) < now.date():
            flags.append("已发布，下一季度预期未采集")
    at = datetime.fromisoformat(known).astimezone(tz).strftime("%m-%d %H:%M")
    f = lambda v, fmt: format(v[1], fmt) if v else "-"
    print(f"{ent:10} {at:14} {(eps[0] if eps else '-'):11} {event or '-':11} {f(eps, '9.3f'):>9} {f(rev, '12,.0f'):>12} {f(tgt, '9.2f'):>9}  {'；'.join(flags)}")
PY
}

# 公司研究包里某只票的共识预期、财务、文档与事实（只看最新）。参数：标的
g0_company() {
  ats data company "$1" | python -c '
import json, sys
d = json.load(sys.stdin)
m = d.get("measurements", [])
cons = [x for x in m if x["dataset_id"] == "market_consensus"]
fin = [x for x in m if x["dataset_id"] == "company_financials"]
if cons:
    latest = max(x["known_at"] for x in cons)
    print(f"共识预期（{cons[0]["source_id"]}，最近一次入库 {latest[:16]} UTC）:")
    for x in sorted((x for x in cons if x["known_at"] == latest and x["metric_id"].rsplit(".", 1)[-1] in ("mean", "low", "high", "median")), key=lambda x: (x["metric_id"], x["period"])):
        print(f"   {x["metric_id"]:44} {x["period"]:12} {x["value"]}")
else:
    print("共识预期: 无")
if fin:
    last = max(x["period"] for x in fin)
    rows = [x for x in fin if x["period"] == last]
    srcs = "、".join(sorted({x["source_id"] for x in rows}))
    print(f"财务（{srcs}）：最新报告期 {last}，{len(rows)} 项指标，入库 {max(x["known_at"] for x in rows)[:16]} UTC")
    for x in sorted(rows, key=lambda x: (x["metric_id"], x.get("period_basis") or ""))[:16]:
        print(f"   {x["metric_id"]:44} {x.get("period_basis") or "":8} {x["value"]:,} {x.get("unit") or ""}")
else:
    print("财务: 无")
docs = d.get("documents", [])
print(f"文档 {len(docs)} 篇；事实 {len(d.get("facts", []))} 条")
'
}

# 财报/宏观日历：各来源最近一次刷新、待裁决冲突数
g0_calendar() {
  ats events status | python -c '
import json, os, sys
from datetime import datetime
from zoneinfo import ZoneInfo
tz = ZoneInfo(os.environ.get("G0_TZ", "Asia/Taipei"))
q = json.load(sys.stdin)["quality"]
print("日历总体:", q["status"], "| 待裁决冲突候选:", q["conflicting_candidates"], "| 身份隔离:", q["identity_quarantine"])
for src, r in sorted(q.get("latest_by_source", {}).items()):
    at = datetime.fromisoformat(r["started_at"]).astimezone(tz).strftime("%m-%d %H:%M")
    err = (r.get("error_code") + " " + r.get("error_detail", ""))[:90] if r.get("error_code") else ""
    print(f"{src:28} {at}  {r["status"]:9} 发现 {r["discovered"]:>3} 发布 {r["published"]:>3} 冲突 {r["conflicts"]:>2}  {err}")
'
}

# 财报日历上某只票的事件状态（planned=计划中，released=已发布且有准入材料）。参数：标的
g0_event() {
  ats events status | python -c '
import json, sys
sym = sys.argv[1].upper()
for e in json.load(sys.stdin)["events"]:
    if e["event_type"] == "earnings" and f":{sym}:" in e["event_id"]:
        conf = e.get("release_confirmations") or []
        mats = [m.get("kind") for c in conf for m in c.get("materials", [])]
        print(f"{e["event_id"]:36} {e["event_date"]} {e.get("local_time") or "":5} {e["status"]:9} 版本 v{e["event_version"]}  准入材料 {mats or "无"}")
' "$1"
}

# 一个分析师读的那一整包数据现在是否合格（只检查，不记日志、不发飞书）。
# 只认 6 个分析师名，不认来源名：
#   information  读 ibkr_news、TrendForce、SemiAnalysis、SEC 目录/正文、电话会稿等全部文档
#   fundamental  读公司财务、共识预期、信息简报
#   layer        读产业链证据与知识库（chain_evidence、ai_hardware_knowledge）
#   macro        读宏观指标、宏观新闻、韩/台出口、FactSet
#   sector       读行业横截面（财务、共识、FactSet 行业分区）
#   technical    读实时行情（运行时，不入库）
g0_product() {
  case "$1" in
    information|fundamental|layer|macro|sector|technical) ;;
    *) echo "g0_product 只接受 6 个分析师名: information fundamental layer macro sector technical"
       echo "来源（如 ibkr_news）没有单独的合格检查，看 g0_refresh 和 g0_sources；它们汇总后由 information 检查。"
       return 1 ;;
  esac
  ats consumer-cutover health --consumer-id "$1" | python -c '
import json, sys
d = json.load(sys.stdin)
print("分析师:", d.get("consumer_id"), "| 结论:", d.get("status"))
for p in d.get("problems", []): print("  问题:", str(p)[:200])
for w in d.get("warnings", [])[:15]: print("  提醒:", str(w)[:200])
'
}

# 数据产品健康与读路由的变化记录（最近 30 行）
g0_product_log() {
  ats consumer-cutover log | python -c '
import json, os, sys
from datetime import datetime
from zoneinfo import ZoneInfo
tz = ZoneInfo(os.environ.get("G0_TZ", "Asia/Taipei"))
for r in json.load(sys.stdin)[-30:]:
    at = datetime.fromisoformat(r["recorded_at"]).astimezone(tz).strftime("%m-%d %H:%M")
    health = json.loads(r.get("health_json") or "{}").get("status", "")
    print(f"{at}  {r["consumer_id"]:12} {r["action"]:9} 路由 {r["from_route"]}→{r["to_route"]:7} 检查 {health or "-":7} {r["actor"][:22]:22} {r["reason"][:60]}")
'
}

# 工作流运行：时间、任务、范围、状态、各标的结果（新算/复用/阻断/失败）。参数：条数（默认 20）、工作流名片段
g0_runs() {
  ats workflow runs --limit "${1:-20}" | python -c '
import json, os, sys, collections
from datetime import datetime
from zoneinfo import ZoneInfo
tz = ZoneInfo(os.environ.get("G0_TZ", "Asia/Taipei"))
want = sys.argv[1] if len(sys.argv) > 1 else ""
for r in json.load(sys.stdin):
    req = r["request"]; res = r.get("result") or {}
    tasks = ",".join(req.get("requested_tasks", []))
    if want not in tasks:
        continue
    c = collections.Counter()
    bad = []
    for o in res.get("outcomes", []):
        kind = "复用" if o.get("reused") else {"succeeded": "新算"}.get(o["status"], o["status"])
        c[kind] += 1
        if o["status"] != "succeeded":
            bad.append(f"{o["task_id"]}/{o["scope"]["id"]}: {o["status"]} {o.get("detail", "")[:90]}")
    start = datetime.fromisoformat(r["started_at"]).astimezone(tz).strftime("%m-%d %H:%M") if r.get("started_at") else "-"
    print(f"{start}  {tasks:30} {req["scope"]["kind"]}:{req["scope"]["id"]:14} {r["status"]:10} " + " ".join(f"{k}{v}" for k, v in c.items()))
    for line in bad[:8]:
        print("        └", line)
    for m in res.get("missing_requirements", [])[:3]:
        print("        缺口:", str(m)[:120])
' "${2:-}"
}

# 触发记录：定时/事件/手动触发及其状态。参数：工作流 id（必填）、条数（默认 20）
g0_triggers() {
  ats workflow triggers --workflow-id "$1" --limit "${2:-20}" | python -c '
import json, os, sys
from datetime import datetime
from zoneinfo import ZoneInfo
tz = ZoneInfo(os.environ.get("G0_TZ", "Asia/Taipei"))
def local(s):
    return datetime.fromisoformat(s).astimezone(tz).strftime("%m-%d %H:%M") if s else "-"
for r in json.load(sys.stdin):
    what = local(r["scheduled_for"]) + " 应触发" if r["scheduled_for"] else r["event_id"][:46]
    print(f"{r["kind"]:8} {what:46} {r["status"]:17} {r["reason_code"][:32]:32} 记录于 {local(r["created_at"])}")
'
}

# 研究产出（投影）：每个标的/层/组合最近一份的时间与摘要。参数：角色（见下）、可选标的
#   information_brief | fundamental_expectation_update | fundamental_event_review | layer_analysis
#   sector_allocation | macro_review | technical_review
g0_latest() {
  sqlite3 -json -cmd "PRAGMA query_only=ON" "$G0_REPO/var/ats.sqlite" "SELECT projection_id, scope_kind, scope_id, as_of, valid_until, status, model_version, payload FROM task_projection_envelopes WHERE agent_role='$1' AND ('${2:-}'='' OR scope_id='${2:-}') ORDER BY created_at DESC LIMIT 400" | python -c '
import json, os, sys
from datetime import datetime
from zoneinfo import ZoneInfo
tz = ZoneInfo(os.environ.get("G0_TZ", "Asia/Taipei"))
def local(s):
    try: return datetime.fromisoformat(s).astimezone(tz).strftime("%m-%d %H:%M")
    except ValueError: return s or "-"
raw = sys.stdin.read().strip()
seen = set()
for r in (json.loads(raw) if raw else []):
    key = (r["scope_kind"], r["scope_id"])
    if key in seen: continue
    seen.add(key)
    p = json.loads(r["payload"] or "{}")
    text = p.get("summary") or p.get("headline") or p.get("notes") or p.get("driver") or p.get("regime") or ""
    print(f"{r["scope_id"][:14]:14} 产出 {local(r["as_of"])}  有效至 {local(r["valid_until"])}  {r["status"]:9} {r["projection_id"][:12]}  {str(text)[:70]}")
'
}

# 打开一份投影的全文（逐字段）。参数：投影 id（g0_latest 输出里的 12 位编号）
g0_show() {
  # 用法: g0_show 编号（g0_latest 输出里的 12 位编号，可只写前几位）。显示部分在 g0_show.py，避免粘贴多行引号块出错
  [[ -z "$1" ]] && { echo "用法: g0_show 编号（先用 g0_latest 找 12 位编号，例如 g0_show d865de3edfa8）"; return 1; }
  sqlite3 -json -cmd "PRAGMA query_only=ON" "$G0_REPO/var/ats.sqlite" "SELECT agent_role, scope_id, as_of, input_refs, data_vintage_refs, model_version, prompt_version, content_hash, payload FROM task_projection_envelopes WHERE projection_id LIKE '$1%' LIMIT 1" | python "${G0_TOOLS_DIR}/g0_show.py"
}

# ⚠ 会写真实数据（不是只读），不发模型请求：批量“现在就重采”，不等例行窗口。
# 每个任务用控制器的 --manual 模式跑：自己一个窗口标签（如 2026-10-21~manual-20261021T143000），
# 同一天可反复跑，也不占用例行窗口；被停用（enabled 未开）的任务仍然不跑。
# 数据是“追加新版本”：上游有变化就写入新的一版并成为最新，上游没变就记 no_change；旧版本保留作审计，不删除。
# 用法：
#   g0_rerun U | S | D          按类：非结构化 / 结构化 / 派生（以 schedules.yaml 的分组为准）
#   g0_rerun network            10-21 圈定的“TLS 被重置”受影响任务（Yahoo、SEC、政府统计、台湾出口、SemiAnalysis、defeatbeta）
#   g0_rerun consensus_ tw_ic   任务名或来源名片段，可写多个
#   加 --dry 只列出将要跑的任务，不执行；加 -y 跳过确认
g0_rerun() {
  local dry=0 yes=0 args=() a
  for a in "$@"; do
    case "$a" in --dry) dry=1 ;; -y) yes=1 ;; *) args+=("$a") ;; esac
  done
  [[ ${#args} -eq 0 ]] && { echo "用法: g0_rerun U|S|D|network|<任务或来源片段>... [--dry] [-y]"; return 1; }
  local jobs
  jobs="$(G0_MAP="$(g0_map)" python -c '
import json, os, sys
jobmap = json.loads(os.environ["G0_MAP"])
domains = {"U": "unstructured", "S": "structured", "D": "derived"}
network = ("yfinance_", "sec_", "defeatbeta_", "tw_mof", "bls_", "bea_", "federal_reserve", "semianalysis")
picked = []
for want in sys.argv[1:]:
    for job, m in jobmap.items():
        hit = (want in domains and m["domain"] == domains[want]) or \
              (want == "network" and any(s.startswith(network) for s in m["sources"])) or \
              (want not in domains and want != "network" and (want in job or any(want in s for s in m["sources"])))
        if hit and job not in picked:
            picked.append(job)
if not picked:
    sys.exit("没有匹配的任务: " + " ".join(sys.argv[1:]))
print("\n".join(picked))
' "${args[@]}")" || return 1
  echo "将要重采 $(echo "$jobs" | wc -l | tr -d ' ') 个任务（写真实数据，不发模型请求）："
  echo "$jobs" | sed 's/^/  /'
  [[ $dry -eq 1 ]] && return 0
  if [[ $yes -ne 1 ]]; then
    read -q "?继续？[y/N] " || { echo; echo "已取消"; return 1; }
    echo
  fi
  local job out n=0 ok=0
  for job in ${(f)jobs}; do
    n=$((n+1))
    printf '[%s] %-48s ' "$(date +%H:%M:%S)" "$job"
    out="$(python -m ats.data.refresh tick --job "$job" --manual 2>/dev/null | head -1)"
    G0_OUT="$out" python -c '
import json, os
try:
    row = json.loads(os.environ["G0_OUT"])[0]
except Exception:
    print("（没有结果，看 var/logs/data-refresh.*.log）"); raise SystemExit
extra = f"  入库批次 {row["ingestion_runs"]} 发布 {row["published"]}" if "ingestion_runs" in row else ""
print(f"{row["status"]:10} {row.get("reason", "")[:30]:30}{extra}")
'
    [[ "$out" == *'"status": "succeeded"'* || "$out" == *'"status": "no_change"'* ]] && ok=$((ok+1))
  done
  echo "完成：$ok/$n 个成功或无变化。下一步：g0_refresh 看总表，g0_history <任务> 看单个任务的批次与错误原文。"
}

# 兼容旧名字
g0_runnow() { g0_rerun "$@"; }

# 某个任务（可多个）最近几个窗口的完整历史：每个窗口的状态、尝试次数、原因，以及对应那次入库批次的结果和错误原文。
# 比 g0_ingest 更适合“只看一只票”：g0_ingest 的入库批次不记标的，这里靠控制器账本把任务和批次对上。
# 参数：任务名或来源名片段（必填，如 consensus_tsm、ibkr_news）、窗口数（默认 5）
g0_history() {
  [[ -z "$1" ]] && { echo "用法: g0_history <任务名或来源名片段> [窗口数]"; return 1; }
  python -m ats.data.refresh status | G0_MAP="$(g0_map)" python -c '
import json, os, sqlite3, sys
from datetime import datetime
from zoneinfo import ZoneInfo
tz = ZoneInfo(os.environ.get("G0_TZ", "Asia/Taipei"))
jobmap = json.loads(os.environ["G0_MAP"])
want, keep = sys.argv[1], int(sys.argv[2])
jobs = [j for j, m in jobmap.items() if want in j or any(want in s for s in m["sources"])]
if not jobs:
    sys.exit(f"没有任务或来源名里含“{want}”")
d = json.load(sys.stdin)
db = sqlite3.connect(os.path.join(os.environ["G0_REPO"], "var/data.sqlite"), timeout=10)
db.execute("PRAGMA query_only=ON")
def run_info(run_id):
    for table in ("structured_ingestion_runs", "data_ingestion_runs"):
        try:
            cols = [c[1] for c in db.execute(f"pragma table_info({table})")]
            note = "note" if "note" in cols else "NULL"
            row = db.execute(f"select status, discovered, accepted, quarantined, {note} from {table} where run_id=?", (run_id,)).fetchone()
        except sqlite3.Error:
            row = None
        if row:
            return row
    return None
def local(s):
    return datetime.fromisoformat(s).astimezone(tz).strftime("%m-%d %H:%M") if s else "-"
for job in jobs:
    rows = sorted((r for r in d["runs"] if r["job_id"] == job), key=lambda r: r["claimed_at"], reverse=True)[:keep]
    print(f"== {job}")
    if not rows:
        print("  从未运行")
    for r in rows:
        print(f"  窗口 {r["window_date"]}  {r["status"]:10} {r["reason_code"][:28]:28} 尝试 {r["attempts"]} 次  完成 {local(r["completed_at"])}")
        for rid in json.loads(r.get("ingestion_run_ids_json") or "[]"):
            info = run_info(rid)
            if info:
                note = (info[4] or "")[:150]
                print(f"      批次 {rid[:8]}: {info[0]}  发现 {info[1]} 准入 {info[2]} 隔离 {info[3]}  {note}")
' "$1" "${2:-5}"
}
