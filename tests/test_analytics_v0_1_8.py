from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from zse_tool.analytics import build_trends, build_ttm_snapshot
from zse_tool.models import EhoItem, Metric, ParsedReport
from zse_tool.storage import Database


def _save(db: Database, root: Path, *, source_id: str, year: int, quarter: int | None,
          period_end: date, publish_date: str, values: dict[str, float | None], audited: bool = False):
    path = root / f"{source_id}.xlsx"
    path.write_bytes(b"x")
    url = f"https://eho.zse.hr/{source_id}.xlsx"
    db.upsert_items([EhoItem(source_id=source_id, variant="financialReports", issuer_code="TEST",
                             issuer_name="TEST d.d.", title=None, publish_date=publish_date,
                             item_link=None, raw={}, document_urls=[url])])
    db.mark_downloaded(source_id=source_id, variant="financialReports", url=url,
                       local_path=path, sha256=source_id, byte_size=1)
    report = ParsedReport(source_path=path, issuer_name="TEST d.d.", period_start=date(year, 1, 1),
                          period_end=period_end, year=year, quarter=quarter, consolidated=True,
                          audited=audited, currency="EUR", scale=1.0, facts=[], warnings=[])
    db.save_parsed_report(report, source_id=source_id, source_variant="financialReports",
                          source_url=url, source_publish_date=publish_date, source_sha256=source_id,
                          issuer_code="TEST",
                          metrics=[Metric(k, v, "EUR") for k, v in values.items()])


def _vals(revenue, ebit, ebitda, ni, cfo, fcf, equity=200.0):
    return {
        "sales_revenue_ytd": revenue,
        "ebit_ytd": ebit,
        "depreciation_ytd": None if ebit is None or ebitda is None else ebitda - ebit,
        "ebitda_simple_ytd": ebitda,
        "net_income_parent_ytd": ni,
        "net_income_total_ytd": ni,
        "interest_expense_ytd": 2.0 if ebit is not None else None,
        "operating_cash_flow_ytd": cfo,
        "capex_ytd": None if cfo is None or fcf is None else cfo - fcf,
        "free_cash_flow_ytd": fcf,
        "cash": 50.0,
        "total_assets": 1000.0,
        "equity_total": equity,
        "equity_parent": equity,
        "gross_financial_debt_ex_other": 20.0,
        "net_debt_ex_other": -30.0,
    }


def test_ttm_explicitly_falls_back_to_same_year_q4_for_missing_audited_fy_flow(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite")
    _save(db, tmp_path, source_id="q2-25", year=2025, quarter=2, period_end=date(2025, 6, 30),
          publish_date="2025-07-30", values=_vals(400, 40, 50, 30, 35, 25, 200))
    # Q4 has complete cumulative Jan-Dec P&L.
    _save(db, tmp_path, source_id="q4-25", year=2025, quarter=4, period_end=date(2025, 12, 31),
          publish_date="2026-02-26", values=_vals(1000, 100, 120, 80, 90, 60, 230))
    # Audited FY retains point/cash-flow data but P&L fields are unavailable.
    fy = _vals(None, None, None, None, 92, 61, 240)
    fy["interest_expense_ytd"] = None
    _save(db, tmp_path, source_id="fy-25", year=2025, quarter=None, period_end=date(2025, 12, 31),
          publish_date="2026-04-16", values=fy, audited=True)
    _save(db, tmp_path, source_id="q2-26", year=2026, quarter=2, period_end=date(2026, 6, 30),
          publish_date="2026-07-30", values=_vals(500, 60, 70, 45, 55, 40, 260))

    snap = build_ttm_snapshot(db, "TEST")
    revenue = snap["metrics"]["sales_revenue_ttm"]
    assert revenue["value"] == pytest.approx(1100)  # Q4-25 + H1-26 - H1-25
    assert revenue["quality"] == "ttm-q4-fallback"
    assert "flow=2025-Q4" in revenue["formula"]
    assert revenue["sources"][1]["period"] == "2025-Q4"
    assert revenue["sources"][1]["fallback_for"] == "2025-FY"
    assert "using same-year consolidated 2025-Q4 cumulative value" in revenue["note"]
    # Cash flow was present in audited FY, so it should not need the Q4 fallback.
    cfo = snap["metrics"]["operating_cash_flow_ttm"]
    assert cfo["value"] == pytest.approx(112)  # audited FY 92 + 55 - 35
    assert cfo["quality"] == "ttm"


def test_annual_trends_use_q4_flow_fallback_but_audited_point_values(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite")
    for year, q4_rev, audited_cash in ((2024, 800.0, 70.0), (2025, 1000.0, 90.0)):
        _save(db, tmp_path, source_id=f"q4-{year}", year=year, quarter=4,
              period_end=date(year, 12, 31), publish_date=f"{year+1}-02-26",
              values=_vals(q4_rev, q4_rev * .1, q4_rev * .12, q4_rev * .08, 80, 55, 200 + year - 2024))
        fy = _vals(None, None, None, None, 82, 56, 210 + year - 2024)
        fy["cash"] = audited_cash
        _save(db, tmp_path, source_id=f"fy-{year}", year=year, quarter=None,
              period_end=date(year, 12, 31), publish_date=f"{year+1}-04-16",
              values=fy, audited=True)

    data = build_trends(db, "TEST", years=2)
    latest = data["annual"][-1]
    assert latest["year"] == 2025
    assert latest["sales_revenue"] == pytest.approx(1000)
    assert latest["cash"] == pytest.approx(90)  # audited FY point value, not Q4's 50
    assert "sales_revenue_ytd" in latest["flow_fallback_metrics"]
    assert latest["flow_fallback_sources"]["sales_revenue_ytd"] == "2025-Q4"


def test_formula_does_not_repeat_fy_suffix(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite")
    _save(db, tmp_path, source_id="q1-25", year=2025, quarter=1, period_end=date(2025, 3, 31),
          publish_date="2025-04-29", values=_vals(200, 20, 24, 15, 18, 12, 190))
    _save(db, tmp_path, source_id="fy-25", year=2025, quarter=None, period_end=date(2025, 12, 31),
          publish_date="2026-04-16", values=_vals(900, 90, 105, 70, 75, 50, 220), audited=True)
    _save(db, tmp_path, source_id="q1-26", year=2026, quarter=1, period_end=date(2026, 3, 31),
          publish_date="2026-04-29", values=_vals(250, 30, 35, 20, 22, 16, 230))
    snap = build_ttm_snapshot(db, "TEST")
    formula = snap["metrics"]["sales_revenue_ttm"]["formula"]
    assert formula == "2025-FY + 2026-Q1 - 2025-Q1"
    assert "FY-FY" not in formula


def test_suspect_parent_equity_suppresses_roe_and_debt_equity(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite")
    _save(db, tmp_path, source_id="q2-25", year=2025, quarter=2, period_end=date(2025, 6, 30),
          publish_date="2025-07-30", values=_vals(400, 40, 50, 30, 35, 25, 200))
    _save(db, tmp_path, source_id="fy-25", year=2025, quarter=None, period_end=date(2025, 12, 31),
          publish_date="2026-04-16", values=_vals(1000, 100, 120, 80, 90, 60, 240), audited=True)
    cur = _vals(500, 60, 70, 45, 55, 40, 260)
    # Consolidated total profit differs from parent profit, but extracted parent
    # equity equals total equity: treat attribution as suspect rather than using
    # it in ROE/debt-to-parent-equity.
    cur["net_income_total_ytd"] = 70.0
    cur["equity_total"] = 260.0
    cur["equity_parent"] = 260.0
    _save(db, tmp_path, source_id="q2-26", year=2026, quarter=2, period_end=date(2026, 6, 30),
          publish_date="2026-07-30", values=cur)

    snap = build_ttm_snapshot(db, "TEST")
    assert snap["equity_attribution_warning"]
    assert snap["metrics"]["equity_parent"]["quality"] == "suspect-current"
    assert snap["metrics"]["roe_ttm"]["value"] is None
    assert snap["metrics"]["debt_to_equity_parent"]["value"] is None
