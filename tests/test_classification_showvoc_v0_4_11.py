from __future__ import annotations

import json
from pathlib import Path

import pytest

import zse_tool.classification_backbone as cb


SHOWVOC_ENDPOINT = (
    "https://showvoc.op.europa.eu/"
    "semanticturkey/"
    "it.uniroma2.art.semanticturkey/"
    "st-core-services/SPARQL/evaluateQuery"
)


def _binding(value: str, *, lang: str | None = None, kind: str = "literal") -> dict[str, str]:
    out = {"type": kind, "value": value}
    if lang:
        out["xml:lang"] = lang
    return out


def _tuple_response(rows: list[dict]) -> bytes:
    return json.dumps(
        {
            "result": {
                "resultType": "tuple",
                "sparql": {
                    "head": {"vars": sorted({k for row in rows for k in row})},
                    "results": {"bindings": rows},
                },
            }
        }
    ).encode()


def _spec_nace() -> cb.ShowVocSchemeSpec:
    return cb.ShowVocSchemeSpec(
        key="NACE_TEST",
        system="NACE",
        version="2.1",
        project="NACE_PROJECT",
        expected_item_count=4,
        expected_levels=4,
        expected_level_counts={1: 1, 2: 1, 3: 1, 4: 1},
        required_languages=("en", "fr", "de"),
    )


def _spec_cpa() -> cb.ShowVocSchemeSpec:
    return cb.ShowVocSchemeSpec(
        key="CPA_TEST",
        system="CPA",
        version="2.2",
        project="CPA_PROJECT",
        expected_item_count=6,
        expected_levels=6,
        expected_level_counts={1: 1, 2: 1, 3: 1, 4: 1, 5: 1, 6: 1},
        required_languages=("en", "fr", "de"),
    )


def _items(prefix: str, codes: list[str]) -> list[dict]:
    rows: list[dict] = []
    for index, code in enumerate(codes):
        uri = f"http://example.test/{prefix}/{code.replace('.', '')}"
        row = {
            "concept": _binding(uri, kind="uri"),
            "code": _binding(code),
        }
        if index:
            parent_uri = f"http://example.test/{prefix}/{codes[index - 1].replace('.', '')}"
            row["parent"] = _binding(parent_uri, kind="uri")
        rows.append(row)
    return rows


def _labels(prefix: str, codes: list[str]) -> list[dict]:
    rows: list[dict] = []
    for code in codes:
        uri = f"http://example.test/{prefix}/{code.replace('.', '')}"
        for language in ("en", "fr", "de"):
            rows.append(
                {
                    "concept": _binding(uri, kind="uri"),
                    "code": _binding(code),
                    "label": _binding(f"{language}:{code}", lang=language),
                }
            )
    return rows


def _pages(rows: list[dict]) -> list[cb.PageResult]:
    return [cb.PageResult(rows=rows, raw_path="/tmp/raw.json", sha256="0" * 64)]


def test_showvoc_parser_accepts_live_semantic_turkey_tuple_shape():
    rows = [{"code": _binding("27.11")}]
    assert cb._parse_showvoc_json(_tuple_response(rows)) == rows


def test_showvoc_catalog_freezes_live_nace_and_cpa_counts():
    catalog = Path("examples/eurostat_classification_catalog_v0_4_11.json")
    endpoint, specs = cb._catalog_v3_from_path(catalog)

    assert endpoint == SHOWVOC_ENDPOINT
    by_key = {spec.key: spec for spec in specs}

    assert by_key["NACE_REV_2_1"].expected_item_count == 1047
    assert by_key["NACE_REV_2_1"].expected_level_counts == {
        1: 22,
        2: 87,
        3: 287,
        4: 651,
    }

    assert by_key["CPA_2_2"].expected_item_count == 5824
    assert by_key["CPA_2_2"].expected_level_counts == {
        1: 22,
        2: 87,
        3: 284,
        4: 644,
        5: 1433,
        6: 3354,
    }


def test_showvoc_normalizer_preserves_official_uri_parent_and_languages():
    codes = ["C", "27", "27.1", "27.11"]
    normalized = cb._normalize_showvoc_scheme(
        _spec_nace(),
        SHOWVOC_ENDPOINT,
        _pages(_items("nace", codes)),
        _pages(_labels("nace", codes)),
    )

    assert normalized["scheme"]["item_count"] == 4
    assert normalized["scheme"]["label_count"] == 12
    assert normalized["scheme"]["languages"] == ["de", "en", "fr"]

    item = next(row for row in normalized["items"] if row["code"] == "27.11")
    assert item["uri"] == "http://example.test/nace/2711"
    assert item["parent_code"] == "27.1"


def test_showvoc_normalizer_rejects_missing_required_language():
    codes = ["C", "27", "27.1", "27.11"]
    labels = [
        row
        for row in _labels("nace", codes)
        if not (
            row["code"]["value"] == "27.11"
            and row["label"].get("xml:lang") == "de"
        )
    ]

    with pytest.raises(cb.ClassificationError, match="missing required prefLabels"):
        cb._normalize_showvoc_scheme(
            _spec_nace(),
            SHOWVOC_ENDPOINT,
            _pages(_items("nace", codes)),
            _pages(labels),
        )


def test_showvoc_normalizer_rejects_multiple_direct_parents():
    codes = ["C", "27", "27.1", "27.11"]
    rows = _items("nace", codes)
    rows.append(
        {
            "concept": _binding("http://example.test/nace/2711", kind="uri"),
            "code": _binding("27.11"),
            "parent": _binding("http://example.test/nace/27", kind="uri"),
        }
    )

    with pytest.raises(cb.ClassificationError, match="multiple direct parents"):
        cb._normalize_showvoc_scheme(
            _spec_nace(),
            SHOWVOC_ENDPOINT,
            _pages(rows),
            _pages(_labels("nace", codes)),
        )


def test_showvoc_sync_builds_reference_db_and_preserves_raw_provenance(
    tmp_path: Path,
    monkeypatch,
):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "schema_version": "official-classification-catalog-v0.3",
                "transport": "eurostat-showvoc-sparql",
                "showvoc_endpoint": SHOWVOC_ENDPOINT,
                "languages": ["en", "fr", "de"],
                "schemes": [
                    {
                        "key": "NACE_REV_2_1",
                        "system": "NACE",
                        "version": "2.1",
                        "project": "NACE_PROJECT",
                        "expected_item_count": 4,
                        "expected_levels": 4,
                        "expected_level_counts": {
                            "1": 1,
                            "2": 1,
                            "3": 1,
                            "4": 1,
                        },
                    },
                    {
                        "key": "CPA_2_2",
                        "system": "CPA",
                        "version": "2.2",
                        "project": "CPA_PROJECT",
                        "expected_item_count": 6,
                        "expected_levels": 6,
                        "expected_level_counts": {
                            "1": 1,
                            "2": 1,
                            "3": 1,
                            "4": 1,
                            "5": 1,
                            "6": 1,
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    project_codes = {
        "NACE_PROJECT": ["C", "27", "27.1", "27.11"],
        "CPA_PROJECT": ["C", "27", "27.1", "27.11", "27.11.1", "27.11.11"],
    }

    def fake_request(endpoint: str, project: str, query: str, *, timeout: float = 90.0) -> bytes:
        assert endpoint == SHOWVOC_ENDPOINT
        codes = project_codes[project]
        prefix = "nace" if project == "NACE_PROJECT" else "cpa"

        if "skos:prefLabel" in query:
            return _tuple_response(_labels(prefix, codes))
        return _tuple_response(_items(prefix, codes))

    monkeypatch.setattr(cb, "_showvoc_request", fake_request)

    db = tmp_path / "classification.sqlite"
    raw_dir = tmp_path / "raw"
    result = cb.sync(catalog, db, raw_dir)

    assert result["transport"] == "eurostat-showvoc-sparql"
    assert result["structural_link_count"] == 6
    assert Path(result["source_manifest"]).is_file()

    manifest = json.loads(Path(result["source_manifest"]).read_text())
    assert manifest["transport"] == "eurostat-showvoc-sparql"
    assert {row["project"] for row in manifest["schemes"]} == {
        "NACE_PROJECT",
        "CPA_PROJECT",
    }

    shown = cb.show_code(db, "CPA_2_2", "27.11.11")
    assert shown is not None
    assert shown["item"]["uri"] == "http://example.test/cpa/271111"
    assert shown["item"]["parent_code"] == "27.11.1"
    assert shown["outbound_links"][0]["target_code"] == "27.11"


def test_showvoc_endpoint_is_allow_listed():
    cb._validate_https_endpoint(SHOWVOC_ENDPOINT)
