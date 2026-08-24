from __future__ import annotations

import json
from pathlib import Path

import pytest

from zse_tool.market_profile import (
    build_commercial_search_profile,
    read_json_bounded,
    validate_evidence_document,
)


def _doc() -> dict:
    return {
        "schema_version": "commercial-market-evidence-v0.1",
        "company": {"name": "Example", "ticker": "EX"},
        "evidence": [
            {
                "evidence_id": "scope",
                "evidence_class": "R1_REPORTED_NUMERIC",
                "kind": "business_scope",
                "period": "2025-FY",
                "scope": {"level": "group_division", "name": "Grid"},
                "metrics": {"share_total_revenue_pct": 70},
                "activity_terms": ["transformers", "substations"],
                "source": {"url": "https://example.com/a", "title": "A", "published": "2026-01-01"},
            },
            {
                "evidence_id": "de-rev-2025",
                "evidence_class": "R1_REPORTED_NUMERIC",
                "kind": "geography_revenue",
                "period": "2025-FY",
                "scope": {"level": "group"},
                "market": {"iso2": "DE", "name": "Germany"},
                "metrics": {"revenue_eur_m": 100},
                "activity_terms": [],
                "source": {"url": "https://example.com/b", "title": "B", "published": "2026-01-01"},
            },
        ],
    }


def test_validation_rejects_non_https_sources():
    doc = _doc()
    doc["evidence"][0]["source"]["url"] = "http://example.com/a"
    with pytest.raises(ValueError, match="HTTPS"):
        validate_evidence_document(doc)


def test_group_geography_does_not_become_direct_activity_market_link():
    profile = build_commercial_search_profile(_doc())
    de = profile["market_anchors"][0]
    assert de["direct_activity_market_link"] is False
    assert de["direct_activity_terms"] == []
    assert profile["search_hypotheses"][0]["evidence_class"] == "H1_SEARCH_HYPOTHESIS"
    assert "does not prove" in profile["search_hypotheses"][0]["basis"]


def test_direct_contract_creates_confirmed_activity_market_example():
    doc = _doc()
    doc["evidence"].append({
        "evidence_id": "de-project",
        "evidence_class": "R2_REPORTED_TEXT",
        "kind": "contract_project",
        "period": "2026-01",
        "scope": {"level": "project"},
        "market": {"iso2": "DE", "name": "Germany"},
        "metrics": {},
        "activity_terms": ["125 MVA transformers"],
        "customer": "Utility",
        "project": "Project X",
        "source": {"url": "https://example.com/c", "title": "C", "published": "2026-01-02"},
    })
    profile = build_commercial_search_profile(doc)
    de = profile["market_anchors"][0]
    assert de["status"] == "DIRECT_ACTIVITY_MARKET_EVIDENCE"
    assert de["direct_activity_terms"] == ["125 MVA transformers"]
    assert profile["confirmed_activity_market_examples"][0]["customer"] == "Utility"


def test_repeated_reported_market_is_recognized_without_claiming_activity():
    doc = _doc()
    extra = json.loads(json.dumps(doc["evidence"][1]))
    extra["evidence_id"] = "de-rev-2024"
    extra["period"] = "2024-FY"
    doc["evidence"].append(extra)
    profile = build_commercial_search_profile(doc)
    de = profile["market_anchors"][0]
    assert de["status"] == "REPEATED_REPORTED_MARKET"
    assert de["reported_revenue_periods"] == ["2024-FY", "2025-FY"]
    assert de["direct_activity_market_link"] is False


def test_sub_50pct_scope_is_not_used_as_core_search_scope():
    doc = _doc()
    doc["evidence"][0]["metrics"]["share_total_revenue_pct"] = 49.9
    profile = build_commercial_search_profile(doc)
    assert profile["business_anchors"][0]["is_core_scope"] is False
    assert profile["search_hypotheses"] == []


def test_policy_explicitly_disables_peer_competitor_and_llm_decisions():
    profile = build_commercial_search_profile(_doc())
    policy = profile["policy"]
    assert policy["automatic_database_writes"] is False
    assert policy["automatic_competitor_assignment"] is False
    assert policy["automatic_peer_assignment"] is False
    assert policy["automatic_similarity_scoring"] is False
    assert policy["llm_used"] is False
    assert policy["group_geography_is_not_segment_geography"] is True


def test_bounded_reader_rejects_large_input(tmp_path: Path):
    p = tmp_path / "x.json"
    p.write_text("{}" + " " * 100)
    with pytest.raises(ValueError, match="byte limit"):
        read_json_bounded(p, max_bytes=10)


def test_koei_example_preserves_market_scope_and_direct_links():
    example = Path(__file__).resolve().parents[1] / "examples" / "koei_market_evidence_v0_4_6.json"
    payload = read_json_bounded(example)
    profile = build_commercial_search_profile(payload)
    markets = {row["market"]["iso2"]: row for row in profile["market_anchors"]}
    assert {"DE", "SE", "NO", "NL", "AT", "RO"}.issubset(markets)
    assert markets["DE"]["direct_activity_market_link"] is True
    assert markets["SE"]["direct_activity_market_link"] is True
    assert markets["NO"]["direct_activity_market_link"] is True
    assert markets["NL"]["direct_activity_market_link"] is False
    assert markets["RO"]["direct_activity_market_link"] is False
    core = [row for row in profile["business_anchors"] if row["is_core_scope"]]
    assert len(core) == 1
    assert core[0]["metrics"]["share_total_revenue_pct"] == 74.6
