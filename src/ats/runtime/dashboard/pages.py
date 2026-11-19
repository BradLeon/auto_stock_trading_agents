"""HTML for the dashboard: plain server-rendered pages, inline styles, no JavaScript build.

Every ⓘ is a native <details>: click to open the Chinese note, no script needed. Pages refresh themselves
every 60 seconds (minutes are fine-grained enough: sources are checked hourly, workflows run daily).
"""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from urllib.parse import quote
from zoneinfo import ZoneInfo

from .queries import Note

TZ = ZoneInfo("Asia/Taipei")

STATUS_CN = {
    "succeeded": "成功", "no_change": "无新数据", "failed": "失败", "quarantined": "隔离",
    "discovery_only": "仅发现", "complete": "完成", "incomplete": "不完整", "running": "运行中",
    "planned": "已计划", "cancelled": "已取消", "missed": "缺跑", "paused": "已暂停", "holiday": "休市",
    "future": "未到时", "none": "", "blocked": "阻断", "missing": "缺输入", "stale": "过期",
    "approved": "已批准", "approved_not_executed": "批准未执行", "manual_review": "待人工复核",
    "superseded": "被取代", "approval_rejected": "已驳回", "executed": "已执行",
}
LIGHT_CN = {"green": "正常", "yellow": "临近", "red": "逾期", "grey": "停用"}
DOMAIN_CN = {"structured": "结构化", "unstructured": "非结构化", "derived": "派生"}

_CSS = """
:root{--bg:#fbfbf9;--card:#fff;--ink:#1d1d1b;--ink2:#5c5b57;--ink3:#8b8a84;--line:rgba(0,0,0,.09);
--ok:#0b8a0b;--okbg:#e7f4e4;--warn:#9a6700;--warnbg:#fbf0d5;--bad:#b42318;--badbg:#fbe6e4;--idle:#6b6a65;
--idlebg:#efeeea;--acc:#1f6fd1;--pur:#5b4bc4;--purbg:#ecebfb}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--card:#1e1e1d;--ink:#eeede8;--ink2:#bdbcb3;--ink3:#8b8a84;
--line:rgba(255,255,255,.1);--ok:#5cc85c;--okbg:#17321a;--warn:#e3b341;--warnbg:#3a2e0c;--bad:#f2766b;
--badbg:#3d1714;--idle:#a9a8a1;--idlebg:#2a2a28;--acc:#6aa5f0;--pur:#a69cf0;--purbg:#2a2650}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.6 -apple-system,"PingFang SC","Helvetica Neue",sans-serif}
main{max-width:1180px;margin:0 auto;padding:16px}
nav{display:flex;gap:4px;align-items:center;border-bottom:.5px solid var(--line);padding:10px 16px;
background:var(--card);position:sticky;top:0;z-index:2;flex-wrap:wrap}
nav a{color:var(--ink2);text-decoration:none;padding:4px 10px;border-radius:6px}
nav a.on{background:var(--idlebg);color:var(--ink)}nav .t{margin-left:auto;color:var(--ink3);font-size:12px}
h1{font-size:20px;font-weight:500;margin:8px 0 12px}h2{font-size:15px;font-weight:500;margin:20px 0 8px}
a{color:var(--acc)}.muted{color:var(--ink3);font-size:12px}.sub{color:var(--ink2)}
.card{background:var(--card);border:.5px solid var(--line);border-radius:12px;padding:12px 16px;margin:12px 0}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.kpi{background:var(--idlebg);border-radius:8px;padding:10px 14px}.kpi .l{font-size:12px;color:var(--ink2)}
.kpi .v{font-size:24px;font-weight:500}
table{width:100%;border-collapse:collapse;font-size:13px}th{color:var(--ink3);font-weight:400;text-align:left;
padding:6px;border-bottom:.5px solid var(--line)}td{padding:6px;border-bottom:.5px solid var(--line);vertical-align:top}
.pill{display:inline-block;font-size:12px;padding:1px 8px;border-radius:6px;white-space:nowrap}
.green{background:var(--okbg);color:var(--ok)}.yellow{background:var(--warnbg);color:var(--warn)}
.red{background:var(--badbg);color:var(--bad)}.grey{background:var(--idlebg);color:var(--idle)}
.purple{background:var(--purbg);color:var(--pur)}
.mono{font-family:ui-monospace,Menlo,monospace;font-size:12px}
details.note{display:inline}details.note summary{display:inline;cursor:pointer;color:var(--acc);list-style:none}
details.note summary::-webkit-details-marker{display:none}
details.note[open] .nb{display:block}.nb{background:var(--idlebg);border-radius:8px;padding:8px 12px;margin:6px 0;
font-size:12px;line-height:1.7;white-space:normal;max-width:640px}
.strip{display:flex;gap:1px}.strip i{display:block;width:6px;height:14px;border-radius:1px}
.grid{display:grid;gap:3px;font-size:12px;align-items:center}
.cell{height:28px;border-radius:4px;display:flex;align-items:center;justify-content:center;font-size:11px;
text-decoration:none}
.tree{margin:0;padding-left:18px}.tree li{margin:2px 0}
.scroll{overflow-x:auto}table.wide{min-width:1100px}td,th{white-space:nowrap}td.wrap{white-space:normal}table.inner{width:100%;border-collapse:collapse;margin:4px 0}table.inner td,table.inner th{padding:3px 6px;border-bottom:.5px solid var(--line);font-size:12px;white-space:normal;vertical-align:top}td.key{white-space:normal;word-break:break-all;max-width:220px}td.outcell{min-width:620px;white-space:normal}details.out{margin:2px 0}details.out>summary{cursor:pointer;white-space:normal}.outbox{background:var(--idlebg);border-radius:8px;padding:8px 12px;margin:4px 0 8px;font-size:12px;line-height:1.7;white-space:normal;max-width:900px}details.raw>summary{cursor:pointer;color:var(--acc);font-size:12px}pre.raw{white-space:pre-wrap;word-break:break-all;font-size:11px;max-height:420px;overflow:auto;background:var(--card);border-radius:6px;padding:8px}
"""

_STATE_CLASS = {"complete": "green", "succeeded": "green", "incomplete": "yellow", "running": "grey",
                "planned": "grey", "failed": "red", "missed": "red", "paused": "grey", "holiday": "grey",
                "future": "grey", "none": "", "no_change": "grey", "quarantined": "yellow",
                "discovery_only": "grey", "manual_review": "purple"}
_STRIP_COLOR = {"succeeded": "var(--ok)", "no_change": "#b4b2a9", "quarantined": "#e0a526",
                "failed": "var(--bad)", "discovery_only": "#b4b2a9"}


def layout(title: str, active: str, body: str, *, now: datetime | None = None, refresh: bool = True) -> str:
    now = (now or datetime.now(timezone.utc)).astimezone(TZ)
    links = [("/dashboard", "总览", "overview"), ("/dashboard/sources", "数据源", "sources"),
             ("/dashboard/workflows", "流程", "workflows"), ("/dashboard/lineage", "决策溯源", "lineage")]
    nav = "".join(f'<a href="{h}" class="{"on" if k == active else ""}">{t}</a>' for h, t, k in links)
    return (f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            + ('<meta http-equiv="refresh" content="60">' if refresh else '') +
            f'<title>{escape(title)} · 运维看板</title>'
            f'<style>{_CSS}</style></head><body><nav><b style="margin-right:8px">运维看板</b>{nav}'
            f'<span class="t">{now:%m-%d %H:%M:%S} 台北 · {"每 60 秒刷新" if refresh else "已结束，不自动刷新"}</span>'
            f'</nav><main>{body}</main></body></html>')


def t(at: datetime | None, *, date_only: bool = False) -> str:
    """Times shown in Taipei (the reader's clock); a date-only source time stays a date."""
    if at is None:
        return "—"
    if (at.hour, at.minute, at.second, at.microsecond) == (0, 0, 0, 0) or date_only:
        return f"{at:%Y-%m-%d}"
    return f"{at.astimezone(TZ):%m-%d %H:%M}"


def ago(at: datetime | None, now: datetime) -> str:
    if at is None:
        return ""
    s = (now - at).total_seconds()
    if s < 3600:
        return f"{max(1, int(s // 60))} 分钟前"
    if s < 86_400:
        return f"{s / 3600:.1f} 小时前"
    return f"{s / 86_400:.1f} 天前"


def pill(text: str, cls: str) -> str:
    return f'<span class="pill {cls}">{escape(text)}</span>' if text else ""


def note(n: Note | None) -> str:
    """The ⓘ: click to open the Chinese explanation; a missing one says so instead of staying blank."""
    if n is None or n.missing:
        return ' <span class="pill grey" title="config/ops/catalog_zh.yaml 里还没有这一条">缺说明</span>'
    rows = [("是什么", n.what), ("来源", n.source), ("谁在用", n.used_by), ("坏了影响", n.if_broken)]
    body = "<br>".join(f"<b>{k}：</b>{escape(v)}" for k, v in rows if v)
    return (f' <details class="note"><summary title="说明">ⓘ</summary>'
            f'<div class="nb">{body}</div></details>')


def label(n: Note | None) -> str:
    return f' <span class="sub">{escape(n.label)}</span>' if n and n.label else ""


def money(v) -> str:
    if v is None:
        return "未知"
    return f"${v:,.4f}" if v < 1 else f"${v:,.2f}"


# --------------------------------------------------------------------------- #
# overview
# --------------------------------------------------------------------------- #
def overview_page(ov: dict, catalog: dict) -> str:
    now = ov["now"]
    c = ov["counts"]
    kpi = lambda l, v, color="": (f'<div class="kpi"><div class="l">{l}</div>'  # noqa: E731
                                  f'<div class="v" style="color:{color}">{v}</div></div>')
    llm = (ov.get("llm_today") or [{}])[-1] if ov.get("llm_today") else {}
    parts = ['<h1>总览</h1><div class="kpis">',
             kpi("数据源 正常", c.get("green", 0), "var(--ok)"), kpi("临近 SLO", c.get("yellow", 0), "var(--warn)"),
             kpi("逾期", c.get("red", 0), "var(--bad)"), kpi("停用", c.get("grey", 0)),
             kpi("今日模型调用", f'{llm.get("n") or 0} 次 · {money(llm.get("cost")) if llm.get("n") else "$0"}'),
             "</div>"]
    jobs = catalog.get("jobs", {})
    rows = "".join(
        f"<tr><td>{pill(LIGHT_CN[r.light], r.light)}</td>"
        f'<td><a class="mono" href="/dashboard/sources/{quote(r.job_id)}">{escape(r.job_id)}</a>'
        f"{label(jobs.get(r.group))}{note(jobs.get(r.group))}</td>"
        f"<td>{pill(STATUS_CN.get(r.last_status, r.last_status), _STATE_CLASS.get(r.last_status, 'grey'))} "
        f'<span class="muted">{escape(r.last_reason)}</span></td>'
        f"<td>{t(r.last_healthy_at)}<div class=muted>{ago(r.last_healthy_at, now)}</div></td>"
        f"<td>{r.consecutive_failures or ''}</td></tr>"
        for r in ov["attention"])
    parts.append('<div class="card"><h2 style="margin-top:0">需要处理（红、黄与最近一次失败的置顶）</h2>'
                 + (f'<table><tr><th>灯</th><th>采集点</th><th>最近尝试</th><th>最近正常运行</th><th>连续失败</th></tr>'
                    f'{rows}</table>' if rows else '<p class="muted">没有需要处理的采集点。</p>') + "</div>")
    wf = catalog.get("workflows", {})
    today = "".join(
        f"<tr><td>{escape(w.workflow_id)}{label(wf.get(w.workflow_id))}{note(wf.get(w.workflow_id))}</td>"
        f"<td>{escape(w.schedule)}</td><td>{pill(STATUS_CN.get(cell.state, cell.state), _STATE_CLASS.get(cell.state, ''))}"
        f"{' ×' + str(cell.count) if cell.count > 1 else ''} {pill(STATUS_CN.get(cell.note, cell.note), 'purple') if cell.note else ''}</td></tr>"
        for w, cell in ov["today"])
    parts.append(f'<div class="card"><h2 style="margin-top:0">今日流程（纽约日期）</h2><table>'
                 f'<tr><th>流程</th><th>计划</th><th>状态</th></tr>{today}</table></div>')
    chief = ov.get("chief")
    if chief:
        fr = "".join(f"<tr><td>{escape(f['label'])}</td><td>{pill(f['age'], f['light'])}</td>"
                     f"<td class=muted>窗口 {_window(f['window'])}</td><td>{t(f['at'])}</td></tr>"
                     for f in chief.get("freshness") or [])
        parts.append(f'<div class="card"><h2 style="margin-top:0">最近一次首席决策 · '
                     f'<a href="/dashboard/lineage/{quote(chief["cycle_id"])}">{escape(chief["cycle_id"])}</a> '
                     f'{pill(STATUS_CN.get(chief["status"], chief["status"]), "purple")}</h2>'
                     f'<p class="muted">各类研究最新一份在决策时刻的年龄；黄 = 已过新鲜度窗口一半，红 = 已超窗口。</p>'
                     f'<table><tr><th>研究</th><th>距决策</th><th>窗口</th><th>截至（台北）</th></tr>{fr}</table></div>')
    return layout("总览", "overview", "".join(parts), now=now)


def _window(seconds) -> str:
    if not seconds:
        return "—"
    return f"{seconds / 86_400:g} 天" if seconds >= 2 * 86_400 else f"{seconds / 3600:g} 小时"


# --------------------------------------------------------------------------- #
# sources
# --------------------------------------------------------------------------- #
def _strip(history, days_shown: int = 30) -> str:
    cells = [f'<i title="{escape(d)} {escape(STATUS_CN.get(s, s))}" style="background:{_STRIP_COLOR.get(s, "#ccc")}"></i>'
             for d, s in history[-days_shown:]]
    return f'<div class="strip">{"".join(cells)}</div>'


def _data_time(dt, now) -> str:
    if dt.at is None and not dt.period:
        return '<span class="muted">未登记</span>'
    head = f"{escape(dt.kind)} {t(dt.at)}" if dt.at else ""
    period = f'<div class="muted">数据期 {escape(dt.period)}</div>' if dt.period else ""
    return f'{head}<div class="muted">{ago(dt.at, now)}</div>{period}'


def _source_row(r, now, jobs, *, indent=False, show_note=True) -> str:
    n = jobs.get(r.group)
    name = escape(r.entity if indent else r.job_id)
    return (f"<tr><td>{pill(LIGHT_CN[r.light], r.light)}</td>"
            f'<td style="{"padding-left:24px" if indent else ""}"><a class="mono" href="/dashboard/sources/{quote(r.job_id)}">{name}</a>'
            f'{(label(n) + note(n)) if show_note else ""}</td>'
            f"<td>{escape(DOMAIN_CN.get(r.domain, r.domain))}<div class=muted>{escape(r.schedule)}</div></td>"
            f"<td>{pill(STATUS_CN.get(r.last_status, r.last_status), _STATE_CLASS.get(r.last_status, 'grey'))}"
            f'<div class="muted">{escape(r.last_reason)}</div><div class=muted>{t(r.last_at)}</div></td>'
            f"<td>{t(r.last_healthy_at)}<div class=muted>{ago(r.last_healthy_at, now)}</div></td>"
            f"<td>{t(r.last_new_at)}<div class=muted>{ago(r.last_new_at, now) or '窗口内无'}</div></td>"
            f"<td>{_data_time(r.data_time, now)}</td><td>{r.slo_days} 天</td>"
            f"<td>{r.consecutive_failures or ''}</td><td>{_strip(r.history)}</td></tr>")


def sources_page(groups, catalog: dict, now: datetime, *, only: str = "", domain: str = "") -> str:
    jobs = catalog.get("jobs", {})
    total = sum(len(g.rows) for g in groups)
    issues = sum(1 for g in groups for r in g.rows if r.light in ("red", "yellow") or r.last_status in ("failed", "quarantined"))
    filt = lambda label_, q, on: (f'<a class="pill {"green" if on else "grey"}" style="text-decoration:none" '  # noqa: E731
                                  f'href="/dashboard/sources{q}">{label_}</a>')
    bar = " ".join([filt(f"全部 {total}", "", not only and not domain), filt(f"只看异常 {issues}", "?only=issues", only == "issues")]
                   + [filt(v, f"?domain={k}", domain == k) for k, v in DOMAIN_CN.items()])
    head = ("<tr><th>灯</th><th>采集点</th><th>类别 / 频率</th><th>最近尝试</th><th>最近正常运行</th>"
            "<th>上次拿到新数据</th><th>数据最新</th><th>SLO</th><th>连续失败</th><th>近 30 天</th></tr>")
    body = []
    for g in groups:
        rows = [r for r in g.rows
                if (not only or r.light in ("red", "yellow") or r.last_status in ("failed", "quarantined"))
                and (not domain or r.domain == domain)]
        if not rows:
            continue
        if not g.expanded:
            body.append(_source_row(rows[0], now, jobs))
            continue
        bad = sum(1 for r in g.rows if r.light in ("red", "yellow"))
        n = jobs.get(g.group)
        body.append(f'<tr><td>{pill(LIGHT_CN[g.light], g.light)}</td><td colspan="9"><b class="mono">{escape(g.group)}</b>'
                    f"{label(n)}{note(n)} <span class=muted>逐个标的 {len(g.rows)} 个"
                    f"{f' · {bad} 个临近或逾期' if bad else ''}</span></td></tr>")
        body += [_source_row(r, now, jobs, indent=True, show_note=False) for r in rows]
    legend = ('<p class="muted">灯按“距最近一次正常运行（成功或无新数据）”对照 SLO：超过 SLO 红，超过一半黄。'
              "“上次拿到新数据”只作参考、不参与着色——季度财报类任务在两次财报之间“无新数据”是正常的。"
              "时间为台北时间；只有日期的是来源本身只给到日期。</p>")
    return layout("数据源", "sources",
                  f'<h1>数据源</h1><div>{bar}</div>{legend}<div class="card scroll"><table class="wide">{head}{"".join(body)}</table></div>',
                  now=now)


def source_detail_page(job_id: str, row, runs: list[dict], catalog: dict, now: datetime) -> str:
    n = catalog.get("jobs", {}).get(row.group if row else job_id)
    head = (f'<h1 class="mono">{escape(job_id)}</h1><p>{label(n)}{note(n)}</p>' +
            (f'<p>{pill(LIGHT_CN[row.light], row.light)} 最近正常运行 {t(row.last_healthy_at)}（{ago(row.last_healthy_at, now)}）'
             f' · SLO {row.slo_days} 天 · 源 <span class="mono">{escape(", ".join(row.sources))}</span></p>' if row else ""))
    trs = []
    for r in runs:
        attempts = "<br>".join(f"#{a['attempt_no']} {escape(STATUS_CN.get(a['status'], a['status'] or ''))} "
                               f"<span class=muted>{escape(a.get('reason_code') or '')}</span>"
                               for a in r.get("attempt_rows") or [])
        trs.append(f"<tr><td>{escape(r['window_date'])}</td>"
                   f"<td>{pill(STATUS_CN.get(r['status'], r['status']), _STATE_CLASS.get(r['status'], 'grey'))}</td>"
                   f"<td class=mono>{escape(r.get('reason_code') or '')}</td><td>{r.get('attempts') or ''}</td>"
                   f"<td>{escape((r.get('started_at') or '')[:19])}</td><td>{escape((r.get('completed_at') or '')[:19])}</td>"
                   f"<td>{attempts}</td></tr>")
    table = ("<table><tr><th>窗口</th><th>状态</th><th>原因码</th><th>尝试</th><th>开始（UTC）</th><th>结束（UTC）</th>"
             f"<th>逐次尝试</th></tr>{''.join(trs)}</table>") if trs else '<p class="muted">近期没有运行记录。</p>'
    return layout(job_id, "sources", f'{head}<div class="card scroll">{table}</div>', now=now)


# --------------------------------------------------------------------------- #
# workflows
# --------------------------------------------------------------------------- #
_CELL_COLOR = {"complete": ("var(--ok)", "#fff"), "incomplete": ("#e0a526", "#2c2c2a"),
               "failed": ("var(--bad)", "#fff"), "missed": ("var(--badbg)", "var(--bad)"),
               "running": ("var(--acc)", "#fff"), "planned": ("var(--acc)", "#fff"),
               "paused": ("var(--idlebg)", "var(--idle)"), "holiday": ("var(--idlebg)", "var(--idle)"),
               "future": ("transparent", "var(--ink3)"), "none": ("transparent", "var(--ink3)"),
               "cancelled": ("var(--idlebg)", "var(--idle)")}


def workflows_page(days, rows, catalog: dict, llm_days: list[dict], now: datetime) -> str:
    wf = catalog.get("workflows", {})
    cols = f"170px repeat({len(days)}, minmax(30px, 1fr))"
    out = [f'<div class="grid" style="grid-template-columns:{cols}"><div></div>']
    out += [f'<div class="muted" style="text-align:center">{d:%m-%d}<br>周{"一二三四五六日"[d.weekday()]}</div>' for d in days]
    for w in rows:
        out.append(f'<div>{escape(w.workflow_id)}{note(wf.get(w.workflow_id))}'
                   f'<div class="muted">{escape(w.schedule)}</div></div>')
        for c in w.cells:
            bg, fg = _CELL_COLOR.get(c.state, ("var(--idlebg)", "var(--ink)"))
            border = "border:.5px dashed var(--line);" if c.state in ("none", "future") else ""
            text = str(c.count) if c.count > 1 else ("缺" if c.state == "missed" else "")
            if c.note == "manual_review":
                bg, fg = "var(--pur)", "#fff"
            tip = f"{w.workflow_id} {c.day} {STATUS_CN.get(c.state, c.state)}" + \
                  (f" · {STATUS_CN.get(c.note, c.note)}" if c.note else "") + (f" · {c.count} 次" if c.count else "") + \
                  (f" · 模型 {money(w.llm_cost.get(c.day))}" if c.day in w.llm_cost else "")
            href = (f"/dashboard/lineage/{quote(c.cycle_id)}" if c.cycle_id else
                    f"/dashboard/workflows/run/{quote(c.run_ids[0])}" if len(c.run_ids) == 1 else
                    f"/dashboard/workflows/{quote(w.workflow_id)}/{c.day.isoformat()}" if c.run_ids else "")
            cell = (f'<a class="cell" href="{href}" title="{escape(tip)}" style="background:{bg};color:{fg};{border}">{text}</a>'
                    if href else f'<div class="cell" title="{escape(tip)}" style="background:{bg};color:{fg};{border}">{text}</div>')
            out.append(cell)
    by_day = {r["day"]: r for r in llm_days}
    out.append('<div class="muted">模型费用 / 日</div>')
    for d in days:
        r = by_day.get(d.isoformat())
        out.append(f'<div class="muted" style="text-align:center;font-size:11px">{money(r["cost"]) if r and r["cost"] is not None else ("未知" if r else "")}</div>')
    out.append("</div>")
    legend = ('<p class="muted">绿 完成 · 黄 不完整（缺输入）· 红 失败 · 浅红“缺” 按计划应跑而未跑 · 紫 首席转人工复核 · '
              '灰 暂停/休市 · 虚线 无计划或未到时。格内数字 = 当天运行次数（>1 表示重试或重跑）。点格子：一次运行直接看明细，多次运行看当天全部运行，首席看当轮决策溯源。'
              '研究与交易流程按纽约日期归日，周六的宏观/行业按台北日期。模型费用从入账上线之日起才有。</p>')
    return layout("流程", "workflows", f'<h1>流程（近 {len(days)} 天）</h1>{legend}<div class="card scroll">{"".join(out)}</div>',
                  now=now)


def _seconds(value) -> str:
    return "" if value is None else f"{value:.1f}s"


def run_page(d: dict, catalog: dict, now: datetime) -> str:
    run, req = d["run"], d["request"]
    wid = (req.get("requested_tasks") or [""])[0]
    n = catalog.get("workflows", {}).get(wid)
    head = (f'<h1>{escape(wid)} · <span class="mono">{escape(run["run_id"])}</span></h1>'
            f'<p>{label(n)}{note(n)} {pill(STATUS_CN.get(run["status"], run["status"]), _STATE_CLASS.get(run["status"], "grey"))}'
            f' · 开始 {escape((run.get("started_at") or "")[:19])} · 结束 {escape((run.get("ended_at") or "")[:19])}（UTC）'
            f' · 作用域 <span class="mono">{escape(str(req.get("scope") or ""))}</span></p>')
    from .outputs import task_outputs

    def detail(x) -> str:
        if x["status"] == "succeeded":
            return task_outputs(x)
        code = f'<span class="mono">{escape(x.get("error_code") or "")}</span> ' if x.get("error_code") else ""
        return code + escape((x.get("error_detail") or "")[:300]) + (task_outputs(x) if x.get("outputs") else "")

    tasks = "".join(
        f"<tr><td class='mono key'>{escape(x['task_instance_key'] or x['task_id'])}</td><td>{x['attempt_no']}</td>"
        f"<td>{pill(STATUS_CN.get(x['status'], x['status']), _STATE_CLASS.get(x['status'], 'yellow' if x['status'] in ('missing', 'blocked', 'stale') else 'grey'))}</td>"
        f"<td>{_seconds(x['seconds'])}</td><td class=outcell>{detail(x)}</td></tr>"
        for x in d["tasks"])
    missing = "".join(f"<li><b>{escape(str(m.get('kind', '')))}</b> <span class=mono>{escape(str(m.get('scope', '')))}</span> "
                      f"{escape(str(m.get('detail', ''))[:400])}</li>" for m in d["missing"])
    llm = "".join(f"<tr><td>{escape(c['role'])}</td><td class=mono>{escape(c['model'] or '')}</td><td>{c['n']}</td>"
                  f"<td>{c['input_tokens'] or 0:,}</td><td>{c['output_tokens'] or 0:,}</td><td>{c['reasoning_tokens'] or 0:,}</td>"
                  f"<td>{money(c['cost']) if not c['unknown'] else (money(c['cost']) + '（部分未知）' if c['cost'] else '未知')}"
                  f"{' 估算' if 'estimate' in (c['sources'] or '') else ''}</td></tr>" for c in d["llm"])
    body = [head,
            f'<div class="card scroll"><h2 style="margin-top:0">任务</h2>'
            f'<p class="muted">成功的任务点开看产出：摘要在前，再点“查看完整内容”看原始字段。标“复用”的是沿用此前生成的产出。</p>'
            f'<table><tr><th>任务</th><th>尝试</th><th>状态</th>'
            f'<th>耗时</th><th>产出 / 说明</th></tr>{tasks}</table></div>']
    if missing:
        body.append(f'<div class="card"><h2 style="margin-top:0">缺失的必需输入</h2><ul>{missing}</ul></div>')
    body.append('<div class="card scroll"><h2 style="margin-top:0">模型调用</h2>' +
                (f'<table><tr><th>角色</th><th>模型</th><th>次数</th><th>输入 token</th><th>输出 token</th><th>推理 token</th>'
                 f'<th>费用</th></tr>{llm}</table>' if llm else
                 '<p class="muted">没有入账记录（入账上线前的运行，或本次运行没有调用模型）。</p>') + "</div>")
    finished = run.get("status") not in ("planned", "running")
    return layout(wid, "workflows", "".join(body), now=now, refresh=not finished)


# --------------------------------------------------------------------------- #
# lineage
# --------------------------------------------------------------------------- #
def lineage_list_page(cycles: list[dict], now: datetime) -> str:
    rows = "".join(f'<tr><td><a class="mono" href="/dashboard/lineage/{quote(c["cycle_id"])}">{escape(c["cycle_id"])}</a></td>'
                   f'<td>{pill(STATUS_CN.get(c["status"], c["status"]), "purple")}</td>'
                   f'<td>{escape((c.get("created_at") or "")[:19])}</td></tr>' for c in cycles)
    return layout("决策溯源", "lineage",
                  f'<h1>决策溯源</h1><div class="card"><table><tr><th>决策周期</th><th>状态</th><th>创建（UTC）</th></tr>'
                  f'{rows}</table></div>', now=now)


def _tree(nodes) -> str:
    if not nodes:
        return ""
    items = []
    for n in nodes:
        style = ' style="color:var(--bad)"' if n.unknown or "⚠️" in n.label else ""
        ref = f' <span class="muted mono">{escape(n.ref)}</span>' if n.ref else ""
        items.append(f"<li><span{style}>{escape(n.label)}</span>{ref}{_tree(n.children)}</li>")
    return f'<ul class="tree">{"".join(items)}</ul>'


def lineage_page(d: dict, now: datetime) -> str:
    c = d["cycle"]
    audit = d.get("audit") or {}
    unknown = audit.get("unknown") or []
    warn = (f'<p>{pill(f"❓ {len(unknown)} 个引用不在本轮快照中（未拦截）", "red")} '
            f'<span class="mono muted">{escape("、".join(u[:8] for u in unknown))}</span></p>') if unknown else ""
    body = [f'<h1 class="mono">{escape(c["cycle_id"])}</h1>',
            f'<p>{pill(STATUS_CN.get(c["status"], c["status"]), "purple")} 决策时刻 {d["decided_at"]:%Y-%m-%d %H:%M} UTC · '
            f'“距决策”以此为准；⚠️ = 超出该类研究的新鲜度窗口。口径与首席报告的「依据清单」相同。</p>{warn}',
            f'<div class="card"><p class="sub" style="white-space:pre-wrap">{escape(str(d["summary"])[:1200])}</p></div>',
            f'<div class="card"><h2 style="margin-top:0">决策 → 引用的分析师结论 → 数据</h2>'
            f'{_tree(d["decisions"]) or "<p class=muted>本轮没有订单。</p>"}</div>']
    if d["weighing"]:
        body.append(f'<div class="card"><h2 style="margin-top:0">证据权衡</h2>{_tree(d["weighing"])}</div>')
    return layout(c["cycle_id"], "lineage", "".join(body), now=now)


def runs_day_page(workflow_id: str, day, runs: list[dict], catalog: dict, now: datetime) -> str:
    n = catalog.get("workflows", {}).get(workflow_id)
    rows = []
    for r in runs:
        counts = " ".join(f"{STATUS_CN.get(k, k)} {v}" for k, v in sorted(r["task_counts"].items()))
        rows.append(f'<tr><td><a class="mono" href="/dashboard/workflows/run/{quote(r["run_id"])}">{escape(r["run_id"][-12:])}</a></td>'
                    f'<td>{pill(STATUS_CN.get(r["status"], r["status"]), _STATE_CLASS.get(r["status"], "grey"))}</td>'
                    f'<td>{escape((r.get("started_at") or "")[11:19])}</td><td>{escape((r.get("ended_at") or "")[11:19])}</td>'
                    f'<td class=mono>{escape(str(r["scope"].get("id", "")))}</td><td>{escape(counts)}</td>'
                    f'<td class=wrap>{escape(r["first_missing"])}</td></tr>')
    body = (f'<h1>{escape(workflow_id)} · {day}</h1><p>{label(n)}{note(n)} 共 {len(runs)} 次运行</p>'
            f'<div class="card scroll"><table><tr><th>运行</th><th>状态</th><th>开始（UTC）</th><th>结束（UTC）</th>'
            f'<th>作用域</th><th>任务结果</th><th>首个缺口</th></tr>{"".join(rows)}</table></div>')
    return layout(f"{workflow_id} {day}", "workflows", body, now=now)
