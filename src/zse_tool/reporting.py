from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class ReportDescriptor:
    period_key: str
    period_label: str
    scope: str
    audit: str


def _get(obj: Any, key: str, default=None):
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    try:
        return obj[key]
    except (KeyError, IndexError, TypeError):
        return getattr(obj, key, default)


def period_key(*, year: int | None, quarter: int | None, period_end: str | date | None, fallback: str = "unknown") -> str:
    """Stable grouping key for reports covering the same reporting period.

    Prefer an actual reporting-period end date when available. If a legacy or
    unusual XLSX does not expose that date, fall back to year/quarter. This lets
    report selection work even when General data metadata is incomplete.
    """
    if isinstance(period_end, date):
        return period_end.isoformat()
    if period_end:
        return str(period_end)
    if year is not None and quarter in (1, 2, 3, 4):
        return f"{year:04d}-Q{quarter}"
    if year is not None:
        return f"{year:04d}-FY"
    return fallback


def period_label(*, year: int | None, quarter: int | None, period_start: str | date | None = None,
                 period_end: str | date | None = None) -> str:
    """Human-readable report-period label without inventing accounting values."""
    if year is not None and quarter in (1, 2, 3, 4):
        return f"{year}-Q{quarter}"

    # Annual EHO reports normally have quarter=None and Jan-1..Dec-31 dates.
    if year is not None and period_end:
        end_text = period_end.isoformat() if isinstance(period_end, date) else str(period_end)
        if end_text.startswith(f"{year:04d}-12-31"):
            return f"{year}-FY"
    if year is not None:
        return f"{year}-FY"
    if period_end:
        return str(period_end)
    return "unknown-period"


def scope_label(consolidated: bool | int | None) -> str:
    if consolidated in (True, 1):
        return "CONSOLIDATED"
    if consolidated in (False, 0):
        return "UNCONSOLIDATED"
    return "SCOPE-UNKNOWN"


def audit_label(audited: bool | int | None) -> str:
    if audited in (True, 1):
        return "AUDITED"
    if audited in (False, 0):
        return "UNAUDITED"
    return "AUDIT-UNKNOWN"


def describe_report(obj: Any) -> ReportDescriptor:
    year = _get(obj, "year")
    quarter = _get(obj, "quarter")
    start = _get(obj, "period_start")
    end = _get(obj, "period_end")
    fallback = str(_get(obj, "local_path", "unknown"))
    return ReportDescriptor(
        period_key=period_key(year=year, quarter=quarter, period_end=end, fallback=fallback),
        period_label=period_label(year=year, quarter=quarter, period_start=start, period_end=end),
        scope=scope_label(_get(obj, "consolidated")),
        audit=audit_label(_get(obj, "audited")),
    )
