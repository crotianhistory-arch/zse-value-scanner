from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path

import pytest
from openpyxl import Workbook

import zse_tool.classification_crosswalk as cw


def workbook_bytes(rows: list[tuple[str, str, str, str]], *, multirow: bool = False) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Correspondence"
    if multirow:
        ws.append(["ISIC Rev. 4", "ISIC Rev. 4", "ISIC Rev. 5", "ISIC Rev. 5", "GSIM", "Change"])
        ws.append(["Code (numeric only)", "Title", "Code (numeric only)", "Title", "Type of Change", "Description of changed content"])
    else:
        ws.append([
            "ISIC Rev. 4 Code (numeric only)",
            "ISIC Rev. 4 Title",
            "ISIC Rev. 5 Code (numeric only)",
            "ISIC Rev. 5 Title",
            "GSIM Type of Change",
            "Description of changed content",
        ])
    for from_code, to_code, change_type, note in rows:
        ws.append([
            from_code,
            f"Rev4 {from_code}",
            to_code,
            f"Rev5 {to_code}",
            change_type,
            note,
        ])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def fake_spec(from_count: int, to_count: int, *, source_id: str = "FAKE") -> cw.CrosswalkSourceSpec:
    return cw.CrosswalkSourceSpec(
        source_id=source_id,
        provider="Fixture provider",
        adapter="unsd-isic-rev4-rev5-xlsx",
        url="https://unstats.un.org/fixture.xlsx",
        from_system="ISIC",
        from_version="4",
        to_system="ISIC",
        to_version="5",
        expected_from_code_count=from_count,
        expected_to_code_count=to_count,
    )


def write_graph(db: Path, edges: list[dict], *, source_id: str = "S1", from_version: str = "4", to_version: str = "5") -> None:
    spec = cw.CrosswalkSourceSpec(
        source_id=source_id,
        provider="Fixture",
        adapter="unsd-isic-rev4-rev5-xlsx",
        url="https://unstats.un.org/fixture.xlsx",
        from_system="ISIC",
        from_version=from_version,
        to_system="ISIC",
        to_version=to_version,
        expected_from_code_count=len({x["from_code"] for x in edges}),
        expected_to_code_count=len({x["to_code"] for x in edges}),
    )
    conn = cw._connect(db)
    try:
        cw._init_db(conn)
        counts = cw._validate_edges(spec, edges)
        cw._write_source(
            conn,
            spec,
            retrieved_at="2026-08-24T00:00:00Z",
            raw_path=Path("/tmp/fake.xlsx"),
            raw_sha256="0" * 64,
            edges=edges,
            counts=counts,
        )
        conn.commit()
    finally:
        conn.close()


def edge(a: str, b: str, change: str = "no change") -> dict:
    return {
        "from_code": a,
        "from_label": f"from {a}",
        "to_code": b,
        "to_label": f"to {b}",
        "official_change_type": change,
        "official_note": None,
    }


def test_catalog_accepts_official_unsd_adapter(tmp_path: Path):
    p = tmp_path / "catalog.json"
    p.write_text(json.dumps({
        "schema_version": cw.CATALOG_SCHEMA_VERSION,
        "sources": [{
            "source_id": "UN",
            "provider": "UNSD",
            "adapter": "unsd-isic-rev4-rev5-xlsx",
            "url": "https://unstats.un.org/x.xlsx",
            "from_system": "ISIC",
            "from_version": "4",
            "to_system": "ISIC",
            "to_version": "5",
            "expected_from_code_count": 419,
            "expected_to_code_count": 463,
        }],
    }))
    specs = cw.load_catalog(p)
    assert specs[0].expected_from_code_count == 419
    assert specs[0].expected_to_code_count == 463


def test_catalog_rejects_nonallowlisted_source(tmp_path: Path):
    p = tmp_path / "catalog.json"
    p.write_text(json.dumps({
        "schema_version": cw.CATALOG_SCHEMA_VERSION,
        "sources": [{
            "source_id": "X", "provider": "X", "adapter": "unsd-isic-rev4-rev5-xlsx",
            "url": "https://example.com/x.xlsx",
            "from_system": "ISIC", "from_version": "4", "to_system": "ISIC", "to_version": "5",
            "expected_from_code_count": 1, "expected_to_code_count": 1,
        }],
    }))
    with pytest.raises(cw.CrosswalkError, match="allowlisted"):
        cw.load_catalog(p)


def test_unsd_parser_is_sector_agnostic_and_preserves_metadata():
    data = workbook_bytes([
        ("2710", "2710", "RC1 no change", "electrical fixture"),
        ("1073", "1073", "RC1 no change", "food fixture"),
    ], multirow=True)
    rows = cw.parse_unsd_isic_rev4_rev5_xlsx(data)
    assert {(x["from_code"], x["to_code"]) for x in rows} == {("2710", "2710"), ("1073", "1073")}
    assert {x["official_note"] for x in rows} == {"electrical fixture", "food fixture"}


def test_unsd_parser_accepts_section_prefixed_codes():
    rows = cw.parse_unsd_isic_rev4_rev5_xlsx(workbook_bytes([("C2710", "C2710", "x", "y")]))
    assert rows[0]["from_code"] == "2710"
    assert rows[0]["to_code"] == "2710"


def test_integrity_gate_checks_complete_class_sets():
    spec = fake_spec(2, 3)
    rows = [edge("1000", "2000"), edge("1000", "2001"), edge("1001", "2002")]
    assert cw._validate_edges(spec, rows) == {"edge_count": 3, "from_code_count": 2, "to_code_count": 3}
    with pytest.raises(cw.CrosswalkError, match="expected 2 source codes"):
        cw._validate_edges(fake_spec(2, 1), [edge("1000", "2000")])


def test_mapping_shape_is_derived_from_graph(tmp_path: Path):
    db = tmp_path / "x.sqlite"
    write_graph(db, [
        edge("1000", "2000"),
        edge("1000", "2001"),
        edge("1001", "2002"),
        edge("1002", "2002"),
        edge("1003", "2003"),
    ])
    assert {x["mapping_shape"] for x in cw.show_code(db, "ISIC", "4", "1000")["forward"]} == {"one_to_many"}
    assert {x["mapping_shape"] for x in cw.show_code(db, "ISIC", "5", "2002")["reverse"]} == {"many_to_one"}
    assert cw.show_code(db, "ISIC", "4", "1003")["forward"][0]["mapping_shape"] == "one_to_one"


def test_translate_supports_reverse_and_chained_official_paths(tmp_path: Path):
    db = tmp_path / "x.sqlite"
    write_graph(db, [edge("1000", "2000")], source_id="S1", from_version="3", to_version="4")
    conn = cw._connect(db)
    try:
        spec = cw.CrosswalkSourceSpec(
            source_id="S2", provider="Fixture", adapter="unsd-isic-rev4-rev5-xlsx",
            url="https://unstats.un.org/y.xlsx", from_system="ISIC", from_version="4",
            to_system="ISIC", to_version="5", expected_from_code_count=1, expected_to_code_count=1,
        )
        rows = [edge("2000", "3000")]
        cw._write_source(conn, spec, retrieved_at="2026-08-24T00:00:00Z", raw_path=Path("/tmp/y"), raw_sha256="1"*64, edges=rows, counts=cw._validate_edges(spec, rows))
        conn.commit()
    finally:
        conn.close()

    forward = cw.translate(db, "ISIC", "3", "1000", "ISIC", "5")
    assert forward["shortest_hops"] == 2
    assert {p["target_code"] for p in forward["paths"]} == {"3000"}
    reverse = cw.translate(db, "ISIC", "5", "3000", "ISIC", "3")
    assert {p["target_code"] for p in reverse["paths"]} == {"1000"}
    assert reverse["paths"][0]["edges"][0]["direction"] == "reverse"


def test_empirical_observation_remains_separate_evidence_class(tmp_path: Path):
    db = tmp_path / "x.sqlite"
    write_graph(db, [edge("2710", "2710")])
    before = sqlite3.connect(db).execute("SELECT COUNT(*) FROM crosswalk_edges").fetchone()[0]
    result = cw.add_empirical_observation(db, {
        "observation_id": "obs-1",
        "entity_key": "example-entity",
        "left": {"system": "NACE", "version": "2.1", "code": "27.11"},
        "right": {"system": "NAICS", "version": "2022", "code": "335311"},
        "source_url": "https://example.org/company-filing",
        "observed_at": "2026-08-24",
        "note": "fixture only",
    })
    assert result["evidence_class"] == cw.EMPIRICAL_EVIDENCE_CLASS
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM crosswalk_edges").fetchone()[0] == before
        assert conn.execute("SELECT evidence_class FROM empirical_observations").fetchone()[0] == cw.EMPIRICAL_EVIDENCE_CLASS
    finally:
        conn.close()


def test_sync_preserves_raw_and_builds_standalone_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data = workbook_bytes([("2710", "2710", "same", "electrical"), ("1073", "1073", "same", "food")])
    monkeypatch.setattr(cw, "_download_bounded", lambda url: data)
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({
        "schema_version": cw.CATALOG_SCHEMA_VERSION,
        "sources": [{
            "source_id": "UN", "provider": "UNSD", "adapter": "unsd-isic-rev4-rev5-xlsx",
            "url": "https://unstats.un.org/x.xlsx",
            "from_system": "ISIC", "from_version": "4", "to_system": "ISIC", "to_version": "5",
            "expected_from_code_count": 2, "expected_to_code_count": 2,
        }],
    }))
    db = tmp_path / "crosswalk.sqlite"
    raw = tmp_path / "raw"
    result = cw.sync(catalog, db, raw)
    assert db.exists()
    assert Path(result["sources"][0]["raw_path"]).exists()
    assert Path(result["manifest_path"]).exists()
    assert cw.status(db)["edge_count"] == 2


def test_translate_unknown_code_returns_no_paths(tmp_path: Path):
    db = tmp_path / "x.sqlite"
    write_graph(db, [edge("2710", "2710")])
    result = cw.translate(db, "ISIC", "4", "9999", "ISIC", "5")
    assert result["path_count"] == 0
    assert result["shortest_hops"] is None
