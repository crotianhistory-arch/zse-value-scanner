from __future__ import annotations

import json

import pytest

from zse_tool.esef import ESEFFiling
from zse_tool.esef_activity_baseline import (
    assess_annuality,
    build_annual_activity_evidence,
    candidate_period_filings,
    select_annual_activity_baseline,
)

LEI = "529900197LKWCEQ0NL18"


def filing(period: str, *, lang: str = "en", api_id: str | None = None, errors: int = 0) -> ESEFFiling:
    tag = api_id or period
    return ESEFFiling(
        api_id=tag,
        filing_index=f"{LEI}-{period}-ESEF-DK-0",
        country="DK",
        period_end=period,
        language=lang,
        date_added="2026-01-01",
        processed=f"2026-01-01 {tag}",
        entity_name="NKT A/S",
        entity_identifier=LEI,
        error_count=errors,
        warning_count=0,
        inconsistency_count=0,
        package_sha256="abc",
        json_url=f"https://filings.xbrl.org/{tag}.json",
        package_url=f"https://filings.xbrl.org/{tag}.zip",
        xhtml_url=f"https://filings.xbrl.org/{tag}.xhtml",
        viewer_url=f"https://filings.xbrl.org/{tag}/viewer",
        api_source_url="https://filings.xbrl.org/api/filings?...",
    )


def payload(period_end: str, *, start: str, activity: bool = True) -> dict:
    end_exclusive = {
        "2026-03-31": "2026-04-01T00:00:00",
        "2025-12-31": "2026-01-01T00:00:00",
        "2025-09-30": "2025-10-01T00:00:00",
    }[period_end]
    duration = f"{start}T00:00:00/{end_exclusive}"
    facts = {
        "start": {
            "value": start,
            "dimensions": {"concept": "gsd:ReportingPeriodStartDate", "language": "en"},
        },
        "end": {
            "value": period_end,
            "dimensions": {"concept": "gsd:ReportingPeriodEndDate", "language": "en"},
        },
        "rev": {
            "value": "100",
            "dimensions": {"concept": "ifrs-full:Revenue", "period": duration, "unit": "iso4217:EUR"},
        },
        "profit": {
            "value": "10",
            "dimensions": {"concept": "ifrs-full:ProfitLoss", "period": duration, "unit": "iso4217:EUR"},
        },
        "cash": {
            "value": "8",
            "dimensions": {"concept": "ifrs-full:CashFlowsFromUsedInOperatingActivities", "period": duration, "unit": "iso4217:EUR"},
        },
    }
    if activity:
        facts["activity"] = {
            "value": "The Group designs, manufactures and sells high-voltage power cable systems and services.",
            "dimensions": {
                "concept": "ifrs-full:DescriptionOfNatureOfEntitysOperationsAndPrincipalActivities",
                "period": duration,
                "language": "en",
            },
        }
    return {"facts": facts}


def test_explicit_full_year_is_annual_like():
    a = assess_annuality(payload("2025-12-31", start="2025-01-01"), "2025-12-31")
    assert a["annual_like"] is True
    assert a["state"] == "annual_like"
    assert 365 in a["reporting_period_spans_days"]


def test_q1_reporting_period_is_not_annual_like():
    a = assess_annuality(payload("2026-03-31", start="2026-01-01", activity=False), "2026-03-31")
    assert a["annual_like"] is False
    assert a["state"] == "interim_or_nonannual"


def test_duration_facts_can_prove_annual_when_explicit_dates_absent():
    p = payload("2025-12-31", start="2025-01-01")
    p["facts"].pop("start")
    p["facts"].pop("end")
    a = assess_annuality(p, "2025-12-31")
    assert a["annual_like"] is True
    assert a["aligned_annual_duration_fact_count"] >= 3
    assert a["aligned_core_annual_duration_fact_count"] >= 1


def test_candidate_periods_deduplicate_and_prefer_language_then_validation():
    rows = [
        filing("2025-12-31", lang="fr", api_id="fr", errors=0),
        filing("2025-12-31", lang="en", api_id="en-bad", errors=3),
        filing("2025-12-31", lang="en", api_id="en-good", errors=0),
        filing("2026-03-31", lang="en", api_id="q1"),
    ]
    selected = candidate_period_filings(rows, prefer_language="en")
    assert [x.period_end for x in selected] == ["2026-03-31", "2025-12-31"]
    assert selected[1].api_id == "en-good"


def test_selector_skips_latest_interim_and_uses_prior_annual():
    rows = [filing("2026-03-31", api_id="q1"), filing("2025-12-31", api_id="fy")]
    blobs = {
        rows[0].json_url: json.dumps(payload("2026-03-31", start="2026-01-01", activity=False)).encode(),
        rows[1].json_url: json.dumps(payload("2025-12-31", start="2025-01-01")).encode(),
    }

    def fetcher(url, **kwargs):
        return blobs[url]

    selected = select_annual_activity_baseline(rows, fetcher=fetcher)
    assert selected.filing.period_end == "2025-12-31"
    assert [x["state"] for x in selected.candidate_audit] == [
        "rejected_interim_or_nonannual",
        "accepted_annual_baseline",
    ]


def test_selector_is_bounded_and_fails_without_annual_candidate():
    rows = [
        filing("2026-03-31", api_id="q1"),
        filing("2025-09-30", api_id="q3"),
        filing("2025-12-31", api_id="fy"),
    ]
    blobs = {
        rows[0].json_url: json.dumps(payload("2026-03-31", start="2026-01-01", activity=False)).encode(),
        rows[1].json_url: json.dumps(payload("2025-09-30", start="2025-01-01", activity=False)).encode(),
        rows[2].json_url: json.dumps(payload("2025-12-31", start="2025-01-01")).encode(),
    }

    def fetcher(url, **kwargs):
        return blobs[url]

    with pytest.raises(ValueError, match="no annual-like"):
        select_annual_activity_baseline(rows, max_candidates=1, fetcher=fetcher)


def test_build_preserves_selection_audit_and_reported_activity():
    q1 = filing("2026-03-31", api_id="q1")
    fy = filing("2025-12-31", api_id="fy")
    blobs = {
        q1.json_url: json.dumps(payload("2026-03-31", start="2026-01-01", activity=False)).encode(),
        fy.json_url: json.dumps(payload("2025-12-31", start="2025-01-01")).encode(),
    }

    def discoverer(*args, **kwargs):
        return [q1, fy]

    def fetcher(url, **kwargs):
        return blobs[url]

    out = build_annual_activity_evidence(LEI, discoverer=discoverer, fetcher=fetcher)
    assert out["mode"] == "read-only-annual-activity-baseline"
    assert out["filing"]["period_end"] == "2025-12-31"
    assert len(out["selection"]["candidate_audit"]) == 2
    assert out["tagged_activity"]["selected_fact_count"] == 1
    assert out["policy"]["interim_filing_used_as_activity_baseline"] is False
    assert out["policy"]["automatic_database_writes"] is False
