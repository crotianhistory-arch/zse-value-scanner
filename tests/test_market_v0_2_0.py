from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from zse_tool.cli import build_parser
from zse_tool.market import parse_instrument_market, parse_share_directory
from zse_tool.models import EhoItem, Metric, ParsedReport
from zse_tool.storage import Database
from zse_tool.valuation import build_valuation


DIRECTORY_HTML = """
<table>
<tr><th>Symbol</th><th>ISIN</th><th>Name</th><th>Sector</th><th>Listed Quantity</th><th>Nominal Value</th><th>Listing Date</th><th>Delisting Date</th></tr>
<tr><td>KOEI</td><td>HRKOEIRA0009</td><td>KONČAR d.d.</td><td>CJ</td><td>2,572,119</td><td>62.00 EUR</td><td>12/21/2010</td><td>-</td></tr>
<tr><td>OLD</td><td>HROLD0RA0001</td><td>Old d.d.</td><td>C</td><td>100</td><td>-</td><td>01/01/2000</td><td>01/01/2020</td></tr>
</table>
"""

INSTRUMENT_HTML = """
<html><body>
<div>ISIN</div><div>HRKOEIRA0009</div>
<div>Symbol</div><div>KOEI</div>
<div>Listed Quantity</div><div>2,572,119</div>
<div>Market Cap</div><div>2,597.84 mil. EUR</div>
</body></html>
"""


def test_parse_official_zse_directory_and_market_cap():
    sec = parse_share_directory(DIRECTORY_HTML, "koei")
    assert sec.isin == "HRKOEIRA0009"
    assert sec.listed_quantity == 2_572_119
    market = parse_instrument_market(INSTRUMENT_HTML, ticker="KOEI", isin=sec.isin, directory_quantity=sec.listed_quantity)
    assert market["market_cap_eur"] == pytest.approx(2_597_840_000)
    assert market["implied_price_eur"] == pytest.approx(1010.0, rel=1e-6)
    assert market["quality"] == "official-zse-market-cap"


def test_cli_exposes_market_and_valuation_commands():
    parser = build_parser()
    assert parser.parse_args(["market-sync", "--ticker", "KOEI"]).cmd == "market-sync"
    assert parser.parse_args(["market", "--ticker", "KOEI"]).cmd == "market"
    assert parser.parse_args(["valuation", "--ticker", "KOEI"]).cmd == "valuation"


def _save_report(db: Database, root: Path, *, sid: str, year: int, quarter: int | None,
                 end: date, publish: str, vals: dict[str, float], audited: bool = False):
    path = root / f"{sid}.xlsx"
    path.write_bytes(b"x")
    url = f"https://eho.zse.hr/{sid}.xlsx"
    db.upsert_items([EhoItem(source_id=sid, variant="financialReports", issuer_code="TEST",
                             issuer_name="TEST d.d.", title=None, publish_date=publish,
                             item_link=None, raw={}, document_urls=[url])])
    db.mark_downloaded(source_id=sid, variant="financialReports", url=url,
                       local_path=path, sha256=sid, byte_size=1)
    report = ParsedReport(source_path=path, issuer_name="TEST d.d.", period_start=date(year, 1, 1),
                          period_end=end, year=year, quarter=quarter, consolidated=True,
                          audited=audited, currency="EUR", scale=1.0, facts=[], warnings=[])
    db.save_parsed_report(report, source_id=sid, source_variant="financialReports",
                          source_url=url, source_publish_date=publish, source_sha256=sid,
                          issuer_code="TEST", metrics=[Metric(k, v, "EUR") for k, v in vals.items()])


def _vals(rev, ebit, ebitda, ni, cfo, fcf, equity):
    return {
        "sales_revenue_ytd": rev,
        "ebit_ytd": ebit,
        "depreciation_ytd": ebitda - ebit,
        "ebitda_simple_ytd": ebitda,
        "net_income_parent_ytd": ni,
        "net_income_total_ytd": ni,
        "interest_expense_ytd": 2.0,
        "operating_cash_flow_ytd": cfo,
        "capex_ytd": cfo - fcf,
        "free_cash_flow_ytd": fcf,
        "cash": 80.0,
        "total_assets": 1000.0,
        "equity_total": equity,
        "equity_parent": equity,
        "gross_financial_debt_ex_other": 30.0,
        "net_debt_ex_other": -50.0,
    }


def test_valuation_uses_official_market_cap_and_ttm(tmp_path: Path):
    db = Database(tmp_path / "db.sqlite")
    _save_report(db, tmp_path, sid="q2-25", year=2025, quarter=2, end=date(2025, 6, 30),
                 publish="2025-07-30", vals=_vals(400, 40, 50, 30, 35, 25, 200))
    _save_report(db, tmp_path, sid="fy-25", year=2025, quarter=None, end=date(2025, 12, 31),
                 publish="2026-04-16", vals=_vals(1000, 100, 120, 80, 90, 60, 240), audited=True)
    _save_report(db, tmp_path, sid="q2-26", year=2026, quarter=2, end=date(2026, 6, 30),
                 publish="2026-07-30", vals=_vals(500, 60, 70, 45, 55, 40, 260))
    db.save_market_snapshot({
        "ticker": "TEST", "isin": "HRTESTRA0001", "observed_at": "2026-08-14T12:00:00+00:00",
        "source_url": "https://zse.hr/en/instrument/310?isin=HRTESTRA0001",
        "source_kind": "official_zse_instrument_page", "listed_quantity": 100,
        "market_cap_eur": 1000.0, "implied_price_eur": 10.0,
        "price_basis": "ZSE published market cap / listed quantity", "quality": "official-zse-market-cap",
        "note": "15 minute delay", "raw_json": "{}",
    })
    data = build_valuation(db, "TEST")
    assert data["ok"] is True
    assert data["metrics"]["enterprise_value_eur"]["value"] == pytest.approx(950.0)
    assert data["metrics"]["pe_ttm"]["value"] == pytest.approx(1000 / 95)
    assert data["metrics"]["ev_to_ebitda_simple_ttm"]["value"] == pytest.approx(950 / 140)
    assert data["metrics"]["fcf_yield_ttm"]["value"] == pytest.approx(75 / 1000)
