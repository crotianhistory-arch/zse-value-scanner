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
    return ParsedReport(source_path=Path("x.xlsx"), issuer_name="TEST", period_start=date(2026, 1, 1),
                        period_end=date(2026, 3, 31), year=2026, quarter=1, consolidated=True,
                        audited=False, currency="EUR", scale=1.0, facts=facts, warnings=[])


def test_aggregate_rows_with_trailing_aop_annotations_override_shifted_fallback_codes():
    facts = [
        _fact("balance_sheet", 500, "C) KRATKOTRAJNA IMOVINA (AOP 038+046+053+063)", 1220.0),
        _fact("balance_sheet", 37, "historic fallback now some detail", 1.0),
        _fact("balance_sheet", 501, "C) DUGOROČNE OBVEZE (AOP 098 do 108)", 60.0),
        _fact("balance_sheet", 97, "historic fallback now some detail", 2.0),
        _fact("balance_sheet", 502, "D) KRATKOROČNE OBVEZE (AOP 110 do 123)", 625.0),
        _fact("balance_sheet", 109, "historic fallback now some detail", 3.0),
        _fact("balance_sheet", 503, "A) KAPITAL I REZERVE (AOP 068 do 089)", 919.0),
        _fact("balance_sheet", 504, "1. Pripisano imateljima kapitala matice", 678.0),
        _fact("balance_sheet", 505, "UKUPNO AKTIVA (AOP 001+037)", 1719.0),
        _fact("income_statement", 600, "I. POSLOVNI PRIHODI (AOP 002 do 006)", 1365.0, "current_cumulative"),
        _fact("income_statement", 601, "II. POSLOVNI RASHODI (AOP 008+009+013+017+018+019+022+029)", 1145.0, "current_cumulative"),
        _fact("income_statement", 602, "4. AMORTIZACIJA (AOP 018)", 30.0, "current_cumulative"),
        _fact("income_statement", 603, "6. VRIJEDNOSNA USKLAĐENJA (AOP 020+021)", 6.0, "current_cumulative"),
        _fact("income_statement", 19, "historic fallback now some detail", 0.5, "current_cumulative"),
        _fact("income_statement", 604, "III. FINANCIJSKI PRIHODI (AOP 031 do 040)", 20.0, "current_cumulative"),
        _fact("income_statement", 605, "IV. FINANCIJSKI RASHODI (AOP 042 do 048)", 5.0, "current_cumulative"),
        _fact("income_statement", 606, "XI. DOBIT ILI GUBITAK PRIJE OPOREZIVANJA (AOP 053-054)", 280.0, "current_cumulative"),
    ]
    m = {x.name: x for x in calculate_metrics(_report(facts))}
    assert m["current_assets"].value == pytest.approx(1220.0)
    assert m["current_liabilities"].value == pytest.approx(625.0)
    assert m["long_term_liabilities"].value == pytest.approx(60.0)
    assert m["total_debt_md"].value == pytest.approx(685.0)
    assert m["current_ratio"].value == pytest.approx(1220.0 / 625.0)
    assert m["total_debt_to_equity_parent_md"].value == pytest.approx(685.0 / 678.0)
    assert m["value_adjustments_ytd"].value == pytest.approx(6.0)
    assert m["ebit_ytd"].value == pytest.approx(220.0)
    assert m["ebitda_simple_ytd"].value == pytest.approx(250.0)
    assert m["md_ebit_ytd"].value == pytest.approx(265.0)
    assert m["md_ebitda_ytd"].value == pytest.approx(301.0)
