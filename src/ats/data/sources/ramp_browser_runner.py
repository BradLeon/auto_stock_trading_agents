"""Headless browser runner for the Ramp AI Index page (upgrade-ramp-ai-index-v2, design D2).

It opens the public page, switches to each registered chart with its visible buttons and clicks the visible
``Get the data`` control. Before any page script runs, ``navigator.clipboard.writeText`` is wrapped so the
exact text the button copies is captured. That is the official export, not a screenshot and not a guessed
URL. Each export is header-checked and written atomically to the governed inbox
(``<scope>.tsv`` plus ``<scope>.json`` with the export time and SHA-256), so the second dataset job of the
same probe reads the inbox instead of opening the browser again.

Playwright is an optional dependency (``pip install '.[ramp-browser]'`` + ``playwright install chromium``).
Without it every page scope fails as ``export_unreadable`` with reason ``browser_not_installed``; the FRED
part (``ramp_ai_index_fred``) does not depend on this module.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Callable

from .ramp_ai_index import PAGE_URL, SCOPES, RampBrowserAdapter, RampExportError, read_clipboard_tsv


# Visible controls clicked, in order, to show each chart. Text must match the page exactly.
CHART_CLICKS: dict[str, tuple[str, ...]] = {
    "adoption_overall_models": ("Adoption", "Overall + Models"),
    "adoption_sector": ("Adoption", "Sector"),
    "spend_per_employee_overall": ("AI spend per employee", "Overall"),
    "token_spend_share_by_maker": ("Token spend", "By model maker", "Shares"),
    "token_spend_share_by_model": ("Token spend", "By model", "Shares"),
}
EXPECTED_TITLE = "Ramp AI Index"
TOTAL_TIMEOUT_SECONDS = 300
CHART_TIMEOUT_SECONDS = 45
# The page keeps background requests open, so "network idle" never arrives; wait for a control instead.
CONTROL_WAIT_SECONDS = 15

CAPTURE_SCRIPT = """
(() => {
  window.__atsRampCopies = [];
  const record = (text) => { window.__atsRampCopies.push(String(text)); };
  const clip = navigator.clipboard;
  if (clip && clip.writeText) {
    const original = clip.writeText.bind(clip);
    clip.writeText = async (text) => { record(text); try { await original(text); } catch (e) {} };
  } else {
    Object.defineProperty(navigator, 'clipboard', {configurable: true,
      value: {writeText: async (text) => { record(text); }}});
  }
})();
"""


class RampRunnerError(RampExportError):
    """A runner failure with a reason code (browser_not_installed, navigation_blocked, control_missing,
    clipboard_not_captured, timeout)."""

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        super().__init__(f"export_unreadable:{reason}" + (f":{detail}" if detail else ""))


def playwright_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return True


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
        handle.write(data)
        temporary = Path(handle.name)
    os.replace(temporary, path)


class RampBrowserRunner:
    """Exports every registered page chart in one browser session, once per process."""

    def __init__(self, export_dir: str | Path, *, page_url: str = PAGE_URL,
                 expected_title: str = EXPECTED_TITLE,
                 total_timeout: float = TOTAL_TIMEOUT_SECONDS, chart_timeout: float = CHART_TIMEOUT_SECONDS,
                 clock: Callable[[], datetime] | None = None):
        self.export_dir = Path(export_dir)
        self.page_url = page_url
        self.expected_title = expected_title
        self.total_timeout = total_timeout
        self.chart_timeout = chart_timeout
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._results: dict[str, bytes] | None = None
        self._errors: dict[str, RampRunnerError] = {}

    def export(self, scope: str) -> bytes:
        if scope not in SCOPES:
            raise RampExportError(f"scope_not_registered:{scope}")
        if self._results is None:
            self._run()
        if scope in self._errors:
            raise self._errors[scope]
        return self._results[scope]

    def _run(self) -> None:
        self._results = {}
        if not playwright_available():
            error = RampRunnerError("browser_not_installed")
            self._errors = {scope: error for scope in SCOPES}
            return
        from playwright.sync_api import Error as PlaywrightError, sync_playwright

        deadline = time.monotonic() + self.total_timeout
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    context = browser.new_context()
                    context.add_init_script(CAPTURE_SCRIPT)
                    page = context.new_page()
                    response = page.goto(self.page_url, wait_until="domcontentloaded",
                                         timeout=self.chart_timeout * 1000)
                    if response is not None and response.status >= 400:
                        raise RampRunnerError("navigation_blocked", f"http_{response.status}")
                    if self.expected_title not in (page.title() or ""):
                        raise RampRunnerError("navigation_blocked", "unexpected_title")
                    self._visible_button(page, "Get the data", exact=False)
                    for scope in SCOPES:
                        if time.monotonic() > deadline:
                            self._errors[scope] = RampRunnerError("timeout", "total")
                            continue
                        try:
                            self._results[scope] = self._export_chart(page, scope)
                            self._persist(scope, self._results[scope])
                        except RampRunnerError as exc:
                            self._errors[scope] = exc
                            self._screenshot(page, scope)
                        except PlaywrightError as exc:
                            # One chart's click or wait failing must not cost the other charts.
                            reason = "timeout" if "timeout" in str(exc).casefold() else "control_missing"
                            self._errors[scope] = RampRunnerError(reason, type(exc).__name__)
                            self._screenshot(page, scope)
                finally:
                    browser.close()
        except RampRunnerError as exc:
            self._errors = {scope: exc for scope in SCOPES if scope not in self._results}
        except PlaywrightError as exc:
            reason = "timeout" if "timeout" in str(exc).casefold() else "navigation_blocked"
            error = RampRunnerError(reason, type(exc).__name__)
            self._errors.update({scope: error for scope in SCOPES if scope not in self._results})

    def _visible_button(self, page, label: str, *, exact: bool = True):
        """The first visible button with this label, waiting for the page to render it.

        Sub-controls (e.g. "By model maker", "Shares") only appear after the parent control is clicked, and
        some labels also exist as hidden duplicates, so a plain count() right after a click is not enough.
        """
        from playwright.sync_api import Error as PlaywrightError

        for locator in (page.get_by_role("button", name=label, exact=exact),
                        page.get_by_text(label, exact=exact)):
            visible = locator.locator("visible=true")
            try:
                visible.first.wait_for(state="visible", timeout=CONTROL_WAIT_SECONDS * 1000)
            except PlaywrightError:
                continue
            return visible.first
        raise RampRunnerError("control_missing", label)

    def _click_visible(self, page, label: str) -> None:
        self._visible_button(page, label).click(timeout=self.chart_timeout * 1000)
        page.wait_for_timeout(800)

    def _export_chart(self, page, scope: str) -> bytes:
        for label in CHART_CLICKS[scope]:
            self._click_visible(page, label)
        page.wait_for_timeout(1500)
        before = page.evaluate("window.__atsRampCopies.length")
        self._visible_button(page, "Get the data", exact=False).click(timeout=self.chart_timeout * 1000)
        waited = 0.0
        while page.evaluate("window.__atsRampCopies.length") <= before:
            if waited >= self.chart_timeout:
                raise RampRunnerError("clipboard_not_captured", scope)
            page.wait_for_timeout(250)
            waited += 0.25
        text = page.evaluate("window.__atsRampCopies[window.__atsRampCopies.length - 1]")
        try:
            raw, _ = read_clipboard_tsv(text, required_headers=("Date",))
        except RampExportError as exc:
            raise RampRunnerError("clipboard_not_captured", str(exc)) from exc
        return raw

    def _persist(self, scope: str, payload: bytes) -> None:
        exported_at = self.clock().astimezone(timezone.utc).isoformat()
        _write_atomic(self.export_dir / f"{scope}.tsv", payload)
        _write_atomic(self.export_dir / f"{scope}.json", json.dumps({
            "scope": scope, "exported_at": exported_at, "page_url": self.page_url,
            "controls": list(CHART_CLICKS[scope]) + ["Get the data"],
            "payload_sha256": hashlib.sha256(payload).hexdigest(),
            "export_method": "official_clipboard"}, ensure_ascii=False, indent=2).encode("utf-8"))

    def _screenshot(self, page, scope: str) -> None:
        """For human diagnosis only; a screenshot never becomes data."""
        try:
            target = self.export_dir / "diagnostics" / f"{scope}-{self.clock():%Y%m%dT%H%M%S}.png"
            target.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(target), full_page=False)
        except Exception:  # noqa: BLE001 - diagnostics must not mask the original failure
            pass


def browser_adapter_or_none(export_dir: str | Path) -> RampBrowserAdapter:
    """The adapter the registry injects. Without Playwright it still exists and fails every scope explicitly."""
    return RampBrowserAdapter(exporter=RampBrowserRunner(export_dir).export)
