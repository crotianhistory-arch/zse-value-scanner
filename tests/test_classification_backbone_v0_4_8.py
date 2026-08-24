from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from zse_tool.classification_backbone import (
    ClassificationError,
    PageResult,
    SchemeSpec,
    _cpa_to_nace_code,
    _level_from_code,
    _normalize_scheme,
    _parse_sparql_json,
    _validate_https_endpoint,
    _write_database,
    search,
    show_code,
    status,
)


def _binding(value: str, *, lang: str | None = None, kind: str = "literal") -> dict[str, str]:
    out = {"type": kind, "value": value}
    if lang:
        out["xml:lang"] = lang
    return out


def _page(rows: list[dict]) -> list[PageResult]:
    return [PageResult(rows=rows, raw_path="raw.json", sha256="0" * 64)]


def _normalized_nace() -> dict:
    spec = SchemeSpec("NACE_REV_2_1", "NACE", "2.1", "nace", 4, 4)
    items = [
        {"concept": _binding("urn:nace:C", kind="uri"), "code": _binding("C")},
        {"concept": _binding("urn:nace:27", kind="uri"), "code": _binding("27"), "parent": _binding("urn:nace:C", kind="uri")},
        {"concept": _binding("urn:nace:27.1", kind="uri"), "code": _binding("27.1"), "parent": _binding("urn:nace:27", kind="uri")},
        {"concept": _binding("urn:nace:27.11", kind="uri"), "code": _binding("27.11"), "parent": _binding("urn:nace:27.1", kind="uri")},
    ]
    labels = []
    for code, text in [("C", "Manufacturing"), ("27", "Electrical equipment"), ("27.1", "Motors and transformers"), ("27.11", "Manufacture of transformers")]:
        uri = f"urn:nace:{code}"
        labels.extend([
            {"concept": _binding(uri, kind="uri"), "code": _binding(code), "labelKind": _binding("http://www.w3.org/2004/02/skos/core#prefLabel", kind="uri"), "label": _binding(text, lang="en")},
            {"concept": _binding(uri, kind="uri"), "code": _binding(code), "labelKind": _binding("http://www.w3.org/2004/02/skos/core#altLabel", kind="uri"), "label": _binding("Transformatoren" if code == "27.11" else text, lang="de")},
        ])
    notes = [
        {"concept": _binding("urn:nace:27.11", kind="uri"), "code": _binding("27.11"), "noteKind": _binding("http://rdf-vocabulary.ddialliance.org/xkos#coreContentNote", kind="uri"), "note": _binding("Includes transformer manufacturing.", lang="en")}
    ]
    return _normalize_scheme(spec, "urn:nace:scheme", _page(items), _page(labels), _page(notes))


def _normalized_cpa() -> dict:
    spec = SchemeSpec("CPA_2_2", "CPA", "2.2", "cpa", 6, 6)
    rows = [
        ("C", None),
        ("27", "C"),
        ("27.1", "27"),
        ("27.11", "27.1"),
        ("27.11.1", "27.11"),
        ("27.11.11", "27.11.1"),
    ]
    items = []
    labels = []
    for code, parent in rows:
        uri = f"urn:cpa:{code}"
        row = {"concept": _binding(uri, kind="uri"), "code": _binding(code)}
        if parent:
            row["parent"] = _binding(f"urn:cpa:{parent}", kind="uri")
        items.append(row)
        labels.append({
            "concept": _binding(uri, kind="uri"),
            "code": _binding(code),
            "labelKind": _binding("http://www.w3.org/2004/02/skos/core#prefLabel", kind="uri"),
            "label": _binding(f"CPA label {code}", lang="en"),
        })
    return _normalize_scheme(spec, "urn:cpa:scheme", _page(items), _page(labels), _page([]))


def test_level_parser_supports_nace_and_cpa_shapes():
    assert [_level_from_code(x) for x in ["C", "27", "27.1", "27.11", "27.11.1", "27.11.11"]] == [1, 2, 3, 4, 5, 6]


def test_level_parser_rejects_unknown_shape():
    with pytest.raises(ClassificationError):
        _level_from_code("2711")


def test_endpoint_is_https_and_allowlisted():
    _validate_https_endpoint("https://publications.europa.eu/webapi/rdf/sparql")
    with pytest.raises(ClassificationError):
        _validate_https_endpoint("http://publications.europa.eu/webapi/rdf/sparql")
    with pytest.raises(ClassificationError):
        _validate_https_endpoint("https://example.com/sparql")


def test_sparql_json_parser_is_strict():
    data = json.dumps({"results": {"bindings": [{"x": _binding("ok")} ]}}).encode()
    assert len(_parse_sparql_json(data)) == 1
    with pytest.raises(ClassificationError):
        _parse_sparql_json(b"{}")


def test_normalization_preserves_multilingual_labels_and_parent_codes():
    normalized = _normalized_nace()
    assert normalized["scheme"]["item_count"] == 4
    assert normalized["scheme"]["languages"] == ["de", "en"]
    item = next(x for x in normalized["items"] if x["code"] == "27.11")
    assert item["parent_code"] == "27.1"
    assert any(x["language"] == "de" and x["label"] == "Transformatoren" for x in normalized["labels"])


def test_normalization_requires_exact_official_item_count():
    spec = SchemeSpec("NACE_REV_2_1", "NACE", "2.1", "nace", 5, 4)
    with pytest.raises(ClassificationError, match="expected 5 items"):
        _normalize_scheme(spec, "urn:nace:scheme", _page(_normalized_items_for_count()), _page(_normalized_labels_for_count()), _page([]))


def _normalized_items_for_count():
    return [
        {"concept": _binding(f"urn:x:{code}", kind="uri"), "code": _binding(code)}
        for code in ["A", "01", "01.1", "01.11"]
    ]


def _normalized_labels_for_count():
    return [
        {"concept": _binding(f"urn:x:{code}", kind="uri"), "code": _binding(code), "labelKind": _binding("http://www.w3.org/2004/02/skos/core#prefLabel", kind="uri"), "label": _binding(code, lang="en")}
        for code in ["A", "01", "01.1", "01.11"]
    ]


def test_cpa_structural_origin_mapping():
    assert _cpa_to_nace_code("27.11", 4) == "27.11"
    assert _cpa_to_nace_code("27.11.1", 5) == "27.11"
    assert _cpa_to_nace_code("27.11.11", 6) == "27.11"


def test_database_build_is_atomic_and_requires_explicit_replace(tmp_path: Path):
    db = tmp_path / "reference.sqlite"
    result = _write_database(db, "https://publications.europa.eu/webapi/rdf/sparql", [_normalized_nace(), _normalized_cpa()], retrieved_at="2026-08-23T15:00:00Z", replace=False)
    assert db.exists()
    assert len(result["sha256"]) == 64
    with pytest.raises(ClassificationError, match="already exists"):
        _write_database(db, "https://publications.europa.eu/webapi/rdf/sparql", [_normalized_nace(), _normalized_cpa()], retrieved_at="2026-08-23T15:00:00Z", replace=False)


def test_database_creates_cpa_to_nace_links(tmp_path: Path):
    db = tmp_path / "reference.sqlite"
    result = _write_database(db, "https://publications.europa.eu/webapi/rdf/sparql", [_normalized_nace(), _normalized_cpa()], retrieved_at="2026-08-23T15:00:00Z", replace=False)
    assert result["structural_link_count"] == 6
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("SELECT source_code, target_code FROM classification_links ORDER BY source_code").fetchall()
    finally:
        conn.close()
    assert ("27.11.11", "27.11") in rows


def test_multilingual_local_search_needs_no_network(tmp_path: Path):
    db = tmp_path / "reference.sqlite"
    _write_database(db, "https://publications.europa.eu/webapi/rdf/sparql", [_normalized_nace(), _normalized_cpa()], retrieved_at="2026-08-23T15:00:00Z", replace=False)
    rows = search(db, "Transformatoren", language="de")
    assert rows[0]["code"] == "27.11"
    assert rows[0]["system"] == "NACE"


def test_status_and_show_code_preserve_provenance_and_links(tmp_path: Path):
    db = tmp_path / "reference.sqlite"
    _write_database(db, "https://publications.europa.eu/webapi/rdf/sparql", [_normalized_nace(), _normalized_cpa()], retrieved_at="2026-08-23T15:00:00Z", replace=False)
    s = status(db)
    assert s["metadata"]["schema_version"] == "official-classification-backbone-v0.1"
    assert {x["key"] for x in s["schemes"]} == {"NACE_REV_2_1", "CPA_2_2"}
    shown = show_code(db, "CPA_2_2", "27.11.11")
    assert shown is not None
    assert shown["outbound_links"][0]["target_code"] == "27.11"
    assert shown["outbound_links"][0]["evidence_class"] == "D1_DETERMINISTIC_FROM_OFFICIAL_STRUCTURE"
