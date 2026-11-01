"""Offline tests for scripts/check_fee_schedule.py. Fixtures are trimmed saved pages."""

from __future__ import annotations

import copy
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from application.firstrade_fee_schedule import DEFAULT_FEE_SCHEDULE_CONFIG
from scripts import check_fee_schedule as cfs

FIXTURES = Path(__file__).parent / "fixtures" / "fee_schedule_check"
TAF_2026 = (Decimal("0.000195"), Decimal("9.79"))


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# Frozen copy of the rate tables the saved fixtures were taken against, so that a later,
# legitimate rate edit in the schedule does not break the parser tests.
FROZEN_CONFIG = {
    "verified_through": "2026-12-01",
    "sec_rate_per_million": [
        {"effective_from": "2024-05-22", "rate": "27.80"},
        {"effective_from": "2025-05-14", "rate": "0.00"},
        {"effective_from": "2026-04-04", "rate": "20.60"},
    ],
    "taf_per_share": [
        {"effective_from": "2024-01-01", "rate": "0.000166", "max_per_trade": "8.30"},
        {"effective_from": "2026-01-01", "rate": "0.000195", "max_per_trade": "9.79"},
        {"effective_from": "2026-10-01", "rate": "0", "max_per_trade": "0"},
        {"effective_from": "2027-01-01", "rate": "0.000195", "max_per_trade": "9.79"},
        {"effective_from": "2029-01-01", "rate": "0.000232", "max_per_trade": "11.61"},
        {"effective_from": "2030-01-01", "rate": "0.000240", "max_per_trade": "12.05"},
        {"effective_from": "2031-01-01", "rate": "0.000249", "max_per_trade": "12.50"},
    ],
}


def config() -> dict:
    return copy.deepcopy(FROZEN_CONFIG)


# --- SEC ---------------------------------------------------------------------------------

def test_sec_index_finds_latest_section_31_advisory():
    url, title = cfs.parse_sec_latest_section31(fixture("sec_index.html"))
    assert url == "https://www.sec.gov/rules-regulations/fee-rate-advisories/2026-2"
    assert title == "Section 31 Transaction Fee Rate Advisory for Fiscal Year 2026"


def test_sec_index_without_the_heading_fails():
    with pytest.raises(cfs.CheckError):
        cfs.parse_sec_latest_section31("<html><main><h2>Something else</h2></main></html>")


def test_sec_index_rejects_links_off_official_hosts():
    html = fixture("sec_index.html").replace("https://www.sec.gov/rules-regulations/fee-rate-advisories/2026-2",
                                              "https://example.com/advisory")
    with pytest.raises(cfs.CheckError):
        cfs.parse_sec_latest_section31(html)


def test_sec_advisory_parses_effective_date_and_rate():
    advisory = cfs.parse_sec_advisory(fixture("sec_advisory.html"), cfs.SEC_INDEX_URL, "Section 31 test")
    assert advisory.effective_from == date(2026, 4, 4)
    assert advisory.rate_per_million == Decimal("20.60")


def test_sec_advisory_with_disagreeing_sentences_fails():
    html = fixture("sec_advisory.html").replace("a rate of $20.60 per million", "a rate of $21.60 per million")
    with pytest.raises(cfs.CheckError, match="disagree"):
        cfs.parse_sec_advisory(html, cfs.SEC_INDEX_URL, "Section 31 test")


def test_sec_advisory_with_unrecognised_wording_fails():
    with pytest.raises(cfs.CheckError):
        cfs.parse_sec_advisory("<main>Section 31 fees changed, details soon.</main>", cfs.SEC_INDEX_URL, "t")
    with pytest.raises(cfs.CheckError):
        cfs.parse_sec_advisory("<main>Unrelated page.</main>", cfs.SEC_INDEX_URL, "t")


def test_sec_filing_page_ignores_navigation_when_looking_for_suspension():
    # The fixture's nav links to "Trading Suspensions"; only <main> counts.
    main = cfs.parse_sec_filing_page(fixture("sec_filing_020.html"), "SR-FINRA-2026-020")
    assert "suspen" not in main.lower()
    suspended = fixture("sec_filing_020.html").replace(
        "<h2>Details</h2>", "<h2>Details</h2><p>The Commission temporarily suspended the rule change.</p>")
    assert "suspen" in cfs.parse_sec_filing_page(suspended, "SR-FINRA-2026-020").lower()


def test_sec_filing_page_for_the_wrong_filing_fails():
    with pytest.raises(cfs.CheckError):
        cfs.parse_sec_filing_page(fixture("sec_filing_021.html"), "SR-FINRA-2026-020")
    with pytest.raises(cfs.CheckError, match="no main"):
        cfs.parse_sec_filing_page("<html>SR-FINRA-2026-020</html>", "SR-FINRA-2026-020")


# --- FINRA -------------------------------------------------------------------------------

def test_finra_schedule_table_gives_covered_equity_rates_by_year():
    table = cfs.parse_finra_schedule_table(fixture("finra_schedule.html"))
    assert table[2024] == (Decimal("0.000166"), Decimal("8.30"))
    assert table[2026] == (Decimal("0.000195"), Decimal("9.79"))


def test_finra_schedule_table_missing_fails():
    with pytest.raises(cfs.CheckError):
        cfs.parse_finra_schedule_table("<html><main><p>No tables</p></main></html>")


def test_finra_listing_parses_numbers_and_titles():
    filings = cfs.parse_finra_listing(fixture("finra_listing.html"))
    assert len(filings) >= 10
    assert all(num.startswith("SR-FINRA-") and title for num, title in filings)


def test_finra_listing_with_too_few_rows_fails():
    with pytest.raises(cfs.CheckError):
        cfs.parse_finra_listing("<table><tr><td>SR-FINRA-2026-001</td><td>One</td></tr></table>")


def test_unreviewed_taf_filings_flags_only_new_taf_filings():
    filings = [
        ("SR-FINRA-2026-020", "Modify the Implementation Schedule of Amendments to Schedule A to the FINRA By-Laws"),
        ("SR-FINRA-2026-021", "Temporarily Pause Assessment of the Trading Activity Fee"),
        ("SR-FINRA-2026-030", "Adjust the Trading Activity Fee"),
        ("SR-FINRA-2026-031", "Amend Rule 1240 on continuing education"),
    ]
    assert cfs.unreviewed_taf_filings(filings) == [("SR-FINRA-2026-030", "Adjust the Trading Activity Fee")]


def test_find_finra_pdf_url_picks_the_filing_itself():
    url = cfs.find_finra_pdf_url(
        fixture("finra_filing_020.html"), "sr-finra-2026-020",
        "https://www.finra.org/rules-guidance/rule-filings/sr-finra-2026-020")
    assert url == "https://www.finra.org/sites/default/files/2026-09/SR-FINRA-2026-020.pdf"


def test_find_finra_pdf_url_missing_or_off_host_fails():
    base = "https://www.finra.org/rules-guidance/rule-filings/x"
    with pytest.raises(cfs.CheckError):
        cfs.find_finra_pdf_url("<a href='/a/other.pdf'>x</a>", "sr-finra-2026-020", base)
    with pytest.raises(cfs.CheckError):
        cfs.find_finra_pdf_url("<a href='https://example.com/SR-FINRA-2026-020.pdf'>x</a>",
                               "sr-finra-2026-020", base)


def test_finra_pdf_taf_table_by_year():
    table = cfs.parse_finra_pdf_taf_table(fixture("finra_2026_020_taf.txt"))
    assert sorted(table) == [2026, 2027, 2028, 2029, 2030, 2031]
    assert table[2026] == TAF_2026
    assert table[2029] == (Decimal("0.000232"), Decimal("11.61"))
    assert table[2031] == (Decimal("0.000249"), Decimal("12.50"))


def test_finra_pdf_taf_table_with_a_missing_cap_fails():
    text = fixture("finra_2026_020_taf.txt").replace("to $12.50", "to 12.50")
    with pytest.raises(cfs.CheckError):
        cfs.parse_finra_pdf_taf_table(text)
    with pytest.raises(cfs.CheckError, match="not found"):
        cfs.parse_finra_pdf_taf_table("nothing here")


def test_finra_pause_window_and_previous_rate():
    pause = cfs.parse_finra_pause(fixture("finra_2026_021_pause.txt"), date(2026, 10, 1), date(2026, 12, 31))
    assert pause.previous == TAF_2026
    assert (pause.paused_from, pause.paused_through) == (date(2026, 10, 1), date(2026, 12, 31))


def test_finra_pause_with_a_different_window_fails():
    with pytest.raises(cfs.CheckError, match="window"):
        cfs.parse_finra_pause(fixture("finra_2026_021_pause.txt"), date(2026, 10, 1), date(2027, 1, 31))
    with pytest.raises(cfs.CheckError, match="edit"):
        cfs.parse_finra_pause("from October 1, 2026 through December 31, 2026", date(2026, 10, 1),
                              date(2026, 12, 31))


# --- Comparison --------------------------------------------------------------------------

def sec_advisory(rate: str = "20.60", effective: date = date(2026, 4, 4)) -> cfs.SecAdvisory:
    return cfs.SecAdvisory("https://www.sec.gov/x", "advisory", effective, Decimal(rate))


def test_compare_sec_matches_and_differs():
    ok = cfs.Report()
    cfs.compare_sec(sec_advisory(), config(), ok)
    assert not ok.findings and ok.notes

    changed = cfs.Report()
    cfs.compare_sec(sec_advisory("21.00", date(2026, 11, 1)), config(), changed)
    assert len(changed.findings) == 1
    assert "21.00" in changed.findings[0]


def test_compare_taf_years_reports_each_difference_and_missing_years():
    official = {2026: TAF_2026, 2027: (Decimal("0.000300"), Decimal("9.79"))}
    report = cfs.Report()
    cfs.compare_taf_years("test", official, config(), range(2026, 2029), report)
    assert len(report.findings) == 1
    assert "2027" in report.findings[0] and "2028: missing" in report.findings[0]


def test_compare_pause_checks_window_and_previous_rate():
    cfg = config()
    window = cfs.repo_pause(cfg)
    assert window == (date(2026, 10, 1), date(2026, 12, 31))

    ok = cfs.Report()
    cfs.compare_pause(cfs.TafPause(TAF_2026, *window), cfg, ok)
    assert not ok.findings

    shifted = cfs.Report()
    cfs.compare_pause(cfs.TafPause(TAF_2026, window[0], date(2027, 1, 31)), cfg, shifted)
    assert shifted.findings

    other_rate = cfs.Report()
    cfs.compare_pause(cfs.TafPause((Decimal("0.0003"), Decimal("9.79")), *window), cfg, other_rate)
    assert other_rate.findings


# --- Whole run, with the saved pages instead of the network -------------------------------

SEC = "https://www.sec.gov/rules-regulations/"
FINRA = "https://www.finra.org/rules-guidance/rule-filings"
PAGES = {
    cfs.SEC_INDEX_URL: "sec_index.html",
    SEC + "fee-rate-advisories/2026-2": "sec_advisory.html",
    SEC + "self-regulatory-organization-rulemaking/sr-finra-2026-020": "sec_filing_020.html",
    SEC + "self-regulatory-organization-rulemaking/sr-finra-2026-021": "sec_filing_021.html",
    cfs.FINRA_SCHEDULE_URL: "finra_schedule.html",
    cfs.FINRA_LISTING_URL: "finra_listing.html",
    FINRA + "/sr-finra-2026-020": "finra_filing_020.html",
    FINRA + "/sr-finra-2026-021": "finra_filing_021.html",
}
PDFS = {
    "https://www.finra.org/sites/default/files/2026-09/SR-FINRA-2026-020.pdf": "finra_2026_020_taf.txt",
    "https://www.finra.org/sites/default/files/2026-09/SR-FINRA-2026-021.pdf": "finra_2026_021_pause.txt",
}


def offline_run(cfg: dict, overrides: dict[str, str] | None = None) -> cfs.Report:
    pages = {**{u: fixture(n) for u, n in PAGES.items()}, **(overrides or {})}

    def fetch_html(url: str) -> str:
        if url not in pages:
            raise cfs.CheckError(f"unexpected fetch {url}")
        return pages[url]

    def fetch_pdf(url: str) -> str:
        if url not in PDFS:
            raise cfs.CheckError(f"unexpected fetch {url}")
        return fixture(PDFS[url])

    return cfs.run_check(cfg, fetch_html, fetch_pdf)


def test_full_run_is_unchanged_for_the_frozen_schedule():
    report = offline_run(config())
    assert report.findings == []
    assert len(report.notes) == 7
    assert all(url.startswith(("https://www.sec.gov/", "https://www.finra.org/")) for url in report.sources)


def test_full_run_reports_a_sec_rate_change():
    cfg = config()
    cfg["sec_rate_per_million"][-1]["rate"] = "19.00"
    assert any("SEC Section 31" in f for f in offline_run(cfg).findings)


def test_full_run_reports_a_taf_change():
    cfg = config()
    cfg["taf_per_share"][-1]["rate"] = "0.000200"
    assert offline_run(cfg).findings


def test_full_run_reports_an_sec_suspension():
    page = fixture("sec_filing_021.html").replace(
        "<h2>Details</h2>", "<h2>Details</h2><p>Order temporarily suspending the proposed rule change.</p>")
    key = SEC + "self-regulatory-organization-rulemaking/sr-finra-2026-021"
    assert any("suspension" in f for f in offline_run(config(), {key: page}).findings)


def test_full_run_reports_a_new_taf_filing():
    listing = fixture("finra_listing.html").replace(
        "Senior Leader Regulatory Element", "Trading Activity Fee Increase", 1)
    findings = offline_run(config(), {cfs.FINRA_LISTING_URL: listing}).findings
    assert any("Unreviewed FINRA fee filings" in f for f in findings)


def test_full_run_fails_when_a_source_cannot_be_parsed():
    with pytest.raises(cfs.CheckError):
        offline_run(config(), {cfs.SEC_INDEX_URL: "<html><main>redesigned page</main></html>"})
    with pytest.raises(cfs.CheckError):
        offline_run(config(), {cfs.FINRA_SCHEDULE_URL: "<html><main></main></html>"})


# --- Rendering, fetching, exit codes -----------------------------------------------------

def test_render_report_never_calls_an_error_a_pass():
    text = cfs.render_report(cfs.STATUS_ERROR, None, "fetch failed", date(2026, 11, 22), "2026-12-01")
    assert "Status: **error**" in text and "never a pass" in text
    assert "#497" in text


def test_fetch_rejects_non_https_and_foreign_hosts():
    for url in ("http://www.sec.gov/x", "https://example.com/x", "https://sec.gov.evil.example/x",
                "file:///etc/passwd"):
        with pytest.raises(cfs.CheckError):
            cfs.fetch_bytes(url, attempts=1)


def test_user_agent_carries_a_contact_and_no_url():
    assert "@" in cfs.user_agent()
    assert "ops@example.org" in cfs.user_agent("ops@example.org")
    assert "github" not in cfs.user_agent("not an address").lower()


def test_pdf_to_text_rejects_non_pdf_bytes():
    with pytest.raises(cfs.CheckError):
        cfs.pdf_to_text(b"<html>not a pdf</html>")


# --- Bump --------------------------------------------------------------------------------

def test_next_month_end():
    assert cfs.next_month_end(date(2026, 11, 22)) == date(2026, 12, 31)
    assert cfs.next_month_end(date(2026, 12, 22)) == date(2027, 1, 31)
    assert cfs.next_month_end(date(2027, 1, 22)) == date(2027, 2, 28)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def current_pin() -> date:
    return date.fromisoformat(str(DEFAULT_FEE_SCHEDULE_CONFIG["verified_through"]))


def test_apply_bump_moves_every_pin_and_nothing_else():
    old = current_pin()
    new = old + timedelta(days=30)
    today = old
    docs = {str(p): read(p) for p in cfs.DOC_FILES}
    schedule, test, new_docs = cfs.apply_bump(
        old, new, today, read(cfs.SCHEDULE_FILE), read(cfs.TEST_FILE), docs)
    assert f'"verified_through": "{new}"' in schedule
    assert f"Sources (checked {today})" in schedule
    assert f"VERIFIED_THROUGH = date({new.year}, {new.month}, {new.day})" in test
    assert all(f"`{new}`" in body and f"`{old}`" not in body for body in new_docs.values())
    before = read(cfs.SCHEDULE_FILE).splitlines()
    changed = [a for a, b in zip(before, schedule.splitlines(), strict=True) if a != b]
    assert len(changed) <= 2  # the pin and the "checked" date; no rate row moves
    assert all("rate" not in line and "max_per_trade" not in line for line in changed)


def test_apply_bump_fails_closed_when_a_pin_is_missing():
    old = current_pin()
    new = old + timedelta(days=30)
    docs = {str(p): read(p) for p in cfs.DOC_FILES}
    schedule, test = read(cfs.SCHEDULE_FILE), read(cfs.TEST_FILE)
    with pytest.raises(cfs.CheckError):  # the schedule does not carry this date
        cfs.apply_bump(old - timedelta(days=1), new, old, schedule, test, docs)
    with pytest.raises(cfs.CheckError):
        cfs.apply_bump(old, new, old, schedule, "no pin here", docs)
    with pytest.raises(cfs.CheckError):
        cfs.apply_bump(old, new, old, schedule, test, {"doc.md": "no date"})
    with pytest.raises(cfs.CheckError):
        cfs.apply_bump(old, old, old, schedule, test, docs)
