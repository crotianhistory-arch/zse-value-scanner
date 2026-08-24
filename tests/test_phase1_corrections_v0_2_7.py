from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from zse_tool.analytics import build_ttm_snapshot
from zse_tool.metrics import calculate_metrics
from zse_tool.models import EhoItem, Fact, Metric, ParsedReport
from zse_tool.storage import Database


def _report(facts: list[Fact]) -> ParsedReport:
    return ParsedReport(
        source_path=Path("test.xlsx"), issuer_name="TEST",
        period_start=date(2026, 1, 1), period_end=date(2026, 3, 31),
        year=2026, quarter=1, consolidated=True, audited=False,
        currency="EUR", scale=1.0, facts=facts, warnings=[],
    )


def test_comprehensive_income_preserves_prior_year_comparative_from_current_filing():
    facts = [
        Fact("income_statement", 100, "Pripisana imateljima kapitala matice", "previous_cumulative", 38.52, "EUR", "RDG", 100),
        Fact("income_statement", 100, "Pripisana imateljima kapitala matice", "current_cumulative", 40.73, "EUR", "RDG", 100),
        Fact("income_statement", 100, "Pripisana imateljima kapitala matice", "current_quarter", 40.73, "EUR", "RDG", 100),
    ]
    metrics = {m.name: m for m in calculate_metrics(_report(facts))}
    assert metrics["comprehensive_income_parent_ytd"].value == pytest.approx(40.73)
    assert metrics["comprehensive_income_parent_previous_cumulative"].value == pytest.approx(38.52)


def _save(db: Database, root: Path, sid: str, year: int, quarter: int | None, end: date,
          values: dict[str, tuple[float, str] | float], audited: bool = False):
    path = root / f"{sid}.xlsx"
    path.write_bytes(b"x")
    url = f"https://eho.zse.hr/{sid}.xlsx"
    db.upsert_items([EhoItem(source_id=sid, variant="financialReports", issuer_code="TEST", issuer_name="TEST",
                             title=None, publish_date=end.isoformat(), item_link=None, raw={}, document_urls=[url])])
    db.mark_downloaded(source_id=sid, variant="financialReports", url=url, local_path=path,
                       sha256=sid, byte_size=1)
    report = ParsedReport(source_path=path, issuer_name="TEST", period_start=date(year, 1, 1), period_end=end,
                          year=year, quarter=quarter, consolidated=True, audited=audited,
                          currency="EUR", scale=1.0, facts=[], warnings=[])
    metrics = []
    for name, raw in values.items():
        value, unit = raw if isinstance(raw, tuple) else (raw, "EUR")
        metrics.append(Metric(name, value, unit))
    db.save_parsed_report(report, source_id=sid, source_variant="financialReports", source_url=url,
                          source_publish_date=end.isoformat(), source_sha256=sid, issuer_code="TEST", metrics=metrics)


def _minimum_flows(v: float = 1.0) -> dict[str, float]:
    # Every flow can use the same placeholder; only comprehensive income is asserted.
    names = [
        "sales_revenue_ytd", "operating_revenue_ytd", "total_revenue_ytd", "ebit_ytd",
        "depreciation_ytd", "value_adjustments_ytd", "provisions_ytd",
        "md_value_adjustments_provisions_ytd", "ebitda_simple_ytd", "financial_income_ytd",
        "financial_expense_ytd", "ebt_ytd", "md_ebit_ytd", "md_ebitda_ytd",
        "net_income_parent_ytd", "net_income_total_ytd", "interest_expense_ytd",
        "operating_cash_flow_ytd", "asset_sale_proceeds_ytd", "capex_ytd", "net_capex_ytd",
        "free_cash_flow_ytd", "free_cash_flow_net_capex_ytd",
    ]
    out = {n: v for n in names}
    out.update({
        "cash": 10.0, "short_term_financial_assets": 0.0, "liquid_financial_assets": 10.0,
        "current_assets": 50.0, "current_liabilities": 20.0, "long_term_liabilities": 10.0,
        "total_debt_md": 30.0, "total_assets": 100.0, "equity_total": 60.0,
        "equity_nci": 0.0, "equity_parent": 60.0, "share_capital": 20.0,
        "retained_earnings_reserves_parent": 40.0, "gross_financial_debt_ex_other": 5.0,
        "gross_financial_debt_standardized": 5.0, "net_debt_ex_other": -5.0,
        "net_debt_liquid_assets": -5.0,
    })
    return out


def test_q1_comprehensive_ttm_prefers_comparative_column_in_current_filing(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite")
    fy = _minimum_flows(10.0)
    fy["comprehensive_income_parent_ytd"] = 144.20
    _save(db, tmp_path, "fy25", 2025, None, date(2025, 12, 31), fy, audited=True)

    # Deliberately wrong prior-Q1 normalized value: v0.2.7 must not depend on it.
    q125 = _minimum_flows(2.0)
    q125["comprehensive_income_parent_ytd"] = 144.20
    q125["comprehensive_income_parent_quarter"] = 144.20
    _save(db, tmp_path, "q125", 2025, 1, date(2025, 3, 31), q125)

    q126 = _minimum_flows(3.0)
    q126["comprehensive_income_parent_ytd"] = 40.73
    q126["comprehensive_income_parent_previous_cumulative"] = 38.52
    q126["comprehensive_income_parent_quarter"] = 40.73
    _save(db, tmp_path, "q126", 2026, 1, date(2026, 3, 31), q126)

    snap = build_ttm_snapshot(db, "TEST", "2026-Q1")
    comp = snap["metrics"]["comprehensive_income_parent_ttm"]
    assert comp["value"] == pytest.approx(146.41)
    assert "comparative from 2026-Q1" in comp["formula"]
    assert comp["sources"][-1]["metric"] == "comprehensive_income_parent_previous_cumulative"
    assert comp["sources"][-1]["coefficient"] == -1.0
    assert comp["sources"][-1]["comparative_period"] == "2025-Q1"


def test_employee_summary_prefers_explicit_period_end_but_keeps_average():
    facts = [
        Fact("supplemental", 1, "avg", "employees_average_group", 5687.0, "count", "Bilješke", 10),
        Fact("supplemental", 1, "end", "employees_period_end_group", 5747.0, "count", "Bilješke", 11),
    ]
    metrics = {m.name: m for m in calculate_metrics(_report(facts))}
    assert metrics["employees_average_current_period"].value == 5687.0
    assert metrics["employees_period_end"].value == 5747.0
    assert metrics["employees_reported"].value == 5747.0
    assert "period-end" in (metrics["employees_reported"].note or "")
