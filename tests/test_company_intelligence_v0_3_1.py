from pathlib import Path

import pytest

from zse_tool.company_intelligence import (
    build_peer_candidates,
    load_bundled_profile,
    profile_history,
    profile_quality,
    seed_all_bundled_profiles,
    seed_bundled_profile,
    validate_profile_bundle,
)
from zse_tool.storage import Database


def test_all_four_bundled_profiles_validate(tmp_path: Path):
    db = Database(tmp_path / "zse.sqlite")
    for ticker in ("GRNL", "KOEI", "PODR", "HT"):
        payload = validate_profile_bundle(db, load_bundled_profile(ticker))
        assert payload["profile"]["ticker"] == ticker
        assert payload["activities"]


def test_new_profiles_capture_distinct_business_models(tmp_path: Path):
    db = Database(tmp_path / "zse.sqlite")
    for ticker in ("KOEI", "PODR", "HT"):
        seed_bundled_profile(db, ticker)
    koei = {r["segment_key"]: r for r in db.company_segments("KOEI")}
    assert koei["td"]["revenue_eur"] == pytest.approx(985_606_000)
    podr = {r["segment_key"]: r for r in db.company_segments("PODR")}
    assert podr["food"]["revenue_share"] == pytest.approx(0.573)
    ht = {r["segment_key"]: r for r in db.company_segments("HT")}
    assert ht["mobile"]["revenue_eur"] == pytest.approx(628_900_000)


def test_profile_history_preserves_multiple_dates(tmp_path: Path):
    from zse_tool.company_intelligence import import_profile_bundle
    import copy
    db = Database(tmp_path / "zse.sqlite")
    p1 = load_bundled_profile("GRNL")
    p2 = copy.deepcopy(p1)
    p2["profile"]["profile_date"] = "2025-12-31"
    p2["profile"]["source_date"] = "2026-04-30"
    import_profile_bundle(db, p1)
    import_profile_bundle(db, p2)
    rows = profile_history(db, "GRNL")
    assert [r["profile_date"] for r in rows] == ["2025-12-31", "2024-12-31"]


def test_profile_quality_marks_grnl_stale_against_2026_financials(tmp_path: Path):
    db = Database(tmp_path / "zse.sqlite")
    seed_bundled_profile(db, "GRNL")
    # A minimal preferred report is enough to provide a latest financial date.
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO parsed_reports(local_path, issuer_code, period_end, year, quarter, consolidated, audited, parsed_at) VALUES (?,?,?,?,?,?,?,?)",
            ("/tmp/grnl.xlsx", "GRNL", "2026-06-30", 2026, 2, 1, 0, "2026-07-31T00:00:00+00:00"),
        )
    q = profile_quality(db, "GRNL")
    assert q["latest_financial_period_end"] == "2026-06-30"
    assert q["freshness"] == "STALE"
    assert "business profile is stale" in " ".join(q["gaps"])


def test_peer_engine_uses_broad_nace_division_as_weak_not_exact_overlap(tmp_path: Path):
    db = Database(tmp_path / "zse.sqlite")
    seed_all_bundled_profiles(db)
    peers = build_peer_candidates(db, "GRNL", limit=10)
    podr = next(p for p in peers if p.ticker == "PODR")
    assert podr.activity_score > 0
    assert podr.activity_score < 40
    assert not podr.exact_overlap
    assert any("division" in x for x in podr.group_overlap)
