from __future__ import annotations

from datetime import date
from pathlib import Path

from zse_tool.eho import stable_fallback_source_id
from zse_tool.models import EhoItem, Fact, ParsedReport
from zse_tool.storage import Database
from zse_tool.validation import validate_report


def _report(path: Path, facts=None) -> ParsedReport:
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
        facts=facts or [],
        warnings=[],
    )


def test_fallback_source_id_is_stable_and_not_list_position():
    raw_a = {"ticker": "KOEI", "date": "2026-07-30", "files": ["https://eho/a.xlsx"], "x": 1}
    raw_b = {"x": 1, "files": ["https://eho/a.xlsx"], "date": "2026-07-30", "ticker": "KOEI"}
    a = stable_fallback_source_id(raw_a, "financialReports")
    b = stable_fallback_source_id(raw_b, "financialReports")
    assert a == b
    assert a.startswith("auto-")
    assert a != stable_fallback_source_id({**raw_a, "date": "2026-07-31"}, "financialReports")


def test_upsert_repairs_legacy_unknown_id_by_document_url(tmp_path: Path):
    db = Database(tmp_path / "x.sqlite")
    url = "https://eho.zse.hr/fileadmin/KOEI/report.xlsx"
    path = tmp_path / "report.xlsx"
    path.write_bytes(b"placeholder")

    legacy = EhoItem(
        "unknown-1", "financialReports", "KOEI", "KOEI d.d.", None,
        "2026-07-30 11:46", None, {}, [url],
    )
    db.upsert_items([legacy])
    db.mark_downloaded(
        source_id="unknown-1", variant="financialReports", url=url,
        local_path=path, sha256="abc", byte_size=11,
    )
    db.save_parsed_report(_report(path), source_id="unknown-1", issuer_code="KOEI")

    stable = EhoItem(
        "auto-deadbeef", "financialReports", "KOEI", "KOEI d.d.", None,
        "2025-04-29 12:19", None, {"stable": True}, [url],
    )
    db.upsert_items([stable])

    rows = db.downloaded_xlsx("KOEI")
    assert len(rows) == 1
    assert rows[0]["source_id"] == "auto-deadbeef"
    assert rows[0]["local_path"] == str(path)
    assert rows[0]["sha256"] == "abc"

    report = db.report_inventory("KOEI")[0]
    assert report["source_id"] == "auto-deadbeef"
    assert report["publish_date"] == "2025-04-29 12:19"
    assert report["source_url"] == url


def test_validation_uses_labels_not_shifted_adp_codes(tmp_path: Path):
    facts = [
        Fact("balance_sheet", 999, "UKUPNO AKTIVA", "current_period", 100.0, "EUR", "Bilanca", 10),
        Fact("balance_sheet", 125, "Neka druga stavka", "current_period", 5.0, "EUR", "Bilanca", 11),
        Fact("balance_sheet", 1005, "UKUPNO KAPITAL I OBVEZE", "current_period", 100.0, "EUR", "Bilanca", 12),
    ]
    warnings = validate_report(_report(tmp_path / "x.xlsx", facts))
    assert not any("Balance sheet does not balance" in w for w in warnings)


def test_validation_ignores_structural_zero_cash_flow_value(tmp_path: Path):
    facts = [
        Fact("balance_sheet", 63, "Novac i novčani ekvivalenti", "current_period", 50.0, "EUR", "Bilanca", 10),
        Fact("cash_flow_indirect", 50, "Novac i novčani ekvivalenti na kraju razdoblja", "current_period", 0.0, "EUR", "NT_I", 20),
    ]
    warnings = validate_report(_report(tmp_path / "x.xlsx", facts))
    assert not any("Cash mismatch" in w for w in warnings)


def test_invalid_content_status_excludes_download_from_parser_queue(tmp_path: Path):
    db = Database(tmp_path / "x.sqlite")
    url = "https://eho.zse.hr/bad.xlsx"
    item = EhoItem("auto-1", "financialReports", "KOEI", "KOEI", None, "2025-01-01", None, {}, [url])
    db.upsert_items([item])
    path = tmp_path / "bad.xlsx"
    path.write_text("not really xlsx")
    db.mark_downloaded(
        source_id="auto-1", variant="financialReports", url=url,
        local_path=path, sha256="x", byte_size=15,
    )
    db.mark_document_content_status(
        source_id="auto-1", variant="financialReports", url=url,
        status="invalid", note="not OOXML",
    )
    assert db.downloaded_xlsx("KOEI") == []
