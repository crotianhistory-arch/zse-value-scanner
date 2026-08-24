from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from zse_tool.analytics import HRK_PER_EUR, build_trends, build_ttm_snapshot
from zse_tool.models import EhoItem, Metric, ParsedReport
from zse_tool.storage import Database


def _save(
    db: Database,
    root: Path,
    *,
    source_id: str,
    year: int,
    quarter: int | None,
    period_end: date,
    publish_date: str,
    currency: str,
    values: dict[str, float],
    audited: bool = False,
):
    path = root / f"{source_id}.xlsx"
    path.write_bytes(b"x")
    url = f"https://eho.zse.hr/{source_id}.xlsx"
    db.upsert_items([
        EhoItem(
            source_id=source_id,
            variant="financialReports",
            issuer_code="TEST",
            issuer_name="TEST d.d.",
            title=None,
            publish_date=publish_date,
            item_link=None,
            raw={},
            document_urls=[url],
        )
    ])
    db.mark_downloaded(
        source_id=source_id,
        variant="financialReports",
        url=url,
        local_path=path,
        sha256=source_id,
        byte_size=1,
    )
    report = ParsedReport(
        source_path=path,
        issuer_name="TEST d.d.",
        period_start=date(year, 1, 1),
        period_end=period_end,
        year=year,
        quarter=quarter,
        consolidated=True,
        audited=audited,
        currency=currency,
        scale=1.0,
        facts=[],
        warnings=[],
    )
    db.save_parsed_report(
        report,
        source_id=source_id,
        source_variant="financialReports",
        source_url=url,
        source_publish_date=publish_date,
        source_sha256=source_id,
        issuer_code="TEST",
        metrics=[Metric(k, v, currency) for k, v in values.items()],
    )


def _vals(revenue, ebit, ebitda, ni, cfo, fcf, equity, cash=50, debt=20):
    return {
        "sales_revenue_ytd": revenue,
        "ebit_ytd": ebit,
        "depreciation_ytd": ebitda - ebit,
        "ebitda_simple_ytd": ebitda,
        "net_income_parent_ytd": ni,
        "net_income_total_ytd": ni,
        "interest_expense_ytd": 2.0,
        "operating_cash_flow_ytd": cfo,
        "capex_ytd": cfo - fcf,
        "free_cash_flow_ytd": fcf,
        "cash": cash,
        "total_assets": 1000.0,
        "equity_total": equity,
        "equity_parent": equity,
        "gross_financial_debt_ex_other": debt,
        "net_debt_ex_other": debt - cash,
    }


def test_true_ttm_formula_and_roe(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite")
    # Prior same quarter, previous FY, current quarter. All EUR here for simple arithmetic.
    _save(db, tmp_path, source_id="q2-25", year=2025, quarter=2, period_end=date(2025, 6, 30),
          publish_date="2025-07-30", currency="EUR", values=_vals(400, 40, 50, 30, 35, 25, 200))
    _save(db, tmp_path, source_id="fy-25", year=2025, quarter=None, period_end=date(2025, 12, 31),
          publish_date="2026-04-15", currency="EUR", values=_vals(1000, 100, 120, 80, 90, 60, 240), audited=True)
    _save(db, tmp_path, source_id="q2-26", year=2026, quarter=2, period_end=date(2026, 6, 30),
          publish_date="2026-07-30", currency="EUR", values=_vals(500, 60, 70, 45, 55, 40, 260, cash=80, debt=30))

    snap = build_ttm_snapshot(db, "TEST")
    assert snap["ok"] is True
    assert snap["as_of"] == "2026-Q2"
    # FY25 + H1-26 - H1-25
    assert snap["metrics"]["sales_revenue_ttm"]["value"] == pytest.approx(1100)
    assert snap["metrics"]["ebit_ttm"]["value"] == pytest.approx(120)
    assert snap["metrics"]["net_income_parent_ttm"]["value"] == pytest.approx(95)
    # ROE = 95 / avg(Q2-25 equity 200, Q2-26 equity 260)
    assert snap["metrics"]["roe_ttm"]["value"] == pytest.approx(95 / 230)
    assert snap["metrics"]["net_debt_ex_other"]["value"] == pytest.approx(-50)


def test_ttm_converts_hrk_sources_before_arithmetic(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite")
    # Make old HRK amounts correspond exactly to 100/20/10 EUR etc.
    _save(db, tmp_path, source_id="q1-22", year=2022, quarter=1, period_end=date(2022, 3, 31),
          publish_date="2022-04-30", currency="HRK",
          values=_vals(100 * HRK_PER_EUR, 20 * HRK_PER_EUR, 22 * HRK_PER_EUR, 10 * HRK_PER_EUR,
                       12 * HRK_PER_EUR, 8 * HRK_PER_EUR, 100 * HRK_PER_EUR))
    _save(db, tmp_path, source_id="fy-22", year=2022, quarter=None, period_end=date(2022, 12, 31),
          publish_date="2023-04-15", currency="HRK",
          values=_vals(500 * HRK_PER_EUR, 80 * HRK_PER_EUR, 90 * HRK_PER_EUR, 50 * HRK_PER_EUR,
                       60 * HRK_PER_EUR, 40 * HRK_PER_EUR, 120 * HRK_PER_EUR), audited=True)
    _save(db, tmp_path, source_id="q1-23", year=2023, quarter=1, period_end=date(2023, 3, 31),
          publish_date="2023-04-30", currency="EUR", values=_vals(150, 30, 33, 18, 20, 15, 130))

    snap = build_ttm_snapshot(db, "TEST")
    assert snap["metrics"]["sales_revenue_ttm"]["value"] == pytest.approx(550)
    assert snap["metrics"]["ebit_ttm"]["value"] == pytest.approx(90)
    notes = snap["metrics"]["sales_revenue_ttm"]["note"]
    assert notes and "converted from HRK" in notes


def test_ttm_refuses_to_invent_missing_prior_period(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite")
    _save(db, tmp_path, source_id="fy-25", year=2025, quarter=None, period_end=date(2025, 12, 31),
          publish_date="2026-04-15", currency="EUR", values=_vals(1000, 100, 120, 80, 90, 60, 240), audited=True)
    _save(db, tmp_path, source_id="q2-26", year=2026, quarter=2, period_end=date(2026, 6, 30),
          publish_date="2026-07-30", currency="EUR", values=_vals(500, 60, 70, 45, 55, 40, 260))
    snap = build_ttm_snapshot(db, "TEST")
    assert snap["metrics"]["sales_revenue_ttm"]["value"] is None
    assert snap["metrics"]["sales_revenue_ttm"]["quality"] == "unavailable"


def test_annual_trends_and_cagr(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite")
    # 2020 -> 2025 revenue grows 100 -> 200, so 5y CAGR is 2^(1/5)-1.
    for year, revenue in ((2020, 100), (2021, 115), (2022, 130), (2023, 150), (2024, 170), (2025, 200)):
        _save(db, tmp_path, source_id=f"fy-{year}", year=year, quarter=None, period_end=date(year, 12, 31),
              publish_date=f"{year+1}-04-15", currency="EUR",
              values=_vals(revenue, revenue * .1, revenue * .12, revenue * .07, revenue * .09, revenue * .05, 100 + year - 2020),
              audited=True)
    data = build_trends(db, "TEST", years=7)
    assert len(data["annual"]) == 6
    assert data["growth"]["sales_revenue"]["cagr_5y"] == pytest.approx(2 ** (1/5) - 1)
    latest = data["annual"][-1]
    assert latest["year"] == 2025
    assert latest["ebit_margin"] == pytest.approx(.1)
