"""`/dashboard` routes on the `ats serve` app. GET only: the dashboard never changes system state.

Handlers are plain `def`, so FastAPI runs them in its thread pool and a slow query never blocks the approval
callbacks served by the same process.

Local only (user decision 2026-11-19, no login): `ats serve` also sits behind the cloudflared quick tunnel that
carries Feishu callbacks (scripts/start_tunnel.sh forwards the whole port), so every dashboard route refuses a
request that came through a proxy (Cloudflare / forwarding headers) or names a non-local Host.
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import pages, queries


_LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
_PROXY_HEADERS = ("cf-ray", "cf-connecting-ip", "cdn-loop", "x-forwarded-for", "x-forwarded-host", "forwarded")


def is_local_request(headers, client_host: str | None) -> bool:
    """True only for a browser on this machine talking to the server directly."""
    if any(h in headers for h in _PROXY_HEADERS):
        return False
    host = (headers.get("host") or "").rsplit(":", 1)[0] if not (headers.get("host") or "").startswith("[") \
        else (headers.get("host") or "").split("]")[0] + "]"
    return host in _LOCAL_HOSTS and client_host in ("127.0.0.1", "::1")


def mount(app) -> None:
    from fastapi import HTTPException, Request
    from fastapi.responses import HTMLResponse, PlainTextResponse

    @app.middleware("http")
    async def dashboard_local_only(request: Request, call_next):
        if request.url.path.startswith("/dashboard") and not is_local_request(
                request.headers, request.client.host if request.client else None):
            return PlainTextResponse("dashboard is local only", status_code=403)
        return await call_next(request)

    def paths():
        return queries.DashboardPaths.from_env()

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard_overview():
        return pages.overview_page(queries.overview(paths()), queries.load_catalog())

    @app.get("/dashboard/sources", response_class=HTMLResponse)
    def dashboard_sources(only: str = "", domain: str = ""):
        now = datetime.now(timezone.utc)
        groups = queries.source_groups(queries.source_rows(paths(), now))
        return pages.sources_page(groups, queries.load_catalog(), now, only=only, domain=domain)

    @app.get("/dashboard/sources/{job_id}", response_class=HTMLResponse)
    def dashboard_source(job_id: str):
        now = datetime.now(timezone.utc)
        p = paths()
        row = next((r for r in queries.source_rows(p, now) if r.job_id == job_id), None)
        runs = queries.source_attempts(p, job_id)
        if row is None and not runs:
            raise HTTPException(404, f"unknown job {job_id}")
        return pages.source_detail_page(job_id, row, runs, queries.load_catalog(), now)

    @app.get("/dashboard/workflows", response_class=HTMLResponse)
    def dashboard_workflows():
        now = datetime.now(timezone.utc)
        p = paths()
        days, rows = queries.workflow_grid(p, now)
        return pages.workflows_page(days, rows, queries.load_catalog(), queries.llm_daily(p, len(days), now), now)

    @app.get("/dashboard/workflows/run/{run_id}", response_class=HTMLResponse)
    def dashboard_run(run_id: str):
        detail = queries.run_detail(paths(), run_id)
        if detail is None:
            raise HTTPException(404, f"unknown run {run_id}")
        return pages.run_page(detail, queries.load_catalog(), datetime.now(timezone.utc))

    # Registered after /run/{run_id}: FastAPI matches in order, and "run" is not a workflow.
    @app.get("/dashboard/workflows/{workflow_id}/{day}", response_class=HTMLResponse)
    def dashboard_runs_day(workflow_id: str, day: str):
        from datetime import date

        try:
            when = date.fromisoformat(day)
        except ValueError:
            raise HTTPException(404, f"bad day {day}") from None
        now = datetime.now(timezone.utc)
        runs = queries.runs_on_day(paths(), workflow_id, when, now)
        return pages.runs_day_page(workflow_id, when, runs, queries.load_catalog(), now)

    @app.get("/dashboard/lineage", response_class=HTMLResponse)
    def dashboard_lineage_list():
        return pages.lineage_list_page(queries.decision_cycles(paths()), datetime.now(timezone.utc))

    @app.get("/dashboard/lineage/{cycle_id}", response_class=HTMLResponse)
    def dashboard_lineage(cycle_id: str):
        detail = queries.lineage(paths(), cycle_id)
        if detail is None:
            raise HTTPException(404, f"unknown cycle {cycle_id}")
        return pages.lineage_page(detail, datetime.now(timezone.utc))
