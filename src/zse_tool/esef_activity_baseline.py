from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from .esef import ESEFFiling, discover_filings
from .esef_activity import EvidenceTooLarge, _fetch_bounded, inventory_xbrl_activity, parse_xbrl_json
from .esef_tagged_activity import extract_reported_activity_facts, write_json_atomic
from .gleif import normalize_lei

DEFAULT_JSON_LIMIT = 25 * 1024 * 1024
DEFAULT_MAX_CANDIDATES = 6
MAX_MAX_CANDIDATES = 12
MIN_ANNUAL_DAYS = 320
MAX_ANNUAL_DAYS = 410
CORE_DURATION_TERMS = (
    "revenue",
    "sales",
    "profit",
    "loss",
    "income",
    "cashflow",
    "operatingactivities",
)


@dataclass(frozen=True)
class BaselineSelection:
    filing: ESEFFiling
    xbrl_payload: dict[str, Any]
    annuality: dict[str, Any]
    candidate_audit: list[dict[str, Any]]


def _date_from_value(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return None
    text = str(value).strip()
    if len(text) < 10:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _duration_from_period(value: Any) -> tuple[date, date, int] | None:
    if not isinstance(value, str) or "/" not in value:
        return None
    left, right = value.split("/", 1)
    start = _date_from_value(left)
    end_exclusive = _date_from_value(right)
    if not start or not end_exclusive or end_exclusive <= start:
        return None
    return start, end_exclusive, (end_exclusive - start).days


def _concept_fold(value: Any) -> str:
    return str(value or "").casefold().replace("_", "").replace("-", "")


def assess_annuality(payload: dict[str, Any], filing_period_end: str | None) -> dict[str, Any]:
    """Assess whether XBRL evidence represents a full fiscal-year report.

    This is a deterministic filing-selection heuristic, not a statement about a
    regulator's legal document classification. It first uses explicit reporting
    period start/end facts when available, then corroborates with duration facts
    aligned to the filing period end.
    """
    filing_end = _date_from_value(filing_period_end)
    if filing_end is None:
        return {
            "evidence_class": "D1_DETERMINISTIC_EXTRACTION",
            "state": "undetermined",
            "annual_like": False,
            "reason": "filing period_end is missing or invalid",
            "reporting_period_spans_days": [],
            "aligned_annual_duration_fact_count": 0,
            "aligned_core_annual_duration_fact_count": 0,
        }

    facts = payload.get("facts") or {}
    starts: set[date] = set()
    ends: set[date] = set()
    aligned_duration_count = 0
    aligned_core_duration_count = 0
    expected_exclusive_end = filing_end + timedelta(days=1)

    for fact in facts.values():
        if not isinstance(fact, dict):
            continue
        dims = fact.get("dimensions") or {}
        if not isinstance(dims, dict):
            continue
        concept = _concept_fold(dims.get("concept"))
        fact_value = fact.get("value")

        if "reportingperiodstartdate" in concept:
            parsed = _date_from_value(fact_value)
            if parsed:
                starts.add(parsed)
        if "reportingperiodenddate" in concept:
            parsed = _date_from_value(fact_value)
            if parsed:
                ends.add(parsed)

        duration = _duration_from_period(dims.get("period"))
        if duration is None:
            continue
        _start, end_exclusive, days = duration
        if end_exclusive != expected_exclusive_end:
            continue
        if MIN_ANNUAL_DAYS <= days <= MAX_ANNUAL_DAYS:
            aligned_duration_count += 1
            if any(term in concept for term in CORE_DURATION_TERMS):
                aligned_core_duration_count += 1

    explicit_spans: list[int] = []
    for start in starts:
        for end in ends:
            if end != filing_end or end < start:
                continue
            explicit_spans.append((end - start).days + 1)

    explicit_annual = any(MIN_ANNUAL_DAYS <= days <= MAX_ANNUAL_DAYS for days in explicit_spans)
    duration_annual = aligned_duration_count >= 3 and aligned_core_duration_count >= 1
    annual_like = explicit_annual or duration_annual

    if explicit_annual:
        reason = "explicit reporting-period start/end facts span a full fiscal year"
        confidence = "high_explicit_reporting_period"
    elif duration_annual:
        reason = "multiple current full-year duration facts align to filing period end"
        confidence = "high_aligned_duration_facts"
    else:
        reason = "no full-fiscal-year reporting-period evidence aligned to filing period end"
        confidence = "not_annual_like"

    return {
        "evidence_class": "D1_DETERMINISTIC_EXTRACTION",
        "state": "annual_like" if annual_like else "interim_or_nonannual",
        "annual_like": annual_like,
        "confidence": confidence,
        "reason": reason,
        "filing_period_end": filing_end.isoformat(),
        "reporting_period_start_dates": sorted(x.isoformat() for x in starts),
        "reporting_period_end_dates": sorted(x.isoformat() for x in ends),
        "reporting_period_spans_days": sorted(set(explicit_spans)),
        "aligned_annual_duration_fact_count": aligned_duration_count,
        "aligned_core_annual_duration_fact_count": aligned_core_duration_count,
        "annual_day_range": [MIN_ANNUAL_DAYS, MAX_ANNUAL_DAYS],
    }


def _filing_choice_key(filing: ESEFFiling, preferred: str) -> tuple[int, int, int, str, str]:
    error_count = filing.error_count if filing.error_count is not None else 999_999
    warning_count = filing.warning_count if filing.warning_count is not None else 999_999
    return (
        1 if preferred and filing.language == preferred else 0,
        -error_count,
        -warning_count,
        filing.processed or "",
        filing.api_id,
    )


def candidate_period_filings(
    filings: list[ESEFFiling],
    *,
    prefer_language: str = "en",
) -> list[ESEFFiling]:
    """Return one best ESEF JSON filing per period, newest period first."""
    preferred = prefer_language.strip().lower() if prefer_language else ""
    by_period: dict[str, ESEFFiling] = {}
    for filing in filings:
        if not filing.is_esef or not filing.period_end or not filing.json_url:
            continue
        current = by_period.get(filing.period_end)
        if current is None or _filing_choice_key(filing, preferred) > _filing_choice_key(current, preferred):
            by_period[filing.period_end] = filing
    return sorted(by_period.values(), key=lambda f: (f.period_end or "", f.api_id), reverse=True)


def select_annual_activity_baseline(
    filings: list[ESEFFiling],
    *,
    prefer_language: str = "en",
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    timeout: float = 30.0,
    fetcher: Callable[..., bytes] = _fetch_bounded,
    json_limit: int = DEFAULT_JSON_LIMIT,
) -> BaselineSelection:
    """Select the newest deterministically annual-like ESEF filing.

    Recent interims are inspected and explicitly rejected rather than silently
    treated as annual business-profile evidence. The scan is bounded.
    """
    if not 1 <= int(max_candidates) <= MAX_MAX_CANDIDATES:
        raise ValueError(f"max_candidates must be between 1 and {MAX_MAX_CANDIDATES}")

    candidates = candidate_period_filings(filings, prefer_language=prefer_language)
    if not candidates:
        raise ValueError("no ESEF JSON filing candidates found for requested entity")

    audit: list[dict[str, Any]] = []
    for filing in candidates[: int(max_candidates)]:
        row: dict[str, Any] = {
            "api_id": filing.api_id,
            "period_end": filing.period_end,
            "language": filing.language,
            "json_url": filing.json_url,
        }
        try:
            raw = fetcher(filing.json_url, max_bytes=json_limit, timeout=timeout)
        except EvidenceTooLarge as exc:
            row.update({"state": "skipped_oversize", "reason": str(exc)})
            audit.append(row)
            continue

        payload = parse_xbrl_json(raw)
        annuality = assess_annuality(payload, filing.period_end)
        reported = extract_reported_activity_facts(payload)
        row.update({
            "state": "accepted_annual_baseline" if annuality["annual_like"] else "rejected_interim_or_nonannual",
            "annuality": annuality,
            "xbrl_fact_count": len(payload.get("facts") or {}),
            "reported_activity_fact_count": reported.get("selected_fact_count"),
            "category_counts": reported.get("category_counts"),
        })
        audit.append(row)
        if annuality["annual_like"]:
            return BaselineSelection(
                filing=filing,
                xbrl_payload=payload,
                annuality=annuality,
                candidate_audit=audit,
            )

    periods = ", ".join(row.get("period_end") or "?" for row in audit) or "none"
    raise ValueError(
        f"no annual-like ESEF filing found within {min(len(candidates), int(max_candidates))} "
        f"bounded candidate periods ({periods})"
    )


def build_annual_activity_evidence(
    lei: str,
    *,
    timeout: float = 30.0,
    limit: int = 25,
    prefer_language: str = "en",
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    discoverer: Callable[..., list[ESEFFiling]] = discover_filings,
    fetcher: Callable[..., bytes] = _fetch_bounded,
    json_limit: int = DEFAULT_JSON_LIMIT,
) -> dict[str, Any]:
    lei_norm = normalize_lei(lei)
    filings = discoverer(lei_norm, limit=limit, timeout=timeout)
    selected = select_annual_activity_baseline(
        filings,
        prefer_language=prefer_language,
        max_candidates=max_candidates,
        timeout=timeout,
        fetcher=fetcher,
        json_limit=json_limit,
    )
    filing = selected.filing
    payload = selected.xbrl_payload
    inventory = inventory_xbrl_activity(payload)
    reported = extract_reported_activity_facts(payload)

    return {
        "mode": "read-only-annual-activity-baseline",
        "policy": {
            "automatic_database_writes": False,
            "automatic_peer_assignment": False,
            "automatic_similarity_scoring": False,
            "llm_used": False,
            "full_xhtml_fetched": False,
            "interim_filing_used_as_activity_baseline": False,
            "reported_facts_are_not_analytical_mappings": True,
            "annuality_is_deterministic_selection_evidence_not_legal_classification": True,
        },
        "entity": {
            "lei": lei_norm,
            "reported_name": filing.entity_name,
            "country": filing.country,
        },
        "filing": filing.to_dict(),
        "selection": {
            "mode": "latest_deterministically_annual_like_esef",
            "annuality": selected.annuality,
            "candidate_audit": selected.candidate_audit,
            "max_candidates": int(max_candidates),
        },
        "xbrl_activity": inventory,
        "tagged_activity": reported,
        "provenance": {
            "discovery": filing.api_source_url,
            "xbrl_json": filing.json_url,
            "original_report_package": filing.package_url,
            "reported_package_sha256": filing.package_sha256,
        },
    }


def build_batch(
    leis: list[str],
    *,
    builder: Callable[..., dict[str, Any]] = build_annual_activity_evidence,
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
            audit = payload["selection"]["candidate_audit"]
            rows.append({
                "lei": lei,
                "state": "ok",
                "entity_name": payload["entity"].get("reported_name"),
                "country": payload["entity"].get("country"),
                "period_end": payload["filing"].get("period_end"),
                "language": payload["filing"].get("language"),
                "candidate_periods_checked": len(audit),
                "xbrl_fact_count": payload["xbrl_activity"].get("fact_count"),
                "reported_activity_fact_count": tagged.get("selected_fact_count"),
                "category_counts": tagged.get("category_counts"),
                "payload": payload,
            })
        except Exception as exc:  # bounded batch isolation; error remains explicit
            rows.append({
                "lei": lei,
                "state": "error",
                "error_type": type(exc).__name__,
                "error": str(exc),
            })

    return {
        "mode": "read-only-annual-activity-baseline-batch",
        "policy": {
            "automatic_database_writes": False,
            "automatic_peer_assignment": False,
            "automatic_similarity_scoring": False,
            "llm_used": False,
            "interim_filing_used_as_activity_baseline": False,
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


def _print_entity(payload: dict[str, Any]) -> None:
    tagged = payload["tagged_activity"]
    selection = payload["selection"]
    print(f"Entity: {payload['entity'].get('reported_name') or '-'}")
    print(f"LEI: {payload['entity']['lei']}")
    print(f"Country: {payload['entity'].get('country') or '-'}")
    print(f"Annual baseline period: {payload['filing'].get('period_end') or '-'}")
    print(f"Language: {payload['filing'].get('language') or '-'}")
    print(f"Candidate periods checked: {len(selection['candidate_audit'])}")
    print(f"XBRL facts: {payload['xbrl_activity'].get('fact_count')}")
    print(f"Reported activity facts: {tagged.get('selected_fact_count')}")
    print(f"Categories: {tagged.get('category_counts')}")
    print("Selection audit:")
    for row in selection["candidate_audit"]:
        print(f"  {row.get('period_end')}: {row.get('state')}")
    print()
    print("Read-only: no entity, peer, similarity, financial-fact, job or raw-artifact database write was performed.")


def _print_batch(batch: dict[str, Any]) -> None:
    print(f"Requested: {batch['requested']}  Succeeded: {batch['succeeded']}  Failed: {batch['failed']}")
    print("STATE  COUNTRY PERIOD      CHECKED LEI                   ENTITY / ERROR")
    for row in batch["results"]:
        if row["state"] == "ok":
            print(
                f"ok     {(row.get('country') or '-'):7} {(row.get('period_end') or '-'):11} "
                f"{str(row.get('candidate_periods_checked') or '-'):>7} {row['lei']:20} {row.get('entity_name') or '-'}"
            )
        else:
            print(f"error  {'-':7} {'-':11} {'-':>7} {row['lei']:20} {row.get('error_type')}: {row.get('error')}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m zse_tool.esef_activity_baseline",
        description="Select a bounded deterministic annual ESEF baseline and extract tagged business-activity evidence.",
    )
    parser.add_argument("--lei", action="append", required=True, help="Exact LEI; repeat for a bounded batch")
    parser.add_argument("--annual-baseline", action="store_true", help="Required semantic marker")
    parser.add_argument("--prefer-language", default="en")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--max-candidates", type=int, default=DEFAULT_MAX_CANDIDATES)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--json-max-mib", type=int, default=25)
    parser.add_argument("--output")
    parser.add_argument("--output-dir")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if not args.annual_baseline:
        parser.error("v0.4.4 requires --annual-baseline; interim trajectory analysis is a separate later layer")
    if not 1 <= args.max_candidates <= MAX_MAX_CANDIDATES:
        parser.error(f"--max-candidates must be between 1 and {MAX_MAX_CANDIDATES}")
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
        max_candidates=args.max_candidates,
        json_limit=args.json_max_mib * 1024 * 1024,
    )

    if len(args.lei) == 1:
        payload = build_annual_activity_evidence(args.lei[0], **kwargs)
        output_path = write_json_atomic(payload, Path(args.output)) if args.output else None
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            _print_entity(payload)
            if output_path:
                print(f"Annual activity baseline manifest: {output_path}")
        return 0

    batch = build_batch(args.lei, **kwargs)
    summary_path = write_batch_outputs(batch, Path(args.output_dir)) if args.output_dir else None
    if args.json:
        printable = dict(batch)
        printable["results"] = [{k: v for k, v in row.items() if k != "payload"} for row in batch["results"]]
        print(json.dumps(printable, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_batch(batch)
        if summary_path:
            print(f"Batch summary: {summary_path}")
    return 0 if batch["succeeded"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
