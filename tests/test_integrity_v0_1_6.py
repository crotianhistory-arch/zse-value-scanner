from __future__ import annotations

from datetime import date
from pathlib import Path

from zse_tool.models import EhoItem, Fact, Metric, ParsedReport
from zse_tool.storage import Database


def _report(path: Path, value: float = 1.0) -> ParsedReport:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"placeholder")
    return ParsedReport(
        source_path=path,
        issuer_name="TEST d.d.",
        period_start=date(2025, 1, 1),
        period_end=date(2025, 3, 31),
        year=2025,
        quarter=1,
        consolidated=True,
        audited=False,
        currency="EUR",
        scale=1.0,
        facts=[Fact("balance_sheet", 1, "X", "current_period", value, "EUR", "Bilanca", 1)],
        warnings=[],
    )


def _add_document(db: Database, path: Path, source_id: str = "auto-1", url: str = "https://eho/report.xlsx"):
    item = EhoItem(source_id, "financialReports", "TEST", "TEST", None, "2025-04-30", None, {}, [url])
    db.upsert_items([item])
    db.mark_downloaded(
        source_id=source_id, variant="financialReports", url=url,
        local_path=path, sha256="abc", byte_size=11,
    )


def test_integrity_repair_removes_orphan_report_and_children(tmp_path: Path):
    db = Database(tmp_path / "x.sqlite")
    current = tmp_path / "current.xlsx"
    stale = tmp_path / "legacy" / "stale.xlsx"
    _add_document(db, current)

    # Simulate a v0.1.5 migration leftover: a parsed row not referenced by any
    # current document, but claiming the same stable source provenance.
    db.save_parsed_report(
        _report(stale), source_id="auto-1", source_variant="financialReports",
        source_url="https://eho/report.xlsx", source_publish_date="2025-04-30",
        source_sha256="abc", issuer_code="TEST", metrics=[Metric("cash", 1.0, "EUR")],
    )
    before = db.integrity_report("TEST")
    assert len(before["orphan_parsed_reports"]) == 1
    assert before["ok"] is False

    repaired = db.cleanup_report_integrity("TEST")
    assert repaired["removed_total"] == 1
    assert db.integrity_report("TEST")["ok"] is False  # current XLSX is still unparsed

    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM parsed_reports").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0] == 0


def test_save_prevents_same_stable_source_url_from_reappearing(tmp_path: Path):
    db = Database(tmp_path / "x.sqlite")
    current = tmp_path / "current.xlsx"
    stale = tmp_path / "legacy" / "stale.xlsx"
    _add_document(db, current)

    db.save_parsed_report(
        _report(stale, 1.0), source_id="auto-1", source_variant="financialReports",
        source_url="https://eho/report.xlsx", source_publish_date="2025-04-30",
        source_sha256="abc", issuer_code="TEST", metrics=[Metric("cash", 1.0, "EUR")],
    )
    db.save_parsed_report(
        _report(current, 2.0), source_id="auto-1", source_variant="financialReports",
        source_url="https://eho/report.xlsx", source_publish_date="2025-04-30",
        source_sha256="abc", issuer_code="TEST", metrics=[Metric("cash", 2.0, "EUR")],
    )

    with db.connect() as conn:
        rows = conn.execute("SELECT local_path FROM parsed_reports").fetchall()
        assert [r["local_path"] for r in rows] == [str(current.resolve())]
        assert conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0] == 1
    assert db.integrity_report("TEST")["ok"] is True


def test_duplicate_sha_is_informational_not_integrity_failure(tmp_path: Path):
    db = Database(tmp_path / "x.sqlite")
    for i in (1, 2):
        path = tmp_path / f"r{i}.xlsx"
        path.write_bytes(b"placeholder")
        source_id = f"auto-{i}"
        url = f"https://eho/r{i}.xlsx"
        _add_document(db, path, source_id=source_id, url=url)
        # overwrite hash to the same value for both documents
        db.mark_downloaded(
            source_id=source_id, variant="financialReports", url=url,
            local_path=path, sha256="same", byte_size=11,
        )
        db.save_parsed_report(
            _report(path), source_id=source_id, source_variant="financialReports",
            source_url=url, source_publish_date="2025-04-30", source_sha256="same",
            issuer_code="TEST", metrics=[],
        )

    data = db.integrity_report("TEST")
    assert len(data["duplicate_document_hashes"]) == 1
    assert data["ok"] is True
