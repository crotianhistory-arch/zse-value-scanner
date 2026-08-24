from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from zse_tool.metrics import calculate_metrics
from zse_tool.models import Fact, ParsedReport


def _fact(statement: str, code: int, label: str, value: float, column: str = "current_period", row: int | None = None) -> Fact:
    return Fact(
        statement=statement,
        adp_code=code,
        label=label,
        column_name=column,
        value=value,
        unit="EUR",
        source_sheet="X",
        source_row=code if row is None else row,
    )


def _report(facts: list[Fact]) -> ParsedReport:
    return ParsedReport(
        source_path=Path("test.xlsx"),
        issuer_name="TEST",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 3, 31),
        year=2026,
        quarter=1,
        consolidated=True,
        audited=False,
        currency="EUR",
        scale=1.0,
        facts=facts,
        warnings=[],
    )


def test_md_ebitda_adds_provisions_but_company_simple_ebitda_does_not():
    facts = [
        _fact("income_statement", 1, "POSLOVNI PRIHODI", 100.0, "current_cumulative"),
        _fact("income_statement", 7, "POSLOVNI RASHODI", 60.0, "current_cumulative"),
        _fact("income_statement", 17, "AMORTIZACIJA", 10.0, "current_cumulative"),
        _fact("income_statement", 19, "VRIJEDNOSNA USKLAĐENJA", 3.0, "current_cumulative"),
        _fact("income_statement", 22, "REZERVIRANJA", 7.0, "current_cumulative"),
        _fact("income_statement", 30, "FINANCIJSKI PRIHODI", 2.0, "current_cumulative"),
        _fact("income_statement", 41, "FINANCIJSKI RASHODI", 5.0, "current_cumulative"),
        _fact("income_statement", 55, "DOBIT ILI GUBITAK PRIJE OPOREZIVANJA", 37.0, "current_cumulative"),
    ]
    m = {x.name: x for x in calculate_metrics(_report(facts))}

    assert m["ebit_ytd"].value == pytest.approx(40.0)
    assert m["ebitda_simple_ytd"].value == pytest.approx(50.0)
    assert m["md_ebit_ytd"].value == pytest.approx(40.0)
    assert m["value_adjustments_ytd"].value == pytest.approx(3.0)
    assert m["provisions_ytd"].value == pytest.approx(7.0)
    assert m["md_value_adjustments_provisions_ytd"].value == pytest.approx(10.0)
    assert m["md_ebitda_ytd"].value == pytest.approx(60.0)


def test_md_ebitda_provisions_fallback_uses_aop22():
    # A translated/unfamiliar label should still be recovered by the official
    # standardized AOP 022 fallback rather than silently omitting provisions.
    facts = [
        _fact("income_statement", 17, "AMORTIZACIJA", 10.0, "current_cumulative"),
        _fact("income_statement", 19, "VRIJEDNOSNA USKLAĐENJA", 3.0, "current_cumulative"),
        _fact("income_statement", 22, "some template-specific wording", 7.0, "current_cumulative"),
        _fact("income_statement", 30, "FINANCIJSKI PRIHODI", 2.0, "current_cumulative"),
        _fact("income_statement", 41, "FINANCIJSKI RASHODI", 5.0, "current_cumulative"),
        _fact("income_statement", 55, "DOBIT ILI GUBITAK PRIJE OPOREZIVANJA", 37.0, "current_cumulative"),
    ]
    m = {x.name: x for x in calculate_metrics(_report(facts))}
    assert m["provisions_ytd"].value == pytest.approx(7.0)
    assert m["md_ebitda_ytd"].value == pytest.approx(60.0)


def test_retained_earnings_reserves_summary_is_parent_equity_less_share_capital():
    facts = [
        _fact("balance_sheet", 67, "KAPITAL I REZERVE", 919.11),
        _fact("balance_sheet", 190, "Pripisano imateljima kapitala matice", 678.18),
        _fact("balance_sheet", 191, "Pripisano nekontrolirajućem interesu", 240.93),
        _fact("balance_sheet", 68, "TEMELJNI (UPISANI) KAPITAL", 159.47),
    ]
    m = {x.name: x for x in calculate_metrics(_report(facts))}
    assert m["share_capital"].value == pytest.approx(159.47)
    assert m["retained_earnings_reserves_parent"].value == pytest.approx(518.71)


def test_comprehensive_income_parent_uses_current_aop100():
    facts = [
        _fact("income_statement", 100, "Pripisano imateljima kapitala matice", 146.41, "current_cumulative"),
    ]
    m = {x.name: x for x in calculate_metrics(_report(facts))}
    assert m["comprehensive_income_parent_ytd"].value == pytest.approx(146.41)

from zse_tool.analytics import build_ttm_snapshot
from zse_tool.models import EhoItem, Metric
from zse_tool.storage import Database


def _save_metrics(db: Database, root: Path, sid: str, year: int, quarter: int | None, end: date, values: dict[str, float], audited: bool = False):
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
    db.save_parsed_report(report, source_id=sid, source_variant="financialReports", source_url=url,
                          source_publish_date=end.isoformat(), source_sha256=sid, issuer_code="TEST",
                          metrics=[Metric(k, v, "EUR") for k, v in values.items()])


def test_ttm_provisions_and_report_status_flow_through_database(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite")
    base = {
        "sales_revenue_ytd": 100.0, "operating_revenue_ytd": 100.0, "total_revenue_ytd": 100.0,
        "ebit_ytd": 20.0, "depreciation_ytd": 10.0, "value_adjustments_ytd": 2.0,
        "provisions_ytd": 3.0, "md_value_adjustments_provisions_ytd": 5.0,
        "ebitda_simple_ytd": 30.0, "financial_income_ytd": 1.0, "financial_expense_ytd": 2.0,
        "ebt_ytd": 19.0, "md_ebit_ytd": 20.0, "md_ebitda_ytd": 35.0,
        "net_income_parent_ytd": 15.0, "net_income_total_ytd": 15.0,
        "comprehensive_income_parent_ytd": 16.0, "interest_expense_ytd": 1.0,
        "operating_cash_flow_ytd": 20.0, "asset_sale_proceeds_ytd": 0.0, "capex_ytd": 5.0,
        "net_capex_ytd": 5.0, "free_cash_flow_ytd": 15.0, "free_cash_flow_net_capex_ytd": 15.0,
        "cash": 10.0, "short_term_financial_assets": 0.0, "liquid_financial_assets": 10.0,
        "current_assets": 50.0, "current_liabilities": 20.0, "long_term_liabilities": 10.0,
        "total_debt_md": 30.0, "total_assets": 100.0, "equity_total": 60.0, "equity_nci": 0.0,
        "equity_parent": 60.0, "share_capital": 20.0, "retained_earnings_reserves_parent": 40.0,
        "gross_financial_debt_ex_other": 5.0, "gross_financial_debt_standardized": 5.0,
        "net_debt_ex_other": -5.0, "net_debt_liquid_assets": -5.0,
    }
    _save_metrics(db, tmp_path, "fy25", 2025, None, date(2025, 12, 31), base, audited=True)
    snap = build_ttm_snapshot(db, "TEST", "2025-FY")
    assert snap["consolidated"] is True
    assert snap["audited"] is True
    assert snap["metrics"]["provisions_ttm"]["value"] == pytest.approx(3.0)
    assert snap["metrics"]["md_value_adjustments_provisions_ttm"]["value"] == pytest.approx(5.0)
    assert snap["metrics"]["comprehensive_income_parent_ttm"]["value"] == pytest.approx(16.0)
    assert snap["metrics"]["retained_earnings_reserves_parent"]["value"] == pytest.approx(40.0)
