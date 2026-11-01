#!/usr/bin/env python3
"""Monthly check of the Firstrade fee schedule against official sources.

Two subcommands, both standard library only (``pdftotext`` is needed for the
FINRA filing PDFs):

``check``
    Fetch the SEC Section 31 advisory and the FINRA Trading Activity Fee (TAF)
    sources, compare them with ``application/firstrade_fee_schedule.py`` and
    write a Markdown report plus a JSON result. Any fetch or parse failure is an
    ``error``; any difference is ``changed``. Only a complete match is
    ``unchanged``. Rates are never edited.

``bump``
    Move ``verified_through`` to the last day of the next month, together with
    the date pin in the test and the two doc mentions, as in PR #498. Used only
    after ``check`` reported ``unchanged``.

Exit codes of ``check``: 0 unchanged, 2 changed, 1 error.
Usage: python -m scripts.check_fee_schedule check --report report.md --json result.json
"""
from __future__ import annotations

import argparse
import calendar
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from application.firstrade_fee_schedule import DEFAULT_FEE_SCHEDULE_CONFIG

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEDULE_FILE = REPO_ROOT / "application" / "firstrade_fee_schedule.py"
TEST_FILE = REPO_ROOT / "tests" / "test_statement_reconciliation.py"
DOC_FILES = (
    REPO_ROOT / "docs" / "statement-reconciliation.md",
    REPO_ROOT / "docs" / "statement-reconciliation.zh-CN.md",
)

SEC_INDEX_URL = "https://www.sec.gov/rules-regulations/fee-rate-advisories"
SEC_SRO_URL = "https://www.sec.gov/rules-regulations/self-regulatory-organization-rulemaking/{slug}"
FINRA_SCHEDULE_URL = (
    "https://www.finra.org/rules-guidance/rule-filings/sr-finra-2024-019/fee-adjustment-schedule")
FINRA_LISTING_URL = "https://www.finra.org/rules-guidance/rule-filings"
FINRA_FILING_URL = "https://www.finra.org/rules-guidance/rule-filings/{slug}"

# FINRA filings whose effect is already encoded in the schedule. A listed filing
# that touches the TAF and is not in this set is reported as a change.
REVIEWED_FINRA_FILINGS = frozenset({
    "SR-FINRA-2024-019", "SR-FINRA-2025-007", "SR-FINRA-2026-020", "SR-FINRA-2026-021"})
TAF_TITLE_RE = re.compile(r"Trading Activity Fee|Schedule A to the FINRA By-Laws|\bTAF\b", re.I)
# Filings still inside the SEC's 60-day suspension window when this was written.
WATCHED_SEC_FILINGS = ("SR-FINRA-2026-020", "SR-FINRA-2026-021")

ALLOWED_HOSTS = ("www.sec.gov", "sec.gov", "www.finra.org", "finra.org")
MAX_BYTES = 25 * 1024 * 1024
DEFAULT_CONTACT = "fee-check@quantstrategylab.invalid"  # override with FEE_CHECK_CONTACT
STATUS_UNCHANGED, STATUS_CHANGED, STATUS_ERROR = "unchanged", "changed", "error"


class CheckError(Exception):
    """A source could not be fetched or parsed. Never treated as a pass."""


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #

def user_agent(contact: str | None = None) -> str:
    """Identify the client. sec.gov answers 403 unless the agent carries a contact address
    and, in practice, no URL or host name such as github.com, so keep it plain."""
    contact = (contact or os.environ.get("FEE_CHECK_CONTACT") or DEFAULT_CONTACT).strip()
    if not re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+", contact):
        contact = DEFAULT_CONTACT
    return f"QuantStrategyLab-fee-check/1.0 {contact}"


def _check_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise CheckError(f"refusing to fetch outside the official hosts: {url}")


def fetch_bytes(url: str, *, attempts: int = 3, delay: float = 2.0, timeout: int = 30) -> bytes:
    """GET ``url`` over https from an official host, with a few polite retries."""
    _check_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": user_agent()})
    last: Exception | None = None
    for attempt in range(attempts):
        if attempt:
            time.sleep(delay * attempt)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                _check_url(response.geturl())
                body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise CheckError(f"response too large: {url}")
            if not body:
                raise CheckError(f"empty response: {url}")
            return body
        except CheckError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
    raise CheckError(f"fetch failed for {url}: {last}")


def fetch_text(url: str) -> str:
    return fetch_bytes(url).decode("utf-8", errors="replace")


def pdf_to_text(data: bytes) -> str:
    """Convert a PDF to layout text with poppler's ``pdftotext``."""
    if not data.startswith(b"%PDF-"):
        raise CheckError("response is not a PDF")
    exe = shutil.which("pdftotext")
    if exe is None:
        raise CheckError("pdftotext (poppler-utils) is not installed")
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "doc.pdf"
        src.write_bytes(data)
        try:
            proc = subprocess.run(
                [exe, "-layout", str(src), "-"], capture_output=True, timeout=120, check=False)
        except subprocess.TimeoutExpired:
            raise CheckError("pdftotext timed out") from None
    if proc.returncode != 0 or not proc.stdout.strip():
        raise CheckError(f"pdftotext failed with exit code {proc.returncode}")
    return proc.stdout.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# HTML helpers
# --------------------------------------------------------------------------- #

class _TextParser(HTMLParser):
    """Collect visible text, links, tables and headings."""

    SKIP = {"script", "style", "noscript", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.links: list[tuple[str, str]] = []
        self.tables: list[list[list[str]]] = []
        self.headings: list[tuple[str, str]] = []  # (tag, text) in document order
        self._skip = 0
        self._link: list[str] | None = None
        self._href = ""
        self._table_depth = 0
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._heading: tuple[str, list[str]] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "a":
            self._link = []
            self._href = dict(attrs).get("href") or ""
        elif tag == "table":
            self._table_depth += 1
            self.tables.append([])
        elif tag == "tr" and self._table_depth:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag in ("h1", "h2", "h3", "h4"):
            self._heading = (tag, [])
        elif tag in ("br", "p", "li", "div"):
            self.text.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag == "a" and self._link is not None:
            self.links.append((self._href, _norm(" ".join(self._link))))
            self._link = None
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(_norm(" ".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None and self.tables:
            self.tables[-1].append(self._row)
            self._row = None
        elif tag == "table" and self._table_depth:
            self._table_depth -= 1
        elif tag in ("h1", "h2", "h3", "h4") and self._heading is not None:
            self.headings.append((self._heading[0], _norm(" ".join(self._heading[1]))))
            self._heading = None

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        self.text.append(data)
        if self._link is not None:
            self._link.append(data)
        if self._cell is not None:
            self._cell.append(data)
        if self._heading is not None:
            self._heading[1].append(data)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def parse_html(html: str) -> _TextParser:
    parser = _TextParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:  # HTMLParser is lenient; anything else is a failure
        raise CheckError(f"HTML parse error: {exc}") from None
    return parser


def html_text(html: str) -> str:
    return _norm("".join(parse_html(html).text))


# --------------------------------------------------------------------------- #
# SEC Section 31
# --------------------------------------------------------------------------- #

_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}


def parse_long_date(text: str) -> date:
    match = re.fullmatch(r"([A-Za-z]+)\.?\s+(\d{1,2}),\s+(\d{4})", _norm(text))
    month = _MONTHS.get(match.group(1)[:3].lower()) if match else None
    if not match or not month:
        raise CheckError(f"unrecognised date: {text!r}")
    try:
        return date(int(match.group(3)), month, int(match.group(2)))
    except ValueError:
        raise CheckError(f"invalid date: {text!r}") from None


def _money(text: str, what: str) -> Decimal:
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise CheckError(f"{what}: not a number: {text!r}") from None
    if not value.is_finite() or value < 0:
        raise CheckError(f"{what}: out of range: {text!r}")
    return value


@dataclass(frozen=True)
class SecAdvisory:
    url: str
    title: str
    effective_from: date
    rate_per_million: Decimal


def parse_sec_latest_section31(html: str, base_url: str = SEC_INDEX_URL) -> tuple[str, str]:
    """Return (url, title) of the advisory listed under the latest Section 31 heading."""
    page = parse_html(html)
    heading = "Latest Section 31 Transaction Fee Rate Advisory"
    if not any(tag == "h2" and text == heading for tag, text in page.headings):
        raise CheckError("SEC index: latest Section 31 heading not found")
    # Walk the raw HTML after the heading to the first link before the next heading.
    start = re.search(re.escape(heading) + r"\s*</h2>", html)
    if not start:
        raise CheckError("SEC index: latest Section 31 heading markup not recognised")
    tail = html[start.end():]
    end = re.search(r"<h[1-4][\s>]", tail)
    block = tail[:end.start()] if end else tail
    link = re.search(r'<a\s[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
    if not link:
        raise CheckError("SEC index: no advisory link under the latest Section 31 heading")
    title = html_text(link.group(2))
    if "Section 31" not in title:
        raise CheckError(f"SEC index: unexpected advisory title: {title!r}")
    url = urllib.parse.urljoin(base_url, link.group(1))
    _check_url(url)
    return url, title


def parse_sec_advisory(html: str, url: str, title: str) -> SecAdvisory:
    """Parse the effective date and rate from a Section 31 advisory page."""
    text = html_text(html)
    if "Section 31" not in text:
        raise CheckError("SEC advisory: not a Section 31 page")
    headline = re.search(
        r"starting on ([A-Z][a-z]+\.? \d{1,2}, \d{4}), the fee rates applicable to most "
        r"securities transactions will be set at \$(\d+(?:\.\d+)?) per million", text)
    after = re.search(
        r"a rate of \$(\d+(?:\.\d+)?) per million for covered sales occurring on charge "
        r"dates on or after ([A-Z][a-z]+\.? \d{1,2}, \d{4})", text)
    if not headline or not after:
        raise CheckError("SEC advisory: rate sentences not found")
    d1, r1 = parse_long_date(headline.group(1)), _money(headline.group(2), "SEC rate")
    d2, r2 = parse_long_date(after.group(2)), _money(after.group(1), "SEC rate")
    if (d1, r1) != (d2, r2):
        raise CheckError("SEC advisory: the two rate sentences disagree")
    return SecAdvisory(url=url, title=title, effective_from=d1, rate_per_million=r1)


def parse_sec_filing_page(html: str, filing: str) -> str:
    """Return the text of the <main> region of an SEC SRO rulemaking page."""
    main = _main_text(html)
    if filing not in main:
        raise CheckError(f"SEC {filing}: file number not found on the page")
    return main


def _main_text(html: str) -> str:
    match = re.search(r"<main\b[^>]*>(.*)</main>", html, re.S | re.I)
    if not match:
        raise CheckError("page has no main region")
    return html_text(match.group(1))


# --------------------------------------------------------------------------- #
# FINRA
# --------------------------------------------------------------------------- #

Taf = tuple[Decimal, Decimal]  # (rate per share, max per trade)


def parse_finra_schedule_table(html: str) -> dict[int, Taf]:
    """Covered-equity TAF by year from the SR-FINRA-2024-019 fee adjustment schedule."""
    tables = [t for t in parse_html(html).tables
              if any(row and row[0] == "Covered Equity Security" for row in t)]
    if len(tables) != 1:
        raise CheckError("FINRA schedule: expected exactly one covered-equity table")
    table = tables[0]
    header = next((row for row in table if row and row[0] == "Security Type"), None)
    row = next(row for row in table if row and row[0] == "Covered Equity Security")
    if header is None or len(header) != len(row):
        raise CheckError("FINRA schedule: header and row do not line up")
    result: dict[int, Taf] = {}
    for head, cell in zip(header[1:], row[1:]):
        year = re.match(r"(\d{4})\b", head)
        values = re.fullmatch(
            r"\$(\d\.\d+) per share \(up to \$(\d+\.\d{2}) max per trade\)", cell)
        if not year or not values:
            raise CheckError(f"FINRA schedule: unrecognised cell {head!r}: {cell!r}")
        result[int(year.group(1))] = (_money(values.group(1), "TAF"), _money(values.group(2), "TAF cap"))
    if len(result) < 3:
        raise CheckError("FINRA schedule: too few years parsed")
    return result


def parse_finra_listing(html: str) -> list[tuple[str, str]]:
    """(filing number, title) pairs from the FINRA rule filings listing."""
    out: list[tuple[str, str]] = []
    for table in parse_html(html).tables:
        for row in table:
            if len(row) >= 2 and re.fullmatch(r"SR-FINRA-\d{4}-\d{3}", row[0].strip()):
                out.append((row[0].strip(), row[1].strip()))
    if len(out) < 10:
        raise CheckError(f"FINRA listing: only {len(out)} filings parsed")
    return out


def unreviewed_taf_filings(filings: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return [(num, title) for num, title in filings
            if TAF_TITLE_RE.search(title) and num not in REVIEWED_FINRA_FILINGS]


def find_finra_pdf_url(html: str, slug: str, base_url: str) -> str:
    target = f"{slug.upper()}.pdf"
    for href, _ in parse_html(html).links:
        if href.rsplit("/", 1)[-1] == target:
            url = urllib.parse.urljoin(base_url, href)
            _check_url(url)
            return url
    raise CheckError(f"FINRA {slug}: PDF link not found")


def parse_finra_pdf_taf_table(text: str) -> dict[int, Taf]:
    """Covered-equity TAF by year from the 'Trading Activity Fee*' table in a filing PDF."""
    blocks = re.findall(
        r"Trading Activity Fee\*(.*?)\n\s*Options\b", text, re.S)
    if not blocks:
        raise CheckError("FINRA PDF: TAF table not found")
    parsed = []
    for block in blocks:
        years = [int(y) for y in re.findall(r"\b(20\d{2})\b", block.split("Covered", 1)[0])]
        rates = re.findall(r"\$(0\.\d{6})\b", block)
        caps = re.findall(r"to \$(\d+\.\d{2})\b", block)
        if not years or len(rates) != len(years) or len(caps) != len(years):
            raise CheckError("FINRA PDF: TAF table does not have one rate and cap per year")
        parsed.append({y: (_money(r, "TAF"), _money(c, "TAF cap"))
                       for y, r, c in zip(years, rates, caps)})
    if any(p != parsed[0] for p in parsed):
        raise CheckError("FINRA PDF: TAF tables disagree with each other")
    return parsed[0]


@dataclass(frozen=True)
class TafPause:
    previous: Taf
    paused_from: date
    paused_through: date


def parse_finra_pause(text: str, start: date, through: date) -> TafPause:
    """Check the pause filing text for the covered-equity rate edit and the pause window."""
    flat = _norm(text)
    window = (f"from {start:%B} {start.day}, {start.year} through "
              f"{through:%B} {through.day}, {through.year}")
    if window not in flat:
        raise CheckError(f"FINRA pause: window {window!r} not found")
    edit = re.search(
        r"\$\[(0\.\d{6})\]0\.00 per share for each sale of a covered equity security, "
        r"with a maximum charge of \$\[(\d+\.\d{2})\]0\.00 per trade", flat)
    if not edit:
        raise CheckError("FINRA pause: covered-equity rate edit not found")
    return TafPause((_money(edit.group(1), "TAF"), _money(edit.group(2), "TAF cap")),
                    start, through)


# --------------------------------------------------------------------------- #
# Repo encoding and comparison
# --------------------------------------------------------------------------- #

def _rows(table_name: str, config: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = config.get(table_name)
    if not isinstance(raw, list) or not raw:
        raise CheckError(f"schedule has no {table_name}")
    return sorted(raw, key=lambda r: r["effective_from"])


def _in_effect(rows: list[dict[str, Any]], day: date) -> dict[str, Any] | None:
    chosen = None
    for row in rows:
        if date.fromisoformat(row["effective_from"]) <= day:
            chosen = row
    return chosen


def repo_taf_on(config: Mapping[str, Any], day: date) -> Taf | None:
    row = _in_effect(_rows("taf_per_share", config), day)
    return None if row is None else (Decimal(row["rate"]), Decimal(row["max_per_trade"]))


def repo_pause(config: Mapping[str, Any]) -> tuple[date, date] | None:
    """(first day, last day) of a zero-rate TAF segment, if the schedule has one."""
    rows = _rows("taf_per_share", config)
    for i, row in enumerate(rows):
        if Decimal(row["rate"]) == 0 and i + 1 < len(rows):
            return (date.fromisoformat(row["effective_from"]),
                    date.fromisoformat(rows[i + 1]["effective_from"]) - timedelta(days=1))
    return None


@dataclass
class Report:
    findings: list[str] = field(default_factory=list)  # differences => changed
    notes: list[str] = field(default_factory=list)     # what was verified
    sources: list[str] = field(default_factory=list)

    def source(self, url: str) -> None:
        if url not in self.sources:
            self.sources.append(url)


def compare_sec(advisory: SecAdvisory, config: Mapping[str, Any], report: Report) -> None:
    report.source(advisory.url)
    last = _rows("sec_rate_per_million", config)[-1]
    repo = (date.fromisoformat(last["effective_from"]), Decimal(last["rate"]))
    official = (advisory.effective_from, advisory.rate_per_million)
    if repo == official:
        report.notes.append(
            f"SEC Section 31: latest advisory ({advisory.title}) sets ${official[1]} per "
            f"million from {official[0]}; matches the last schedule row.")
    else:
        report.findings.append(
            f"SEC Section 31: latest advisory ({advisory.title}) sets ${official[1]} per "
            f"million from {official[0]}, but the last schedule row is ${repo[1]} from {repo[0]}.")


def compare_taf_years(label: str, official: Mapping[int, Taf], config: Mapping[str, Any],
                      years: range, report: Report) -> None:
    mismatches = []
    for year in years:
        if year not in official:
            mismatches.append(f"{year}: missing in {label}")
            continue
        repo = repo_taf_on(config, date(year, 1, 1))
        if repo != official[year]:
            mismatches.append(f"{year}: {label} {official[year][0]} / {official[year][1]}, "
                              f"schedule {repo[0] if repo else None} / {repo[1] if repo else None}")
    if mismatches:
        report.findings.append(f"FINRA TAF ({label}) differs from the schedule: " + "; ".join(mismatches))
    else:
        report.notes.append(
            f"FINRA TAF ({label}): covered-equity rate and cap for {years.start}-{years.stop - 1} "
            "match the schedule.")


def compare_pause(pause: TafPause, config: Mapping[str, Any], report: Report) -> None:
    expected = repo_pause(config)
    if expected is None:
        report.findings.append("FINRA TAF pause filing is published but the schedule has no zero-rate segment.")
        return
    if (pause.paused_from, pause.paused_through) != expected:
        report.findings.append(
            f"FINRA TAF pause window {pause.paused_from}..{pause.paused_through} differs from "
            f"the schedule's {expected[0]}..{expected[1]}.")
        return
    before = repo_taf_on(config, expected[0] - timedelta(days=1))
    if before != pause.previous:
        report.findings.append(
            f"FINRA TAF pause filing replaces {pause.previous}, schedule has {before} before the pause.")
        return
    report.notes.append(
        f"FINRA TAF pause (SR-FINRA-2026-021): $0.00 from {expected[0]} through {expected[1]}; "
        "matches the schedule.")


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def run_check(config: Mapping[str, Any], fetch_html: Callable[[str], str],
              fetch_pdf: Callable[[str], str], pause_between: float = 0.0) -> Report:
    """Run every source check. Raises CheckError on any fetch or parse failure."""
    report = Report()

    def sec(url: str) -> str:
        time.sleep(pause_between)  # stay well under the SEC request-rate limit
        return fetch_html(url)

    # SEC Section 31
    index_html = sec(SEC_INDEX_URL)
    report.source(SEC_INDEX_URL)
    adv_url, adv_title = parse_sec_latest_section31(index_html)
    advisory = parse_sec_advisory(sec(adv_url), adv_url, adv_title)
    compare_sec(advisory, config, report)

    # SEC suspension tripwire for the FINRA filings (60-day window)
    for filing in WATCHED_SEC_FILINGS:
        url = SEC_SRO_URL.format(slug=filing.lower())
        main = parse_sec_filing_page(sec(url), filing)
        report.source(url)
        if re.search(r"suspen", main, re.I):
            report.findings.append(f"SEC page for {filing} mentions a suspension; review it: {url}")
        else:
            report.notes.append(f"SEC page for {filing}: no suspension mentioned.")

    # FINRA: original schedule table, years up to 2026 (later years were deferred)
    report.source(FINRA_SCHEDULE_URL)
    compare_taf_years("fee adjustment schedule SR-FINRA-2024-019",
                      parse_finra_schedule_table(fetch_html(FINRA_SCHEDULE_URL)),
                      config, range(2024, 2027), report)

    # FINRA: new TAF filings that have not been reviewed
    report.source(FINRA_LISTING_URL)
    new = unreviewed_taf_filings(parse_finra_listing(fetch_html(FINRA_LISTING_URL)))
    if new:
        report.findings.append("Unreviewed FINRA fee filings: " + "; ".join(f"{n} {t}" for n, t in new))
    else:
        report.notes.append("FINRA rule filings listing: no unreviewed TAF filing.")

    # FINRA: SR-FINRA-2026-020 current schedule (2026-2031)
    f20 = FINRA_FILING_URL.format(slug="sr-finra-2026-020")
    report.source(f20)
    pdf20 = find_finra_pdf_url(fetch_html(f20), "SR-FINRA-2026-020", f20)
    report.source(pdf20)
    table20 = parse_finra_pdf_taf_table(fetch_pdf(pdf20))
    compare_taf_years("SR-FINRA-2026-020", table20, config, range(2026, 2032), report)

    # FINRA: SR-FINRA-2026-021 pause
    pause_window = repo_pause(config)
    if pause_window is None:
        report.notes.append("The schedule has no zero-rate TAF segment; the pause filing was not checked.")
    else:
        f21 = FINRA_FILING_URL.format(slug="sr-finra-2026-021")
        report.source(f21)
        pdf21 = find_finra_pdf_url(fetch_html(f21), "SR-FINRA-2026-021", f21)
        report.source(pdf21)
        compare_pause(parse_finra_pause(fetch_pdf(pdf21), *pause_window), config, report)
    return report


def render_report(status: str, report: Report | None, error: str | None, today: date,
                  verified_through: str) -> str:
    lines = [f"# Fee schedule check, {today.isoformat()}", "",
             f"Status: **{status}**. Current `verified_through`: `{verified_through}`.", ""]
    if error:
        lines += ["## Check failed", "", f"`{error}`", "",
                  "A failed check is never a pass. Nothing was changed and no bump PR was opened.", ""]
    if report:
        if report.findings:
            lines += ["## Differences", ""] + [f"- {f}" for f in report.findings] + [""]
        if report.notes:
            lines += ["## Verified", ""] + [f"- {n}" for n in report.notes] + [""]
        if report.sources:
            lines += ["## Sources", ""] + [f"- {s}" for s in report.sources] + [""]
    if status != STATUS_UNCHANGED:
        lines += ["Rates are never changed automatically. Review the sources, update "
                  "`application/firstrade_fee_schedule.py`, its tests and docs in a normal "
                  "PR, then move `verified_through`. Tracking issue: #497.", ""]
    return "\n".join(lines)


def cmd_check(args: argparse.Namespace) -> int:
    today = date.today()
    config = DEFAULT_FEE_SCHEDULE_CONFIG
    report: Report | None = None
    error: str | None = None
    try:
        report = run_check(config, fetch_text, lambda url: pdf_to_text(fetch_bytes(url)),
                           pause_between=1.0)
        status = STATUS_CHANGED if report.findings else STATUS_UNCHANGED
    except CheckError as exc:
        status, error = STATUS_ERROR, str(exc)
    except Exception as exc:  # never let an unexpected bug look like a pass
        status, error = STATUS_ERROR, f"unexpected {type(exc).__name__}: {exc}"
    markdown = render_report(status, report, error, today, str(config["verified_through"]))
    Path(args.report).write_text(markdown, encoding="utf-8")
    Path(args.json).write_text(json.dumps({
        "status": status, "date": today.isoformat(), "error": error,
        "findings": report.findings if report else [],
        "sources": report.sources if report else [],
    }, indent=2), encoding="utf-8")
    print(markdown)
    return {STATUS_UNCHANGED: 0, STATUS_CHANGED: 2}.get(status, 1)


# --------------------------------------------------------------------------- #
# Bump
# --------------------------------------------------------------------------- #

def next_month_end(today: date) -> date:
    year, month = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
    return date(year, month, calendar.monthrange(year, month)[1])


def _replace_exact(text: str, old: str, new: str, minimum: int, label: str) -> str:
    count = text.count(old)
    if count < minimum:
        raise CheckError(f"{label}: expected at least {minimum} occurrence(s) of {old!r}, found {count}")
    return text.replace(old, new)


def apply_bump(old: date, new: date, today: date, schedule_src: str, test_src: str,
               docs: Mapping[str, str]) -> tuple[str, str, dict[str, str]]:
    """Return updated file contents. Raises CheckError if any pin is not where expected."""
    if new <= old:
        raise CheckError("new verified_through must be later than the current one")
    old_s, new_s = old.isoformat(), new.isoformat()
    schedule_src = _replace_exact(
        schedule_src, f'"verified_through": "{old_s}"', f'"verified_through": "{new_s}"', 1,
        "schedule verified_through")
    schedule_src, n = re.subn(r"Sources \(checked \d{4}-\d{2}-\d{2}\)",
                              f"Sources (checked {today.isoformat()})", schedule_src)
    if n != 1:
        raise CheckError("schedule docstring 'Sources (checked ...)' line not found once")
    # The test keeps the review pin on one line; the day-after value is derived from it.
    pin_line = f"VERIFIED_THROUGH = date({old.year}, {old.month}, {old.day})"
    test_src = _replace_exact(
        test_src, pin_line, f"VERIFIED_THROUGH = date({new.year}, {new.month}, {new.day})", 1,
        "test review pin")
    if test_src.count("VERIFIED_THROUGH = date(") != 1:
        raise CheckError("test review pin must appear exactly once")
    new_docs = {name: _replace_exact(body, f"`{old_s}`", f"`{new_s}`", 1, f"doc {name}")
                for name, body in docs.items()}
    return schedule_src, test_src, new_docs


def cmd_bump(args: argparse.Namespace) -> int:
    today = date.fromisoformat(args.today) if args.today else date.today()
    old = date.fromisoformat(str(DEFAULT_FEE_SCHEDULE_CONFIG["verified_through"]))
    new = next_month_end(today)
    if old >= new:
        print(f"verified_through {old} already covers {new}; nothing to bump.")
        Path(args.output).write_text("", encoding="utf-8")
        return 0
    docs = {str(p): p.read_text(encoding="utf-8") for p in DOC_FILES}
    schedule_src, test_src, new_docs = apply_bump(
        old, new, today, SCHEDULE_FILE.read_text(encoding="utf-8"),
        TEST_FILE.read_text(encoding="utf-8"), docs)
    SCHEDULE_FILE.write_text(schedule_src, encoding="utf-8")
    TEST_FILE.write_text(test_src, encoding="utf-8")
    for name, body in new_docs.items():
        Path(name).write_text(body, encoding="utf-8")
    Path(args.output).write_text(new.isoformat(), encoding="utf-8")
    print(f"verified_through {old} -> {new}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="compare official sources with the schedule")
    check.add_argument("--report", required=True)
    check.add_argument("--json", required=True)
    check.set_defaults(func=cmd_check)
    bump = sub.add_parser("bump", help="move verified_through to the end of next month")
    bump.add_argument("--today", help="override today's date (YYYY-MM-DD)")
    bump.add_argument("--output", required=True, help="file that receives the new date")
    bump.set_defaults(func=cmd_bump)
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except CheckError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
