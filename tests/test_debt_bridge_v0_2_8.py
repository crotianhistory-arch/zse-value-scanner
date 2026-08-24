from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

import zse_tool.valuation as valuation_mod
from zse_tool.metrics import calculate_metrics
from zse_tool.models import Fact, ParsedReport
from zse_tool.valuation import build_md_comparison, build_valuation


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


def test_debt_bridge_preserves_published_md_and_adds_observed_compatibility():
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
        source_path=Path("debt-bridge.xlsx"), issuer_name="TEST",
        period_start=date(2026, 1, 1), period_end=date(2026, 3, 31),
        year=2026, quarter=1, consolidated=True, audited=False,
        currency="EUR", scale=1.0, facts=facts, warnings=[],
    )
    m = {x.name: x for x in calculate_metrics(report)}

    # Existing/published-method fields are untouched.
    assert m["md_short_term_financial_debt"].value == pytest.approx(70.0)
    assert m["md_financial_debt"].value == pytest.approx(420.0)
    assert m["md_net_debt_liquid_assets"].value == pytest.approx(390.0)

    # Observed compatibility adds ONLY non-related generic ST loans/deposits.
    assert m["md_observed_short_term_financial_debt"].value == pytest.approx(77.0)
    assert m["md_observed_financial_debt"].value == pytest.approx(427.0)
    assert m["md_observed_net_debt_liquid_assets"].value == pytest.approx(397.0)

    # Rich debt bridge retains economic information instead of collapsing it.
    assert m["explicit_long_term_financing_debt"].value == pytest.approx(300.0)
    assert m["explicit_short_term_financing_debt"].value == pytest.approx(88.0)
    assert m["external_financing_debt"].value == pytest.approx(347.0)
    assert m["related_party_financing_debt"].value == pytest.approx(41.0)
    assert m["gross_financial_debt_standardized"].value == pytest.approx(388.0)
    assert m["unclassified_long_term_liabilities_residual"].value == pytest.approx(50.0)
    assert m["unclassified_current_liabilities_residual"].value == pytest.approx(82.0)
    assert m["total_liabilities"].value == pytest.approx(520.0)


class _DummyDB:
    def latest_market_snapshot(self, ticker: str):
        return {
            "ticker": ticker,
            "isin": "HRGRNLRA0006",
            "observed_at": "2026-08-20T08:43:11+00:00",
            "source_url": "https://zse.hr/",
            "source_kind": "official_zse_instrument_page",
            "listed_quantity": 1_901_643,
            "market_cap_eur": 9_888_543.60,  # 1,901,643 shares * EUR 5.20
            "implied_price_eur": 5.20,
            "price_basis": "test",
            "quality": "test",
            "note": "test",
        }


def test_compare_md_uses_observed_bridge_but_keeps_published_ev(monkeypatch):
    def fake_ttm(db, ticker, as_of=None):
        values = {
            "md_net_debt_liquid_assets": 31_720_000.0,
            "md_observed_net_debt_liquid_assets": 36_468_500.0,
            "md_financial_debt": 35_640_000.0,
            "md_observed_financial_debt": 40_388_500.0,
            "md_ebitda_ttm": 7_410_000.0,
            "md_ebit_ttm": 3_300_000.0,
        }
        return {
            "ok": True,
            "ticker": ticker,
            "as_of": as_of or "2026-Q1",
            "period_end": "2026-03-31",
            "source_id": "grnl-q1",
            "consolidated": True,
            "audited": False,
            "equity_attribution_warning": None,
            "metrics": {k: {"value": v} for k, v in values.items()},
        }

    monkeypatch.setattr(valuation_mod, "build_ttm_snapshot", fake_ttm)
    db = _DummyDB()
    v = build_valuation(db, "GRNL", "2026-Q1")

    published_ev = 9_888_543.60 + 31_720_000.0
    observed_ev = 9_888_543.60 + 36_468_500.0
    assert v["metrics"]["enterprise_value_md_eur"]["value"] == pytest.approx(published_ev)
    assert v["metrics"]["enterprise_value_md_observed_eur"]["value"] == pytest.approx(observed_ev)
    assert v["metrics"]["ev_md_observed_to_md_ebitda_ttm"]["value"] == pytest.approx(observed_ev / 7_410_000.0)
    assert v["metrics"]["ev_md_observed_to_md_ebitda_ttm"]["value"] == pytest.approx(6.255, abs=0.01)

    comp = build_md_comparison(db, "GRNL", ["2026-Q1"])
    assert comp["periods"][0]["ev_to_ebitda"] == pytest.approx(observed_ev / 7_410_000.0)
    assert comp["periods"][0]["ev_to_ebitda_published_method"] == pytest.approx(published_ev / 7_410_000.0)
