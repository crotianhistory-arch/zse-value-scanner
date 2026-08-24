from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable

from .esef import ESEFFiling, discover_filings, select_latest_esef
from .esef_activity import _fetch_bounded, inventory_xbrl_activity, parse_xbrl_json
from .gleif import normalize_lei

DEFAULT_JSON_LIMIT = 25 * 1024 * 1024
DEFAULT_TEXT_LIMIT = 16_000
DEFAULT_TABLES_PER_FACT = 12
DEFAULT_ROWS_PER_TABLE = 120
DEFAULT_CELLS_PER_ROW = 40
DEFAULT_CELL_CHARS = 1_000


@dataclass(frozen=True)
class ExtractedTable:
    rows: list[list[str]]
    truncated: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _TaggedHTMLParser(HTMLParser):
    def __init__(
        self,
        *,
        max_tables: int = DEFAULT_TABLES_PER_FACT,
        max_rows: int = DEFAULT_ROWS_PER_TABLE,
        max_cells: int = DEFAULT_CELLS_PER_ROW,
        max_cell_chars: int = DEFAULT_CELL_CHARS,
    ) -> None:
        super().__init__(convert_charrefs=True)
        self.visible_parts: list[str] = []
        self.tables: list[ExtractedTable] = []
        self._skip_depth = 0
        self._table_depth = 0
        self._current_rows: list[list[str]] | None = None
        self._current_row: list[str] | None = None
        self._current_cell_parts: list[str] | None = None
        self._table_truncated = False
        self.max_tables = max_tables
        self.max_rows = max_rows
        self.max_cells = max_cells
        self.max_cell_chars = max_cell_chars

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "table":
            self._table_depth += 1
            if self._table_depth == 1 and len(self.tables) < self.max_tables:
                self._current_rows = []
                self._table_truncated = False
        elif tag == "tr" and self._current_rows is not None and self._table_depth == 1:
            if len(self._current_rows) < self.max_rows:
                self._current_row = []
            else:
                self._current_row = None
                self._table_truncated = True
        elif tag in {"td", "th"} and self._current_row is not None:
            if len(self._current_row) < self.max_cells:
                self._current_cell_parts = []
            else:
                self._current_cell_parts = None
                self._table_truncated = True

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"}:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag in {"td", "th"} and self._current_row is not None and self._current_cell_parts is not None:
            text = " ".join(" ".join(self._current_cell_parts).split())[: self.max_cell_chars]
            self._current_row.append(text)
            self._current_cell_parts = None
        elif tag == "tr" and self._current_rows is not None and self._current_row is not None:
            if any(cell for cell in self._current_row):
                self._current_rows.append(self._current_row)
            self._current_row = None
        elif tag == "table":
            if self._table_depth == 1 and self._current_rows is not None:
                self.tables.append(ExtractedTable(rows=self._current_rows, truncated=self._table_truncated))
                self._current_rows = None
                self._current_row = None
                self._current_cell_parts = None
            if self._table_depth:
                self._table_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        value = " ".join(data.split())
        if not value:
            return
        self.visible_parts.append(value)
        if self._current_cell_parts is not None:
            self._current_cell_parts.append(value)


def extract_tagged_html(value: str, *, text_limit: int = DEFAULT_TEXT_LIMIT) -> tuple[str, list[ExtractedTable], bool]:
    """Convert a tagged XBRL text fact to visible text and bounded table rows."""
    parser = _TaggedHTMLParser()
    parser.feed(value)
    text = " ".join(" ".join(parser.visible_parts).split())
    truncated = len(text) > text_limit
    return text[:text_limit], parser.tables, truncated


def _fold(value: str | None) -> str:
    return (value or "").casefold().replace("_", "").replace("-", "")


def classify_reported_activity(concept: str, text: str, tables: list[ExtractedTable]) -> str | None:
    """Classify reported text by disclosure purpose, not by inferred industry."""
    c = _fold(concept)
    t = text.casefold()

    if "natureofentitysoperationsandprincipalactivities" in c or "principalactivities" in c:
        return "principal_activity"
    if "reportablesegments" in c or "operatingsegments" in c or "segmentinformation" in c:
        return "operating_segments"
    if "significantinvestmentsinsubsidiaries" in c or "subsidiariesexplanatory" in c:
        return "subsidiaries"
    if "principalplaceofbusiness" in c or "geographic" in c:
        return "geography"
    if "majorcustomers" in c:
        return "customers"
    if "product" in c and "service" in c:
        return "products_services"
    if "revenuefromcontractswithcustomers" in c or "revenueexplanatory" in c:
        if tables or any(term in t for term in ("business line", "segment", "product", "service", "geograph", "region")):
            return "revenue_business_line"
    if any(term in c for term in ("segment", "businessline", "businessunit", "division")):
        return "operating_segments"
    if any(term in c for term in ("product", "service")) and "accountingpolicy" not in c:
        return "products_services"
    return None


def extract_reported_activity_facts(
    payload: dict[str, Any],
    *,
    max_facts: int = 160,
    text_limit: int = DEFAULT_TEXT_LIMIT,
) -> dict[str, Any]:
    """Extract clean, provenance-linked reported activity facts from xBRL-JSON."""
    facts = payload.get("facts") or {}
    rows: list[dict[str, Any]] = []
    category_counts: Counter[str] = Counter()

    for fact_id, fact in facts.items():
        if len(rows) >= max_facts:
            break
        if not isinstance(fact, dict):
            continue
        dimensions = fact.get("dimensions") or {}
        if not isinstance(dimensions, dict):
            continue
        value = fact.get("value")
        language = dimensions.get("language")
        if not language or not isinstance(value, str) or not value.strip():
            continue

        concept = str(dimensions.get("concept") or "")
        has_markup = "<" in value and ">" in value
        if has_markup:
            clean_text, tables, text_truncated = extract_tagged_html(value, text_limit=text_limit)
        else:
            clean_text = " ".join(value.split())
            text_truncated = len(clean_text) > text_limit
            clean_text = clean_text[:text_limit]
            tables = []

        category = classify_reported_activity(concept, clean_text, tables)
        if category is None:
            continue

        category_counts[category] += 1
        rows.append({
            "evidence_class": "R2_REPORTED_TEXT",
            "category": category,
            "fact_id": str(fact_id),
            "concept": concept,
            "period": dimensions.get("period"),
            "language": language,
            "text": clean_text,
            "text_truncated": text_truncated,
            "source_value_chars": len(value),
            "html_detected": has_markup,
            "tables": [table.to_dict() for table in tables],
        })

    return {
        "reported_activity_facts": rows,
        "category_counts": dict(sorted(category_counts.items())),
        "selected_fact_count": len(rows),
        "selection_truncated": len(rows) >= max_facts,
    }


def build_tagged_activity_evidence(
    lei: str,
    *,
    timeout: float = 30.0,
    limit: int = 25,
    prefer_language: str = "en",
    discoverer: Callable[..., list[ESEFFiling]] = discover_filings,
    fetcher: Callable[..., bytes] = _fetch_bounded,
    json_limit: int = DEFAULT_JSON_LIMIT,
) -> dict[str, Any]:
    lei_norm = normalize_lei(lei)
    filings = discoverer(lei_norm, limit=limit, timeout=timeout)
    filing = select_latest_esef(filings, prefer_language=prefer_language)
    if not filing.json_url:
        raise ValueError("selected ESEF filing does not provide xBRL-JSON")

    json_bytes = fetcher(filing.json_url, max_bytes=json_limit, timeout=timeout)
    xbrl_payload = parse_xbrl_json(json_bytes)
    inventory = inventory_xbrl_activity(xbrl_payload)
    reported = extract_reported_activity_facts(xbrl_payload)

    return {
        "mode": "read-only-tagged-xbrl-activity-evidence",
        "policy": {
            "automatic_database_writes": False,
            "automatic_peer_assignment": False,
            "automatic_similarity_scoring": False,
            "llm_used": False,
            "full_xhtml_fetched": False,
            "reported_facts_are_not_analytical_mappings": True,
            "classification_scope": "disclosure purpose only; no industry/peer conclusion",
        },
        "entity": {
            "lei": lei_norm,
            "reported_name": filing.entity_name,
            "country": filing.country,
        },
        "filing": filing.to_dict(),
        "xbrl_activity": inventory,
        "tagged_activity": reported,
        "provenance": {
            "discovery": filing.api_source_url,
            "xbrl_json": filing.json_url,
            "original_report_package": filing.package_url,
            "reported_package_sha256": filing.package_sha256,
        },
    }


def write_json_atomic(payload: dict[str, Any], path: Path) -> Path:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, target)
    return target


def build_batch(
    leis: list[str],
    *,
    builder: Callable[..., dict[str, Any]] = build_tagged_activity_evidence,
    **kwargs: Any,
) -> dict[str, Any]:
    unique: list[str] = []
    seen: set[str] = set()
    for raw in leis:
        lei = normalize_lei(raw)
        if lei not in seen:
            seen.add(lei)
            unique.append(lei)

    rows: list[dict[str, Any]] = []
    for lei in unique:
        try:
            payload = builder(lei, **kwargs)
            tagged = payload["tagged_activity"]
            rows.append({
                "lei": lei,
                "state": "ok",
                "entity_name": payload["entity"].get("reported_name"),
                "country": payload["entity"].get("country"),
                "period_end": payload["filing"].get("period_end"),
                "language": payload["filing"].get("language"),
                "xbrl_fact_count": payload["xbrl_activity"].get("fact_count"),
                "reported_activity_fact_count": tagged.get("selected_fact_count"),
                "category_counts": tagged.get("category_counts"),
                "payload": payload,
            })
        except Exception as exc:  # batch isolation is intentional; errors remain explicit
            rows.append({
                "lei": lei,
                "state": "error",
                "error_type": type(exc).__name__,
                "error": str(exc),
            })

    return {
        "mode": "read-only-tagged-xbrl-activity-batch",
        "policy": {
            "automatic_database_writes": False,
            "automatic_peer_assignment": False,
            "automatic_similarity_scoring": False,
            "llm_used": False,
        },
        "requested": len(unique),
        "succeeded": sum(1 for row in rows if row["state"] == "ok"),
        "failed": sum(1 for row in rows if row["state"] == "error"),
        "results": rows,
    }


def write_batch_outputs(batch: dict[str, Any], output_dir: Path) -> Path:
    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    summary_rows: list[dict[str, Any]] = []
    for row in batch["results"]:
        clean = dict(row)
        payload = clean.pop("payload", None)
        if payload is not None:
            path = root / f"{row['lei']}.json"
            write_json_atomic(payload, path)
            clean["manifest_path"] = str(path)
        summary_rows.append(clean)
    summary = dict(batch)
    summary["results"] = summary_rows
    return write_json_atomic(summary, root / "batch-summary.json")


def _print_entity_summary(payload: dict[str, Any]) -> None:
    tagged = payload["tagged_activity"]
    print(f"Entity: {payload['entity'].get('reported_name') or '-'}")
    print(f"LEI: {payload['entity']['lei']}")
    print(f"Country: {payload['entity'].get('country') or '-'}")
    print(f"Filing period: {payload['filing'].get('period_end') or '-'}")
    print(f"Language: {payload['filing'].get('language') or '-'}")
    print(f"XBRL facts: {payload['xbrl_activity'].get('fact_count')}")
    print(f"Reported activity facts: {tagged['selected_fact_count']}")
    print("Categories:")
    for category, count in tagged["category_counts"].items():
        print(f"  {category}: {count}")
    print()
    print("Read-only: no entity, peer, similarity, financial-fact, job or raw-artifact database write was performed.")
    print("Tagged categories describe disclosure purpose only; they are not a peer conclusion.")


def _print_batch_summary(batch: dict[str, Any]) -> None:
    print(f"Requested: {batch['requested']}  Succeeded: {batch['succeeded']}  Failed: {batch['failed']}")
    print("STATE  COUNTRY PERIOD      LEI                   ENTITY / ERROR")
    for row in batch["results"]:
        if row["state"] == "ok":
            print(
                f"ok     {(row.get('country') or '-'):7} {(row.get('period_end') or '-'):11} "
                f"{row['lei']:20} {row.get('entity_name') or '-'}"
            )
        else:
            print(f"error  {'-':7} {'-':11} {row['lei']:20} {row.get('error_type')}: {row.get('error')}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m zse_tool.esef_tagged_activity",
        description="Extract clean, structured business-activity evidence from tagged ESEF xBRL-JSON without fetching full XHTML.",
    )
    parser.add_argument("--lei", action="append", required=True, help="Exact LEI; repeat for a bounded batch")
    parser.add_argument("--latest", action="store_true", help="Required semantic marker; v0.4.3 analyzes latest ESEF only")
    parser.add_argument("--prefer-language", default="en")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--json-max-mib", type=int, default=25)
    parser.add_argument("--output")
    parser.add_argument("--output-dir")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if not args.latest:
        parser.error("v0.4.3 requires --latest; historical activity comparison comes later")
    if args.json_max_mib < 1:
        parser.error("JSON download byte limit must be positive")
    if len(args.lei) == 1 and args.output_dir:
        parser.error("use --output for a single LEI or provide multiple --lei values")
    if len(args.lei) > 1 and args.output:
        parser.error("use --output-dir for multiple LEIs")

    kwargs = dict(
        timeout=args.timeout,
        limit=args.limit,
        prefer_language=args.prefer_language,
        json_limit=args.json_max_mib * 1024 * 1024,
    )

    if len(args.lei) == 1:
        payload = build_tagged_activity_evidence(args.lei[0], **kwargs)
        output_path = write_json_atomic(payload, Path(args.output)) if args.output else None
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            _print_entity_summary(payload)
            if output_path:
                print(f"Tagged activity manifest: {output_path}")
        return 0

    batch = build_batch(args.lei, **kwargs)
    summary_path = write_batch_outputs(batch, Path(args.output_dir)) if args.output_dir else None
    if args.json:
        printable = dict(batch)
        printable["results"] = [{k: v for k, v in row.items() if k != "payload"} for row in batch["results"]]
        print(json.dumps(printable, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_batch_summary(batch)
        if summary_path:
            print(f"Batch summary: {summary_path}")
    return 0 if batch["succeeded"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
