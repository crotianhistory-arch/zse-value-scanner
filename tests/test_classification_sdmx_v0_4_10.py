from __future__ import annotations

import json
from pathlib import Path

import pytest

import zse_tool.classification_backbone as cb


STUBS = b'''<?xml version="1.0" encoding="UTF-8"?>
<mes:Structure xmlns:mes="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message"
               xmlns:str="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure"
               xmlns:com="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common">
  <mes:Structures>
    <str:Codelists>
      <str:Codelist id="NACE_R2_1" agencyID="ESTAT" version="4.0">
        <com:Name xml:lang="en">Statistical classification of economic activities - NACE Rev. 2.1</com:Name>
        <com:Name xml:lang="de">Statistische Systematik der Wirtschaftszweige</com:Name>
      </str:Codelist>
      <str:Codelist id="CPA22" agencyID="ESTAT" version="2.0">
        <com:Name xml:lang="en">Statistical classification of products by activity (CPA 2.2)</com:Name>
      </str:Codelist>
    </str:Codelists>
  </mes:Structures>
</mes:Structure>
'''


def _spec_nace() -> cb.SdmxSchemeSpec:
    return cb.SdmxSchemeSpec(
        key="NACE_TEST",
        system="NACE",
        version="2.1",
        title_contains="classification of economic activities",
        preferred_ids=("NACE_R2_1",),
        expected_item_count=4,
        expected_levels=4,
        expected_level_counts={1: 1, 2: 1, 3: 1, 4: 1},
    )


def _spec_cpa() -> cb.SdmxSchemeSpec:
    return cb.SdmxSchemeSpec(
        key="CPA_TEST",
        system="CPA",
        version="2.2",
        title_contains="classification of products by activity",
        preferred_ids=("CPA22", "CPA_2_2"),
        expected_item_count=6,
        expected_levels=6,
        expected_level_counts={1: 1, 2: 1, 3: 1, 4: 1, 5: 1, 6: 1},
    )


def test_sdmx_stub_parser_and_preferred_id_resolution():
    stubs = cb._parse_sdmx_codelist_stubs(STUBS)
    assert {x["id"] for x in stubs} == {"NACE_R2_1", "CPA22"}
    assert cb._resolve_sdmx_codelist(_spec_nace(), stubs)["id"] == "NACE_R2_1"
    assert cb._resolve_sdmx_codelist(_spec_cpa(), stubs)["id"] == "CPA22"


def test_tsv_parser_and_classification_filter_drop_statistical_aggregates():
    rows = cb._parse_codelist_tsv(
        b"TOTAL\tTotal\nC\tManufacturing\n27\tElectrical equipment\n27.1\tMotors, generators and transformers\n27.11\tManufacture of electric motors, generators and transformers\nB-S_X_O\tIndustry aggregate\n"
    )
    filtered = cb._classification_rows(rows, 4)
    assert [code for code, _ in filtered] == ["C", "27", "27.1", "27.11"]


def test_parent_derivation_uses_order_for_section_and_prefix_for_lower_levels():
    codes = ["C", "27", "27.1", "27.11", "27.11.1", "27.11.11"]
    parents = cb._derive_parent_codes(codes, 6)
    assert parents == {
        "C": None,
        "27": "C",
        "27.1": "27",
        "27.11": "27.1",
        "27.11.1": "27.11",
        "27.11.11": "27.11.1",
    }


def test_sdmx_normalizer_preserves_three_official_languages_and_no_notes():
    codelist = cb._parse_sdmx_codelist_stubs(STUBS)[0]
    labels = {
        "en": [("C", "Manufacturing"), ("27", "Electrical equipment"), ("27.1", "Motors and transformers"), ("27.11", "Manufacture of transformers")],
        "fr": [("C", "Industrie manufacturiere"), ("27", "Equipements electriques"), ("27.1", "Moteurs et transformateurs"), ("27.11", "Fabrication de transformateurs")],
        "de": [("C", "Verarbeitendes Gewerbe"), ("27", "Elektrische Ausruestungen"), ("27.1", "Motoren und Transformatoren"), ("27.11", "Herstellung von Transformatoren")],
    }
    normalized = cb._normalize_sdmx_scheme(_spec_nace(), codelist, labels)
    assert normalized["scheme"]["item_count"] == 4
    assert normalized["scheme"]["label_count"] == 12
    assert normalized["scheme"]["note_count"] == 0
    assert normalized["scheme"]["languages"] == ["de", "en", "fr"]
    item = next(x for x in normalized["items"] if x["code"] == "27.11")
    assert item["parent_code"] == "27.1"


def test_sdmx_normalizer_requires_exact_level_counts():
    codelist = cb._parse_sdmx_codelist_stubs(STUBS)[0]
    labels = {"en": [("C", "Manufacturing"), ("27", "Electrical equipment"), ("27.1", "Motors"), ("27.11", "Transformers")]}
    bad = cb.SdmxSchemeSpec(
        key="BAD", system="NACE", version="2.1", title_contains="nace", preferred_ids=("NACE_R2_1",),
        expected_item_count=4, expected_levels=4, expected_level_counts={1: 1, 2: 1, 3: 0, 4: 2},
    )
    with pytest.raises(cb.ClassificationError, match="level-count mismatch"):
        cb._normalize_sdmx_scheme(bad, codelist, labels)


def test_sdmx_catalog_requires_expected_counts_to_sum(tmp_path: Path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({
        "schema_version": "official-classification-catalog-v0.2",
        "transport": "eurostat-sdmx-codelist",
        "sdmx_base": "https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1",
        "languages": ["en", "fr", "de"],
        "schemes": [{
            "key": "X", "system": "NACE", "version": "2.1", "title_contains": "x",
            "preferred_ids": ["X"], "expected_item_count": 4, "expected_levels": 4,
            "expected_level_counts": {"1": 1, "2": 1, "3": 1, "4": 2}
        }]
    }))
    with pytest.raises(cb.ClassificationError, match="do not sum"):
        cb._catalog_v2_from_path(catalog)


def test_sdmx_sync_builds_reference_db_from_official_style_downloads(tmp_path: Path, monkeypatch):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({
        "schema_version": "official-classification-catalog-v0.2",
        "transport": "eurostat-sdmx-codelist",
        "sdmx_base": "https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1",
        "languages": ["en", "fr", "de"],
        "schemes": [
            {"key": "NACE_REV_2_1", "system": "NACE", "version": "2.1", "title_contains": "classification of economic activities", "preferred_ids": ["NACE_R2_1"], "expected_item_count": 4, "expected_levels": 4, "expected_level_counts": {"1": 1, "2": 1, "3": 1, "4": 1}},
            {"key": "CPA_2_2", "system": "CPA", "version": "2.2", "title_contains": "classification of products by activity", "preferred_ids": ["CPA22"], "expected_item_count": 6, "expected_levels": 6, "expected_level_counts": {"1": 1, "2": 1, "3": 1, "4": 1, "5": 1, "6": 1}}
        ]
    }))
    labels = {
        "NACE_R2_1": {
            "en": "C\tManufacturing\n27\tElectrical equipment\n27.1\tMotors and transformers\n27.11\tManufacture of transformers\nTOTAL\tTotal\n",
            "fr": "C\tIndustrie manufacturiere\n27\tEquipements electriques\n27.1\tMoteurs et transformateurs\n27.11\tFabrication de transformateurs\nTOTAL\tTotal\n",
            "de": "C\tVerarbeitendes Gewerbe\n27\tElektrische Ausruestungen\n27.1\tMotoren und Transformatoren\n27.11\tHerstellung von Transformatoren\nTOTAL\tInsgesamt\n",
        },
        "CPA22": {
            "en": "C\tManufactured products\n27\tElectrical equipment\n27.1\tMotors and transformers\n27.11\tMotors and transformers\n27.11.1\tMotors\n27.11.11\tTransformers\nTOTAL\tTotal\n",
            "fr": "C\tProduits manufactures\n27\tEquipements electriques\n27.1\tMoteurs et transformateurs\n27.11\tMoteurs et transformateurs\n27.11.1\tMoteurs\n27.11.11\tTransformateurs\nTOTAL\tTotal\n",
            "de": "C\tErzeugnisse\n27\tElektrische Ausruestungen\n27.1\tMotoren und Transformatoren\n27.11\tMotoren und Transformatoren\n27.11.1\tMotoren\n27.11.11\tTransformatoren\nTOTAL\tInsgesamt\n",
        },
    }

    def fake_get(url: str, *, accept: str, timeout: float = 60.0) -> bytes:
        if "all/latest?detail=allstubs" in url:
            return STUBS
        codelist_id = "NACE_R2_1" if "/NACE_R2_1/" in url else "CPA22"
        lang = url.rsplit("lang=", 1)[1]
        return labels[codelist_id][lang].encode()

    monkeypatch.setattr(cb, "_http_get", fake_get)
    db = tmp_path / "ref.sqlite"
    result = cb.sync(catalog, db, tmp_path / "raw")
    assert result["transport"] == "eurostat-sdmx-codelist"
    assert result["structural_link_count"] == 6
    assert cb.search(db, "Transformatoren", language="de")
    shown = cb.show_code(db, "CPA_2_2", "27.11.11")
    assert shown is not None
    assert shown["outbound_links"][0]["target_code"] == "27.11"
