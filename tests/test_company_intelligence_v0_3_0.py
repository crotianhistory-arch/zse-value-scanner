from __future__ import annotations

import copy
from pathlib import Path

import pytest

from zse_tool.company_intelligence import (
    ProfileValidationError,
    build_peer_candidates,
    ensure_bundled_taxonomies,
    load_bundled_profile,
    profile_as_dict,
    seed_bundled_profile,
    validate_profile_bundle,
)
from zse_tool.storage import Database


def test_official_taxonomy_seed_contains_grnl_activity_codes(tmp_path: Path):
    db = Database(tmp_path / "zse.sqlite")
    ensure_bundled_taxonomies(db)
    assert db.classification_code_exists("NACE", "2.1", "10.61")
    assert db.classification_code_exists("NACE", "2.1", "10.51")
    assert db.classification_code_exists("NKD", "2025", "46.21.0")
    rows = db.classification_codes("NACE", "2.1", "grain")
    assert any(r["code"] == "10.61" for r in rows)


def test_profile_validator_rejects_invented_classification_code(tmp_path: Path):
    db = Database(tmp_path / "zse.sqlite")
    payload = copy.deepcopy(load_bundled_profile("GRNL"))
    payload["activities"][0]["classifications"][0]["code"] = "99.99"
    with pytest.raises(ProfileValidationError, match="unknown NACE 2.1 code"):
        validate_profile_bundle(db, payload)


def test_grnl_seed_preserves_reported_segment_mix_and_assignment_status(tmp_path: Path):
    db = Database(tmp_path / "zse.sqlite")
    seed_bundled_profile(db, "GRNL")
    data = profile_as_dict(db, "GRNL")
    assert data["profile"]["method"] == "audited-report-grounded"
    segments = {x["segment_key"]: x for x in data["segments"]}
    assert segments["milling"]["revenue_eur"] == pytest.approx(44_585_000)
    assert segments["dairy"]["revenue_eur"] == pytest.approx(32_809_000)
    assert sum(float(x["revenue_share"]) for x in data["segments"]) == pytest.approx(1.0, abs=1e-6)

    activities = {x["activity_key"]: x for x in data["activities"]}
    milling_nace = next(c for c in activities["milling"]["classifications"] if c["scheme"] == "NACE")
    assert milling_nace["code"] == "10.61"
    assert milling_nace["assignment_status"] == "analytical-mapping"
    assert "official-registry" not in milling_nace["assignment_status"]


def _synthetic_profile(ticker: str, code: str, weight: float = 1.0) -> dict:
    return {
        "profile": {
            "ticker": ticker,
            "profile_date": "2024-12-31",
            "legal_name": f"{ticker} d.d.",
            "summary": "test",
            "method": "test-evidence",
            "confidence": 1.0,
            "source_url": "https://example.test/report",
            "source_date": "2025-04-30",
        },
        "activities": [
            {
                "key": "core",
                "name": "core",
                "role": "material-segment",
                "weight": weight,
                "weight_basis": "sales",
                "method": "reported-segment-mapped",
                "confidence": 1.0,
                "source_url": "https://example.test/report",
                "source_date": "2025-04-30",
                "evidence": "test evidence",
                "classifications": [
                    {
                        "scheme": "NACE",
                        "version": "2.1",
                        "code": code,
                        "assignment_status": "analytical-mapping",
                        "method": "reported-segment-mapped",
                        "confidence": 1.0,
                        "source_url": "https://example.test/report",
                        "source_date": "2025-04-30",
                        "evidence": "test evidence",
                    }
                ],
            }
        ],
        "segments": [], "products": [], "geographies": [], "capacities": [], "subsidiaries": [],
    }


def test_peer_candidates_rank_exact_activity_overlap_above_unrelated(tmp_path: Path):
    from zse_tool.company_intelligence import import_profile_bundle

    db = Database(tmp_path / "zse.sqlite")
    seed_bundled_profile(db, "GRNL")
    import_profile_bundle(db, _synthetic_profile("MILL", "10.61"))
    import_profile_bundle(db, _synthetic_profile("STORE", "52.10"))
    peers = build_peer_candidates(db, "GRNL", limit=10)
    assert peers[0].ticker == "MILL"
    assert peers[0].activity_score > next(p.activity_score for p in peers if p.ticker == "STORE")


def test_profile_as_of_uses_latest_profile_not_after_cutoff(tmp_path: Path):
    from zse_tool.company_intelligence import import_profile_bundle

    db = Database(tmp_path / "zse.sqlite")
    first = _synthetic_profile("TEST", "10.61")
    first["profile"]["profile_date"] = "2024-12-31"
    second = copy.deepcopy(first)
    second["profile"]["profile_date"] = "2025-12-31"
    second["profile"]["summary"] = "new"
    import_profile_bundle(db, first)
    import_profile_bundle(db, second)
    data = profile_as_dict(db, "TEST", as_of="2025-06-30")
    assert data["profile"]["profile_date"] == "2024-12-31"
