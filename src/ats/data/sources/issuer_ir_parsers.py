"""Deterministic parsers for issuer IR statement files (no model requests).

Each parser has two layers so it can be unit-tested without the binary file: a thin
reader (pdfplumber / xlrd -> text pages or cell rows) and a pure ``parse_*`` function over
that text.  A parser either returns a complete ``IRDocument`` or raises ``ParseError``;
it never returns a half-read statement.
"""

from __future__ import annotations

from datetime import date, datetime
import io
from pathlib import Path
import re

from .issuer_ir_files import (
    CheckResult,
    IRDocument,
    IRValue,
    Located,
    ParseError,
    quarter_end,
)

# --------------------------------------------------------------------------- #
# Shared text-table helpers
# --------------------------------------------------------------------------- #
_NUMBER = re.compile(r"^\(?-?[\d,]+(?:\.\d+)?\)?$")
_FOOTNOTE = r"(?:\s*\(\d\))"
_MONTHS = ("January February March April May June July August September October "
           "November December").split()


def _to_number(token: str) -> float | None:
    """'(1,234)' -> -1234, '-'/'NM' -> None; anything else is not a table number."""
    if token in {"-", "–", "NM"}:
        return None
    if not _NUMBER.match(token):
        raise ValueError(token)
    negative = token.startswith("(") or token.startswith("-")
    value = float(token.strip("()-").replace(",", ""))
    return -value if negative else value


def match_row(line: str, label: str, *, footnote: bool = False) -> list[float | None] | None:
    """Numbers after ``label`` when the line is exactly ``label`` + numeric cells.

    A "(2)"-style footnote marker after the label is skipped only when ``footnote`` is set:
    otherwise "(6)" would be indistinguishable from the negative number -6.
    """
    marker = _FOOTNOTE + "?" if footnote else ""
    found = re.match(rf"^{label}(?:\d)?{marker}\s+(.*)$", line.strip())
    if not found:
        return None
    cells = found.group(1).replace("$", " ").replace("€", " ").split()
    if not cells:
        return None
    try:
        return [_to_number(cell) for cell in cells]
    except ValueError:
        return None


def find_row(lines: list[str], label: str, *, after: str | None = None,
             before: str | None = None, footnote: bool = False) -> tuple[int, list[float | None]] | None:
    """First line matching ``label``, optionally only between two anchor lines."""
    start, stop = 0, len(lines)
    if after:
        anchors = [i for i, line in enumerate(lines) if re.match(after, line.strip())]
        if not anchors:
            return None
        start = anchors[0] + 1
    if before:
        stops = [i for i, line in enumerate(lines) if i >= start and re.match(before, line.strip())]
        stop = stops[0] if stops else len(lines)
    for index in range(start, stop):
        numbers = match_row(lines[index], label, footnote=footnote)
        if numbers is not None:
            return index, numbers
    return None


class _Page:
    """One statement page: its lines, number and a row accessor with provenance."""

    def __init__(self, number: int, text: str, title: str):
        self.number = number
        self.lines = [line.strip() for line in text.splitlines() if line.strip()]
        self.title = title

    def row(self, label: str, *, expect: int, indexes: tuple[int, ...], after: str | None = None,
            before: str | None = None, required: bool = True, footnote: bool = False) -> list[Located] | None:
        hit = find_row(self.lines, label, after=after, before=before, footnote=footnote)
        if hit is None:
            if required:
                raise ParseError(f"{self.title} (page {self.number}): row {label!r} not found")
            return None
        index, numbers = hit
        if len(numbers) != expect:
            raise ParseError(
                f"{self.title} (page {self.number}): row {label!r} has {len(numbers)} cells, "
                f"expected {expect} -- the table layout changed")
        cleaned = [n for n in (numbers[i] for i in indexes)]
        if any(n is None for n in cleaned):
            raise ParseError(f"{self.title} (page {self.number}): row {label!r} has an empty cell")
        text = re.sub(r"\s+[\$€(\d-].*$", "", self.lines[index])   # the row's own label as printed
        where = f"p{self.number} '{text}'"
        return [Located(float(n), where, text) for n in cleaned]


def read_pdf_pages(payload: bytes) -> list[str]:
    try:
        import pdfplumber
    except ImportError as exc:  # optional "data" extra
        raise ParseError("pdfplumber is not installed (uv sync --extra data)") from exc
    try:
        with pdfplumber.open(io.BytesIO(payload)) as pdf:
            return [page.extract_text() or "" for page in pdf.pages]
    except Exception as exc:
        raise ParseError(f"not a readable PDF: {exc}") from exc


def _date_from_label(text: str) -> date:
    return datetime.strptime(text.replace(",", ""), "%B %d %Y").date()


def _quarter_label(quarter: str, year: str) -> str:
    return quarter_end(int(year), int(quarter)).isoformat()


def _page_with(pages: list[str], *needles: str, none_of: tuple[str, ...] = ()) -> _Page:
    for number, text in enumerate(pages, start=1):
        if all(n in text for n in needles) and not any(n in text for n in none_of):
            return _Page(number, text, needles[0])
    raise ParseError(f"no page contains {' + '.join(needles)!r}")


def _emit(values: list[IRValue], field: str, periods: list[str], cells: list[Located], *,
          sign: str = "as_is", basis: str = "") -> None:
    for period, cell in zip(periods, cells):
        number = abs(cell.value) if sign == "abs" else cell.value
        values.append(IRValue(field=field, period_end=period, value=number,
                              location=cell.location, label=cell.label, basis=basis))


def _put_lines(lines: dict[str, dict[str, Located]], key: str, periods: list[str],
               cells: list[Located], *, sign: str = "as_is") -> None:
    for period, cell in zip(periods, cells):
        number = abs(cell.value) if sign == "abs" else cell.value
        lines.setdefault(period, {})[key] = Located(number, cell.location, cell.label)


# --------------------------------------------------------------------------- #
# TSMC: "Consolidated Condensed ... (Unaudited)" financial statements PDF (FS.pdf)
# --------------------------------------------------------------------------- #
def parse_tsmc_fs_pages(pages: list[str]) -> IRDocument:
    """TSMC quarterly FS.pdf: balance sheet, quarterly income statement, cash flow.

    The same file carries a quarter table and a nine-months table.  Values are read only
    from the page whose title says "Three Months Ended" (income statement) / the quarter
    columns of the cash-flow page, and the rows that exist twice (Property, Plant and
    Equipment) are scoped by the section anchor above them.
    """
    balance = _page_with(pages, "Consolidated Condensed Balance Sheet", "Total Assets")
    income = _page_with(pages, "Statements of Comprehensive Income", "Three Months Ended",
                        none_of=("For the Nine Months Ended September",))
    cash = _page_with(pages, "Cash Flow Statements", "Net Cash Generated by Operating Activities")

    header = "\n".join(pages[income.number - 1].splitlines()[:12]) + "\n" + "\n".join(
        pages[balance.number - 1].splitlines()[:12])
    if "New Taiwan Dollars" not in header:
        raise ParseError("TSMC FS: statements are not expressed in New Taiwan Dollars")
    currency = "TWD"
    if not re.search(r"Expressed in Millions", header):
        raise ParseError("TSMC FS: table unit is not millions")

    quarter_header = next((re.findall(r"([1-4])Q (\d{4})", line) for line in income.lines
                           if re.match(r"^[1-4]Q \d{4} [1-4]Q \d{4}", line)), None)
    if not quarter_header or len(quarter_header) < 3:
        raise ParseError("TSMC FS: income statement quarter header not found")
    periods = [_quarter_label(q, y) for q, y in quarter_header[:3]]           # current, prior quarter, year ago

    bs_dates = next(([_date_from_label(m) for m in re.findall(r"([A-Z][a-z]+ \d{1,2}, \d{4})", line)]
                     for line in balance.lines if re.match(r"^[A-Z][a-z]+ \d{1,2}, \d{4} [A-Z][a-z]+ \d{1,2}, \d{4}", line)),
                    None)
    if not bs_dates or [d.isoformat() for d in bs_dates[:3]] != periods:
        raise ParseError(f"TSMC FS: balance sheet dates {bs_dates} do not match income statement quarters {periods}")
    cash_header = next((re.findall(r"([1-4])Q (\d{4})", line) for line in cash.lines
                        if line.startswith("Nine Months")), None)
    if not cash_header or [_quarter_label(q, y) for q, y in cash_header] != periods:
        raise ParseError("TSMC FS: cash flow quarter columns do not match the income statement")

    values: list[IRValue] = []
    lines: dict[str, dict[str, Located]] = {}

    def usd_ntd_consistent(cells: list[float | None], label: str) -> None:
        # USD column x translation rate ~ NTD column: catches a shifted column read
        if cells[0] and cells[1] and not 25 < cells[1] / cells[0] < 40:
            raise ParseError(f"TSMC FS: {label} USD/NTD columns are inconsistent: {cells[:2]}")

    def bs(label: str) -> list[Located]:
        hit = find_row(balance.lines, label)
        if hit is None:
            raise ParseError(f"TSMC FS balance sheet: row {label!r} not found")
        _, numbers = hit
        if len(numbers) != 11:
            raise ParseError(f"TSMC FS balance sheet: row {label!r} has {len(numbers)} cells, expected 11")
        usd_ntd_consistent(numbers, label)
        return [Located(float(numbers[i]), f"p{balance.number} '{label}'", label) for i in (1, 3, 5)]

    def inc(label: str) -> list[Located]:
        hit = find_row(income.lines, label)
        if hit is None:
            raise ParseError(f"TSMC FS income statement: row {label!r} not found")
        _, numbers = hit
        if len(numbers) != 11:
            raise ParseError(f"TSMC FS income statement: row {label!r} has {len(numbers)} cells, expected 11")
        usd_ntd_consistent(numbers, label)
        return [Located(float(numbers[i]), f"p{income.number} '{label}'", label) for i in (1, 3, 5)]

    def eps(label: str) -> list[Located]:
        return income.row(label, expect=8, indexes=(1, 2, 3), footnote=True)  # type: ignore[return-value]

    def flow(label: str, **kwargs) -> list[Located]:
        return cash.row(label, expect=5, indexes=(2, 3, 4), **kwargs)  # type: ignore[return-value]

    revenue = inc("Net Revenue")
    cost = inc("Cost of Revenue")
    gross = inc("Gross Profit")
    operating = inc("Income from Operations")
    ni_total = inc("Net Income")
    ni_parent = inc("Shareholders of the Parent")
    ni_nci = inc("Noncontrolling Interests")
    eps_ordinary = eps("Earnings per Share - Diluted")
    eps_adr = eps("Earnings per ADR - Diluted")

    cfo = flow("Net Cash Generated by Operating Activities")
    cfi = flow(r"Net Cash (?:Used In|Generated by|Provided by) Investing Activities")
    cff = flow(r"Net Cash (?:Used In|Generated by|Provided by) Financing Activities")
    fx = flow("Effect of Exchange Rate Changes on Cash and Cash Equivalents")
    net_change = flow(r"Net Increase(?: \(Decrease\))? in Cash and Cash Equivalents")
    cash_begin = flow("Cash and Cash Equivalents at Beginning of Period")
    cash_end = flow("Cash and Cash Equivalents at End of Period")
    capex = flow("Property, Plant and Equipment", after=r"^Acquisitions of:", before=r"^Proceeds from")

    cash_bs = bs("Cash and Cash Equivalents")
    inventory = bs("Inventories")
    assets = bs("Total Assets")
    liabilities = bs("Total Liabilities")
    equity = bs("Total Shareholders' Equity")
    current_debt = bs("Current Portion of Bonds Payable and Bank Loans")
    bonds = bs("Bonds Payable")

    _emit(values, "revenue", periods, revenue)
    _emit(values, "gross_profit", periods, gross)
    _emit(values, "operating_income", periods, operating)
    _emit(values, "net_income", periods, ni_parent)
    _emit(values, "eps_diluted", periods, eps_ordinary)
    _emit(values, "eps_diluted_adr", periods, eps_adr)
    _emit(values, "cash_from_operations", periods, cfo)
    _emit(values, "capex", periods, capex, sign="abs")
    _emit(values, "cash_and_equivalents", periods, cash_bs, basis="instant")
    _emit(values, "inventory", periods, inventory, basis="instant")
    _emit(values, "total_assets", periods, assets, basis="instant")
    _emit(values, "total_liabilities", periods, liabilities, basis="instant")
    _emit(values, "stockholders_equity", periods, equity, basis="instant")
    for period, left, right in zip(periods, current_debt, bonds):
        values.append(IRValue(
            field="total_debt", period_end=period, value=left.value + right.value,
            location=f"{left.location} + {right.location}", label="Total debt (condensed statement)",
            basis="instant",
            derived={"calculation": "current_portion_of_bonds_payable_and_bank_loans + bonds_payable",
                     "left": {"label": left.label, "value": left.value, "location": left.location},
                     "right": {"label": right.label, "value": right.value, "location": right.location}},
            notes={"debt_definition": "current portion of bonds payable and bank loans + non-current bonds "
                                      "payable, as shown in the condensed balance sheet"}))

    _put_lines(lines, "revenue", periods, revenue)
    _put_lines(lines, "cost_of_revenue", periods, cost, sign="abs")
    _put_lines(lines, "gross_profit", periods, gross)
    _put_lines(lines, "net_income_total", periods, ni_total)
    _put_lines(lines, "net_income_parent", periods, ni_parent)
    _put_lines(lines, "net_income_nci", periods, ni_nci)
    _put_lines(lines, "cfo", periods, cfo)
    _put_lines(lines, "cfi", periods, cfi)
    _put_lines(lines, "cff", periods, cff)
    _put_lines(lines, "fx", periods, fx)
    _put_lines(lines, "net_change", periods, net_change)
    _put_lines(lines, "cash_begin", periods, cash_begin)
    _put_lines(lines, "cash_end", periods, cash_end)
    _put_lines(lines, "capex", periods, capex, sign="abs")
    _put_lines(lines, "cash_balance_sheet", periods, cash_bs)
    _put_lines(lines, "inventory", periods, inventory)
    _put_lines(lines, "total_assets", periods, assets)
    _put_lines(lines, "total_liabilities", periods, liabilities)
    _put_lines(lines, "equity_total", periods, equity)

    return IRDocument(
        entity="TSM", parser="tsmc_fs_pdf", currency=currency, table_scale=1e6,
        scale_label="NT$ million (EPS in NT$)", values=values, lines=lines,
        periods=sorted(periods), current_period=periods[0],
        identity_text="\n".join(pages[:2]), meta={"pages": len(pages)})


def parse_tsmc_fs_pdf(path: Path, payload: bytes) -> IRDocument:
    return parse_tsmc_fs_pages(read_pdf_pages(payload))


# --------------------------------------------------------------------------- #
# ASML: quarterly results presentation (US GAAP statements, five quarters)
# --------------------------------------------------------------------------- #
def parse_asml_presentation_pages(pages: list[str]) -> IRDocument:
    operations = _page_with(pages, "Consolidated statements of operations", "Quarter on quarter")
    flows = _page_with(pages, "Consolidated statements of cash flows", "Quarter on quarter")
    balance = _page_with(pages, "Consolidated balance sheets", "Quarter end")

    def header(page: _Page) -> list[str]:
        for line in page.lines:
            quarters = re.findall(r"Q([1-4]) (\d{4})", line)
            if line.startswith("(in millions") and len(quarters) >= 3:
                if "€" not in line:
                    raise ParseError(f"ASML {page.title}: statements are not in euros: {line!r}")
                return [_quarter_label(q, y) for q, y in quarters]
        raise ParseError(f"ASML {page.title} (page {page.number}): column header not found")

    periods = header(operations)
    if header(flows) != periods or header(balance) != periods:
        raise ParseError("ASML: the three statements do not share the same quarter columns")
    count = len(periods)
    if len(set(periods)) != count or periods != sorted(periods):
        raise ParseError(f"ASML: unexpected quarter columns {periods}")

    def row(page: _Page, label: str, **kwargs) -> list[Located]:
        return page.row(label, expect=count, indexes=tuple(range(count)), **kwargs)  # type: ignore[return-value]

    revenue = row(operations, "Total net sales")
    gross = row(operations, "Gross profit")
    operating = row(operations, "Income from operations")
    net_income = row(operations, "Net income")
    eps_basic = row(operations, "Basic net income per ordinary share")
    eps_diluted = row(operations, "Diluted net income per ordinary share")

    cash_begin = row(flows, "Cash and cash equivalents, beginning of period")
    cfo = row(flows, r"Net cash provided by \(used in\) operating activities")
    cfi = row(flows, r"Net cash provided by \(used in\) investing activities")
    cff = row(flows, r"Net cash provided by \(used in\) financing activities")
    fx = row(flows, "Effect of changes in exchange rates on cash")
    net_change = row(flows, r"Net increase \(decrease\) in cash and cash equivalents")
    cash_end = row(flows, "Cash and cash equivalents, end of period")
    sti = row(flows, "Short-term investments")
    cash_and_sti = row(flows, "Cash and cash equivalents and short-term investments")
    capex = row(flows, "Purchases of property, plant and equipment and intangible assets")
    fcf = row(flows, "Free cash flow")

    bs_cash_sti = row(balance, "Cash & cash equivalents and short-term investments")
    inventory = row(balance, "Inventories, net")
    assets = row(balance, "Total assets")
    current_liabilities = row(balance, "Current liabilities")
    noncurrent_liabilities = row(balance, "Non-current liabilities")
    equity = row(balance, "Shareholders' equity")

    values: list[IRValue] = []
    lines: dict[str, dict[str, Located]] = {}
    _emit(values, "revenue", periods, revenue)
    _emit(values, "gross_profit", periods, gross)
    _emit(values, "operating_income", periods, operating)
    _emit(values, "net_income", periods, net_income)
    _emit(values, "eps_basic", periods, eps_basic)
    _emit(values, "eps_diluted", periods, eps_diluted)
    _emit(values, "cash_from_operations", periods, cfo)
    for period, cell in zip(periods, capex):
        values.append(IRValue(
            field="capex", period_end=period, value=abs(cell.value), location=cell.location, label=cell.label,
            notes={"capex_scope": "purchases of property, plant and equipment AND intangible assets "
                                  "(the presentation reports them as one line)"}))
    _emit(values, "cash_and_equivalents", periods, cash_end, basis="instant")
    _emit(values, "inventory", periods, inventory, basis="instant")
    _emit(values, "total_assets", periods, assets, basis="instant")
    _emit(values, "stockholders_equity", periods, equity, basis="instant")
    for period, left, right in zip(periods, current_liabilities, noncurrent_liabilities):
        values.append(IRValue(
            field="total_liabilities", period_end=period, value=left.value + right.value,
            location=f"{left.location} + {right.location}", label="Total liabilities", basis="instant",
            derived={"calculation": "current_liabilities + non_current_liabilities",
                     "left": {"label": left.label, "value": left.value, "location": left.location},
                     "right": {"label": right.label, "value": right.value, "location": right.location}}))

    _put_lines(lines, "revenue", periods, revenue)
    _put_lines(lines, "gross_profit", periods, gross)
    _put_lines(lines, "cash_begin", periods, cash_begin)
    _put_lines(lines, "cfo", periods, cfo)
    _put_lines(lines, "cfi", periods, cfi)
    _put_lines(lines, "cff", periods, cff)
    _put_lines(lines, "fx", periods, fx)
    _put_lines(lines, "net_change", periods, net_change)
    _put_lines(lines, "cash_end", periods, cash_end)
    _put_lines(lines, "cash_balance_sheet", periods, cash_end)
    _put_lines(lines, "short_term_investments", periods, sti)
    _put_lines(lines, "cash_and_short_term_investments", periods, cash_and_sti)
    _put_lines(lines, "cash", periods, cash_end)
    _put_lines(lines, "capex", periods, capex, sign="abs")
    _put_lines(lines, "free_cash_flow", periods, fcf)
    _put_lines(lines, "inventory", periods, inventory)
    _put_lines(lines, "total_assets", periods, assets)
    _put_lines(lines, "equity_total", periods, equity)
    _put_lines(lines, "current_liabilities", periods, current_liabilities)
    for period, left, right in zip(periods, current_liabilities, noncurrent_liabilities):
        lines[period]["total_liabilities"] = Located(left.value + right.value, f"{left.location} + {right.location}")

    extra = []
    for period, one, two in zip(periods, bs_cash_sti, cash_and_sti):
        extra.append(CheckResult("balance_sheet_cash_matches_cash_flow_statement", period,
                                 abs(one.value - two.value) <= 3, "hard",
                                 "" if abs(one.value - two.value) <= 3 else
                                 f"balance sheet {one.value:,.0f} vs cash flow statement {two.value:,.0f}"))

    published = None
    found = re.search(r"(" + "|".join(_MONTHS) + r") (\d{1,2}), (\d{4})", pages[0])
    if found:
        published = _date_from_label(f"{found.group(1)} {found.group(2)}, {found.group(3)}")
    doc = IRDocument(
        entity="ASML", parser="asml_presentation_pdf", currency="EUR", table_scale=1e6,
        scale_label="EUR million (per-share in EUR)", values=values, lines=lines,
        periods=sorted(periods), current_period=periods[-1],
        identity_text="\n".join(pages[:3]), published_on=published, meta={"pages": len(pages)})
    doc.meta["extra_checks"] = extra
    return doc


def parse_asml_presentation_pdf(path: Path, payload: bytes) -> IRDocument:
    return parse_asml_presentation_pages(read_pdf_pages(payload))


# --------------------------------------------------------------------------- #
# ASML: EX-99.3 "Financial Statements US GAAP" of the quarterly-results 6-K (slide HTML)
# --------------------------------------------------------------------------- #
_SLIDE = re.compile(r'<IMG[^>]*title="slide(\d+)"[^>]*>\s*<DIV>\s*<FONT[^>]*>(.*?)</FONT>\s*</DIV>', re.S | re.I)
_HEADER_DATES = re.compile(r"((?:[A-Z][a-z]{2} \d{1,2},\s*){5})\|?\s*\(Unaudited[^)]*\)\s*((?:\d{4}\s+){4}\d{4})")
_HEADER_PREFIX = re.compile(r"^\(Unaudited[^)]*\)\s*(?:\d{4}(?:\s+|$))+")
_CELL_NUMBER = re.compile(r"^(?:\(?-?[\d,]+(?:\.\d+)?\)?|—|–)$")


def _slide_rows(text: str) -> list[tuple[str, list[float | None]]]:
    """(label, numbers) rows from one slide's hidden text layer: cells are separated by 2+ spaces."""
    import html as _html
    cleaned = _html.unescape(text).replace("\xa0", " ").replace("’", "'")
    cells = [cell.strip() for cell in re.split(r"\s{2,}", cleaned) if cell.strip()]
    rows: list[tuple[str, list[float | None]]] = []
    label: list[str] = []
    numbers: list[float | None] = []
    for cell in cells:
        cell = _HEADER_PREFIX.sub("", cell).strip()
        cell = re.sub(r"^Three months ended (?:[A-Z][a-z]{2} \d{1,2},\s*)+", "", cell).strip()
        if not cell:
            continue
        if _CELL_NUMBER.match(cell):
            numbers.append(None if cell in {"—", "–"} else _to_number(cell))
            continue
        if numbers:
            rows.append((" ".join(label), numbers))
            label, numbers = [], []
        label.append(cell)
    if numbers:
        rows.append((" ".join(label), numbers))
    return rows


def parse_asml_ex99_3_html(path: Path, payload: bytes) -> IRDocument:
    """ASML 6-K exhibit 99.3: five quarters of the US GAAP statements, in EUR million with one decimal.

    Each slide is an image with the statement text in a hidden text layer.  The three quarterly
    slides are told apart by their five-date column header; the three-month/nine-month slides
    (two dates / "Nine months ended") are never read.
    """
    text = payload.decode("utf-8", errors="replace")
    if "EX-99.3" not in text[:600] and "Financial Statements US GAAP" not in text[:6000]:
        raise ParseError("not ASML's EX-99.3 financial statements exhibit")
    slides = {int(number): body for number, body in _SLIDE.findall(text)}
    if not slides:
        raise ParseError("ASML EX-99.3: no slide text layer found")

    def quarterly(needle: str) -> tuple[int, list[str], list[date], list[tuple[str, list[float | None]]], str]:
        for number in sorted(slides):
            import html as _html
            plain = re.sub(r"\s+", " ", _html.unescape(slides[number]).replace("\xa0", " "))
            header = _HEADER_DATES.search(plain)
            if header and needle in plain and "Nine months ended" not in plain[:400]:
                days = re.findall(r"([A-Z][a-z]{2}) (\d{1,2}),", header.group(1))
                years = header.group(2).split()
                fiscal = [datetime.strptime(f"{m} {d} {y}", "%b %d %Y").date() for (m, d), y in zip(days, years)]
                # ASML's 4-4-5 quarters end near, not on, the calendar quarter end (Sep 27, 2026):
                # the series keeps calendar quarter-ends like every other source
                ends = [quarter_end(day.year, (day.month - 1) // 3 + 1).isoformat() for day in fiscal]
                return number, ends, fiscal, _slide_rows(slides[number]), plain
        raise ParseError(f"ASML EX-99.3: no five-quarter slide containing {needle!r}")

    income_no, periods, fiscal, income_rows, income_text = quarterly("Total net sales")
    balance_no, balance_periods, _, balance_rows, balance_text = quarterly("Total non-current assets")
    cash_no, cash_periods, _, cash_rows, cash_text = quarterly("Cash flows from operating activities")
    if not (periods == balance_periods == cash_periods) or len(set(periods)) != 5 or periods != sorted(periods):
        raise ParseError(f"ASML EX-99.3: statements disagree on the quarter columns {periods}")
    if "€" not in income_text:
        raise ParseError("ASML EX-99.3: statements are not in euros")
    count = len(periods)

    def pick(rows, number: int, label: str, *, exact: bool = True) -> list[Located]:
        pattern = re.compile(rf"(?:^|\s){label}$" if exact else label)
        hits = [(name, values) for name, values in rows if pattern.search(name)]
        if len(hits) != 1:
            raise ParseError(f"ASML EX-99.3 slide {number}: {label!r} matched {len(hits)} rows, expected 1")
        name, values = hits[0]
        if len(values) != count or any(v is None for v in values):
            raise ParseError(f"ASML EX-99.3 slide {number}: row {name!r} has {len(values)} cells, expected {count}")
        shown = label.replace("\\", "")
        return [Located(float(v), f"slide {number} '{shown}'", shown) for v in values]

    revenue = pick(income_rows, income_no, "Total net sales")
    cost = pick(income_rows, income_no, r"Total cost of sales")
    gross = pick(income_rows, income_no, "Gross profit")
    operating = pick(income_rows, income_no, "Income from operations")
    net_income = pick(income_rows, income_no, "Net income")
    eps_basic = pick(income_rows, income_no, "Basic net income per ordinary share")
    eps_diluted = pick(income_rows, income_no, "Diluted net income per ordinary share")

    cash = pick(balance_rows, balance_no, "Cash and cash equivalents")
    sti = pick(balance_rows, balance_no, "Short-term investments")
    inventory = pick(balance_rows, balance_no, "Inventories, net")
    current_assets = pick(balance_rows, balance_no, "Total current assets")
    noncurrent_assets = pick(balance_rows, balance_no, "Total non-current assets")
    assets = pick(balance_rows, balance_no, "Total assets")
    current_liabilities = pick(balance_rows, balance_no, "Total current liabilities")
    long_term_debt = pick(balance_rows, balance_no, "Long-term debt")
    noncurrent_liabilities = pick(balance_rows, balance_no, "Total non-current liabilities")
    liabilities = pick(balance_rows, balance_no, "Total liabilities")
    equity = pick(balance_rows, balance_no, "Total shareholders' equity")

    net_income_cf = pick(cash_rows, cash_no, "Net income")
    cfo = pick(cash_rows, cash_no, r"Net cash provided by \(used in\) operating activities")
    ppe = pick(cash_rows, cash_no, "Purchase of property, plant and equipment")
    intangibles = pick(cash_rows, cash_no, "Purchase of intangible assets")
    cfi = pick(cash_rows, cash_no, r"Net cash provided by \(used in\) investing activities")
    cff = pick(cash_rows, cash_no, r"Net cash provided by \(used in\) financing activities")
    fx = pick(cash_rows, cash_no, "Effect of changes in exchange rates on cash")
    net_change = pick(cash_rows, cash_no, r"Net increase \(decrease\) in cash and cash equivalents")
    cash_begin = pick(cash_rows, cash_no, "Cash and cash equivalents at beginning of the period")
    cash_end = pick(cash_rows, cash_no, "Cash and cash equivalents at end of the period")

    values: list[IRValue] = []
    lines: dict[str, dict[str, Located]] = {}
    _emit(values, "revenue", periods, revenue)
    _emit(values, "gross_profit", periods, gross)
    _emit(values, "operating_income", periods, operating)
    _emit(values, "net_income", periods, net_income)
    _emit(values, "eps_basic", periods, eps_basic)
    _emit(values, "eps_diluted", periods, eps_diluted)
    _emit(values, "cash_from_operations", periods, cfo)
    _emit(values, "cash_and_equivalents", periods, cash, basis="instant")
    _emit(values, "inventory", periods, inventory, basis="instant")
    _emit(values, "total_assets", periods, assets, basis="instant")
    _emit(values, "total_liabilities", periods, liabilities, basis="instant")
    _emit(values, "stockholders_equity", periods, equity, basis="instant")
    _emit(values, "long_term_debt", periods, long_term_debt, basis="instant")
    debt_note = ("long-term debt on the summary balance sheet (non-current). The summary statements do not "
                 "break out the current portion of long-term debt, which sits inside current liabilities, so "
                 "this understates total borrowings in quarters where a bond is due within a year")
    for period, cell in zip(periods, long_term_debt):
        values.append(IRValue(
            field="total_debt", period_end=period, value=cell.value, location=cell.location, label=cell.label,
            basis="instant", provider_field="issuer_ir:total_debt_long_term_only", adjustment="provider_reported",
            notes={"debt_definition": debt_note}))
    for period, left, right in zip(periods, ppe, intangibles):
        values.append(IRValue(
            field="capex", period_end=period, value=abs(left.value) + abs(right.value),
            location=f"{left.location} + {right.location}", label="Purchases of PP&E and intangible assets",
            derived={"calculation": "purchase_of_ppe + purchase_of_intangible_assets",
                     "left": {"label": left.label, "value": abs(left.value), "location": left.location},
                     "right": {"label": right.label, "value": abs(right.value), "location": right.location}},
            notes={"capex_scope": "purchases of property, plant and equipment AND intangible assets, "
                                  "the definition ASML's own free cash flow uses"}))
    for value in values:
        end = value.period_end
        value.notes["fiscal_period_end"] = fiscal[periods.index(end)].isoformat()

    _put_lines(lines, "revenue", periods, revenue)
    _put_lines(lines, "cost_of_revenue", periods, cost, sign="abs")
    _put_lines(lines, "gross_profit", periods, gross)
    _put_lines(lines, "net_income_is", periods, net_income)
    _put_lines(lines, "net_income_cf", periods, net_income_cf)
    _put_lines(lines, "cfo", periods, cfo)
    _put_lines(lines, "cfi", periods, cfi)
    _put_lines(lines, "cff", periods, cff)
    _put_lines(lines, "fx", periods, fx)
    _put_lines(lines, "net_change", periods, net_change)
    _put_lines(lines, "cash_begin", periods, cash_begin)
    _put_lines(lines, "cash_end", periods, cash_end)
    _put_lines(lines, "cash_balance_sheet", periods, cash)
    _put_lines(lines, "inventory", periods, inventory)
    _put_lines(lines, "current_assets", periods, current_assets)
    _put_lines(lines, "noncurrent_assets", periods, noncurrent_assets)
    _put_lines(lines, "total_assets", periods, assets)
    _put_lines(lines, "current_liabilities", periods, current_liabilities)
    _put_lines(lines, "total_liabilities", periods, liabilities)
    _put_lines(lines, "equity_total", periods, equity)
    for period, ppe_cell, intangible_cell in zip(periods, ppe, intangibles):
        lines[period]["capex"] = Located(abs(ppe_cell.value) + abs(intangible_cell.value), ppe_cell.location)

    extra = [CheckResult("liabilities_equal_current_plus_noncurrent", period,
                         abs(c.value + n.value - t.value) <= 0.3, "hard",
                         "" if abs(c.value + n.value - t.value) <= 0.3 else
                         f"{c.value:,.1f} + {n.value:,.1f} != {t.value:,.1f}")
             for period, c, n, t in zip(periods, current_liabilities, noncurrent_liabilities, liabilities)]
    filed = re.search(r"(?:^|\W)Q([1-4]) (\d{4})", text[:6000])
    return IRDocument(
        entity="ASML", parser="asml_ex99_3_html", currency="EUR", table_scale=1e6,
        scale_label="EUR million (per-share in EUR)", values=values, lines=lines, periods=periods,
        current_period=periods[-1], identity_text=re.sub(r"<[^>]+>", " ", text[:6000]),
        abs_tolerance=0.3,
        meta={"slides": sorted(slides), "extra_checks": extra,
              "document_title": f"Financial Statements US GAAP Q{filed.group(1)} {filed.group(2)}" if filed else ""})


# --------------------------------------------------------------------------- #
# Euroland .xls export (SK hynix quarterly statements, third-party aggregator)
# --------------------------------------------------------------------------- #
_EUROLAND_PERIOD = re.compile(r"^(\d{4}) ([1-4])Q$")
_EUROLAND_FIELDS = {
    # (section, label, canonical line, emitted field or None, sign)
    ("Income Statement", "Revenue"): ("revenue", "revenue", "as_is"),
    ("Income Statement", "Gross Profit"): ("gross_profit", "gross_profit", "as_is"),
    ("Income Statement", "Operating Profit"): ("operating_income", None, "as_is"),
    ("Income Statement", "Net Profit"): ("net_income", "net_income_is", "as_is"),
    ("Financial Position", "Total Assets"): ("total_assets", "total_assets", "as_is"),
    ("Financial Position", "Current Assets"): (None, "current_assets", "as_is"),
    ("Financial Position", "Non-Current Assets"): (None, "noncurrent_assets", "as_is"),
    ("Financial Position", "Current Liabilities"): (None, "current_liabilities", "as_is"),
    ("Financial Position", "Total Shareholders' Equity"): ("stockholders_equity", "equity_total", "as_is"),
    ("Financial Position", "Cash & Cash Equivalents"): ("cash_and_equivalents", "cash_balance_sheet", "as_is"),
    ("Financial Position", "Inventories"): ("inventory", "inventory", "as_is"),
    ("Financial Position", "Interest-bearing Debts"): ("total_debt", None, "as_is"),
    ("Cash Flows", "Beginning Cash Balance"): (None, "cash_begin", "as_is"),
    ("Cash Flows", "Cash Flow from Operating Activities"): ("cash_from_operations", "cfo", "as_is"),
    ("Cash Flows", "Cash Flow from Investing Activities"): (None, "cfi", "as_is"),
    ("Cash Flows", "Cash Flow from Financing Activities"): (None, "cff", "as_is"),
    ("Cash Flows", "Net Increase (Decrease) in Cash"): (None, "net_change", "as_is"),
    ("Cash Flows", "Ending Cash Balance"): (None, "cash_end", "as_is"),
    ("Cash Flows", "Net Income"): (None, "net_income_cf", "as_is"),
    ("Cash Flows", "Acquisition of PP&E"): ("capex", "capex", "abs"),
    ("Data Per Share", "Earnings Per Share (EPS)"): ("eps_basic", None, "as_is"),
}


def parse_euroland_rows(rows: list[list]) -> IRDocument:
    """Euroland export rows (as read from the sheet) -> statements.

    Every row is scoped by the section header above it, so "Net Income" in Cash Flows
    can never be confused with "Net Profit" in the Income Statement.
    """
    section = ""
    header: list[tuple[int, str]] = []
    found: dict[tuple[str, str], tuple[int, str, dict[str, float]]] = {}
    identity = ""
    footer = ""
    for index, row in enumerate(rows):
        cells = [cell for cell in row]
        label = str(cells[1]).strip() if len(cells) > 1 else ""
        if index < 3 and label:
            identity += label + "\n"
        if any("Euroland" in str(cell) for cell in cells):
            footer = next(str(cell) for cell in cells if "Euroland" in str(cell))
        periods = [(i, str(cells[i]).strip()) for i in range(3, len(cells))
                   if _EUROLAND_PERIOD.match(str(cells[i]).strip())]
        if label and periods:
            section, header = label, periods
            continue
        unit = str(cells[2]).strip() if len(cells) > 2 else ""
        if section and label and unit in {"KRW million", "KRW"}:
            values = {}
            for column, period in header:
                cell = cells[column] if column < len(cells) else ""
                if isinstance(cell, (int, float)) and cell == cell:
                    values[period] = float(cell)
            key = (section, label)
            if key in found:
                raise ParseError(f"Euroland: row {label!r} appears twice in section {section!r}")
            found[key] = (index + 1, unit, values)
    if not found:
        raise ParseError("Euroland: no statement rows found")
    units = {unit.split()[0] for _, unit, _ in found.values()}
    if units != {"KRW"}:
        raise ParseError(f"Euroland: unexpected currency units {sorted(units)}")

    all_periods = sorted({p for _, _, values in found.values() for p in values})
    periods = {p: quarter_end(int(p[:4]), int(p[5])).isoformat() for p in all_periods}

    values_out: list[IRValue] = []
    lines: dict[str, dict[str, Located]] = {}
    for (section_name, label), (field, line_key, sign) in _EUROLAND_FIELDS.items():
        if (section_name, label) not in found:
            if field in {"revenue", "net_income", "total_assets", "cash_from_operations", "cash_and_equivalents",
                         "eps_basic", "gross_profit", "operating_income", "inventory", "total_debt",
                         "capex", "stockholders_equity"}:
                raise ParseError(f"Euroland: required row {label!r} missing in section {section_name!r}")
            continue
        row_number, unit, series = found[(section_name, label)]
        if len(series) != len(all_periods):
            raise ParseError(f"Euroland: row {label!r} lacks a value for some quarters")
        for period in all_periods:
            number = abs(series[period]) if sign == "abs" else series[period]
            located = Located(number, f"row {row_number} '{label}'", label)
            end = periods[period]
            if field:
                values_out.append(IRValue(
                    field=field, period_end=end, value=number, location=located.location, label=label,
                    basis="instant" if field in {"cash_and_equivalents", "inventory", "total_debt", "total_assets",
                                                  "stockholders_equity"} else ""))
            if line_key:
                lines.setdefault(end, {})[line_key] = located

    # total liabilities are not in the export: derive and say so
    for period, end in periods.items():
        assets = lines[end]["total_assets"]
        equity = lines[end]["equity_total"]
        values_out.append(IRValue(
            field="total_liabilities", period_end=end, value=assets.value - equity.value,
            location=f"{assets.location} - {equity.location}", label="Total liabilities (derived)",
            basis="instant",
            derived={"calculation": "total_assets - stockholders_equity",
                     "left": {"label": assets.label, "value": assets.value, "location": assets.location},
                     "right": {"label": equity.label, "value": equity.value, "location": equity.location}}))
        lines[end]["derived_total_liabilities"] = Located(assets.value - equity.value, "derived")

    for value in values_out:
        if value.field == "total_debt":
            value.notes["debt_definition"] = ("Euroland: short-term borrowings + current portion of long-term "
                                              "borrowings + debentures + convertible bond + long-term borrowings")
        if value.field == "cash_and_equivalents":
            value.notes["cash_definition"] = "Euroland: cash + cash equivalents + short-term financial instruments"
        if value.field == "eps_basic":
            value.notes["eps_basis"] = "basic: net income / weighted average outstanding common shares"
    current = periods[all_periods[-1]]
    return IRDocument(
        entity="SKHY", parser="euroland_xls", currency="KRW", table_scale=1e6,
        scale_label="KRW million (EPS in KRW)", values=values_out, lines=lines,
        periods=[periods[p] for p in all_periods], current_period=current,
        identity_text=identity,
        meta={"footer": footer, "rows": len(rows),
              "aggregator": "Euroland" if "Euroland" in footer else "unknown"})


def read_xls_rows(payload: bytes) -> list[list]:
    try:
        import xlrd
    except ImportError as exc:
        raise ParseError("xlrd is not installed (uv sync --extra data)") from exc
    try:
        book = xlrd.open_workbook(file_contents=payload)
        sheet = book.sheet_by_index(0)
        return [sheet.row_values(r) for r in range(sheet.nrows)]
    except Exception as exc:
        raise ParseError(f"not a readable .xls workbook: {exc}") from exc


def parse_euroland_xls(path: Path, payload: bytes) -> IRDocument:
    return parse_euroland_rows(read_xls_rows(payload))


PARSERS = {
    "tsmc_fs_pdf": parse_tsmc_fs_pdf,
    "asml_presentation_pdf": parse_asml_presentation_pdf,
    "asml_ex99_3_html": parse_asml_ex99_3_html,
    "euroland_xls": parse_euroland_xls,
}
