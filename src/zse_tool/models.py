from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class EhoItem:
    source_id: str
    variant: str
    issuer_code: str | None
    issuer_name: str | None
    title: str | None
    publish_date: str | None
    item_link: str | None
    raw: dict[str, Any]
    document_urls: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ParsedReport:
    source_path: Path
    issuer_name: str | None
    period_start: date | None
    period_end: date | None
    year: int | None
    quarter: int | None
    consolidated: bool | None
    audited: bool | None
    currency: str
    scale: float
    facts: list["Fact"]
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Fact:
    statement: str
    adp_code: int
    label: str
    column_name: str
    value: float | None
    unit: str
    source_sheet: str
    source_row: int


@dataclass(slots=True)
class Metric:
    name: str
    value: float | None
    unit: str
    quality: str = "derived"
    note: str | None = None
