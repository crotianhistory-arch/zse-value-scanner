from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from zse_tool.metrics import calculate_metrics
from zse_tool.models import Fact, ParsedReport


def _fact(statement: str, code: int, label: str, value: float, column: str = "current_period") -> Fact:
    return Fact(statement=statement, adp_code=code, label=label, column_name=column,
                value=value, unit="EUR", source_sheet="X", source_row=code)


def _report(facts: list[Fact]) -> ParsedReport:
    return ParsedReport(source_path=Path("x.xlsx"), issuer_name="TEST", period_start=date(2026,1,1),
                        period_end=date(2026,3,31), year=2026, quarter=1, consolidated=True,
                        audited=False, currency="EUR", scale=1.0, facts=facts, warnings=[])


def test_other_operating_expenses_cannot_masquerade_as_total_operating_expenses():
    facts = [
        _fact("income_statement", 1, "I. POSLOVNI PRIHODI (AOP 002 do 006)", 340.0, "current_cumulative"),
        _fact("income_statement", 7, "II. POSLOVNI RASHODI (AOP 008+009+013+017+018+019+022+029)", 280.0, "current_cumulative"),
        _fact("income_statement", 29, "8. Ostali poslovni rashodi", 5.0, "current_cumulative"),
        _fact("income_statement", 17, "4. Amortizacija", 7.7, "current_cumulative"),
    ]
    m = {x.name: x for x in calculate_metrics(_report(facts))}
    assert m["ebit_ytd"].value == pytest.approx(60.0)
    assert m["ebitda_simple_ytd"].value == pytest.approx(67.7)


def test_official_aop_fallbacks_fill_md_comparison_inputs():
    facts = [
        _fact("balance_sheet", 37, "C) KRATKOTRAJNA IMOVINA (AOP 038+046+053+063)", 900.0),
        _fact("balance_sheet", 53, "III. KRATKOTRAJNA FINANCIJSKA IMOVINA (AOP 054 do 062)", 249.2),
        _fact("balance_sheet", 63, "IV. NOVAC U BANCI I BLAGAJNI", 252.44),
        _fact("balance_sheet", 97, "C) DUGOROČNE OBVEZE (AOP 098 do 108)", 225.0),
        _fact("balance_sheet", 109, "D) KRATKOROČNE OBVEZE (AOP 110 do 123)", 460.0),
        _fact("balance_sheet", 190, "1. Pripisano imateljima kapitala matice", 678.18),
        _fact("income_statement", 1, "I. POSLOVNI PRIHODI (AOP 002 do 006)", 340.0, "current_cumulative"),
        _fact("income_statement", 7, "II. POSLOVNI RASHODI (AOP 008+009+013+017+018+019+022+029)", 280.0, "current_cumulative"),
        _fact("income_statement", 17, "4. Amortizacija", 7.7, "current_cumulative"),
        _fact("income_statement", 19, "6. Vrijednosna usklađenja (AOP 020+021)", 10.0, "current_cumulative"),
        _fact("income_statement", 30, "III. FINANCIJSKI PRIHODI (AOP 031 do 040)", 20.0, "current_cumulative"),
        _fact("income_statement", 41, "IV. FINANCIJSKI RASHODI (AOP 042 do 048)", 5.0, "current_cumulative"),
        _fact("income_statement", 53, "IX. UKUPNI PRIHODI (AOP 001+030+049+050)", 350.0, "current_cumulative"),
        _fact("income_statement", 55, "XI. DOBIT ILI GUBITAK PRIJE OPOREZIVANJA (AOP 053-054)", 80.0, "current_cumulative"),
    ]
    m = {x.name: x for x in calculate_metrics(_report(facts))}
    assert m["short_term_financial_assets"].value == pytest.approx(249.2)
    assert m["liquid_financial_assets"].value == pytest.approx(501.64)
    assert m["current_assets"].value == pytest.approx(900.0)
    assert m["current_liabilities"].value == pytest.approx(460.0)
    assert m["long_term_liabilities"].value == pytest.approx(225.0)
    assert m["total_debt_md"].value == pytest.approx(685.0)
    assert m["current_ratio"].value == pytest.approx(900/460)
    assert m["md_ebit_ytd"].value == pytest.approx(65.0)
    assert m["md_ebitda_ytd"].value == pytest.approx(82.7)
    assert m["total_revenue_ytd"].value == pytest.approx(350.0)
