from __future__ import annotations

import json
from pathlib import Path

import pytest

from zse_tool.commercial_trajectory import TrajectoryError, build_trajectory, main


def _obs(oid, metric, year, basis, amount, *, market=None, scope="KONCAR Group", precision="exact"):
    row = {
        "observation_id": oid,
        "metric": metric,
        "period": {"label": f"{year}-{basis}", "basis": basis, "year": year, "end": f"{year}-12-31"},
        "scope": {"level": "group", "name": scope},
        "value": {"amount": amount, "currency": "EUR", "scale": "million", "unit": "currency", "precision": precision},
        "evidence_class": "R1_REPORTED_METRIC",
        "source": {"title": "official", "url": "https://example.invalid"},
    }
    if market:
        row["market"] = {"iso2": market, "name": market}
    return row


def _data(rows):
    return {"schema_version": "commercial-trajectory-evidence-v0.1", "company": {"name": "KONCAR Group", "ticker": "KOEI"}, "observations": rows}


def test_same_basis_yoy_and_cagr():
    r = build_trajectory(_data([
        _obs("a", "market_revenue", 2023, "FY", 100, market="DE"),
        _obs("b", "market_revenue", 2024, "FY", 120, market="DE"),
        _obs("c", "market_revenue", 2025, "FY", 180, market="DE"),
    ]))
    s = r["series"][0]
    assert s["summary"]["monotonic_direction"] == "INCREASING"
    assert s["summary"]["first_to_latest_pct"] == 80.0
    assert round(s["summary"]["cagr_pct"], 3) == round(((1.8 ** 0.5) - 1) * 100, 3)
    assert s["points"][1]["derived"]["yoy_pct"] == 20.0
    assert s["points"][2]["derived"]["yoy_pct"] == 50.0


def test_fy_and_h1_are_separate_series_and_never_compared():
    r = build_trajectory(_data([
        _obs("a", "market_revenue", 2025, "FY", 188.3, market="DE"),
        _obs("b", "market_revenue", 2026, "H1", 124.4, market="DE"),
    ]))
    assert r["series_count"] == 2
    assert all(not s["points"][0]["derived"] for s in r["series"])


def test_missing_prior_year_does_not_create_yoy():
    r = build_trajectory(_data([
        _obs("a", "market_revenue", 2023, "FY", 100, market="DE"),
        _obs("c", "market_revenue", 2025, "FY", 180, market="DE"),
    ]))
    assert "yoy_pct" not in r["series"][0]["points"][1]["derived"]


def test_market_and_group_metrics_do_not_merge():
    r = build_trajectory(_data([
        _obs("a", "market_revenue", 2025, "FY", 188.3, market="DE"),
        _obs("b", "group_revenue", 2025, "FY", 1319.6),
    ]))
    assert r["series_count"] == 2


def test_duplicate_ids_rejected():
    row = _obs("dup", "group_revenue", 2025, "FY", 1)
    with pytest.raises(TrajectoryError, match="duplicate"):
        build_trajectory(_data([row, dict(row)]))


def test_bad_period_basis_rejected():
    row = _obs("a", "group_revenue", 2025, "FY", 1)
    row["period"]["basis"] = "TTM"
    with pytest.raises(TrajectoryError, match="period basis"):
        build_trajectory(_data([row]))


def test_policy_disallows_market_share_inference():
    r = build_trajectory(_data([_obs("a", "market_revenue", 2025, "FY", 188.3, market="DE")]))
    assert r["policy"]["market_revenue_is_not_market_share"] is True
    assert r["policy"]["competitor_displacement_is_not_inferred"] is True
    assert r["policy"]["automatic_database_writes"] is False


def test_cli_writes_output_and_filter(tmp_path: Path):
    inp = tmp_path / "in.json"
    out = tmp_path / "out.json"
    inp.write_text(json.dumps(_data([
        _obs("a", "market_revenue", 2025, "FY", 188.3, market="DE"),
        _obs("b", "market_revenue", 2025, "FY", 119.8, market="SE"),
    ])))
    assert main(["--input", str(inp), "--output", str(out), "--market", "DE"]) == 0
    data = json.loads(out.read_text())
    assert data["series_count"] == 1
    assert data["series"][0]["series"]["market"]["iso2"] == "DE"


def test_source_registry_reference_is_validated():
    row = _obs("a", "group_revenue", 2025, "FY", 1)
    row.pop("source")
    row["source_id"] = "OFFICIAL"
    data = _data([row])
    data["sources"] = {"OFFICIAL": {"title": "official", "url": "https://example.invalid"}}
    assert build_trajectory(data)["source_count"] == 1
    data["observations"][0]["source_id"] = "MISSING"
    with pytest.raises(TrajectoryError, match="unknown source_id"):
        build_trajectory(data)
