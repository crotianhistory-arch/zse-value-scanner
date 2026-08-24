from __future__ import annotations

import re
import unicodedata
from typing import Iterable

from .models import Fact, Metric, ParsedReport


def _fact_index(facts: Iterable[Fact]) -> dict[tuple[str, str, int], float | None]:
    result: dict[tuple[str, str, int], float | None] = {}
    for fact in facts:
        result[(fact.statement, fact.column_name, fact.adp_code)] = fact.value
    return result


def _sum_present(index, keys: list[tuple[str, str, int]]) -> float | None:
    values = [index.get(k) for k in keys]
    present = [v for v in values if v is not None]
    return sum(present) if present else None


def _safe_ratio(a: float | None, b: float | None) -> float | None:
    if a is None or b in (None, 0):
        return None
    return a / b


def _norm_label(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _strip_trailing_aop_annotation(label: str) -> str:
    """Remove only trailing AOP/ADP aggregation annotations from a normalized label."""
    parts = label.split()
    for i, token in enumerate(parts):
        if token in {"aop", "adp"} and i > 0:
            return " ".join(parts[:i])
    return label


def _strip_structural_prefix(label: str) -> str:
    """Remove standard section/row numbering without broad suffix matching."""
    parts = label.split()
    while parts and (
        re.fullmatch(r"[ivxlcdm]+", parts[0])
        or re.fullmatch(r"[a-z]", parts[0])
        or re.fullmatch(r"\d+", parts[0])
    ):
        parts = parts[1:]
    return " ".join(parts)


def _canonical_label(text: str) -> str:
    label = _norm_label(text)
    label = _strip_trailing_aop_annotation(label)
    label = _strip_structural_prefix(label)
    return label


def _fact_candidates_by_labels(
    report: ParsedReport,
    statement: str,
    column_name: str,
    labels: tuple[str, ...],
) -> list[Fact]:
    """Return all matching non-null facts for stable structural labels.

    Returning all candidates is important because some balance-sheet detail
    labels are intentionally repeated under both long-term and short-term
    liability sections.  Callers that need one of those rows must disambiguate
    by section/source-row rather than accepting the first duplicate.
    """
    wanted = {_canonical_label(x) for x in labels}
    out: list[Fact] = []
    for fact in report.facts:
        if fact.statement != statement or fact.column_name != column_name or fact.value is None:
            continue
        if _canonical_label(fact.label) in wanted:
            out.append(fact)
    return out


def _fact_value_by_labels(
    report: ParsedReport,
    statement: str,
    column_name: str,
    labels: tuple[str, ...],
) -> float | None:
    """Find a unique standardized row by published label.

    EHO template revisions can shift numeric AOP/ADP codes while the accounting
    label remains stable.  Ambiguous duplicate labels deliberately return None;
    use ``_fact_value_in_section`` for repeated liability detail rows.
    """
    candidates = _fact_candidates_by_labels(report, statement, column_name, labels)
    if len(candidates) == 1:
        return candidates[0].value
    return None


def _fact_value_in_section(
    report: ParsedReport,
    statement: str,
    column_name: str,
    section_labels: tuple[str, ...],
    item_labels: tuple[str, ...],
    end_section_labels: tuple[str, ...] | None = None,
) -> float | None:
    """Resolve a repeated detail label inside a structural statement section.

    Croatian balance sheets repeat labels such as bank liabilities in the
    long-term and short-term sections.  ``source_row`` is stable within the
    workbook even when AOP numbers shift, so use section boundaries to choose
    the correct occurrence.
    """
    sections = _fact_candidates_by_labels(report, statement, column_name, section_labels)
    if len(sections) != 1:
        return None
    start_row = sections[0].source_row
    end_row: int | None = None
    if end_section_labels:
        ends = _fact_candidates_by_labels(report, statement, column_name, end_section_labels)
        later = [f.source_row for f in ends if f.source_row > start_row]
        if later:
            end_row = min(later)

    items = _fact_candidates_by_labels(report, statement, column_name, item_labels)
    inside = [
        f for f in items
        if f.source_row > start_row and (end_row is None or f.source_row < end_row)
    ]
    if len(inside) == 1:
        return inside[0].value
    return None


def _first_value(*values: float | None) -> float | None:
    for value in values:
        if value is not None:
            return value
    return None


def _abs_if_present(value: float | None) -> float | None:
    return abs(value) if value is not None else None


def calculate_metrics(report: ParsedReport) -> list[Metric]:
    """Calculate normalized issuer metrics with explicit definition variants.

    Numeric AOP/ADP codes remain useful as backwards-compatible fallbacks, but
    key rows that have shifted across EHO template generations are now resolved
    from the published Croatian/English row labels first.

    ``ebit_ytd`` / ``ebitda_simple_ytd`` retain the scanner's company-style
    operating definitions.  ``md_*`` metrics implement the public methodology
    used by MojeDionice for cross-checking; they are kept separately so one
    definition can never silently replace another.
    """
    x = _fact_index(report.facts)
    unit = report.currency

    bs = lambda code, col="current_period": x.get(("balance_sheet", col, code))
    pnl = lambda code, col="current_cumulative": x.get(("income_statement", col, code))
    cfd = lambda code, col="current_period": x.get(("cash_flow_direct", col, code))
    cfi = lambda code, col="current_period": x.get(("cash_flow_indirect", col, code))
    sup = lambda col: x.get(("supplemental", col, 1))

    def bs_label(labels: tuple[str, ...], col: str = "current_period"):
        return _fact_value_by_labels(report, "balance_sheet", col, labels)

    def pnl_label(labels: tuple[str, ...], col: str = "current_cumulative"):
        return _fact_value_by_labels(report, "income_statement", col, labels)

    def bs_section_label(
        section_labels: tuple[str, ...],
        item_labels: tuple[str, ...],
        end_section_labels: tuple[str, ...] | None = None,
        col: str = "current_period",
    ):
        return _fact_value_in_section(
            report, "balance_sheet", col, section_labels, item_labels, end_section_labels
        )

    # --- Balance sheet -----------------------------------------------------
    cash_labels = (
        "NOVAC I NOVČANI EKVIVALENTI",
        "NOVAC I NOVCANI EKVIVALENTI",
        "NOVAC U BANCI I BLAGAJNI",
        "CASH AND CASH EQUIVALENTS",
        "CASH AT BANK AND IN HAND",
    )
    cash = _first_value(bs_label(cash_labels), bs(63))

    total_assets = _first_value(
        bs_label(("UKUPNO AKTIVA", "TOTAL ASSETS")),
        bs(65),
    )
    equity_total = _first_value(
        bs_label(("KAPITAL I REZERVE", "CAPITAL AND RESERVES", "EQUITY AND RESERVES")),
        bs(67),
    )

    # Consolidated templates contain a balance-sheet addendum with the direct
    # attribution to owners of the parent. This is more reliable than assuming
    # a historic NCI AOP/ADP code survived a template revision.
    equity_parent_direct = bs_label((
        "PRIPISANO IMATELJIMA KAPITALA MATICE",
        "PRIPISANA IMATELJIMA KAPITALA MATICE",
        "PRIPISANO VLASNICIMA MATICE",
        "ATTRIBUTABLE TO OWNERS OF THE PARENT",
        "ATTRIBUTABLE TO EQUITY HOLDERS OF THE PARENT",
    ))
    noncontrolling_label = bs_label((
        "PRIPISANO NEKONTROLIRAJUĆEM INTERESU",
        "PRIPISANO NEKONTROLIRAJUCEM INTERESU",
        "PRIPISANO MANJINSKOM INTERESU",
        "MANJINSKI (NEKONTROLIRAJUĆI) INTERES",
        "MANJINSKI INTERES",
        "NON-CONTROLLING INTEREST",
        "MINORITY INTEREST",
    ))
    noncontrolling_legacy = bs(89)
    noncontrolling = _first_value(noncontrolling_label, noncontrolling_legacy)

    if equity_parent_direct is not None:
        equity_parent = equity_parent_direct
        equity_parent_quality = "reported-addendum"
    elif equity_total is not None and noncontrolling is not None:
        equity_parent = equity_total - noncontrolling
        equity_parent_quality = "derived-total-minus-nci"
    elif report.consolidated is False:
        equity_parent = equity_total
        equity_parent_quality = "same-as-total-unconsolidated"
    else:
        # Critical: for consolidated reports, missing NCI is not evidence that
        # NCI is zero. Leave parent equity unavailable instead of inventing it.
        equity_parent = None
        equity_parent_quality = "unavailable"

    share_capital = _first_value(
        bs_label((
            "TEMELJNI (UPISANI) KAPITAL",
            "TEMELJNI UPISANI KAPITAL",
            "UPISANI KAPITAL",
            "SHARE CAPITAL",
            "SUBSCRIBED CAPITAL",
        )),
        bs(68),
    )
    retained_earnings_reserves_parent = None
    if equity_parent is not None and share_capital is not None:
        # MojeDionice summary convention: parent capital and reserves less
        # subscribed/share capital.  This intentionally groups all remaining
        # reserves, retained earnings and other parent-equity components.
        retained_earnings_reserves_parent = equity_parent - share_capital

    short_term_financial_assets = _first_value(
        bs_label((
            "KRATKOTRAJNA FINANCIJSKA IMOVINA",
            "KRATKOTRAJNA FINANCIJSKA IMOVINA UKUPNO",
            "SHORT-TERM FINANCIAL ASSETS",
            "CURRENT FINANCIAL ASSETS",
        )),
        bs(53),
    )
    liquid_financial_assets = None
    if cash is not None or short_term_financial_assets is not None:
        liquid_financial_assets = (cash or 0.0) + (short_term_financial_assets or 0.0)

    current_assets = _first_value(
        bs_label(("KRATKOTRAJNA IMOVINA", "CURRENT ASSETS")),
        bs(37),
    )
    current_liabilities = _first_value(
        bs_label(("KRATKOROČNE OBVEZE", "CURRENT LIABILITIES")),
        bs(109),
    )
    long_term_liabilities = _first_value(
        bs_label((
            "DUGOROČNE OBVEZE",
            "NON-CURRENT LIABILITIES",
            "LONG-TERM LIABILITIES",
        )),
        bs(97),
    )
    total_debt_md = None
    if current_liabilities is not None or long_term_liabilities is not None:
        total_debt_md = (current_liabilities or 0.0) + (long_term_liabilities or 0.0)

    # --- Debt decomposition ------------------------------------------------
    # Detail labels such as "obveze prema bankama..." occur once under the
    # long-term section and again under the short-term section. Resolve them by
    # source-row section boundaries rather than fragile AOP/ADP numbers.
    lt_section = ("DUGOROČNE OBVEZE", "NON-CURRENT LIABILITIES", "LONG-TERM LIABILITIES")
    st_section = ("KRATKOROČNE OBVEZE", "CURRENT LIABILITIES")

    group_loans_labels = (
        "OBVEZE ZA ZAJMOVE, DEPOZITE I SLIČNO PODUZETNIKA UNUTAR GRUPE",
        "LOANS, DEPOSITS AND SIMILAR LIABILITIES TO GROUP UNDERTAKINGS",
    )
    participating_loans_labels = (
        "OBVEZE ZA ZAJMOVE, DEPOZITE I SLIČNO DRUŠTAVA POVEZANIH SUDJELUJUĆIM INTERESOM",
        "LOANS, DEPOSITS AND SIMILAR LIABILITIES TO PARTICIPATING-INTEREST UNDERTAKINGS",
    )
    loans_labels = (
        "OBVEZE ZA ZAJMOVE, DEPOZITE I SLIČNO",
        "LOANS, DEPOSITS AND SIMILAR LIABILITIES",
    )
    bank_labels = (
        "OBVEZE PREMA BANKAMA I DRUGIM FINANCIJSKIM INSTITUCIJAMA",
        "LIABILITIES TO BANKS AND OTHER FINANCIAL INSTITUTIONS",
        "LIABILITIES TO CREDIT INSTITUTIONS",
    )
    securities_labels = (
        "OBVEZE PO VRIJEDNOSNIM PAPIRIMA",
        "LIABILITIES ARISING FROM SECURITIES",
        "SECURITIES LIABILITIES",
    )

    lt_group_loans = bs_section_label(lt_section, group_loans_labels, st_section)
    lt_participating_loans = bs_section_label(lt_section, participating_loans_labels, st_section)
    lt_loans = bs_section_label(lt_section, loans_labels, st_section)
    lt_banks = bs_section_label(lt_section, bank_labels, st_section)
    lt_securities = bs_section_label(lt_section, securities_labels, st_section)

    st_group_loans = bs_section_label(st_section, group_loans_labels)
    st_participating_loans = bs_section_label(st_section, participating_loans_labels)
    st_loans = bs_section_label(st_section, loans_labels)
    st_banks = bs_section_label(st_section, bank_labels)
    st_securities = bs_section_label(st_section, securities_labels)

    lt_financing_components = (
        lt_group_loans, lt_participating_loans, lt_loans, lt_banks, lt_securities,
    )
    st_financing_components = (
        st_group_loans, st_participating_loans, st_loans, st_banks, st_securities,
    )
    financing_components = lt_financing_components + st_financing_components
    present_financing_components = [v for v in financing_components if v is not None]

    explicit_lt_financing_debt = (
        sum(v for v in lt_financing_components if v is not None)
        if any(v is not None for v in lt_financing_components) else None
    )
    explicit_st_financing_debt = (
        sum(v for v in st_financing_components if v is not None)
        if any(v is not None for v in st_financing_components) else None
    )
    related_party_components = (lt_group_loans, lt_participating_loans, st_group_loans, st_participating_loans)
    related_party_financing_debt = (
        sum(v for v in related_party_components if v is not None)
        if any(v is not None for v in related_party_components) else None
    )
    external_financing_components = (lt_loans, lt_banks, lt_securities, st_loans, st_banks, st_securities)
    external_financing_debt = (
        sum(v for v in external_financing_components if v is not None)
        if any(v is not None for v in external_financing_components) else None
    )

    # Residual liability buckets deliberately remain unclassified. They may
    # include trade payables, provisions, taxes, leases or other obligations;
    # the scanner does not silently promote them to financial debt.
    unclassified_lt_liabilities_residual = None
    if long_term_liabilities is not None and explicit_lt_financing_debt is not None:
        unclassified_lt_liabilities_residual = long_term_liabilities - explicit_lt_financing_debt
    unclassified_st_liabilities_residual = None
    if current_liabilities is not None and explicit_st_financing_debt is not None:
        unclassified_st_liabilities_residual = current_liabilities - explicit_st_financing_debt
    total_liabilities = None
    if long_term_liabilities is not None and current_liabilities is not None:
        total_liabilities = long_term_liabilities + current_liabilities

    # Legacy numeric fallback is retained only when no section-aware detail row
    # could be resolved at all. This protects old templates without allowing a
    # shifted 2025/2026 AOP code to override a valid label.
    debt_codes = (99, 101, 102, 103, 106, 111, 113, 114, 115, 118)
    legacy_gross_financial_debt = _sum_present(
        x, [("balance_sheet", "current_period", code) for code in debt_codes]
    )
    if present_financing_components:
        gross_financial_debt = sum(present_financing_components)
        gross_financial_debt_quality = "derived-section-labels"
    else:
        gross_financial_debt = legacy_gross_financial_debt
        gross_financial_debt_quality = "derived-legacy-aop-fallback"

    # Exact public MojeDionice methodology (as documented by the portal):
    # financial debt = all long-term liabilities + short-term liabilities to
    # financial institutions + short-term securities liabilities. If the
    # short-term type breakdown is unavailable, use total short-term liabilities.
    if st_banks is not None and st_securities is not None:
        md_short_term_financial_debt = st_banks + st_securities
        md_short_term_debt_basis = "bank+securities"
    else:
        md_short_term_financial_debt = current_liabilities
        md_short_term_debt_basis = "total-current-liabilities-fallback"

    md_financial_debt = None
    if long_term_liabilities is not None and md_short_term_financial_debt is not None:
        md_financial_debt = long_term_liabilities + md_short_term_financial_debt

    # Empirical MojeDionice-compatibility bridge. GRNL validation shows the
    # portal's displayed EV also includes the generic non-related-party
    # short-term loans/deposits row. Keep this separate from the portal's
    # published-method metric above so methodology and observed behaviour are
    # both preserved. Group and participating-interest loan rows stay excluded.
    if st_loans is not None and st_banks is not None and st_securities is not None:
        md_observed_short_term_financial_debt = st_loans + st_banks + st_securities
        md_observed_short_term_debt_basis = "loans+bank+securities-observed-compat"
    elif md_short_term_financial_debt is not None:
        md_observed_short_term_financial_debt = md_short_term_financial_debt
        md_observed_short_term_debt_basis = "published-method-fallback"
    else:
        md_observed_short_term_financial_debt = current_liabilities
        md_observed_short_term_debt_basis = "total-current-liabilities-fallback"

    md_observed_financial_debt = None
    if long_term_liabilities is not None and md_observed_short_term_financial_debt is not None:
        md_observed_financial_debt = long_term_liabilities + md_observed_short_term_financial_debt

    net_debt = None
    if gross_financial_debt is not None and cash is not None:
        net_debt = gross_financial_debt - cash
    net_debt_liquid = None
    if gross_financial_debt is not None and liquid_financial_assets is not None:
        net_debt_liquid = gross_financial_debt - liquid_financial_assets

    md_net_debt_liquid = None
    if md_financial_debt is not None and liquid_financial_assets is not None:
        md_net_debt_liquid = md_financial_debt - liquid_financial_assets

    md_observed_net_debt_liquid = None
    if md_observed_financial_debt is not None and liquid_financial_assets is not None:
        md_observed_net_debt_liquid = md_observed_financial_debt - liquid_financial_assets

    # --- Income statement -------------------------------------------------
    sales_revenue = _first_value(
        pnl_label(("PRIHODI OD PRODAJE", "SALES REVENUE", "REVENUE FROM SALES")),
        _sum_present(
            x,
            [
                ("income_statement", "current_cumulative", 2),
                ("income_statement", "current_cumulative", 3),
            ],
        ),
    )
    operating_income = _first_value(
        pnl_label(("POSLOVNI PRIHODI", "OPERATING REVENUE", "OPERATING INCOME")),
        pnl(1),
    )
    operating_expenses = _first_value(
        pnl_label(("POSLOVNI RASHODI", "OPERATING EXPENSES")),
        pnl(7),
    )
    operating_ebit = None
    if operating_income is not None and operating_expenses is not None:
        operating_ebit = operating_income - operating_expenses

    depreciation = _first_value(
        pnl_label(("AMORTIZACIJA", "DEPRECIATION AND AMORTISATION", "DEPRECIATION AND AMORTIZATION", "DEPRECIATION")),
        pnl(17),
    )
    ebitda_simple = None
    if operating_ebit is not None:
        ebitda_simple = operating_ebit + (depreciation or 0.0)

    value_adjustments = _first_value(
        pnl_label((
            "VRIJEDNOSNA USKLAĐENJA",
            "VRIJEDNOSNA USKLADENJA",
            "VALUE ADJUSTMENTS",
            "IMPAIRMENT LOSSES",
            "IMPAIRMENT",
        )),
        pnl(19),
    )
    provisions = _first_value(
        pnl_label((
            "REZERVIRANJA",
            "PROVISIONS",
        )),
        pnl(22),
    )
    md_value_adjustments_provisions = None
    if value_adjustments is not None or provisions is not None:
        md_value_adjustments_provisions = (value_adjustments or 0.0) + (provisions or 0.0)

    # AOP fallbacks below are the official general-issuer RDG totals used in
    # current EHO templates: financial income 030, financial expenses 041,
    # total income 053 and profit/loss before tax 055.  Labels remain first so
    # older/translated templates can still be resolved without hard-coding.
    financial_income = _first_value(
        pnl_label(("FINANCIJSKI PRIHODI", "FINANCIAL INCOME")),
        pnl(30),
    )
    financial_expenses = _first_value(
        pnl_label(("FINANCIJSKI RASHODI", "FINANCIAL EXPENSES")),
        pnl(41),
    )
    ebt = _first_value(
        pnl_label((
            "DOBIT ILI GUBITAK PRIJE OPOREZIVANJA",
            "DOBIT PRIJE OPOREZIVANJA",
            "PROFIT OR LOSS BEFORE TAX",
            "PROFIT BEFORE TAX",
        )),
        pnl(55),
    )
    total_revenue = _first_value(
        pnl_label(("UKUPNI PRIHODI", "TOTAL INCOME", "TOTAL REVENUE")),
        pnl(53),
    )

    # MojeDionice published methodology: EBIT = EBT + net financial expense,
    # where net financial expense = financial expense - financial income.
    md_ebit = None
    if ebt is not None and financial_income is not None and financial_expenses is not None:
        md_ebit = ebt + financial_expenses - financial_income
    md_ebitda = None
    if md_ebit is not None:
        md_ebitda = (
            md_ebit
            + (depreciation or 0.0)
            + (value_adjustments or 0.0)
            + (provisions or 0.0)
        )

    # MojeDionice's summary line groups value adjustments and provisions in
    # the EBITDA add-backs.  The standardized EHO income statement exposes
    # those as separate rows, so preserve both components and the combined
    # comparison value.  Company-style simple EBITDA above remains unchanged.

    net_income_parent = _first_value(
        pnl_label((
            "PRIPISANA IMATELJIMA KAPITALA MATICE",
            "PRIPISANO IMATELJIMA KAPITALA MATICE",
            "ATTRIBUTABLE TO OWNERS OF THE PARENT",
            "ATTRIBUTABLE TO EQUITY HOLDERS OF THE PARENT",
        )),
        pnl(76),
    )
    net_income_total = _first_value(
        pnl_label(("DOBIT ILI GUBITAK RAZDOBLJA", "PROFIT OR LOSS FOR THE PERIOD", "NET INCOME")),
        pnl(75),
    )
    comprehensive_income_parent = _first_value(
        # Current consolidated issuer form: total comprehensive income
        # attributable to owners of the parent. Numeric fallback is preferred
        # because the attribution label can occur more than once in the RDG.
        pnl(100),
        pnl_label((
            "SVEOBUHVATNA DOBIT ILI GUBITAK PRIPISAN IMATELJIMA KAPITALA MATICE",
            "SVEOBUHVATNA DOBIT PRIPISANA IMATELJIMA KAPITALA MATICE",
            "TOTAL COMPREHENSIVE INCOME ATTRIBUTABLE TO OWNERS OF THE PARENT",
        )),
    )
    # Quarterly EHO templates redundantly publish both cumulative and
    # quarter-only columns. In Q1 they cover the same Jan-Mar interval and
    # therefore provide a valuable cross-check for issuer/template anomalies.
    comprehensive_income_parent_quarter = pnl(100, "current_quarter")
    comprehensive_income_parent_previous_cumulative = pnl(100, "previous_cumulative")
    interest_expense = _sum_present(
        x,
        [
            ("income_statement", "current_cumulative", 42),
            ("income_statement", "current_cumulative", 44),
        ],
    )

    # --- Employee statistics ----------------------------------------------
    # Interim issuer notes explicitly report average employees during the
    # current period; some issuers additionally publish a period-end headcount.
    # Keep both measures separate and choose the report-scope value only.
    if report.consolidated is True:
        employees_average = _first_value(
            sup("employees_average_group"),
            sup("employees_average_unspecified"),
        )
        employees_period_end = _first_value(
            sup("employees_period_end_group"),
            sup("employees_period_end_unspecified"),
        )
        employee_scope = "group"
    elif report.consolidated is False:
        employees_average = _first_value(
            sup("employees_average_company"),
            sup("employees_average_unspecified"),
        )
        employees_period_end = _first_value(
            sup("employees_period_end_company"),
            sup("employees_period_end_unspecified"),
        )
        employee_scope = "company"
    else:
        employees_average = _first_value(
            sup("employees_average_unspecified"),
            sup("employees_average_group"),
            sup("employees_average_company"),
        )
        employees_period_end = _first_value(
            sup("employees_period_end_unspecified"),
            sup("employees_period_end_group"),
            sup("employees_period_end_company"),
        )
        employee_scope = "unspecified"

    # MojeDionice-compatible display preference: its summary "Broj zaposlenih"
    # behaves like a reporting-date headcount when such a disclosure is present.
    # Preserve the statutory average-period statistic separately, but prefer an
    # explicit period-end/group headcount for the comparison layer. Never infer
    # one employee measure from the other.
    employees_reported = _first_value(employees_period_end, employees_average)
    employees_measure = (
        "period-end" if employees_period_end is not None
        else ("average-period" if employees_average is not None else "unavailable")
    )

    # --- Cash flow --------------------------------------------------------
    direct_values = [
        f.value for f in report.facts
        if f.statement == "cash_flow_direct" and f.column_name == "current_period" and f.value not in (None, 0)
    ]
    indirect_values = [
        f.value for f in report.facts
        if f.statement == "cash_flow_indirect" and f.column_name == "current_period" and f.value not in (None, 0)
    ]
    use_direct = len(direct_values) >= len(indirect_values)
    cf_statement = "cash_flow_direct" if use_direct else "cash_flow_indirect"
    if use_direct:
        cfo = cfd(14)
        capex_payment = cfd(22)
        cf_method = "direct"
    else:
        cfo = cfi(20)
        capex_payment = cfi(28)
        cf_method = "indirect"

    cfo = _first_value(
        _fact_value_by_labels(report, cf_statement, "current_period", (
            "NETO NOVČANI TOKOVI OD POSLOVNIH AKTIVNOSTI",
            "NETO NOVCANI TOKOVI OD POSLOVNIH AKTIVNOSTI",
            "NET CASH FLOWS FROM OPERATING ACTIVITIES",
            "NET CASH FLOW FROM OPERATING ACTIVITIES",
        )),
        cfo,
    )
    capex_payment = _first_value(
        _fact_value_by_labels(report, cf_statement, "current_period", (
            "NOVČANI IZDACI ZA KUPNJU DUGOTRAJNE MATERIJALNE I NEMATERIJALNE IMOVINE",
            "NOVCANI IZDACI ZA KUPNJU DUGOTRAJNE MATERIJALNE I NEMATERIJALNE IMOVINE",
            "CASH PAYMENTS TO ACQUIRE LONG-TERM TANGIBLE AND INTANGIBLE ASSETS",
            "PURCHASE OF PROPERTY PLANT EQUIPMENT AND INTANGIBLE ASSETS",
        )),
        capex_payment,
    )
    asset_sale_proceeds = _fact_value_by_labels(report, cf_statement, "current_period", (
        "NOVČANI PRIMICI OD PRODAJE DUGOTRAJNE MATERIJALNE I NEMATERIJALNE IMOVINE",
        "NOVCANI PRIMICI OD PRODAJE DUGOTRAJNE MATERIJALNE I NEMATERIJALNE IMOVINE",
        "CASH RECEIPTS FROM SALES OF LONG-TERM TANGIBLE AND INTANGIBLE ASSETS",
        "PROCEEDS FROM SALE OF PROPERTY PLANT EQUIPMENT AND INTANGIBLE ASSETS",
    ))

    capex = _abs_if_present(capex_payment)
    asset_sale_proceeds_abs = _abs_if_present(asset_sale_proceeds)
    fcf = None if cfo is None or capex is None else cfo - capex
    net_capex = None if capex is None else capex - (asset_sale_proceeds_abs or 0.0)
    fcf_net_capex = None if cfo is None or net_capex is None else cfo - net_capex

    # --- Previous-period equity for legacy run-rate metric ----------------
    previous_equity_total = _first_value(
        bs_label(("KAPITAL I REZERVE", "CAPITAL AND RESERVES", "EQUITY AND RESERVES"), "previous_period"),
        bs(67, "previous_period"),
    )
    previous_parent_direct = bs_label((
        "PRIPISANO IMATELJIMA KAPITALA MATICE",
        "PRIPISANA IMATELJIMA KAPITALA MATICE",
        "ATTRIBUTABLE TO OWNERS OF THE PARENT",
        "ATTRIBUTABLE TO EQUITY HOLDERS OF THE PARENT",
    ), "previous_period")
    previous_nci = _first_value(
        bs_label((
            "PRIPISANO NEKONTROLIRAJUĆEM INTERESU",
            "PRIPISANO NEKONTROLIRAJUCEM INTERESU",
            "PRIPISANO MANJINSKOM INTERESU",
            "NON-CONTROLLING INTEREST",
            "MINORITY INTEREST",
        ), "previous_period"),
        bs(89, "previous_period"),
    )
    if previous_parent_direct is not None:
        previous_equity_parent = previous_parent_direct
    elif previous_equity_total is not None and previous_nci is not None:
        previous_equity_parent = previous_equity_total - previous_nci
    elif report.consolidated is False:
        previous_equity_parent = previous_equity_total
    else:
        previous_equity_parent = None

    average_equity_parent = None
    if equity_parent is not None and previous_equity_parent is not None:
        average_equity_parent = (equity_parent + previous_equity_parent) / 2.0

    annualization = None
    if report.quarter in (1, 2, 3, 4):
        annualization = 4.0 / report.quarter
    elif report.quarter is None and report.period_start and report.period_end:
        days = (report.period_end - report.period_start).days + 1
        if days > 0:
            annualization = 365.25 / days

    roe_run_rate = None
    if annualization and net_income_parent is not None:
        roe_run_rate = _safe_ratio(net_income_parent * annualization, average_equity_parent)

    metrics = [
        Metric("cash", cash, unit, "reported"),
        Metric("short_term_financial_assets", short_term_financial_assets, unit, "reported-label"),
        Metric("liquid_financial_assets", liquid_financial_assets, unit, "derived", "Cash + short-term financial assets."),
        Metric("current_assets", current_assets, unit, "reported-label"),
        Metric("current_liabilities", current_liabilities, unit, "reported-label"),
        Metric("long_term_liabilities", long_term_liabilities, unit, "reported-label"),
        Metric("total_debt_md", total_debt_md, unit, "derived", "MojeDionice-style total debt: current + long-term liabilities."),
        Metric("debt_lt_group_loans", lt_group_loans, unit, "reported-section-label"),
        Metric("debt_lt_participating_loans", lt_participating_loans, unit, "reported-section-label"),
        Metric("debt_lt_loans_deposits", lt_loans, unit, "reported-section-label"),
        Metric("debt_lt_banks_financial_institutions", lt_banks, unit, "reported-section-label"),
        Metric("debt_lt_securities", lt_securities, unit, "reported-section-label"),
        Metric("debt_st_group_loans", st_group_loans, unit, "reported-section-label"),
        Metric("debt_st_participating_loans", st_participating_loans, unit, "reported-section-label"),
        Metric("debt_st_loans_deposits", st_loans, unit, "reported-section-label"),
        Metric("debt_st_banks_financial_institutions", st_banks, unit, "reported-section-label"),
        Metric("debt_st_securities", st_securities, unit, "reported-section-label"),
        Metric("md_short_term_financial_debt", md_short_term_financial_debt, unit, "derived-md",
               f"MojeDionice current-debt leg basis: {md_short_term_debt_basis}."),
        Metric("md_financial_debt", md_financial_debt, unit, "derived-md",
               "MojeDionice methodology: all long-term liabilities + short-term bank/financial-institution liabilities + short-term securities liabilities; falls back to all current liabilities when type breakdown is unavailable."),
        Metric("md_net_debt_liquid_assets", md_net_debt_liquid, unit, "derived-md",
               "Published-method MojeDionice financial debt minus cash and short-term financial assets."),
        Metric("md_observed_short_term_financial_debt", md_observed_short_term_financial_debt, unit, "derived-md-observed",
               f"Observed MojeDionice-compatibility current-debt leg basis: {md_observed_short_term_debt_basis}. Excludes group and participating-interest loans."),
        Metric("md_observed_financial_debt", md_observed_financial_debt, unit, "derived-md-observed",
               "Observed compatibility bridge: all long-term liabilities + non-related short-term loans/deposits + banks/financial institutions + securities. Kept separate from published methodology."),
        Metric("md_observed_net_debt_liquid_assets", md_observed_net_debt_liquid, unit, "derived-md-observed",
               "Observed-compatible financial debt minus cash and short-term financial assets."),
        Metric("total_assets", total_assets, unit, "reported"),
        Metric("equity_total", equity_total, unit, "reported"),
        Metric("equity_nci", noncontrolling, unit, "reported/derived"),
        Metric("equity_parent", equity_parent, unit, equity_parent_quality,
               "Prefer consolidated balance-sheet addendum attributable to owners of parent; never assume missing NCI is zero."),
        Metric("share_capital", share_capital, unit, "reported-label",
               "Subscribed/share capital from the balance sheet."),
        Metric("retained_earnings_reserves_parent", retained_earnings_reserves_parent, unit, "derived-md",
               "MojeDionice-style summary = parent equity - share capital."),
        Metric("gross_financial_debt_ex_other", gross_financial_debt, unit, gross_financial_debt_quality,
               "Explicit standardized financing rows (loans/deposits, banks/financial institutions and securities, including related-party loan rows). Excludes generic other liabilities; IFRS 16 leases may be embedded elsewhere."),
        Metric("gross_financial_debt_standardized", gross_financial_debt, unit, gross_financial_debt_quality,
               "Alias with explicit naming for the standardized financing-row debt total; lease-inclusive debt is not claimed unless separately disclosed."),
        Metric("explicit_long_term_financing_debt", explicit_lt_financing_debt, unit, "derived-section-labels",
               "Sum of explicit long-term financing rows, including related-party loans, generic loans/deposits, bank debt and securities."),
        Metric("explicit_short_term_financing_debt", explicit_st_financing_debt, unit, "derived-section-labels",
               "Sum of explicit short-term financing rows, including related-party loans, generic loans/deposits, bank debt and securities."),
        Metric("external_financing_debt", external_financing_debt, unit, "derived-section-labels",
               "Explicit non-related-party financing rows: generic loans/deposits + bank/financial-institution debt + securities, long- and short-term."),
        Metric("related_party_financing_debt", related_party_financing_debt, unit, "derived-section-labels",
               "Explicit financing owed to group and participating-interest undertakings, long- and short-term."),
        Metric("unclassified_long_term_liabilities_residual", unclassified_lt_liabilities_residual, unit, "derived-residual",
               "All long-term liabilities less explicit financing rows. This residual is not automatically classified as debt; it can contain provisions, leases or other obligations."),
        Metric("unclassified_current_liabilities_residual", unclassified_st_liabilities_residual, unit, "derived-residual",
               "All current liabilities less explicit financing rows. This residual is not automatically classified as debt; it can contain trade, tax, lease or other obligations."),
        Metric("total_liabilities", total_liabilities, unit, "derived", "Long-term liabilities + current liabilities."),
        Metric("net_debt_ex_other", net_debt, unit, "derived",
               "Gross financial debt minus cash; may exclude lease liabilities."),
        Metric("net_debt_liquid_assets", net_debt_liquid, unit, "derived",
               "Gross financial debt ex other minus cash and short-term financial assets; debt side remains scanner proxy."),
        Metric("sales_revenue_ytd", sales_revenue, unit, "reported/derived"),
        Metric("operating_revenue_ytd", operating_income, unit, "reported/derived"),
        Metric("total_revenue_ytd", total_revenue, unit, "reported-label"),
        Metric("ebit_ytd", operating_ebit, unit, "derived",
               "Company-style operating EBIT = operating revenue - operating expenses."),
        Metric("depreciation_ytd", depreciation, unit, "reported"),
        Metric("value_adjustments_ytd", value_adjustments, unit, "reported-label"),
        Metric("provisions_ytd", provisions, unit, "reported-label"),
        Metric("md_value_adjustments_provisions_ytd", md_value_adjustments_provisions, unit, "derived-md",
               "MojeDionice-style combined EBITDA add-back: value adjustments + provisions."),
        Metric("ebitda_simple_ytd", ebitda_simple, unit, "derived",
               "Company-style simple EBITDA = operating EBIT + depreciation/amortization; excludes value adjustments."),
        Metric("financial_income_ytd", financial_income, unit, "reported-label"),
        Metric("financial_expense_ytd", financial_expenses, unit, "reported-label"),
        Metric("ebt_ytd", ebt, unit, "reported-label"),
        Metric("md_ebit_ytd", md_ebit, unit, "derived-md",
               "MojeDionice methodology: EBT + financial expenses - financial income."),
        Metric("md_ebitda_ytd", md_ebitda, unit, "derived-md",
               "MojeDionice-compatible EBITDA: EBIT + depreciation/amortization + value adjustments + provisions."),
        Metric("net_income_parent_ytd", net_income_parent, unit, "reported"),
        Metric("net_income_total_ytd", net_income_total, unit, "reported"),
        Metric("comprehensive_income_parent_ytd", comprehensive_income_parent, unit, "reported-md",
               "Total comprehensive income attributable to owners of the parent."),
        Metric("comprehensive_income_parent_quarter", comprehensive_income_parent_quarter, unit, "reported-md-crosscheck",
               "Quarter-only comprehensive income attributable to owners of the parent; retained as a Q1 cross-check."),
        Metric("comprehensive_income_parent_previous_cumulative", comprehensive_income_parent_previous_cumulative, unit, "reported-md-comparative",
               "Prior-year comparative cumulative comprehensive income attributable to owners of the parent, as published in the current filing."),
        Metric("employees_average_current_period", employees_average, "count", "reported-notes",
               f"Explicitly reported average employees during the current period; scope={employee_scope}."),
        Metric("employees_period_end", employees_period_end, "count", "reported-notes",
               f"Explicitly reported period-end employee count; scope={employee_scope}."),
        Metric("employees_reported", employees_reported, "count", "reported-notes",
               f"Summary employee statistic; measure={employees_measure}; scope={employee_scope}."),
        Metric("interest_expense_ytd", interest_expense, unit, "derived"),
        Metric("operating_cash_flow_ytd", cfo, unit, "reported", f"Cash-flow method: {cf_method}"),
        Metric("asset_sale_proceeds_ytd", asset_sale_proceeds_abs, unit, "reported-label", f"Cash-flow method: {cf_method}"),
        Metric("capex_ytd", capex, unit, "derived", f"Gross capex cash payments; cash-flow method: {cf_method}"),
        Metric("net_capex_ytd", net_capex, unit, "derived-md", "Gross capex less proceeds from sale of tangible/intangible long-term assets."),
        Metric("free_cash_flow_ytd", fcf, unit, "derived", "Simple FCF = CFO - gross capex."),
        Metric("free_cash_flow_net_capex_ytd", fcf_net_capex, unit, "derived-md", "MojeDionice-style FCF = CFO - net capex."),
        Metric("net_debt_to_ebitda_run_rate", _safe_ratio(net_debt, ebitda_simple * annualization if ebitda_simple is not None and annualization else None), "x", "derived",
               "Uses YTD simple EBITDA annualized as a run-rate; not TTM."),
        Metric("interest_coverage_ebit", _safe_ratio(operating_ebit, interest_expense), "x", "derived"),
        Metric("debt_to_equity_parent", _safe_ratio(gross_financial_debt, equity_parent), "x", "derived"),
        Metric("total_debt_to_equity_parent_md", _safe_ratio(total_debt_md, equity_parent), "x", "derived-md"),
        Metric("current_ratio", _safe_ratio(current_assets, current_liabilities), "x", "derived-md"),
        Metric("fcf_margin_ytd", _safe_ratio(fcf, sales_revenue), "ratio", "derived"),
        Metric("roe_run_rate", roe_run_rate, "ratio", "derived",
               "Annualized YTD parent earnings / average parent equity; not TTM and can be seasonal."),
    ]
    return metrics
