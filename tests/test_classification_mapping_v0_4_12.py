from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from zse_tool.classification_mapping import (
    CATALOG_SCHEMA_VERSION,
    ClassificationMappingError,
    catalog_status,
    load_catalog,
    translate_activity_profile,
    translate_node,
)


def make_reference_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE classification_items (
            scheme_key TEXT NOT NULL,
            code TEXT NOT NULL,
            uri TEXT NOT NULL,
            parent_code TEXT,
            level INTEGER NOT NULL,
            PRIMARY KEY (scheme_key, code)
        );
        CREATE TABLE classification_labels (
            scheme_key TEXT NOT NULL,
            code TEXT NOT NULL,
            language TEXT NOT NULL,
            kind TEXT NOT NULL,
            label TEXT NOT NULL,
            PRIMARY KEY (scheme_key, code, language, kind, label)
        );
        """
    )
    items = [
        ("NACE_REV_2_1", "27.11", "http://data.europa.eu/ux2/nace2.1/2711", "27.1", 4),
        ("NACE_REV_2_1", "27.12", "http://data.europa.eu/ux2/nace2.1/2712", "27.1", 4),
        ("NACE_REV_2_1", "27.32", "http://data.europa.eu/ux2/nace2.1/2732", "27.3", 4),
        ("CPA_2_2", "27.11.4", "http://data.europa.eu/ehl/cpa22/27114", "27.11", 5),
    ]
    labels = [
        ("NACE_REV_2_1", "27.11", "en", "skos:prefLabel",
         "27.11 Manufacture of electric motors, generators and transformers"),
        ("NACE_REV_2_1", "27.12", "en", "skos:prefLabel",
         "27.12 Manufacture of electricity distribution and control apparatus"),
        ("NACE_REV_2_1", "27.32", "en", "skos:prefLabel",
         "27.32 Manufacture of other electronic and electric wires and cables"),
        ("CPA_2_2", "27.11.4", "en", "skos:prefLabel",
         "27.11.4 Electrical transformers"),
    ]
    conn.executemany(
        "INSERT INTO classification_items VALUES (?, ?, ?, ?, ?)",
        items,
    )
    conn.executemany(
        "INSERT INTO classification_labels VALUES (?, ?, ?, ?, ?)",
        labels,
    )
    conn.commit()
    conn.close()


@pytest.fixture()
def catalog_path() -> Path:
    return Path("examples/activity_classification_mapping_catalog_v0_4_12.json")


@pytest.fixture()
def ref_db(tmp_path: Path) -> Path:
    path = tmp_path / "classification.sqlite"
    make_reference_db(path)
    return path


def targets_by_system(result: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for row in result["targets"]:
        out.setdefault(row["target"]["system"], []).append(row)
    return out


def test_catalog_loads_and_is_versioned(catalog_path: Path):
    catalog = load_catalog(catalog_path)
    assert catalog["schema_version"] == CATALOG_SCHEMA_VERSION
    assert catalog["taxonomy_version"] == "energy-electrical-v0.1"


def test_transformers_translate_to_nace_cpa_isic5_and_naics2022(catalog_path: Path, ref_db: Path):
    result = translate_node("transformers", load_catalog(catalog_path), ref_db)
    systems = targets_by_system(result)
    assert systems["NACE"][0]["target"]["code"] == "27.11"
    assert systems["NACE"][0]["target"]["uri"].endswith("/2711")
    assert systems["CPA"][0]["target"]["code"] == "27.11.4"
    assert systems["CPA"][0]["target"]["label"].endswith("Electrical transformers")
    assert systems["ISIC"][0]["target"]["code"] == "2710"
    assert systems["ISIC"][0]["target"]["validation"] == "embedded_official_reference"
    assert systems["ISIC"][0]["evidence_class"] == "A3_ANALYTICAL_CLASSIFICATION_MAPPING"
    assert systems["NAICS"][0]["target"]["code"] == "335311"
    assert systems["NAICS"][0]["relation"] == "close_activity_match"


def test_switchgear_shows_nace_detail_collapsing_into_isic2710(catalog_path: Path, ref_db: Path):
    catalog = load_catalog(catalog_path)
    transformers = translate_node("transformers", catalog, ref_db)
    switchgear = translate_node("switchgear", catalog, ref_db)
    trans_nace = next(x for x in transformers["targets"] if x["target"]["system"] == "NACE")
    swgr_nace = next(x for x in switchgear["targets"] if x["target"]["system"] == "NACE")
    trans_isic = next(x for x in transformers["targets"] if x["target"]["system"] == "ISIC")
    swgr_isic = next(x for x in switchgear["targets"] if x["target"]["system"] == "ISIC")
    assert trans_nace["target"]["code"] == "27.11"
    assert swgr_nace["target"]["code"] == "27.12"
    assert trans_isic["target"]["code"] == swgr_isic["target"]["code"] == "2710"


def test_cable_translation_exposes_expected_loss_of_detail(catalog_path: Path, ref_db: Path):
    catalog = load_catalog(catalog_path)
    hv = translate_node("high_voltage_cables", catalog, ref_db)
    sub = translate_node("submarine_power_cables", catalog, ref_db)
    hv_nace = next(x for x in hv["targets"] if x["target"]["system"] == "NACE")
    sub_nace = next(x for x in sub["targets"] if x["target"]["system"] == "NACE")
    hv_isic = next(x for x in hv["targets"] if x["target"]["system"] == "ISIC")
    sub_isic = next(x for x in sub["targets"] if x["target"]["system"] == "ISIC")
    assert hv_nace["target"]["code"] == sub_nace["target"]["code"] == "27.32"
    assert hv_isic["target"]["code"] == sub_isic["target"]["code"] == "2732"
    assert hv_nace["relation"] == "broader_activity_match"
    assert sub_isic["relation"] == "broader_activity_match"


def test_profile_translates_explicit_nodes_only(catalog_path: Path, ref_db: Path):
    profile = {
        "taxonomy_version": "energy-electrical-v0.1",
        "entity": {"reported_name": "Pilot Transformer Co.", "lei": "TEST"},
        "filing": {"period_end": "2025-12-31"},
        "explicit_activity_nodes": [
            {"node_id": "transformers"},
            {"node_id": "energy_management"},
        ],
        "derived_ancestor_nodes": [
            {"node_id": "grid_equipment"},
            {"node_id": "electrical_grid"},
        ],
    }
    result = translate_activity_profile(profile, load_catalog(catalog_path), ref_db)
    assert result["translated_source_nodes"] == ["transformers"]
    assert result["unmapped_explicit_node_ids"] == ["energy_management"]
    assert all(row["source_node_id"] == "transformers" for row in result["translations"])
    assert result["policy"]["derived_ancestors_translated"] is False
    assert result["policy"]["company_classification_inferred"] is False


def test_catalog_status_validates_all_reference_db_targets(catalog_path: Path, ref_db: Path):
    status = catalog_status(load_catalog(catalog_path), ref_db)
    assert status["mapped_node_count"] == 5
    assert status["system_mapping_counts"] == {
        "CPA 2.2": 1,
        "ISIC 5": 5,
        "NACE 2.1": 5,
        "NAICS 2022": 5,
    }


def test_missing_reference_code_is_rejected(catalog_path: Path, ref_db: Path, tmp_path: Path):
    catalog = load_catalog(catalog_path)
    broken = json.loads(json.dumps(catalog))
    target = next(x for x in broken["mappings"] if x["system"] == "NACE")
    target["code"] = "99.99"
    with pytest.raises(ClassificationMappingError, match="missing from reference DB"):
        translate_node(target["node_id"], broken, ref_db)


def test_profile_taxonomy_mismatch_is_rejected(catalog_path: Path, ref_db: Path):
    profile = {
        "taxonomy_version": "something-else",
        "explicit_activity_nodes": [],
    }
    with pytest.raises(ClassificationMappingError, match="taxonomy mismatch"):
        translate_activity_profile(profile, load_catalog(catalog_path), ref_db)


def test_external_source_host_is_allowlisted(catalog_path: Path, tmp_path: Path):
    obj = json.loads(catalog_path.read_text())
    row = next(x for x in obj["mappings"] if x["system"] == "ISIC")
    row["source_url"] = "https://example.com/not-official"
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(obj))
    with pytest.raises(ClassificationMappingError, match="not allow-listed"):
        load_catalog(p)
