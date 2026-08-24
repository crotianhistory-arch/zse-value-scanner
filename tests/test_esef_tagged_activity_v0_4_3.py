from __future__ import annotations

import gzip
import json

from zse_tool.esef import ESEFFiling
from zse_tool.esef_tagged_activity import (
    build_batch,
    build_tagged_activity_evidence,
    classify_reported_activity,
    extract_reported_activity_facts,
    extract_tagged_html,
)

LEI = "724500HDW6IWR9J5YT90"


def filing(**overrides):
    data = dict(
        api_id="23638",
        filing_index=f"{LEI}-2025-12-31-ESEF-NL-0",
        country="NL",
        period_end="2025-12-31",
        language="en",
        date_added="2026-02-17",
        processed="2026-02-17",
        entity_name="Alfen N.V.",
        entity_identifier=LEI,
        error_count=0,
        warning_count=0,
        inconsistency_count=0,
        package_sha256="abc",
        json_url="https://filings.xbrl.org/alf.json",
        package_url="https://filings.xbrl.org/alf.zip",
        xhtml_url="https://filings.xbrl.org/alf.xhtml",
        viewer_url="https://filings.xbrl.org/viewer",
        api_source_url="https://filings.xbrl.org/api/filings?...",
    )
    data.update(overrides)
    return ESEFFiling(**data)


def sample_payload():
    long_padding = "<div>" + ("context " * 400) + "</div>"
    return {
        "facts": {
            "principal": {
                "value": (
                    "Note 1 General information. The company develops, manufactures and sells "
                    "products, systems and services related to the electricity grid, including "
                    "Smart Grid Solutions, EV Charging and Energy Storage Systems."
                ),
                "dimensions": {
                    "concept": "ifrs-full:DescriptionOfNatureOfEntitysOperationsAndPrincipalActivities",
                    "entity": f"lei:{LEI}",
                    "period": "2025-01-01T00:00:00/2026-01-01T00:00:00",
                    "language": "en",
                },
            },
            "segments": {
                "value": "<h2>Segment information</h2><p>Operating segments are reviewed by management.</p>",
                "dimensions": {
                    "concept": "ifrs-full:DisclosureOfEntitysReportableSegmentsExplanatory",
                    "entity": f"lei:{LEI}",
                    "period": "2025-01-01T00:00:00/2026-01-01T00:00:00",
                    "language": "en",
                },
            },
            "revenue": {
                "value": long_padding + (
                    "<h2>Revenue</h2><p>Revenue per business line:</p>"
                    "<table><tr><th>Business line</th><th>2025</th><th>2024</th></tr>"
                    "<tr><td>Smart Grid Solutions</td><td>500</td><td>450</td></tr>"
                    "<tr><td>EV Charging</td><td>200</td><td>220</td></tr></table>"
                ),
                "dimensions": {
                    "concept": "ifrs-full:DisclosureOfRevenueFromContractsWithCustomersExplanatory",
                    "entity": f"lei:{LEI}",
                    "period": "2025-01-01T00:00:00/2026-01-01T00:00:00",
                    "language": "en",
                },
            },
            "subs": {
                "value": (
                    "<p>Companies included in the consolidated financial statements:</p>"
                    "<table><tr><th>Company name</th><th>Country</th></tr>"
                    "<tr><td>Alfen Belgium BV</td><td>Belgium</td></tr></table>"
                ),
                "dimensions": {
                    "concept": "ifrs-full:DisclosureOfSignificantInvestmentsInSubsidiariesExplanatory",
                    "entity": f"lei:{LEI}",
                    "period": "2025-01-01T00:00:00/2026-01-01T00:00:00",
                    "language": "en",
                },
            },
            "noise": {
                "value": "Revenue is recognized when control transfers to the customer.",
                "dimensions": {
                    "concept": "ifrs-full:DescriptionOfAccountingPolicyForRecognitionOfRevenue",
                    "entity": f"lei:{LEI}",
                    "period": "2025-01-01T00:00:00/2026-01-01T00:00:00",
                    "language": "en",
                },
            },
        }
    }


def test_extract_tagged_html_cleans_text_and_preserves_table_rows():
    value = "<h2>Revenue</h2><table><tr><th>Line</th><th>EURm</th></tr><tr><td>Grid</td><td>500</td></tr></table>"
    text, tables, truncated = extract_tagged_html(value)
    assert "Revenue" in text
    assert tables[0].rows == [["Line", "EURm"], ["Grid", "500"]]
    assert truncated is False


def test_classification_is_disclosure_purpose_not_industry_inference():
    assert classify_reported_activity(
        "ifrs-full:DescriptionOfNatureOfEntitysOperationsAndPrincipalActivities",
        "Smart Grid Solutions",
        [],
    ) == "principal_activity"
    assert classify_reported_activity(
        "ifrs-full:DisclosureOfEntitysReportableSegmentsExplanatory",
        "Operating segments",
        [],
    ) == "operating_segments"
    assert classify_reported_activity(
        "ifrs-full:DescriptionOfAccountingPolicyForRecognitionOfRevenue",
        "Revenue policy",
        [],
    ) is None


def test_extract_reported_activity_uses_full_fact_not_old_1500_char_truncation():
    result = extract_reported_activity_facts(sample_payload())
    revenue = next(row for row in result["reported_activity_facts"] if row["category"] == "revenue_business_line")
    assert revenue["source_value_chars"] > 1500
    assert revenue["tables"]
    assert revenue["tables"][0]["rows"][1][0] == "Smart Grid Solutions"


def test_extract_reported_activity_categories_principal_segments_revenue_and_subsidiaries():
    result = extract_reported_activity_facts(sample_payload())
    assert result["category_counts"] == {
        "operating_segments": 1,
        "principal_activity": 1,
        "revenue_business_line": 1,
        "subsidiaries": 1,
    }
    principal = next(row for row in result["reported_activity_facts"] if row["category"] == "principal_activity")
    assert "Energy Storage Systems" in principal["text"]
    assert all(row["evidence_class"] == "R2_REPORTED_TEXT" for row in result["reported_activity_facts"])


def test_build_tagged_activity_fetches_json_only_and_is_read_only():
    f = filing()
    calls = []

    def discoverer(*args, **kwargs):
        return [f]

    def fetcher(url, **kwargs):
        calls.append(url)
        return gzip.compress(json.dumps(sample_payload()).encode())

    payload = build_tagged_activity_evidence(LEI, discoverer=discoverer, fetcher=fetcher)
    assert calls == [f.json_url]
    assert payload["policy"]["full_xhtml_fetched"] is False
    assert payload["policy"]["automatic_peer_assignment"] is False
    assert payload["policy"]["automatic_similarity_scoring"] is False
    assert payload["policy"]["automatic_database_writes"] is False
    assert payload["tagged_activity"]["selected_fact_count"] == 4
    assert payload["provenance"]["original_report_package"] == f.package_url


def test_build_batch_deduplicates_and_isolates_errors():
    other = "969500A1YF1XUYYXS284"

    def builder(lei, **kwargs):
        if lei == other:
            raise ValueError("no ESEF filing found for requested entity")
        return {
            "entity": {"reported_name": "Alfen N.V.", "country": "NL"},
            "filing": {"period_end": "2025-12-31", "language": "en"},
            "xbrl_activity": {"fact_count": 358},
            "tagged_activity": {"selected_fact_count": 21, "category_counts": {"principal_activity": 1}},
        }

    batch = build_batch([LEI, LEI, other], builder=builder)
    assert batch["requested"] == 2
    assert batch["succeeded"] == 1
    assert batch["failed"] == 1
    assert batch["results"][0]["state"] == "ok"
    assert batch["results"][1]["state"] == "error"
    assert batch["results"][1]["error_type"] == "ValueError"


def test_tables_are_bounded_without_losing_evidence_classification():
    rows = "".join(f"<tr><td>Line {i}</td><td>{i}</td></tr>" for i in range(200))
    value = f"<p>Revenue per business line</p><table>{rows}</table>"
    text, tables, _ = extract_tagged_html(value)
    assert classify_reported_activity("ifrs-full:DisclosureOfRevenueExplanatory", text, tables) == "revenue_business_line"
    assert len(tables[0].rows) == 120
    assert tables[0].truncated is True
