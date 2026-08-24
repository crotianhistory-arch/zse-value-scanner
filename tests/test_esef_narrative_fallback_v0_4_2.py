from __future__ import annotations

import gzip
import json

import pytest

from zse_tool.esef import ESEFFiling
from zse_tool.esef_activity import EvidenceTooLarge, build_activity_evidence

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


def xbrl_bytes():
    payload = {
        "facts": {
            "f1": {
                "value": "123",
                "dimensions": {
                    "concept": "ifrs-full:Revenue",
                    "entity": f"lei:{LEI}",
                    "period": "2025-01-01T00:00:00/2026-01-01T00:00:00",
                    "unit": "iso4217:EUR",
                    "alf:OperatingSegmentsAxis": "alf:SmartGridSolutionsMember",
                },
            },
            "f2": {
                "value": "Smart Grid Solutions develops grid equipment and substations.",
                "dimensions": {
                    "concept": "alf:DescriptionOfBusinessSegments",
                    "entity": f"lei:{LEI}",
                    "period": "2025-01-01T00:00:00/2026-01-01T00:00:00",
                    "language": "en",
                },
            },
        }
    }
    return gzip.compress(json.dumps(payload).encode())


def test_oversized_xhtml_preserves_structured_activity_evidence():
    f = filing()

    def discoverer(*args, **kwargs):
        return [f]

    def fetcher(url, **kwargs):
        if url.endswith(".json"):
            return xbrl_bytes()
        raise EvidenceTooLarge("evidence object exceeds byte limit while downloading: > 83886080")

    payload = build_activity_evidence(LEI, discoverer=discoverer, fetcher=fetcher)

    assert payload["xbrl_activity"]["fact_count"] == 2
    assert payload["xbrl_activity"]["numeric_activity_facts"]
    assert payload["xbrl_activity"]["text_activity_facts"]
    assert payload["narrative_evidence"] == []
    assert payload["narrative_status"]["state"] == "skipped_oversize"
    assert "83886080" in payload["narrative_status"]["reason"]
    assert payload["policy"]["narrative_enrichment_is_bounded_and_optional"] is True


def test_missing_xhtml_is_nonfatal_for_structured_evidence():
    f = filing(xhtml_url=None)

    def discoverer(*args, **kwargs):
        return [f]

    def fetcher(url, **kwargs):
        return xbrl_bytes()

    payload = build_activity_evidence(LEI, discoverer=discoverer, fetcher=fetcher)
    assert payload["xbrl_activity"]["fact_count"] == 2
    assert payload["narrative_status"]["state"] == "unavailable"
    assert payload["narrative_evidence"] == []


def test_non_size_xhtml_errors_are_not_swallowed():
    f = filing()

    def discoverer(*args, **kwargs):
        return [f]

    def fetcher(url, **kwargs):
        if url.endswith(".json"):
            return xbrl_bytes()
        raise ValueError("only HTTPS evidence URLs are allowed")

    with pytest.raises(ValueError, match="only HTTPS"):
        build_activity_evidence(LEI, discoverer=discoverer, fetcher=fetcher)


def test_structured_json_oversize_remains_fatal():
    f = filing()

    def discoverer(*args, **kwargs):
        return [f]

    def fetcher(url, **kwargs):
        raise EvidenceTooLarge("evidence object exceeds byte limit")

    with pytest.raises(EvidenceTooLarge, match="byte limit"):
        build_activity_evidence(LEI, discoverer=discoverer, fetcher=fetcher)
