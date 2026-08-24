from __future__ import annotations

from typing import Any

from .analytics import build_ttm_snapshot
from .storage import Database


def _ratio(a: float | None, b: float | None) -> float | None:
    if a is None or b in (None, 0):
        return None
    return a / b


def build_valuation(db: Database, ticker: str, as_of: str | None = None) -> dict[str, Any]:
    """Combine one stored ZSE market snapshot with a selected financial period.

    ``as_of`` changes only the financial denominator.  The market numerator is
    always the latest stored official ZSE market-cap snapshot. This is useful
    for reproducing comparison tables that apply today's price to older
    fundamentals, but it must not be confused with historical valuation.
    """
    ticker = ticker.upper()
    market_row = db.latest_market_snapshot(ticker)
    if market_row is None:
        return {
            "ticker": ticker,
            "ok": False,
            "error": "No market snapshot stored. Run: zse-tool market-sync --ticker " + ticker,
        }

    market = dict(market_row)
    ttm = build_ttm_snapshot(db, ticker, as_of)
    if not ttm.get("ok"):
        return {
            "ticker": ticker,
            "ok": False,
            "error": "No usable TTM financial snapshot",
            "market": market,
            "ttm": ttm,
        }

    def mv(name: str) -> float | None:
        return ttm.get("metrics", {}).get(name, {}).get("value")

    market_cap = market.get("market_cap_eur")
    listed_quantity = market.get("listed_quantity")
    net_debt_cash_only = mv("net_debt_ex_other")
    net_debt_liquid = mv("net_debt_liquid_assets")
    md_net_debt_liquid = mv("md_net_debt_liquid_assets")
    md_observed_net_debt_liquid = mv("md_observed_net_debt_liquid_assets")

    # Primary scanner EV uses explicit standardized financing rows and subtracts
    # both cash and short-term financial assets. It is more informative than the
    # old cash-only proxy but still does not claim lease completeness.
    ev_net_debt = net_debt_liquid if net_debt_liquid is not None else net_debt_cash_only
    enterprise_value = None if None in (market_cap, ev_net_debt) else market_cap + ev_net_debt
    enterprise_value_cash_only_legacy = (
        None if None in (market_cap, net_debt_cash_only) else market_cap + net_debt_cash_only
    )
    enterprise_value_md = (
        None if None in (market_cap, md_net_debt_liquid) else market_cap + md_net_debt_liquid
    )
    enterprise_value_md_observed = (
        None if None in (market_cap, md_observed_net_debt_liquid) else market_cap + md_observed_net_debt_liquid
    )

    revenue = mv("sales_revenue_ttm")
    operating_revenue = mv("operating_revenue_ttm")
    ebit = mv("ebit_ttm")
    ebitda = mv("ebitda_simple_ttm")
    md_ebit = mv("md_ebit_ttm")
    md_ebitda = mv("md_ebitda_ttm")
    parent_ni = mv("net_income_parent_ttm")
    depreciation = mv("depreciation_ttm")
    cfo = mv("operating_cash_flow_ttm")
    fcf = mv("free_cash_flow_ttm")
    fcf_net_capex = mv("free_cash_flow_net_capex_ttm")
    parent_equity = mv("equity_parent")
    liquid_assets = mv("liquid_financial_assets")
    cash = mv("cash")
    st_fin = mv("short_term_financial_assets")
    gross_fin_debt = mv("gross_financial_debt_standardized")
    md_fin_debt = mv("md_financial_debt")
    md_observed_fin_debt = mv("md_observed_financial_debt")
    external_fin_debt = mv("external_financing_debt")
    related_party_fin_debt = mv("related_party_financing_debt")
    unclassified_lt = mv("unclassified_long_term_liabilities_residual")
    unclassified_st = mv("unclassified_current_liabilities_residual")
    total_liabilities = mv("total_liabilities")

    equity_warning = ttm.get("equity_attribution_warning")
    pb = None if equity_warning else _ratio(market_cap, parent_equity)
    bvps = None if equity_warning else _ratio(parent_equity, listed_quantity)
    eps = _ratio(parent_ni, listed_quantity)
    liquid_per_share = _ratio(liquid_assets, listed_quantity)

    metrics = {
        # Existing company/scanner definitions remain unchanged.
        "market_cap_eur": {"value": market_cap, "unit": "EUR", "formula": "official ZSE published market cap"},
        "enterprise_value_eur": {
            "value": enterprise_value,
            "unit": "EUR",
            "formula": "market cap + explicit standardized financing debt - cash - short-term financial assets",
            "note": "Uses standardized financing rows; lease liabilities may be embedded in other liabilities and are not claimed as separately complete.",
        },
        "enterprise_value_cash_only_legacy_eur": {
            "value": enterprise_value_cash_only_legacy,
            "unit": "EUR",
            "formula": "market cap + legacy cash-only net debt ex other",
            "note": "Retained for backwards comparison with pre-v0.2.4 output.",
        },
        "enterprise_value_md_eur": {
            "value": enterprise_value_md,
            "unit": "EUR",
            "formula": "market cap + published-method MojeDionice financial debt - cash - short-term financial assets",
            "note": "Published methodology view: all long-term liabilities + short-term bank/financial-institution liabilities + short-term securities liabilities; if current-liability type detail is unavailable, total current liabilities are used.",
        },
        "enterprise_value_md_observed_eur": {
            "value": enterprise_value_md_observed,
            "unit": "EUR",
            "formula": "market cap + observed-compatible MojeDionice debt - cash - short-term financial assets",
            "note": "Empirical compatibility view validated on GRNL: adds non-related short-term loans/deposits to the published-method current-debt leg. Group and participating-interest loans remain excluded. This is kept separate from published methodology.",
        },
        "pe_ttm": {"value": _ratio(market_cap, parent_ni), "unit": "x", "formula": "market cap / parent net income TTM"},
        "earnings_yield_ttm": {"value": _ratio(parent_ni, market_cap), "unit": "ratio", "formula": "parent net income TTM / market cap"},
        "price_to_sales_ttm": {"value": _ratio(market_cap, revenue), "unit": "x", "formula": "market cap / sales revenue TTM"},
        "ev_to_ebitda_simple_ttm": {"value": _ratio(enterprise_value, ebitda), "unit": "x", "formula": "enterprise value / company-style simple EBITDA TTM"},
        "ev_to_ebit_ttm": {"value": _ratio(enterprise_value, ebit), "unit": "x", "formula": "enterprise value / company-style operating EBIT TTM"},
        "fcf_yield_ttm": {"value": _ratio(fcf, market_cap), "unit": "ratio", "formula": "simple FCF TTM / market cap"},
        "cfo_yield_ttm": {"value": _ratio(cfo, market_cap), "unit": "ratio", "formula": "operating cash flow TTM / market cap"},
        "net_cash_to_market_cap": {
            "value": _ratio(-net_debt_liquid if net_debt_liquid is not None else None, market_cap),
            "unit": "ratio",
            "formula": "-(explicit standardized financing debt - cash - short-term financial assets) / market cap",
        },

        # Explicit MojeDionice-comparison definitions. These are separate names
        # so they can never silently alter the scanner's company-style metrics.
        "price_to_book_parent": {
            "value": pb, "unit": "x", "formula": "current market cap / ending parent-attributable equity",
            "note": equity_warning,
        },
        "book_value_per_listed_share": {
            "value": bvps, "unit": "EUR/share", "formula": "ending parent-attributable equity / ZSE listed quantity",
            "note": equity_warning,
        },
        "eps_ttm_per_listed_share": {
            "value": eps, "unit": "EUR/share", "formula": "parent net income TTM / ZSE listed quantity",
            "note": "Uses listed quantity, not weighted-average diluted shares.",
        },
        "cash_plus_short_fin_per_listed_share": {
            "value": liquid_per_share, "unit": "EUR/share", "formula": "(cash + short-term financial assets) / ZSE listed quantity",
        },
        "price_to_operating_sales_ttm": {
            "value": _ratio(market_cap, operating_revenue), "unit": "x", "formula": "market cap / operating revenue TTM",
        },
        "price_to_md_ebit_ttm": {
            "value": _ratio(market_cap, md_ebit), "unit": "x", "formula": "market cap / MojeDionice-style EBIT TTM",
        },
        "price_to_md_ebitda_ttm": {
            "value": _ratio(market_cap, md_ebitda), "unit": "x", "formula": "market cap / MojeDionice-style EBITDA TTM",
        },
        "price_to_earnings_plus_depr_ttm": {
            "value": _ratio(market_cap, None if None in (parent_ni, depreciation) else parent_ni + depreciation),
            "unit": "x", "formula": "market cap / (parent net income TTM + depreciation TTM)",
        },
        "price_to_cfo_ttm": {
            "value": _ratio(market_cap, cfo), "unit": "x", "formula": "market cap / operating cash flow TTM",
        },
        "price_to_fcf_net_capex_ttm": {
            "value": _ratio(market_cap, fcf_net_capex), "unit": "x", "formula": "market cap / FCF TTM using net capex",
        },
        "enterprise_value_liquid_proxy_eur": {
            "value": enterprise_value,
            "unit": "EUR",
            "formula": "alias: market cap + explicit standardized financing debt - cash - short-term financial assets",
            "note": "Backwards-compatible metric name; no longer the MojeDionice comparison EV.",
        },
        "ev_liquid_proxy_to_md_ebitda_ttm": {
            "value": _ratio(enterprise_value, md_ebitda),
            "unit": "x",
            "formula": "scanner standardized EV / MojeDionice-style EBITDA TTM",
            "note": "Backwards-compatible name; use ev_md_to_md_ebitda_ttm for the exact published MojeDionice EV debt convention.",
        },
        "ev_md_to_md_ebitda_ttm": {
            "value": _ratio(enterprise_value_md, md_ebitda),
            "unit": "x",
            "formula": "published-method MojeDionice EV / MojeDionice-style EBITDA TTM",
        },
        "ev_md_observed_to_md_ebitda_ttm": {
            "value": _ratio(enterprise_value_md_observed, md_ebitda),
            "unit": "x",
            "formula": "observed-compatible MojeDionice EV / MojeDionice-style EBITDA TTM",
            "note": "Used for compare-md because it reconciles GRNL while preserving the published-method EV separately.",
        },
        "ev_md_to_md_ebit_ttm": {
            "value": _ratio(enterprise_value_md, md_ebit),
            "unit": "x",
            "formula": "MojeDionice-method EV / MojeDionice-style EBIT TTM",
        },
        "md_financial_debt_eur": {
            "value": md_fin_debt,
            "unit": "EUR",
            "formula": "published-method: all long-term liabilities + current bank/financial-institution liabilities + current securities liabilities (or current-liabilities fallback)",
        },
        "md_observed_financial_debt_eur": {
            "value": md_observed_fin_debt,
            "unit": "EUR",
            "formula": "observed-compatible: all long-term liabilities + current non-related loans/deposits + banks/financial institutions + securities",
        },
        "external_financing_debt_eur": {
            "value": external_fin_debt, "unit": "EUR",
            "formula": "explicit non-related loans/deposits + bank debt + securities, LT and ST",
        },
        "related_party_financing_debt_eur": {
            "value": related_party_fin_debt, "unit": "EUR",
            "formula": "explicit group + participating-interest financing, LT and ST",
        },
        "unclassified_liabilities_residual_eur": {
            "value": None if unclassified_lt is None or unclassified_st is None else unclassified_lt + unclassified_st,
            "unit": "EUR",
            "formula": "unclassified long-term residual + unclassified current residual",
            "note": "Residual is deliberately not called debt; later note-level parsing can classify leases, provisions, trade liabilities and other obligations.",
        },
        "explicit_financing_share_of_total_liabilities": {
            "value": _ratio(gross_fin_debt, total_liabilities),
            "unit": "ratio",
            "formula": "explicit standardized financing debt / total liabilities",
        },
        "related_party_share_of_explicit_financing": {
            "value": _ratio(related_party_fin_debt, gross_fin_debt),
            "unit": "ratio",
            "formula": "related-party financing / explicit standardized financing debt",
        },
        "liquid_assets_coverage_of_explicit_financing": {
            "value": _ratio(liquid_assets, gross_fin_debt),
            "unit": "ratio",
            "formula": "cash + short-term financial assets / explicit standardized financing debt",
        },
        "gross_financial_debt_standardized_eur": {
            "value": gross_fin_debt,
            "unit": "EUR",
            "formula": "sum of explicit standardized financing detail rows",
            "note": "Does not automatically include generic other liabilities or undisclosed lease debt.",
        },
    }

    return {
        "ticker": ticker,
        "ok": True,
        "market": {
            "observed_at": market.get("observed_at"),
            "source_url": market.get("source_url"),
            "source_kind": market.get("source_kind"),
            "isin": market.get("isin"),
            "listed_quantity": listed_quantity,
            "market_cap_eur": market_cap,
            "implied_price_eur": market.get("implied_price_eur"),
            "price_basis": market.get("price_basis"),
            "quality": market.get("quality"),
            "note": market.get("note"),
        },
        "financials": {
            "as_of": ttm.get("as_of"),
            "period_end": ttm.get("period_end"),
            "source_id": ttm.get("source_id"),
            "equity_attribution_warning": equity_warning,
            "market_basis_note": "Market numerator is the latest stored ZSE snapshot even when an older financial --as-of period is selected.",
        },
        "ttm": ttm,
        "metrics": metrics,
    }


def build_debt_snapshot(db: Database, ticker: str, as_of: str | None = None) -> dict[str, Any]:
    """Expose debt construction and provenance without requiring market data."""
    ticker = ticker.upper()
    ttm = build_ttm_snapshot(db, ticker, as_of)
    if not ttm.get("ok"):
        return {"ticker": ticker, "ok": False, "error": "No usable financial snapshot", "ttm": ttm}

    tm = ttm.get("metrics", {})
    names = (
        "long_term_liabilities",
        "current_liabilities",
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
        "gross_financial_debt_standardized",
        "cash",
        "short_term_financial_assets",
        "liquid_financial_assets",
        "net_debt_liquid_assets",
        "md_short_term_financial_debt",
        "md_financial_debt",
        "md_net_debt_liquid_assets",
        "md_observed_short_term_financial_debt",
        "md_observed_financial_debt",
        "md_observed_net_debt_liquid_assets",
        "explicit_long_term_financing_debt",
        "explicit_short_term_financing_debt",
        "external_financing_debt",
        "related_party_financing_debt",
        "unclassified_long_term_liabilities_residual",
        "unclassified_current_liabilities_residual",
        "total_liabilities",
    )
    metrics = {name: tm.get(name, {}) for name in names}
    return {
        "ticker": ticker,
        "ok": True,
        "as_of": ttm.get("as_of"),
        "period_end": ttm.get("period_end"),
        "source_id": ttm.get("source_id"),
        "metrics": metrics,
        "scanner_note": (
            "Scanner standardized debt sums explicit financing rows (loan/deposit, bank/financial-institution and securities liabilities). "
            "Generic other liabilities are excluded; lease liabilities are not claimed as separately complete unless disclosed in those rows."
        ),
        "md_note": (
            "Published MojeDionice debt methodology is preserved separately. The observed-compatibility bridge additionally includes non-related current loans/deposits; "
            "this reconciles GRNL and is used only for compare-md, not as the scanner's economic debt definition."
        ),
        "analysis_note": (
            "For investment analysis, keep explicit external financing, related-party financing and unclassified liability residuals separate. "
            "The residual is not automatically treated as financial debt until note-level evidence identifies items such as leases or other financing obligations."
        ),
    }


def build_md_comparison(db: Database, ticker: str, periods: list[str]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for period in periods:
        valuation = build_valuation(db, ticker, period)
        if not valuation.get("ok"):
            rows.append({"period": period, "ok": False, "error": valuation.get("error")})
            continue
        ttm = valuation["ttm"]
        vm = valuation["metrics"]
        tm = ttm["metrics"]
        get_t = lambda name: tm.get(name, {}).get("value")
        get_v = lambda name: vm.get(name, {}).get("value")
        rows.append({
            "requested_period": period,
            "period": ttm.get("as_of"),
            "period_end": ttm.get("period_end"),
            "ok": True,
            "sales_revenue": get_t("sales_revenue_ttm"),
            "operating_revenue": get_t("operating_revenue_ttm"),
            "md_ebit": get_t("md_ebit_ttm"),
            "md_ebitda": get_t("md_ebitda_ttm"),
            "md_value_adjustments_provisions": get_t("md_value_adjustments_provisions_ttm"),
            "provisions": get_t("provisions_ttm"),
            "net_income_parent": get_t("net_income_parent_ttm"),
            "comprehensive_income_parent": get_t("comprehensive_income_parent_ttm"),
            "equity_parent": get_t("equity_parent"),
            "share_capital": get_t("share_capital"),
            "retained_earnings_reserves_parent": get_t("retained_earnings_reserves_parent"),
            "total_assets": get_t("total_assets"),
            "employees": get_t("employees_reported"),
            "employees_average_current_period": get_t("employees_average_current_period"),
            "employees_period_end": get_t("employees_period_end"),
            "employee_measure": (
                "period-end" if get_t("employees_period_end") is not None
                else ("average-period" if get_t("employees_average_current_period") is not None else None)
            ),
            "consolidated": ttm.get("consolidated"),
            "audited": ttm.get("audited"),
            "p_s": get_v("price_to_sales_ttm"),
            "p_e": get_v("pe_ttm"),
            "p_b": get_v("price_to_book_parent"),
            "bvps": get_v("book_value_per_listed_share"),
            "eps": get_v("eps_ttm_per_listed_share"),
            "roe": get_t("roe_ending_equity_md"),
            "roa": get_t("roa_ending_assets_md"),
            "ebit_margin": get_t("md_ebit_margin_ttm"),
            "ebitda_margin": get_t("md_ebitda_margin_ttm"),
            "npm": get_t("md_npm_ttm"),
            "roce": get_t("roce_md"),
            "current_ratio": get_t("current_ratio"),
            "cash_plus_short_fin_per_share": get_v("cash_plus_short_fin_per_listed_share"),
            "debt_to_equity": get_t("total_debt_to_equity_parent_md"),
            "ev_to_ebitda": (
                get_v("ev_md_observed_to_md_ebitda_ttm")
                if get_v("ev_md_observed_to_md_ebitda_ttm") is not None
                else get_v("ev_md_to_md_ebitda_ttm")
            ),
            "ev_to_ebitda_published_method": get_v("ev_md_to_md_ebitda_ttm"),
            "p_ebitda": get_v("price_to_md_ebitda_ttm"),
            "p_ebit": get_v("price_to_md_ebit_ttm"),
            "p_ea": get_v("price_to_earnings_plus_depr_ttm"),
            "p_cf": get_v("price_to_cfo_ttm"),
            "p_fcf": get_v("price_to_fcf_net_capex_ttm"),
            "equity_warning": ttm.get("equity_attribution_warning"),
        })

    market = db.latest_market_snapshot(ticker.upper())
    return {
        "ticker": ticker.upper(),
        "market": dict(market) if market is not None else None,
        "periods": rows,
        "note": "Comparison uses latest stored ZSE market cap for every selected financial period; it is not historical-price valuation.",
        "ev_note": "compare-md EV/EBITDA uses an observed-compatible bridge: all long-term liabilities + non-related current loans/deposits + current bank/financial-institution liabilities + current securities liabilities, less cash and short-term financial assets. The portal's published-method EV remains stored separately for auditability.",
    }
