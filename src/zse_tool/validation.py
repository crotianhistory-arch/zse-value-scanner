from __future__ import annotations

import re
import unicodedata

from .models import Fact, ParsedReport


def _norm_label(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold()
    text = re.sub(r"^\s*\d+(?:[.\)])?\s*", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _find_fact(report: ParsedReport, statement: str, column: str, labels: tuple[str, ...]) -> Fact | None:
    wanted = tuple(_norm_label(x) for x in labels)
    candidates: list[Fact] = []
    for fact in report.facts:
        if fact.statement != statement or fact.column_name != column or fact.value is None:
            continue
        label = _norm_label(fact.label)
        if label in wanted:
            return fact
        if any(label.endswith(x) for x in wanted):
            candidates.append(fact)
    return candidates[0] if len(candidates) == 1 else None


def validate_report(report: ParsedReport) -> list[str]:
    warnings = list(report.warnings)

    # Do not hard-code the total-balance ADP code. EHO added rows to the
    # balance-sheet template in 2025/2026, which shifted the old ADP=125 total.
    # The published row label is much more stable across template generations.
    assets = _find_fact(
        report,
        "balance_sheet",
        "current_period",
        ("UKUPNO AKTIVA", "TOTAL ASSETS"),
    )
    total = _find_fact(
        report,
        "balance_sheet",
        "current_period",
        (
            "UKUPNO PASIVA",
            "UKUPNO KAPITAL I OBVEZE",
            "UKUPNO KAPITAL I OBAVEZE",
            "TOTAL EQUITY AND LIABILITIES",
            "TOTAL LIABILITIES AND EQUITY",
        ),
    )
    if assets is not None and total is not None:
        delta = abs(float(assets.value) - float(total.value))
        tolerance = max(1.0, abs(float(assets.value)) * 1e-6)
        if delta > tolerance:
            warnings.append(
                f"Balance sheet does not balance: assets={assets.value}, total={total.value}, delta={delta}"
            )

    # Cash validation is useful only when the cash-flow template actually
    # supplies a non-zero end-of-period cash value. Older migrated EHO files can
    # expose a structural zero in that cell; treating it as real produced false
    # warnings for every 2019/2020 report.
    bs_cash = _find_fact(
        report,
        "balance_sheet",
        "current_period",
        ("NOVAC I NOVCANI EKVIVALENTI", "CASH AND CASH EQUIVALENTS"),
    )
    cf_cash = None
    for statement in ("cash_flow_direct", "cash_flow_indirect"):
        candidate = _find_fact(
            report,
            statement,
            "current_period",
            (
                "NOVAC I NOVCANI EKVIVALENTI NA KRAJU RAZDOBLJA",
                "NOVAC I NOVCANI EKVIVALENTI NA KRAJU PERIODA",
                "CASH AND CASH EQUIVALENTS AT END OF PERIOD",
            ),
        )
        if candidate is not None and candidate.value not in (None, 0, 0.0):
            cf_cash = candidate
            break

    if bs_cash is not None and cf_cash is not None:
        delta = abs(float(bs_cash.value) - float(cf_cash.value))
        tolerance = max(1.0, abs(float(bs_cash.value)) * 1e-6)
        if delta > tolerance:
            warnings.append(
                f"Cash mismatch: balance_sheet={bs_cash.value}, cash_flow_end={cf_cash.value}, delta={delta}"
            )

    if report.consolidated is False:
        warnings.append("Report is unconsolidated; for group analysis prefer consolidated statements when available.")
    if report.period_end is None:
        warnings.append("Could not determine reporting period end from General data sheet.")
    return warnings
