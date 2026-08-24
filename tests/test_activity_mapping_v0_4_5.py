from __future__ import annotations

import json
from pathlib import Path

import pytest

from zse_tool.activity_mapping import (
    NODE_BY_ID,
    TAXONOMY_VERSION,
    _load_json_bounded,
    map_annual_activity,
    pairwise_overlaps,
    validate_taxonomy,
)


def baseline(name: str, lei: str, text_facts: list[tuple[str, str, str]]) -> dict:
    facts = []
    for i, (category, concept, text) in enumerate(text_facts, 1):
        facts.append({
            "evidence_class": "R2_REPORTED_TEXT",
            "category": category,
            "fact_id": f"f{i}",
            "concept": concept,
            "period": "2025-01-01T00:00:00/2026-01-01T00:00:00",
            "language": "en",
            "text": text,
            "tables": [],
        })
    return {
        "mode": "read-only-annual-activity-baseline",
        "entity": {"lei": lei, "reported_name": name, "country": "XX"},
        "filing": {"period_end": "2025-12-31", "language": "en"},
        "selection": {"annuality": {"annual_like": True}, "candidate_audit": []},
        "tagged_activity": {"reported_activity_facts": facts},
        "provenance": {"xbrl_json": "https://filings.xbrl.org/x.json", "original_report_package": "https://filings.xbrl.org/x.zip"},
    }


def explicit_ids(mapped: dict) -> set[str]:
    return {row["node_id"] for row in mapped["explicit_activity_nodes"]}


def test_taxonomy_is_valid_and_versioned():
    validate_taxonomy()
    assert TAXONOMY_VERSION == "energy-electrical-v0.1"
    assert NODE_BY_ID["high_voltage_cables"].parent_id == "power_cables"
    assert NODE_BY_ID["wind_turbines"].parent_id == "wind_energy"


def test_mapping_rejects_nonannual_input():
    payload = baseline("X", "724500HDW6IWR9J5YT90", [])
    payload["selection"]["annuality"]["annual_like"] = False
    with pytest.raises(ValueError, match="annual-like"):
        map_annual_activity(payload)


def test_alfen_maps_grid_storage_ev_and_substations():
    payload = baseline(
        "Alfen N.V.",
        "724500HDW6IWR9J5YT90",
        [
            ("principal_activity", "ifrs:PrincipalActivities", "Products, systems and services related to the electricity grid, including Smart Grid Solutions, EV Charging and Energy Storage Systems."),
            ("revenue_business_line", "ifrs:Revenue", "Smart grid solutions. Revenue from standardised substations. Energy storage systems. EV charging."),
        ],
    )
    ids = explicit_ids(map_annual_activity(payload))
    assert {"electrical_grid", "grid_solutions", "substations", "energy_storage", "ev_charging"} <= ids


def test_schneider_french_maps_energy_management_distribution_and_automation():
    payload = baseline(
        "SCHNEIDER ELECTRIC SE",
        "969500A1YF1XUYYXS284",
        [
            ("principal_activity", "ifrs:PrincipalActivities", "Gestion de l’énergie; construction électrique; distribution électrique; alimentation électrique sécurisée; contrôle et automatismes industriels; contrôle, automatismes et sécurité des bâtiments; centres de données."),
        ],
    )
    ids = explicit_ids(map_annual_activity(payload))
    assert {"energy_management", "electrical_distribution", "secure_power", "industrial_automation", "building_automation", "data_center_infrastructure"} <= ids


def test_prysmian_maps_power_cable_grid_and_digital_activities():
    payload = baseline(
        "PRYSMIAN S.P.A.",
        "529900X0H1IO3RS1A464",
        [
            ("principal_activity", "ifrs:PrincipalActivities", "The Group produces power and telecom cables and systems and related accessories."),
            ("operating_segments", "ifrs:ReportableSegments", "Transmission includes High Voltage Direct Current, Submarine Power and Submarine Telecom. The operating segments include Power Grid, Electrification and Digital Solutions."),
        ],
    )
    ids = explicit_ids(map_annual_activity(payload))
    assert {"power_cables", "high_voltage_cables", "submarine_power_cables", "cable_accessories", "telecom_cables", "electrical_grid", "electrification", "digital_solutions"} <= ids


def test_nkt_maps_high_voltage_power_cables_without_wind():
    payload = baseline(
        "NKT A/S",
        "529900197LKWCEQ0NL18",
        [
            ("principal_activity", "ifrs:PrincipalActivities", "Solutions serves the global high-voltage power cable market and delivers technology-leading cable solutions."),
        ],
    )
    ids = explicit_ids(map_annual_activity(payload))
    assert "high_voltage_cables" in ids
    assert "power_cables" in ids
    assert "wind_turbines" not in ids


def test_vestas_maps_wind_business_but_not_grid_business_from_grid_connection_phrase():
    payload = baseline(
        "VESTAS WIND SYSTEMS A/S",
        "549300DYMC8BGZZC8844",
        [
            ("operating_segments", "ifrs:ReportableSegments", "Power Solutions comprises design, development, manufacturing and sale of onshore and offshore wind turbines and construction of wind power plants. Development includes greenfield project development, project maturation and securing grid connection. Wind energy service solutions include operation and maintenance and fleet optimisation."),
        ],
    )
    ids = explicit_ids(map_annual_activity(payload))
    assert {"wind_turbines", "wind_power_plants", "wind_project_development", "wind_services"} <= ids
    assert "electrical_grid" not in ids
    assert "grid_solutions" not in ids


def test_pairwise_overlap_separates_specific_from_broad_ancestors():
    alfen = map_annual_activity(baseline(
        "Alfen", "724500HDW6IWR9J5YT90",
        [("principal_activity", "x", "electricity grid Smart Grid Solutions EV Charging Energy Storage Systems")],
    ))
    nkt = map_annual_activity(baseline(
        "NKT", "529900197LKWCEQ0NL18",
        [("principal_activity", "x", "high-voltage power cable market")],
    ))
    vestas = map_annual_activity(baseline(
        "Vestas", "549300DYMC8BGZZC8844",
        [("operating_segments", "x", "wind turbines and wind energy service solutions")],
    ))
    rows = pairwise_overlaps([alfen, nkt, vestas])
    an = next(r for r in rows if {r["left_name"], r["right_name"]} == {"Alfen", "NKT"})
    av = next(r for r in rows if {r["left_name"], r["right_name"]} == {"Alfen", "Vestas"})
    assert any(x["node_id"] == "electrical_grid" for x in an["shared_specific_ancestors"])
    assert not av["shared_specific_ancestors"]
    assert any(x["node_id"] == "energy_infrastructure" for x in av["shared_broad_ancestors"])


def test_input_loader_is_bounded(tmp_path: Path):
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"x": "y" * 100}))
    with pytest.raises(ValueError, match="byte limit"):
        _load_json_bounded(p, max_bytes=10)
