from __future__ import annotations

import gzip
import json

import pytest

from zse_tool.esef import ESEFFiling, discover_filings, select_latest_esef
from zse_tool.esef_activity import (
    _fetch_bounded,
    _maybe_gunzip,
    build_activity_evidence,
    extract_narrative_evidence,
    inventory_xbrl_activity,
    parse_xbrl_json,
)

LEI = "2138001CNF45JP5XZK38"


def filing(**overrides):
    data = dict(
        api_id="f1",
        filing_index=f"{LEI}-2024-12-31-ESEF-FI-0",
        country="FI",
        period_end="2024-12-31",
        language="en",
        date_added="2025-02-01",
        processed="2025-02-02",
        entity_name="Example Oyj",
        entity_identifier=LEI,
        error_count=0,
        warning_count=1,
        inconsistency_count=0,
        package_sha256="abc",
        json_url="https://filings.xbrl.org/x.json.gz",
        package_url="https://filings.xbrl.org/x.zip",
        xhtml_url="https://filings.xbrl.org/x.xhtml.gz",
        viewer_url="https://filings.xbrl.org/viewer",
        api_source_url="https://filings.xbrl.org/api/filings?...",
    )
    data.update(overrides)
    return ESEFFiling(**data)


def api_payload(identifier=LEI):
    return {
        "data": [{
            "type": "filing",
            "id": "f1",
            "attributes": {
                "country": "FI",
                "fxo_id": f"{LEI}-2024-12-31-ESEF-FI-0",
                "period_end": "2024-12-31",
                "error_count": 0,
                "warning_count": 2,
                "inconsistency_count": 1,
                "date_added": "2025-02-01",
                "processed": "2025-02-02",
                "json_url": "https://filings.xbrl.org/foo-2024-12-31-en.json.gz",
                "package_url": "https://filings.xbrl.org/foo-2024-12-31-en.zip",
                "report_url": "https://filings.xbrl.org/foo-2024-12-31-en.xhtml.gz",
                "viewer_url": "https://filings.xbrl.org/viewer",
                "sha256": "deadbeef",
            },
            "relationships": {"entity": {"data": {"type": "entity", "id": "e1"}}},
        }],
        "included": [{
            "type": "entity",
            "id": "e1",
            "attributes": {"identifier": identifier, "name": "Example Oyj"},
        }],
    }


def test_discover_is_exact_lei_bounded_query():
    seen = {}
    class Response:
        url = "https://filings.xbrl.org/api/filings?x"
        def raise_for_status(self): pass
        def json(self): return api_payload()
    class Session:
        def get(self, url, **kwargs):
            seen["url"] = url
            seen.update(kwargs)
            return Response()
    rows = discover_filings(LEI, limit=7, session=Session())
    assert seen["params"]["filter[entity.identifier]"] == LEI
    assert seen["params"]["page[size]"] == 7
    assert seen["params"]["include"] == "entity"
    assert rows[0].entity_name == "Example Oyj"
    assert rows[0].language == "en"
    assert rows[0].package_sha256 == "deadbeef"


def test_discover_rejects_entity_mismatch():
    class Response:
        url = "x"
        def raise_for_status(self): pass
        def json(self): return api_payload("549300A0JPRWG1KI7U06")
    class Session:
        def get(self, *a, **k): return Response()
    with pytest.raises(ValueError, match="entity mismatch"):
        discover_filings(LEI, session=Session())


def test_discover_limit_is_bounded():
    with pytest.raises(ValueError, match="limit"):
        discover_filings(LEI, limit=999, session=object())


def test_latest_esef_prefers_latest_period_then_english():
    rows = [
        filing(api_id="old", period_end="2023-12-31", language="en"),
        filing(api_id="new-fi", period_end="2024-12-31", language="fi"),
        filing(api_id="new-en", period_end="2024-12-31", language="en"),
    ]
    assert select_latest_esef(rows).api_id == "new-en"


def test_latest_esef_ignores_non_esef():
    rows = [filing(filing_index=f"{LEI}-2025-12-31-UKSEF-GB-0")]
    with pytest.raises(ValueError, match="no ESEF"):
        select_latest_esef(rows)


def sample_xbrl():
    return {
        "documentInfo": {"documentType": "https://xbrl.org/2021/xbrl-json"},
        "facts": {
            "f1": {
                "value": "1200000000",
                "dimensions": {
                    "concept": "ifrs-full:Revenue",
                    "entity": f"lei:{LEI}",
                    "period": "2024-01-01T00:00:00/2025-01-01T00:00:00",
                    "unit": "iso4217:EUR",
                    "ex:OperatingSegmentsAxis": "ex:GridSolutionsMember",
                },
            },
            "f2": {
                "value": "Grid Solutions designs transformers, switchgear and substations for utilities.",
                "dimensions": {
                    "concept": "ex:DescriptionOfBusinessSegments",
                    "entity": f"lei:{LEI}",
                    "period": "2024-01-01T00:00:00/2025-01-01T00:00:00",
                    "language": "en",
                },
            },
            "f3": {
                "value": "10",
                "dimensions": {
                    "concept": "ifrs-full:Assets",
                    "entity": f"lei:{LEI}",
                    "period": "2025-01-01T00:00:00",
                    "unit": "iso4217:EUR",
                    "ex:UnrelatedAxis": "ex:ClassAMember",
                },
            },
        },
    }


def test_parse_xbrl_json_accepts_gzip():
    raw = json.dumps(sample_xbrl()).encode()
    assert len(parse_xbrl_json(gzip.compress(raw))["facts"]) == 3


def test_inventory_extracts_segment_dimension_and_numeric_fact():
    inv = inventory_xbrl_activity(sample_xbrl())
    seg = next(x for x in inv["custom_dimensions"] if x["dimension"] == "ex:OperatingSegmentsAxis")
    assert seg["activity_relevance"] is True
    assert seg["members"][0]["member"] == "ex:GridSolutionsMember"
    assert inv["numeric_activity_facts"][0]["concept"] == "ifrs-full:Revenue"
    assert inv["numeric_activity_facts"][0]["evidence_class"] == "R1_REPORTED_XBRL_FACT"


def test_inventory_preserves_non_activity_dimension_without_promoting_it():
    inv = inventory_xbrl_activity(sample_xbrl())
    unrelated = next(x for x in inv["custom_dimensions"] if x["dimension"] == "ex:UnrelatedAxis")
    assert unrelated["activity_relevance"] is False


def test_inventory_extracts_tagged_text_candidate():
    inv = inventory_xbrl_activity(sample_xbrl())
    assert inv["text_activity_facts"][0]["concept"] == "ex:DescriptionOfBusinessSegments"
    assert "transformers" in inv["text_activity_facts"][0]["text"]


def test_narrative_windows_are_heuristic_not_reported_fact():
    html = b"<html><body><h2>Operating segments</h2><p>Grid Solutions makes transformers and switchgear.</p></body></html>"
    rows = extract_narrative_evidence(html)
    assert rows
    assert rows[0].evidence_class == "H1_HEURISTIC_ACTIVITY_CANDIDATE"
    assert "Grid Solutions" in rows[0].text


def test_fetch_bounded_stops_oversized_content_length():
    class Response:
        headers = {"Content-Length": "1000"}
        def raise_for_status(self): pass
        def iter_content(self, chunk_size): yield b"x"
    class Session:
        def get(self, *a, **k): return Response()
    with pytest.raises(ValueError, match="byte limit"):
        _fetch_bounded("https://example.test/x", max_bytes=10, session=Session())


def test_gzip_decode_is_bounded():
    compressed = gzip.compress(b"x" * 100)
    with pytest.raises(ValueError, match="decompressed evidence"):
        _maybe_gunzip(compressed, max_output_bytes=10)


def test_build_activity_pack_is_read_only_and_provenanced():
    f = filing()
    raw_json = gzip.compress(json.dumps(sample_xbrl()).encode())
    raw_html = gzip.compress(b"<h2>Products and services</h2><p>Transformers and grid equipment.</p>")
    def discoverer(*a, **k): return [f]
    def fetcher(url, **kwargs): return raw_json if url.endswith("json.gz") else raw_html
    payload = build_activity_evidence(LEI, discoverer=discoverer, fetcher=fetcher)
    assert payload["mode"] == "read-only-external-activity-evidence"
    assert payload["policy"]["automatic_database_writes"] is False
    assert payload["policy"]["automatic_peer_assignment"] is False
    assert payload["policy"]["llm_used"] is False
    assert payload["provenance"]["original_report_package"] == f.package_url
    assert payload["entity"]["lei"] == LEI
    assert payload["narrative_evidence"]
