from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .reporting import describe_report
from .storage import Database

# Croatia's irrevocable euro conversion rate. Historical HRK statement values
# are divided by this rate solely to put nominal money amounts on a comparable
# EUR scale; this is not an inflation adjustment.
HRK_PER_EUR = 7.53450

FLOW_METRICS = {
    "sales_revenue_ytd": "sales_revenue_ttm",
    "operating_revenue_ytd": "operating_revenue_ttm",
    "total_revenue_ytd": "total_revenue_ttm",
    "ebit_ytd": "ebit_ttm",
    "depreciation_ytd": "depreciation_ttm",
    "value_adjustments_ytd": "value_adjustments_ttm",
    "provisions_ytd": "provisions_ttm",
    "md_value_adjustments_provisions_ytd": "md_value_adjustments_provisions_ttm",
    "ebitda_simple_ytd": "ebitda_simple_ttm",
    "financial_income_ytd": "financial_income_ttm",
    "financial_expense_ytd": "financial_expense_ttm",
    "ebt_ytd": "ebt_ttm",
    "md_ebit_ytd": "md_ebit_ttm",
    "md_ebitda_ytd": "md_ebitda_ttm",
    "net_income_parent_ytd": "net_income_parent_ttm",
    "net_income_total_ytd": "net_income_total_ttm",
    "comprehensive_income_parent_ytd": "comprehensive_income_parent_ttm",
    "interest_expense_ytd": "interest_expense_ttm",
    "operating_cash_flow_ytd": "operating_cash_flow_ttm",
    "asset_sale_proceeds_ytd": "asset_sale_proceeds_ttm",
    "capex_ytd": "capex_ttm",
    "net_capex_ytd": "net_capex_ttm",
    "free_cash_flow_ytd": "free_cash_flow_ttm",
    "free_cash_flow_net_capex_ytd": "free_cash_flow_net_capex_ttm",
}

POINT_METRICS = (
    "cash",
    "short_term_financial_assets",
    "liquid_financial_assets",
    "current_assets",
    "current_liabilities",
    "long_term_liabilities",
    "total_debt_md",
    "debt_lt_group_loans",
    "debt_lt_participating_loans",
    "debt_lt_loans_deposits",
    "debt_lt_banks_financial_institutions",
    "debt_lt_securities",
    "debt_st_group_loans",
    "debt_st_participating_loans",
    "debt_st_loans_deposits",
    "debt_st_banks_financial_institutions",
    "debt_st_securities",
    "md_short_term_financial_debt",
    "md_financial_debt",
    "md_net_debt_liquid_assets",
    "md_observed_short_term_financial_debt",
    "md_observed_financial_debt",
    "md_observed_net_debt_liquid_assets",
    "total_assets",
    "equity_total",
    "equity_nci",
    "equity_parent",
    "share_capital",
    "retained_earnings_reserves_parent",
    "gross_financial_debt_ex_other",
    "gross_financial_debt_standardized",
    "explicit_long_term_financing_debt",
    "explicit_short_term_financing_debt",
    "external_financing_debt",
    "related_party_financing_debt",
    "unclassified_long_term_liabilities_residual",
    "unclassified_current_liabilities_residual",
    "total_liabilities",
    "net_debt_ex_other",
    "net_debt_liquid_assets",
)

COUNT_POINT_METRICS = (
    "employees_average_current_period",
    "employees_period_end",
    "employees_reported",
)

ANNUAL_COLUMNS = (
    "sales_revenue_ytd",
    "operating_revenue_ytd",
    "total_revenue_ytd",
    "ebit_ytd",
    "md_ebit_ytd",
    "ebitda_simple_ytd",
    "md_ebitda_ytd",
    "provisions_ytd",
    "md_value_adjustments_provisions_ytd",
    "net_income_parent_ytd",
    "net_income_total_ytd",
    "comprehensive_income_parent_ytd",
    "operating_cash_flow_ytd",
    "free_cash_flow_ytd",
    "free_cash_flow_net_capex_ytd",
    "cash",
    "short_term_financial_assets",
    "liquid_financial_assets",
    "current_assets",
    "current_liabilities",
    "long_term_liabilities",
    "total_debt_md",
    "md_short_term_financial_debt",
    "md_financial_debt",
    "md_net_debt_liquid_assets",
    "md_observed_short_term_financial_debt",
    "md_observed_financial_debt",
    "md_observed_net_debt_liquid_assets",
    "gross_financial_debt_ex_other",
    "gross_financial_debt_standardized",
    "explicit_long_term_financing_debt",
    "explicit_short_term_financing_debt",
    "external_financing_debt",
    "related_party_financing_debt",
    "unclassified_long_term_liabilities_residual",
    "unclassified_current_liabilities_residual",
    "total_liabilities",
    "net_debt_ex_other",
    "net_debt_liquid_assets",
    "equity_total",
    "equity_nci",
    "equity_parent",
    "share_capital",
    "retained_earnings_reserves_parent",
)


@dataclass(slots=True)
class ReportSlice:
    row: dict[str, Any]
    metrics: dict[str, dict[str, Any]]

    @property
    def label(self) -> str:
        return describe_report(self.row).period_label

    @property
    def year(self) -> int | None:
        value = self.row.get("year")
        return int(value) if value is not None else None

    @property
    def quarter(self) -> int | None:
        value = self.row.get("quarter")
        return int(value) if value is not None else None

    @property
    def period_end(self) -> str | None:
        return self.row.get("period_end")


@dataclass(slots=True)
class DerivedMetric:
    name: str
    value: float | None
    unit: str
    quality: str
    formula: str
    sources: list[dict[str, Any]] = field(default_factory=list)
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "quality": self.quality,
            "formula": self.formula,
            "sources": self.sources,
            "note": self.note,
        }


def _safe_ratio(a: float | None, b: float | None) -> float | None:
    if a is None or b in (None, 0):
        return None
    return a / b


def _money_to_eur(value: float | None, unit: str | None) -> tuple[float | None, str, str | None]:
    if value is None:
        return None, "EUR", None
    unit = (unit or "").upper()
    if unit == "EUR":
        return float(value), "EUR", None
    if unit == "HRK":
        return float(value) / HRK_PER_EUR, "EUR", f"converted from HRK at {HRK_PER_EUR} HRK/EUR"
    return None, "EUR", f"unsupported monetary unit {unit or '?'}"


def _source(report: ReportSlice, metric_name: str, coefficient: float = 1.0) -> dict[str, Any]:
    m = report.metrics.get(metric_name, {})
    return {
        "period": report.label,
        "period_end": report.period_end,
        "source_id": report.row.get("source_id"),
        "local_path": report.row.get("local_path"),
        "metric": metric_name,
        "raw_value": m.get("value"),
        "raw_unit": m.get("unit"),
        "coefficient": coefficient,
    }


def _metric_eur(report: ReportSlice | None, metric_name: str) -> tuple[float | None, str | None]:
    if report is None:
        return None, "missing report"
    m = report.metrics.get(metric_name)
    if not m:
        return None, f"missing metric {metric_name} in {report.label}"
    return _money_to_eur(m.get("value"), m.get("unit"))[0], _money_to_eur(m.get("value"), m.get("unit"))[2]


def _metric_value(report: ReportSlice | None, metric_name: str) -> tuple[float | None, str | None, str | None]:
    """Read a non-monetary reported metric without applying currency conversion."""
    if report is None:
        return None, None, "missing report"
    m = report.metrics.get(metric_name)
    if not m:
        return None, None, f"missing metric {metric_name} in {report.label}"
    value = m.get("value")
    if value is None:
        return None, m.get("unit"), f"missing value {metric_name} in {report.label}"
    return float(value), m.get("unit"), None


def load_preferred_reports(db: Database, ticker: str) -> list[ReportSlice]:
    # Analysis normally uses the preferred consolidated report for a period,
    # but year-end flow fallback needs the non-preferred consolidated Q4 report
    # alongside the later audited FY report for the same Dec-31 period. Keep all
    # consolidated candidates per period; if an issuer has no consolidated
    # candidate for a period, retain only that period's normal preferred report.
    inventory = db.report_inventory(ticker, preferred_only=False)
    grouped: dict[str, list[Any]] = {}
    for row in inventory:
        grouped.setdefault(str(row["period_key"]), []).append(row)

    rows = []
    for group in grouped.values():
        consolidated = [r for r in group if r["consolidated"] == 1]
        if consolidated:
            rows.extend(consolidated)
        else:
            preferred = [r for r in group if r["preference_rank"] == 1]
            if preferred:
                rows.append(preferred[0])

    reports: list[ReportSlice] = []
    for row in rows:
        d = dict(row)
        metrics = {r["metric_name"]: dict(r) for r in db.report_metrics(d["local_path"])}
        reports.append(ReportSlice(d, metrics))
    reports.sort(
        key=lambda r: (r.period_end or "", -(int(r.row.get("preference_rank") or 999)), r.row.get("publish_date") or ""),
        reverse=True,
    )
    return reports


def _is_year_end(report: ReportSlice) -> bool:
    end = report.period_end or ""
    return end.endswith("-12-31") or (report.quarter == 4)


def _find_report(reports: list[ReportSlice], *, year: int, quarter: int | None = None, year_end: bool = False) -> ReportSlice | None:
    candidates = [r for r in reports if r.year == year]
    if year_end:
        candidates = [r for r in candidates if _is_year_end(r)]
    elif quarter is not None:
        candidates = [r for r in candidates if r.quarter == quarter]
    return candidates[0] if candidates else None


def _select_target(reports: list[ReportSlice], as_of: str | None) -> ReportSlice | None:
    if not reports:
        return None
    if as_of is None:
        return reports[0]
    wanted = as_of.upper().strip()
    for r in reports:
        if r.label.upper() == wanted:
            return r
        if wanted.endswith("-FY") and r.year is not None and wanted == f"{r.year}-FY" and _is_year_end(r):
            return r
    return None


def _year_end_flow_metric(
    reports: list[ReportSlice], report: ReportSlice | None, metric_name: str
) -> tuple[float | None, str | None, ReportSlice | None, bool]:
    """Read a full-year flow metric, with an explicit same-year Q4 fallback.

    Some audited EHO XLSX workbooks expose balance-sheet/cash-flow values in the
    normalized parser but leave standardized P&L metrics empty.  When that
    happens, the consolidated Q4 workbook covers the same Jan-Dec interval and
    is preferable to inventing/annualizing a value.  The fallback is never
    silent: the actual Q4 source is returned for provenance and quality flags.
    """
    if report is None:
        return None, "missing full-year report", None, False

    value, note = _metric_eur(report, metric_name)
    if value is not None:
        return value, note, report, False

    # Only audited/annual-style reports (quarter=None) may fall back. A true Q4
    # report is already the fallback candidate and must not recursively fallback.
    if report.year is not None and report.quarter is None:
        q4 = _find_report(reports, year=report.year, quarter=4)
        q4_value, q4_note = _metric_eur(q4, metric_name)
        if q4_value is not None and q4 is not None:
            notes = [
                f"{report.label} {metric_name} unavailable; using same-year consolidated {q4.label} cumulative value"
            ]
            if q4_note:
                notes.append(q4_note)
            return q4_value, "; ".join(notes), q4, True

    return None, note or f"missing metric {metric_name} in {report.label}", report, False


def _flow_source(report: ReportSlice, metric_name: str, coefficient: float, *, fallback_for: str | None = None) -> dict[str, Any]:
    src = _source(report, metric_name, coefficient)
    if fallback_for:
        src["fallback_for"] = fallback_for
    return src


def _flow_ttm(reports: list[ReportSlice], target: ReportSlice, source_metric: str, out_name: str) -> DerivedMetric:
    year = target.year
    q = target.quarter
    if year is None:
        return DerivedMetric(out_name, None, "EUR", "unavailable", "unknown reporting year", note="Target report has no year")

    # A year-end cumulative report already covers twelve months. If the audited
    # FY workbook lacks this flow metric, use the same-year consolidated Q4
    # cumulative value and expose that fallback in provenance.
    if _is_year_end(target):
        value, note, actual, fallback = _year_end_flow_metric(reports, target, source_metric)
        sources = []
        if actual is not None:
            sources.append(_flow_source(actual, source_metric, 1.0, fallback_for=target.label if fallback else None))
        return DerivedMetric(
            out_name, value, "EUR",
            "ttm-q4-fallback" if value is not None and fallback else ("ttm" if value is not None else "unavailable"),
            f"{target.label} {source_metric}" + (f" [flow source {actual.label}]" if fallback and actual else ""),
            sources, note=note,
        )

    if q not in (1, 2, 3):
        return DerivedMetric(out_name, None, "EUR", "unavailable", "unsupported reporting period")

    prev_fy = _find_report(reports, year=year - 1, year_end=True)
    prev_same = _find_report(reports, year=year - 1, quarter=q)

    # Comprehensive income has exposed a template/source-selection edge case in
    # real HT Q1 filings. The current quarterly workbook already republishes the
    # comparable prior-year cumulative Q1 value, so use that directly when it is
    # available:
    #   prior FY + current Q1 cumulative - prior-year Q1 comparative in current filing.
    # This is stronger provenance than depending on a separately stored prior-Q1
    # workbook and is valid only for Q1 here; Q2/Q3 keep the generic YTD logic.
    source_metric_current = source_metric
    comparative_from_target = False
    if source_metric == "comprehensive_income_parent_ytd" and q == 1:
        comparative_v, comparative_note = _metric_eur(target, "comprehensive_income_parent_previous_cumulative")
        if comparative_v is not None:
            cur_v, cur_note = _metric_eur(target, source_metric)
            prev_v, prev_note = comparative_v, comparative_note
            comparative_from_target = True
        else:
            # Backwards-compatible fallback for already-normalized databases that
            # have not yet been reparsed with the comparative metric.
            cur_qtr, _ = _metric_eur(target, "comprehensive_income_parent_quarter")
            prev_qtr, _ = _metric_eur(prev_same, "comprehensive_income_parent_quarter")
            if cur_qtr is not None and prev_qtr is not None:
                source_metric_current = "comprehensive_income_parent_quarter"
            cur_v, cur_note = _metric_eur(target, source_metric_current)
            prev_v, prev_note = _metric_eur(prev_same, source_metric_current)
    else:
        cur_v, cur_note = _metric_eur(target, source_metric_current)
        prev_v, prev_note = _metric_eur(prev_same, source_metric_current)

    fy_v, fy_note, fy_actual, fy_fallback = _year_end_flow_metric(reports, prev_fy, source_metric)

    missing = [x for x in (cur_note if cur_v is None else None, fy_note if fy_v is None else None, prev_note if prev_v is None else None) if x]
    value = None if None in (cur_v, fy_v, prev_v) else fy_v + cur_v - prev_v

    fy_label = prev_fy.label if prev_fy else f"{year-1}-FY"
    if fy_fallback and fy_actual is not None:
        fy_label = f"{fy_label}[flow={fy_actual.label}]"
    if comparative_from_target:
        formula = f"{fy_label} + {target.label} current - {year-1}-Q{q} comparative from {target.label}"
    else:
        formula = f"{fy_label} + {target.label} - {(prev_same.label if prev_same else f'{year-1}-Q{q}')}"

    sources = [_flow_source(target, source_metric_current, 1.0)]
    if fy_actual is not None:
        sources.append(_flow_source(fy_actual, source_metric, 1.0, fallback_for=prev_fy.label if fy_fallback and prev_fy else None))
    if comparative_from_target:
        sources.append(_flow_source(target, "comprehensive_income_parent_previous_cumulative", -1.0))
        sources[-1]["comparative_period"] = f"{year-1}-Q{q}"
    elif prev_same:
        sources.append(_flow_source(prev_same, source_metric_current, -1.0))

    conversion_notes = [n for n in (cur_note, fy_note, prev_note) if n and "converted from HRK" in n]
    fallback_notes = [fy_note] if fy_fallback and fy_note else []
    notes = missing + fallback_notes + sorted(set(conversion_notes))
    # Deduplicate while preserving order.
    notes = list(dict.fromkeys(n for n in notes if n))
    quality = "ttm-q4-fallback" if value is not None and fy_fallback else ("ttm" if value is not None else "unavailable")
    return DerivedMetric(out_name, value, "EUR", quality, formula, sources, "; ".join(notes) or None)


def build_ttm_snapshot(db: Database, ticker: str, as_of: str | None = None) -> dict[str, Any]:
    ticker = ticker.upper()
    reports = load_preferred_reports(db, ticker)
    target = _select_target(reports, as_of)
    if target is None:
        return {
            "ticker": ticker,
            "as_of": as_of,
            "ok": False,
            "error": "No preferred report found for requested period" if as_of else "No preferred reports found",
            "metrics": {},
        }

    out: dict[str, DerivedMetric] = {}
    for source_name, out_name in FLOW_METRICS.items():
        out[out_name] = _flow_ttm(reports, target, source_name, out_name)

    # Current point-in-time balance sheet values, normalized to EUR.
    for name in POINT_METRICS:
        value, note = _metric_eur(target, name)
        out[name] = DerivedMetric(
            name, value, "EUR", "reported/derived-current" if value is not None else "unavailable",
            f"{target.label} closing balance", [_source(target, name)], note=note,
        )

    # Explicit non-monetary report statistics keep their reported units.
    for name in COUNT_POINT_METRICS:
        value, unit, note = _metric_value(target, name)
        out[name] = DerivedMetric(
            name, value, unit or "count", "reported-current" if value is not None else "unavailable",
            f"{target.label} reported statistic", [_source(target, name)], note=note,
        )

    equity_warning = None
    eq_total_now = out["equity_total"].value
    eq_parent_now = out["equity_parent"].value
    ni_total_ttm = out["net_income_total_ttm"].value
    ni_parent_ttm = out["net_income_parent_ttm"].value
    if (target.row.get("consolidated") == 1 and None not in (eq_total_now, eq_parent_now, ni_total_ttm, ni_parent_ttm)):
        eq_tol = max(1.0, abs(eq_total_now) * 1e-9)
        ni_tol = max(1.0, abs(ni_total_ttm) * 1e-9)
        if abs(eq_total_now - eq_parent_now) <= eq_tol and abs(ni_total_ttm - ni_parent_ttm) > ni_tol:
            equity_warning = (
                "Parent equity equals total equity while consolidated total and parent TTM earnings differ; "
                "non-controlling-interest equity extraction may be incomplete. ROE and debt/parent-equity are suppressed."
            )
            out["equity_parent"].quality = "suspect-current"
            out["equity_parent"].note = equity_warning

    def add_ratio(name: str, numerator: str, denominator: str, formula: str):
        value = _safe_ratio(out[numerator].value, out[denominator].value)
        out[name] = DerivedMetric(name, value, "ratio", "derived_ttm" if value is not None else "unavailable", formula)

    add_ratio("ebit_margin_ttm", "ebit_ttm", "sales_revenue_ttm", "EBIT TTM / revenue TTM")
    add_ratio("ebitda_margin_ttm", "ebitda_simple_ttm", "sales_revenue_ttm", "simple EBITDA TTM / revenue TTM")
    add_ratio("net_margin_parent_ttm", "net_income_parent_ttm", "sales_revenue_ttm", "parent net income TTM / revenue TTM")
    add_ratio("fcf_margin_ttm", "free_cash_flow_ttm", "sales_revenue_ttm", "simple FCF TTM / revenue TTM")
    add_ratio("md_ebit_margin_ttm", "md_ebit_ttm", "operating_revenue_ttm", "MojeDionice-style EBIT TTM / operating revenue TTM")
    add_ratio("md_ebitda_margin_ttm", "md_ebitda_ttm", "operating_revenue_ttm", "MojeDionice-style EBITDA TTM / operating revenue TTM")
    add_ratio("md_npm_ttm", "net_income_parent_ttm", "total_revenue_ttm", "parent net income TTM / total revenue TTM")
    add_ratio("current_ratio", "current_assets", "current_liabilities", "current assets / current liabilities")
    add_ratio("total_debt_to_equity_parent_md", "total_debt_md", "equity_parent", "current + long-term liabilities / parent equity")

    nde = out["net_debt_ex_other"].value
    ebitda = out["ebitda_simple_ttm"].value
    out["net_debt_to_ebitda_ttm"] = DerivedMetric(
        "net_debt_to_ebitda_ttm", _safe_ratio(nde, ebitda), "x", "derived_ttm",
        "current net debt ex other / simple EBITDA TTM",
        note="Debt definition may exclude lease liabilities and generic other liabilities.",
    )
    out["interest_coverage_ebit_ttm"] = DerivedMetric(
        "interest_coverage_ebit_ttm", _safe_ratio(out["ebit_ttm"].value, out["interest_expense_ttm"].value), "x", "derived_ttm",
        "EBIT TTM / interest expense TTM",
    )
    debt_eq = None if equity_warning else _safe_ratio(out["gross_financial_debt_ex_other"].value, out["equity_parent"].value)
    out["debt_to_equity_parent"] = DerivedMetric(
        "debt_to_equity_parent", debt_eq, "x", "derived-current" if debt_eq is not None else "unavailable",
        "current gross financial debt ex other / current parent equity",
        note=equity_warning,
    )
    roe_ending = None if equity_warning else _safe_ratio(out["net_income_parent_ttm"].value, out["equity_parent"].value)
    out["roe_ending_equity_md"] = DerivedMetric(
        "roe_ending_equity_md", roe_ending, "ratio", "derived-md" if roe_ending is not None else "unavailable",
        "parent net income TTM / ending parent equity", note=equity_warning,
    )
    roa_ending = _safe_ratio(out["net_income_parent_ttm"].value, out["total_assets"].value)
    out["roa_ending_assets_md"] = DerivedMetric(
        "roa_ending_assets_md", roa_ending, "ratio", "derived-md" if roa_ending is not None else "unavailable",
        "parent net income TTM / ending total assets",
    )
    capital_employed = None
    if out["total_assets"].value is not None and out["current_liabilities"].value is not None:
        capital_employed = out["total_assets"].value - out["current_liabilities"].value
    roce = _safe_ratio(out["md_ebit_ttm"].value, capital_employed)
    out["roce_md"] = DerivedMetric(
        "roce_md", roce, "ratio", "derived-md" if roce is not None else "unavailable",
        "MojeDionice-style EBIT TTM / (total assets - current liabilities)",
    )

    # ROE uses opening and closing parent equity twelve months apart, not a
    # quarter annualization. For Q2-2026, for example: avg(Q2-2025, Q2-2026).
    prior_equity_report = None
    if target.year is not None:
        if _is_year_end(target):
            prior_equity_report = _find_report(reports, year=target.year - 1, year_end=True)
        elif target.quarter in (1, 2, 3):
            prior_equity_report = _find_report(reports, year=target.year - 1, quarter=target.quarter)
    prior_eq, prior_note = _metric_eur(prior_equity_report, "equity_parent")
    current_eq = out["equity_parent"].value
    avg_eq = None if None in (prior_eq, current_eq) else (prior_eq + current_eq) / 2.0
    roe = None if equity_warning else _safe_ratio(out["net_income_parent_ttm"].value, avg_eq)
    roe_sources = [_source(target, "equity_parent")]
    if prior_equity_report:
        roe_sources.append(_source(prior_equity_report, "equity_parent"))
    out["roe_ttm"] = DerivedMetric(
        "roe_ttm", roe, "ratio", "derived_ttm" if roe is not None else "unavailable",
        "parent net income TTM / average parent equity over trailing 12 months",
        roe_sources,
        note=equity_warning or (prior_note if roe is None else None),
    )

    return {
        "ticker": ticker,
        "as_of": target.label,
        "period_end": target.period_end,
        "source_id": target.row.get("source_id"),
        "consolidated": True if target.row.get("consolidated") == 1 else (False if target.row.get("consolidated") == 0 else None),
        "audited": True if target.row.get("audited") == 1 else (False if target.row.get("audited") == 0 else None),
        "currency": "EUR",
        "currency_note": f"Historical HRK money values are converted at the fixed rate {HRK_PER_EUR} HRK/EUR; no inflation adjustment.",
        "equity_attribution_warning": equity_warning,
        "ok": True,
        "metrics": {name: metric.as_dict() for name, metric in out.items()},
    }


def _cagr(start: float | None, end: float | None, years: int) -> float | None:
    if start is None or end is None or years <= 0 or start <= 0 or end <= 0:
        return None
    return (end / start) ** (1.0 / years) - 1.0


def _annual_record(
    reports: list[ReportSlice], report: ReportSlice, previous_year_end: ReportSlice | None
) -> dict[str, Any]:
    values: dict[str, float | None] = {}
    fallback_metrics: list[str] = []
    fallback_sources: dict[str, str] = {}

    flow_names = set(FLOW_METRICS)
    for name in ANNUAL_COLUMNS:
        if name in flow_names:
            value, _note, actual, fallback = _year_end_flow_metric(reports, report, name)
            values[name] = value
            if fallback:
                fallback_metrics.append(name)
                if actual is not None:
                    fallback_sources[name] = actual.label
        else:
            values[name], _ = _metric_eur(report, name)

    sales = values["sales_revenue_ytd"]
    ebit = values["ebit_ytd"]
    ebitda = values["ebitda_simple_ytd"]
    ni = values["net_income_parent_ytd"]
    fcf = values["free_cash_flow_ytd"]
    prev_eq, _ = _metric_eur(previous_year_end, "equity_parent")
    cur_eq = values["equity_parent"]
    avg_eq = None if None in (prev_eq, cur_eq) else (prev_eq + cur_eq) / 2.0
    equity_suspect = False
    eq_total = values.get("equity_total")
    ni_total = values.get("net_income_total_ytd")
    if None not in (eq_total, cur_eq, ni_total, ni):
        eq_tol = max(1.0, abs(eq_total) * 1e-9)
        ni_tol = max(1.0, abs(ni_total) * 1e-9)
        equity_suspect = abs(eq_total - cur_eq) <= eq_tol and abs(ni_total - ni) > ni_tol
    return {
        "year": report.year,
        "period": report.label,
        "source_id": report.row.get("source_id"),
        "audited": report.row.get("audited") == 1,
        "currency": "EUR",
        "sales_revenue": sales,
        "ebit": ebit,
        "ebitda_simple": ebitda,
        "net_income_parent": ni,
        "net_income_total": values["net_income_total_ytd"],
        "operating_cash_flow": values["operating_cash_flow_ytd"],
        "free_cash_flow": fcf,
        "cash": values["cash"],
        "gross_financial_debt_ex_other": values["gross_financial_debt_ex_other"],
        "net_debt_ex_other": values["net_debt_ex_other"],
        "equity_parent": cur_eq,
        "ebit_margin": _safe_ratio(ebit, sales),
        "ebitda_margin": _safe_ratio(ebitda, sales),
        "net_margin_parent": _safe_ratio(ni, sales),
        "fcf_margin": _safe_ratio(fcf, sales),
        "roe": None if equity_suspect else _safe_ratio(ni, avg_eq),
        "equity_parent_suspect": equity_suspect,
        "flow_fallback_metrics": sorted(fallback_metrics),
        "flow_fallback_sources": fallback_sources,
    }


def build_trends(db: Database, ticker: str, years: int = 7) -> dict[str, Any]:
    ticker = ticker.upper()
    reports = load_preferred_reports(db, ticker)
    year_ends = [r for r in reports if _is_year_end(r) and r.year is not None]
    # One preferred report per Dec-31 period should already be present. Keep a
    # defensive one-per-year map in case an unusual issuer has duplicate dates.
    by_year: dict[int, ReportSlice] = {}
    for r in year_ends:
        by_year.setdefault(int(r.year), r)
    ordered_years = sorted(by_year)
    annual = []
    for y in ordered_years:
        annual.append(_annual_record(reports, by_year[y], by_year.get(y - 1)))
    if years > 0:
        annual = annual[-years:]

    latest_ttm = build_ttm_snapshot(db, ticker)
    yoy = None
    if latest_ttm.get("ok"):
        target = _select_target(reports, latest_ttm["as_of"])
        if target and target.year is not None:
            prior_label = f"{target.year - 1}-FY" if _is_year_end(target) else f"{target.year - 1}-Q{target.quarter}"
            prior_ttm = build_ttm_snapshot(db, ticker, prior_label)
            if prior_ttm.get("ok"):
                yoy = {}
                for name in ("sales_revenue_ttm", "ebit_ttm", "ebitda_simple_ttm", "net_income_parent_ttm", "free_cash_flow_ttm"):
                    cur = latest_ttm["metrics"].get(name, {}).get("value")
                    prev = prior_ttm["metrics"].get(name, {}).get("value")
                    yoy[name.replace("_ttm", "_yoy")] = _safe_ratio((cur - prev) if None not in (cur, prev) else None, prev)

    growth: dict[str, dict[str, float | None]] = {}
    annual_all = [_annual_record(reports, by_year[y], by_year.get(y - 1)) for y in ordered_years]
    if annual_all:
        end = annual_all[-1]
        end_year = int(end["year"])
        key_map = {
            "sales_revenue": "sales_revenue",
            "ebitda_simple": "ebitda_simple",
            "net_income_parent": "net_income_parent",
            "free_cash_flow": "free_cash_flow",
        }
        for label, key in key_map.items():
            entry: dict[str, float | None] = {}
            for horizon in (3, 5):
                start_year = end_year - horizon
                start = next((r for r in annual_all if r["year"] == start_year), None)
                entry[f"cagr_{horizon}y"] = _cagr(start[key] if start else None, end[key], horizon)
            growth[label] = entry

    return {
        "ticker": ticker,
        "currency": "EUR",
        "currency_note": f"Historical HRK money values converted at {HRK_PER_EUR} HRK/EUR; nominal series, no inflation adjustment.",
        "annual": annual,
        "latest_ttm": latest_ttm,
        "yoy": yoy,
        "growth": growth,
    }
