from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from zse_tool.metrics import calculate_metrics
from zse_tool.models import EhoItem, Fact, Metric, ParsedReport
from zse_tool.storage import Database
from zse_tool.valuation import build_debt_snapshot, build_md_comparison, build_valuation


def _fact(code: int, row: int, label: str, value: float) -> Fact:
    return Fact(
        statement="balance_sheet",
        adp_code=code,
        label=label,
        column_name="current_period",
        value=value,
        unit="EUR",
        source_sheet="Bilanca",
        source_row=row,
    )


def test_section_aware_debt_decomposition_and_md_formula():
    facts = [
        _fact(900, 100, "C) DUGOROČNE OBVEZE (AOP 901 do 911)", 350.0),
        _fact(901, 102, "2. Obveze za zajmove, depozite i slično poduzetnika unutar grupe", 10.0),
        _fact(902, 104, "4. Obveze za zajmove, depozite i slično društava povezanih sudjelujućim interesom", 20.0),
        _fact(903, 105, "5. Obveze za zajmove, depozite i slično", 30.0),
        _fact(904, 106, "6. Obveze prema bankama i drugim financijskim institucijama", 200.0),
        _fact(905, 109, "9. Obveze po vrijednosnim papirima", 40.0),
        _fact(950, 200, "D) KRATKOROČNE OBVEZE (AOP 951 do 964)", 170.0),
        _fact(951, 202, "2. Obveze za zajmove, depozite i slično poduzetnika unutar grupe", 5.0),
        _fact(952, 204, "4. Obveze za zajmove, depozite i slično društava povezanih sudjelujućim interesom", 6.0),
        _fact(953, 205, "5. Obveze za zajmove, depozite i slično", 7.0),
        _fact(954, 206, "6. Obveze prema bankama i drugim financijskim institucijama", 50.0),
        _fact(955, 209, "9. Obveze po vrijednosnim papirima", 20.0),
        _fact(800, 50, "KRATKOTRAJNA FINANCIJSKA IMOVINA", 10.0),
        _fact(801, 60, "NOVAC I NOVČANI EKVIVALENTI", 20.0),
        _fact(802, 70, "UKUPNO AKTIVA", 1000.0),
        _fact(803, 80, "KAPITAL I REZERVE", 500.0),
        _fact(804, 81, "Pripisano imateljima kapitala matice", 480.0),
    ]
    report = ParsedReport(
        source_path=Path("debt.xlsx"), issuer_name="TEST",
        period_start=date(2026, 1, 1), period_end=date(2026, 3, 31),
        year=2026, quarter=1, consolidated=True, audited=False,
        currency="EUR", scale=1.0, facts=facts, warnings=[],
    )
    m = {x.name: x for x in calculate_metrics(report)}

    # Duplicate labels are correctly separated by LT/ST source-row sections.
    assert m["debt_lt_banks_financial_institutions"].value == 200.0
    assert m["debt_st_banks_financial_institutions"].value == 50.0
    assert m["debt_lt_securities"].value == 40.0
    assert m["debt_st_securities"].value == 20.0

    # Scanner debt = explicit financing detail rows only.
    assert m["gross_financial_debt_standardized"].value == pytest.approx(388.0)
    assert m["net_debt_liquid_assets"].value == pytest.approx(358.0)

    # Public MojeDionice formula = all LT liabilities + ST banks + ST securities.
    assert m["md_short_term_financial_debt"].value == pytest.approx(70.0)
    assert m["md_financial_debt"].value == pytest.approx(420.0)
    assert m["md_net_debt_liquid_assets"].value == pytest.approx(390.0)


def test_md_short_term_debt_falls_back_to_all_current_liabilities_when_breakdown_missing():
    facts = [
        _fact(900, 100, "C) DUGOROČNE OBVEZE", 300.0),
        _fact(950, 200, "D) KRATKOROČNE OBVEZE", 180.0),
        _fact(800, 50, "NOVAC I NOVČANI EKVIVALENTI", 20.0),
        _fact(802, 70, "UKUPNO AKTIVA", 1000.0),
        _fact(803, 80, "KAPITAL I REZERVE", 500.0),
        _fact(804, 81, "Pripisano imateljima kapitala matice", 480.0),
    ]
    report = ParsedReport(
        source_path=Path("fallback.xlsx"), issuer_name="TEST",
        period_start=date(2026, 1, 1), period_end=date(2026, 3, 31),
        year=2026, quarter=1, consolidated=True, audited=False,
        currency="EUR", scale=1.0, facts=facts, warnings=[],
    )
    m = {x.name: x for x in calculate_metrics(report)}
    assert m["md_short_term_financial_debt"].value == 180.0
    assert "total-current-liabilities-fallback" in (m["md_short_term_financial_debt"].note or "")
    assert m["md_financial_debt"].value == 480.0


def _save_fy(db: Database, root: Path) -> None:
    sid = "fy-25"
    path = root / "fy-25.xlsx"
    path.write_bytes(b"x")
    url = "https://eho.zse.hr/fy-25.xlsx"
    db.upsert_items([EhoItem(
        source_id=sid, variant="financialReports", issuer_code="TEST", issuer_name="TEST",
        title=None, publish_date="2026-03-31", item_link=None, raw={}, document_urls=[url],
    )])
    db.mark_downloaded(source_id=sid, variant="financialReports", url=url, local_path=path,
                       sha256=sid, byte_size=1)
    report = ParsedReport(
        source_path=path, issuer_name="TEST", period_start=date(2025, 1, 1),
        period_end=date(2025, 12, 31), year=2025, quarter=None, consolidated=True,
        audited=True, currency="EUR", scale=1.0, facts=[], warnings=[],
    )
    values = {
        "sales_revenue_ytd": 1013.86,
        "operating_revenue_ytd": 1107.85,
        "total_revenue_ytd": 1108.0,
        "ebit_ytd": 150.0,
        "depreciation_ytd": 50.0,
        "value_adjustments_ytd": 10.05,
        "ebitda_simple_ytd": 200.0,
        "financial_income_ytd": 5.0,
        "financial_expense_ytd": 10.0,
        "ebt_ytd": 164.56,
        "md_ebit_ytd": 169.56,
        "md_ebitda_ytd": 229.61,
        "net_income_parent_ytd": 135.38,
        "net_income_total_ytd": 136.0,
        "interest_expense_ytd": 10.0,
        "operating_cash_flow_ytd": 99.3,
        "capex_ytd": 70.0,
        "free_cash_flow_ytd": 29.3,
        "asset_sale_proceeds_ytd": 6.0,
        "net_capex_ytd": 64.0,
        "free_cash_flow_net_capex_ytd": 35.3,
        "cash": 20.0,
        "short_term_financial_assets": 20.3,
        "liquid_financial_assets": 40.3,
        "current_assets": 600.0,
        "current_liabilities": 224.0,
        "long_term_liabilities": 400.0,
        "total_debt_md": 624.0,
        "debt_st_banks_financial_institutions": 25.0,
        "debt_st_securities": 5.0,
        "md_short_term_financial_debt": 30.0,
        "md_financial_debt": 430.0,
        "md_net_debt_liquid_assets": 389.7,
        "gross_financial_debt_ex_other": 80.0,
        "gross_financial_debt_standardized": 80.0,
        "net_debt_ex_other": 60.0,
        "net_debt_liquid_assets": 39.7,
        "total_assets": 1349.96,
        "equity_total": 730.0,
        "equity_nci": 1.92,
        "equity_parent": 728.08,
    }
    db.save_parsed_report(
        report, source_id=sid, source_variant="financialReports", source_url=url,
        source_publish_date="2026-03-31", source_sha256=sid, issuer_code="TEST",
        metrics=[Metric(k, v, "EUR") for k, v in values.items()],
    )


def test_md_ev_uses_public_debt_convention_not_scanner_proxy(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite")
    _save_fy(db, tmp_path)
    db.save_market_snapshot({
        "ticker": "TEST", "isin": "HRTESTRA0001", "observed_at": "2026-08-14T22:39:58+00:00",
        "source_url": "https://zse.hr/en/instrument/310?isin=HRTESTRA0001",
        "source_kind": "official_zse_instrument_page", "listed_quantity": 7_120_003,
        "market_cap_eur": 1093.0, "implied_price_eur": 153.5,
        "price_basis": "ZSE published market cap / listed quantity", "quality": "official-zse-market-cap",
        "note": "15 minute delay", "raw_json": "{}",
    })

    valuation = build_valuation(db, "TEST", "2025-FY")
    assert valuation["metrics"]["enterprise_value_md_eur"]["value"] == pytest.approx(1482.7)
    assert valuation["metrics"]["ev_md_to_md_ebitda_ttm"]["value"] == pytest.approx(1482.7 / 229.61)

    # Scanner explicit financing debt remains a separate, narrower analytical definition.
    assert valuation["metrics"]["enterprise_value_eur"]["value"] == pytest.approx(1132.7)

    comp = build_md_comparison(db, "TEST", ["2025-FY"])
    assert comp["periods"][0]["ev_to_ebitda"] == pytest.approx(1482.7 / 229.61)
    assert "all long-term liabilities" in comp["ev_note"]

    debt = build_debt_snapshot(db, "TEST", "2025-FY")
    assert debt["metrics"]["md_financial_debt"]["value"] == pytest.approx(430.0)
