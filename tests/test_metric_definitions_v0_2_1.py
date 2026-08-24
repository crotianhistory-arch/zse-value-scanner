from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from zse_tool.metrics import calculate_metrics
from zse_tool.models import EhoItem, Fact, Metric, ParsedReport
from zse_tool.storage import Database
from zse_tool.valuation import build_md_comparison, build_valuation


def _fact(statement: str, code: int, label: str, value: float, column: str = "current_period") -> Fact:
    return Fact(statement=statement, adp_code=code, label=label, column_name=column,
                value=value, unit="EUR", source_sheet="X", source_row=code)


def test_label_first_parent_equity_and_md_definitions():
    facts = [
        _fact("balance_sheet", 67, "A. KAPITAL I REZERVE", 919.11),
        _fact("balance_sheet", 190, "1. Pripisano imateljima kapitala matice", 678.18),
        _fact("balance_sheet", 191, "2. Pripisano nekontrolirajućem interesu", 240.93),
        _fact("balance_sheet", 50, "KRATKOTRAJNA FINANCIJSKA IMOVINA", 249.20),
        _fact("balance_sheet", 63, "NOVAC I NOVČANI EKVIVALENTI", 252.44),
        _fact("balance_sheet", 60, "KRATKOTRAJNA IMOVINA", 900.0),
        _fact("balance_sheet", 110, "KRATKOROČNE OBVEZE", 460.0),
        _fact("balance_sheet", 100, "DUGOROČNE OBVEZE", 225.0),
        _fact("balance_sheet", 65, "UKUPNO AKTIVA", 1718.79),
        _fact("balance_sheet", 103, "bank debt LT", 100.0),
        _fact("balance_sheet", 115, "bank debt ST", 68.0),
        _fact("income_statement", 500, "PRIHODI OD PRODAJE", 330.5, "current_cumulative"),
        _fact("income_statement", 501, "POSLOVNI PRIHODI", 340.0, "current_cumulative"),
        _fact("income_statement", 502, "POSLOVNI RASHODI", 280.0, "current_cumulative"),
        _fact("income_statement", 503, "AMORTIZACIJA", 7.7, "current_cumulative"),
        _fact("income_statement", 504, "VRIJEDNOSNA USKLAĐENJA", 10.0, "current_cumulative"),
        _fact("income_statement", 505, "FINANCIJSKI PRIHODI", 20.0, "current_cumulative"),
        _fact("income_statement", 506, "FINANCIJSKI RASHODI", 5.0, "current_cumulative"),
        _fact("income_statement", 507, "DOBIT ILI GUBITAK PRIJE OPOREZIVANJA", 80.0, "current_cumulative"),
        _fact("income_statement", 508, "UKUPNI PRIHODI", 350.0, "current_cumulative"),
        _fact("income_statement", 509, "Pripisana imateljima kapitala matice", 48.8, "current_cumulative"),
        _fact("income_statement", 510, "DOBIT ILI GUBITAK RAZDOBLJA", 72.7, "current_cumulative"),
        _fact("cash_flow_direct", 14, "NETO NOVČANI TOKOVI OD POSLOVNIH AKTIVNOSTI", 100.0),
        _fact("cash_flow_direct", 22, "Novčani izdaci za kupnju dugotrajne materijalne i nematerijalne imovine", -16.0),
        _fact("cash_flow_direct", 15, "Novčani primici od prodaje dugotrajne materijalne i nematerijalne imovine", 3.0),
    ]
    report = ParsedReport(source_path=Path("x.xlsx"), issuer_name="TEST", period_start=date(2026,1,1),
                          period_end=date(2026,3,31), year=2026, quarter=1, consolidated=True,
                          audited=False, currency="EUR", scale=1.0, facts=facts, warnings=[])
    m = {x.name: x for x in calculate_metrics(report)}

    assert m["equity_parent"].value == pytest.approx(678.18)
    assert m["equity_parent"].quality == "reported-addendum"
    assert m["equity_nci"].value == pytest.approx(240.93)
    assert m["liquid_financial_assets"].value == pytest.approx(501.64)
    assert m["ebit_ytd"].value == pytest.approx(60.0)
    assert m["ebitda_simple_ytd"].value == pytest.approx(67.7)
    assert m["md_ebit_ytd"].value == pytest.approx(65.0)
    assert m["md_ebitda_ytd"].value == pytest.approx(82.7)
    assert m["net_capex_ytd"].value == pytest.approx(13.0)
    assert m["free_cash_flow_ytd"].value == pytest.approx(84.0)
    assert m["free_cash_flow_net_capex_ytd"].value == pytest.approx(87.0)
    assert m["current_ratio"].value == pytest.approx(900/460)
    assert m["total_debt_to_equity_parent_md"].value == pytest.approx(685/678.18)


def test_consolidated_missing_nci_does_not_assume_zero():
    facts = [
        _fact("balance_sheet", 67, "KAPITAL I REZERVE", 100.0),
        _fact("balance_sheet", 65, "UKUPNO AKTIVA", 200.0),
    ]
    report = ParsedReport(source_path=Path("x.xlsx"), issuer_name="TEST", period_start=date(2026,1,1),
                          period_end=date(2026,3,31), year=2026, quarter=1, consolidated=True,
                          audited=False, currency="EUR", scale=1.0, facts=facts, warnings=[])
    m = {x.name: x for x in calculate_metrics(report)}
    assert m["equity_total"].value == 100.0
    assert m["equity_parent"].value is None
    assert m["equity_parent"].quality == "unavailable"


def _save(db: Database, root: Path, sid: str, year: int, quarter: int | None, end: date,
          values: dict[str, float], audited: bool = False):
    path = root / f"{sid}.xlsx"
    path.write_bytes(b"x")
    url = f"https://eho.zse.hr/{sid}.xlsx"
    db.upsert_items([EhoItem(source_id=sid, variant="financialReports", issuer_code="TEST",
                             issuer_name="TEST", title=None, publish_date=end.isoformat(), item_link=None,
                             raw={}, document_urls=[url])])
    db.mark_downloaded(source_id=sid, variant="financialReports", url=url, local_path=path,
                       sha256=sid, byte_size=1)
    report = ParsedReport(source_path=path, issuer_name="TEST", period_start=date(year,1,1),
                          period_end=end, year=year, quarter=quarter, consolidated=True, audited=audited,
                          currency="EUR", scale=1.0, facts=[], warnings=[])
    db.save_parsed_report(report, source_id=sid, source_variant="financialReports", source_url=url,
                          source_publish_date=end.isoformat(), source_sha256=sid, issuer_code="TEST",
                          metrics=[Metric(k,v,"EUR") for k,v in values.items()])


def _vals(rev: float, oprev: float, md_ebit: float, md_ebitda: float, ni: float, eq: float):
    return {
        "sales_revenue_ytd": rev,
        "operating_revenue_ytd": oprev,
        "total_revenue_ytd": oprev + 5,
        "ebit_ytd": md_ebit - 5,
        "depreciation_ytd": 10,
        "value_adjustments_ytd": md_ebitda - md_ebit - 10,
        "ebitda_simple_ytd": md_ebit + 5,
        "financial_income_ytd": 20,
        "financial_expense_ytd": 5,
        "ebt_ytd": md_ebit + 15,
        "md_ebit_ytd": md_ebit,
        "md_ebitda_ytd": md_ebitda,
        "net_income_parent_ytd": ni,
        "net_income_total_ytd": ni + 5,
        "interest_expense_ytd": 2,
        "operating_cash_flow_ytd": ni + 20,
        "asset_sale_proceeds_ytd": 2,
        "capex_ytd": 10,
        "net_capex_ytd": 8,
        "free_cash_flow_ytd": ni + 10,
        "free_cash_flow_net_capex_ytd": ni + 12,
        "cash": 50,
        "short_term_financial_assets": 25,
        "liquid_financial_assets": 75,
        "current_assets": 300,
        "current_liabilities": 150,
        "long_term_liabilities": 50,
        "total_debt_md": 200,
        "total_assets": 500,
        "equity_total": eq + 50,
        "equity_nci": 50,
        "equity_parent": eq,
        "gross_financial_debt_ex_other": 60,
        "net_debt_ex_other": 10,
        "net_debt_liquid_assets": -15,
    }


def test_as_of_valuation_and_md_comparison_use_current_market_snapshot(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite")
    _save(db,tmp_path,"q1-25",2025,1,date(2025,3,31),_vals(200,210,30,45,20,180))
    _save(db,tmp_path,"fy-25",2025,None,date(2025,12,31),_vals(1000,1020,160,200,100,250),audited=True)
    _save(db,tmp_path,"q1-26",2026,1,date(2026,3,31),_vals(250,260,45,60,30,280))
    _save(db,tmp_path,"fy-24",2024,None,date(2024,12,31),_vals(800,820,120,150,70,200),audited=True)
    db.save_market_snapshot({
        "ticker":"TEST","isin":"HRTESTRA0001","observed_at":"2026-08-14T12:00:00+00:00",
        "source_url":"https://zse.hr/x","source_kind":"official_zse_instrument_page","listed_quantity":100,
        "market_cap_eur":1000.0,"implied_price_eur":10.0,"price_basis":"cap/qty","quality":"official-zse-market-cap",
        "note":"15 minute delay","raw_json":"{}",
    })

    v = build_valuation(db,"TEST","2025-FY")
    assert v["financials"]["as_of"] == "2025-FY"
    assert v["market"]["market_cap_eur"] == 1000.0
    assert v["metrics"]["price_to_book_parent"]["value"] == pytest.approx(4.0)
    assert v["metrics"]["eps_ttm_per_listed_share"]["value"] == pytest.approx(1.0)

    comp = build_md_comparison(db,"TEST",["2026-Q1","2025-FY","2024-FY"])
    assert [r["period"] for r in comp["periods"]] == ["2026-Q1","2025-FY","2024-FY"]
    # 2026-Q1 TTM = FY25 + Q1-26 - Q1-25
    assert comp["periods"][0]["sales_revenue"] == pytest.approx(1050.0)
    assert comp["periods"][0]["md_ebit"] == pytest.approx(175.0)
    assert comp["periods"][0]["p_e"] == pytest.approx(1000/110)
    assert "not historical-price valuation" in comp["note"]
