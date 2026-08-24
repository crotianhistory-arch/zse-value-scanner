from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable

import requests

from .esef import ESEFFiling, discover_filings, select_latest_esef
from .gleif import normalize_lei

CORE_DIMENSIONS = {"concept", "entity", "period", "unit", "language", "noteId"}
ACTIVITY_TERMS = (
    "segment", "business", "product", "service", "geograph", "region", "market",
    "division", "operating", "customer", "revenue", "sales", "subsidiar", "activity",
)
NUMERIC_ACTIVITY_TERMS = (
    "revenue", "sales", "profit", "loss", "income", "result", "ebit", "assets", "customers",
)
NARRATIVE_TERMS = (
    "operating segments", "segment information", "business model", "products and services",
    "product and service", "business activities", "principal activities", "geographical information",
    "geographic information", "subsidiaries", "subsidiary", "group companies", "markets",
    "major customers", "order backlog", "business divisions", "business units",
)
DEFAULT_JSON_LIMIT = 25 * 1024 * 1024
DEFAULT_XHTML_LIMIT = 80 * 1024 * 1024
DEFAULT_JSON_DECOMPRESSED_LIMIT = 100 * 1024 * 1024
DEFAULT_XHTML_DECOMPRESSED_LIMIT = 200 * 1024 * 1024


@dataclass(frozen=True)
class NarrativeEvidence:
    evidence_class: str
    matched_term: str
    text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"} and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        value = " ".join(data.split())
        if value:
            self.parts.append(value)


def _contains_activity_term(value: str | None) -> bool:
    folded = (value or "").casefold()
    return any(term in folded for term in ACTIVITY_TERMS)


class EvidenceTooLarge(ValueError):
    """Evidence object exceeded an explicit bounded-download limit."""


def _fetch_bounded(
    url: str,
    *,
    max_bytes: int,
    timeout: float = 30.0,
    session: Any | None = None,
) -> bytes:
    if not url.lower().startswith("https://"):
        raise ValueError("only HTTPS evidence URLs are allowed")
    client = session or requests
    response = client.get(
        url,
        headers={"User-Agent": "zse-value-scanner/0.4.0 (+bounded ESEF activity evidence discovery)"},
        timeout=timeout,
        stream=True,
    )
    response.raise_for_status()
    length = response.headers.get("Content-Length") if getattr(response, "headers", None) else None
    if length:
        try:
            if int(length) > max_bytes:
                raise EvidenceTooLarge(f"evidence object exceeds byte limit: {length} > {max_bytes}")
        except ValueError as exc:
            if "exceeds byte limit" in str(exc):
                raise
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=256 * 1024):
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            raise EvidenceTooLarge(f"evidence object exceeds byte limit while downloading: > {max_bytes}")
        chunks.append(chunk)
    return b"".join(chunks)


def _maybe_gunzip(data: bytes, *, max_output_bytes: int) -> bytes:
    if data[:2] != b"\x1f\x8b":
        if len(data) > max_output_bytes:
            raise ValueError(f"decoded evidence exceeds byte limit: {len(data)} > {max_output_bytes}")
        return data
    with gzip.GzipFile(fileobj=io.BytesIO(data), mode="rb") as handle:
        decoded = handle.read(max_output_bytes + 1)
    if len(decoded) > max_output_bytes:
        raise ValueError(f"decompressed evidence exceeds byte limit: > {max_output_bytes}")
    return decoded


def parse_xbrl_json(
    data: bytes,
    *,
    max_output_bytes: int = DEFAULT_JSON_DECOMPRESSED_LIMIT,
) -> dict[str, Any]:
    payload = json.loads(_maybe_gunzip(data, max_output_bytes=max_output_bytes).decode("utf-8-sig"))
    if not isinstance(payload, dict) or not isinstance(payload.get("facts"), dict):
        raise ValueError("xBRL-JSON payload does not contain a facts object")
    return payload


def inventory_xbrl_activity(
    payload: dict[str, Any],
    *,
    max_dimensions: int = 100,
    max_members_per_dimension: int = 100,
    max_numeric_facts: int = 200,
    max_text_facts: int = 100,
) -> dict[str, Any]:
    facts = payload.get("facts") or {}
    dimension_counts: Counter[str] = Counter()
    member_counts: dict[str, Counter[str]] = defaultdict(Counter)
    concept_counts: Counter[str] = Counter()
    numeric_activity_facts: list[dict[str, Any]] = []
    text_activity_facts: list[dict[str, Any]] = []

    for fact_id, fact in facts.items():
        if not isinstance(fact, dict):
            continue
        dimensions = fact.get("dimensions") or {}
        if not isinstance(dimensions, dict):
            continue
        concept = str(dimensions.get("concept") or "")
        if concept:
            concept_counts[concept] += 1
        custom_dims = {str(k): v for k, v in dimensions.items() if str(k) not in CORE_DIMENSIONS}
        for dim, member in custom_dims.items():
            dimension_counts[dim] += 1
            member_counts[dim][str(member)] += 1

        activity_dimensions = {
            dim: str(member)
            for dim, member in custom_dims.items()
            if _contains_activity_term(dim) or _contains_activity_term(str(member))
        }
        concept_folded = concept.casefold()
        value = fact.get("value")
        unit = dimensions.get("unit")
        is_numeric_candidate = unit is not None and any(term in concept_folded for term in NUMERIC_ACTIVITY_TERMS)
        if activity_dimensions and is_numeric_candidate and len(numeric_activity_facts) < max_numeric_facts:
            numeric_activity_facts.append({
                "evidence_class": "R1_REPORTED_XBRL_FACT",
                "fact_id": str(fact_id),
                "concept": concept,
                "value": value,
                "unit": unit,
                "period": dimensions.get("period"),
                "activity_dimensions": activity_dimensions,
            })

        language = dimensions.get("language")
        if (
            language
            and isinstance(value, str)
            and value.strip()
            and (_contains_activity_term(concept) or _contains_activity_term(value[:600]))
            and len(text_activity_facts) < max_text_facts
        ):
            text_activity_facts.append({
                "evidence_class": "R1_REPORTED_XBRL_FACT",
                "fact_id": str(fact_id),
                "concept": concept,
                "language": language,
                "period": dimensions.get("period"),
                "text": " ".join(value.split())[:1500],
            })

    dimensions: list[dict[str, Any]] = []
    for dim, count in dimension_counts.most_common(max_dimensions):
        members = [
            {"member": member, "fact_count": member_count}
            for member, member_count in member_counts[dim].most_common(max_members_per_dimension)
        ]
        dimensions.append({
            "evidence_class": "D1_DETERMINISTIC_EXTRACTION",
            "dimension": dim,
            "fact_count": count,
            "activity_relevance": _contains_activity_term(dim) or any(_contains_activity_term(m["member"]) for m in members),
            "members": members,
        })

    relevant_concepts = [
        {"concept": concept, "fact_count": count}
        for concept, count in concept_counts.most_common()
        if _contains_activity_term(concept)
    ][:200]

    return {
        "fact_count": len(facts),
        "custom_dimensions": dimensions,
        "activity_concepts": relevant_concepts,
        "numeric_activity_facts": numeric_activity_facts,
        "text_activity_facts": text_activity_facts,
    }


def extract_narrative_evidence(
    html: bytes,
    *,
    max_windows: int = 60,
    radius: int = 500,
    max_output_bytes: int = DEFAULT_XHTML_DECOMPRESSED_LIMIT,
) -> list[NarrativeEvidence]:
    text = _maybe_gunzip(html, max_output_bytes=max_output_bytes).decode("utf-8", errors="replace")
    parser = _VisibleTextParser()
    parser.feed(text)
    visible = "\n".join(parser.parts)
    folded = visible.casefold()
    rows: list[NarrativeEvidence] = []
    seen: set[tuple[str, str]] = set()
    for term in NARRATIVE_TERMS:
        start = 0
        term_folded = term.casefold()
        while len(rows) < max_windows:
            idx = folded.find(term_folded, start)
            if idx < 0:
                break
            lo = max(0, idx - radius)
            hi = min(len(visible), idx + len(term) + radius)
            window = " ".join(visible[lo:hi].split())
            key = (term_folded, window[:180].casefold())
            if key not in seen:
                seen.add(key)
                rows.append(NarrativeEvidence(
                    evidence_class="H1_HEURISTIC_ACTIVITY_CANDIDATE",
                    matched_term=term,
                    text=window,
                ))
            start = idx + len(term_folded)
        if len(rows) >= max_windows:
            break
    return rows


def build_activity_evidence(
    lei: str,
    *,
    timeout: float = 30.0,
    limit: int = 25,
    prefer_language: str = "en",
    discoverer: Callable[..., list[ESEFFiling]] = discover_filings,
    fetcher: Callable[..., bytes] = _fetch_bounded,
    json_limit: int = DEFAULT_JSON_LIMIT,
    xhtml_limit: int = DEFAULT_XHTML_LIMIT,
) -> dict[str, Any]:
    lei_norm = normalize_lei(lei)
    filings = discoverer(lei_norm, limit=limit, timeout=timeout)
    filing = select_latest_esef(filings, prefer_language=prefer_language)
    if not filing.json_url:
        raise ValueError("selected ESEF filing does not provide xBRL-JSON")

    # Structured xBRL evidence is the required foundation. Narrative XHTML is a
    # bounded enrichment layer: very large annual reports must not discard valid
    # structured evidence or force an unbounded download.
    json_bytes = fetcher(filing.json_url, max_bytes=json_limit, timeout=timeout)
    xbrl_payload = parse_xbrl_json(json_bytes)
    inventory = inventory_xbrl_activity(xbrl_payload)

    narrative: list[NarrativeEvidence] = []
    narrative_status: dict[str, Any]
    if not filing.xhtml_url:
        narrative_status = {
            "state": "unavailable",
            "reason": "selected ESEF filing does not provide XHTML",
            "download_limit_bytes": xhtml_limit,
        }
    else:
        try:
            xhtml_bytes = fetcher(filing.xhtml_url, max_bytes=xhtml_limit, timeout=timeout)
            narrative = extract_narrative_evidence(xhtml_bytes)
            narrative_status = {
                "state": "available",
                "reason": None,
                "download_limit_bytes": xhtml_limit,
            }
        except EvidenceTooLarge as exc:
            narrative_status = {
                "state": "skipped_oversize",
                "reason": str(exc),
                "download_limit_bytes": xhtml_limit,
            }

    return {
        "mode": "read-only-external-activity-evidence",
        "policy": {
            "automatic_peer_assignment": False,
            "automatic_database_writes": False,
            "llm_used": False,
            "reported_facts_are_not_analytical_mappings": True,
            "narrative_enrichment_is_bounded_and_optional": True,
            "evidence_classes": {
                "R1_REPORTED_XBRL_FACT": "fact reported in the selected XBRL filing",
                "R2_REPORTED_TEXT": "verbatim reported narrative text (reserved for curated follow-up)",
                "D1_DETERMINISTIC_EXTRACTION": "deterministic inventory derived from reported XBRL dimensions/concepts",
                "H1_HEURISTIC_ACTIVITY_CANDIDATE": "bounded keyword evidence window; not a peer conclusion",
            },
        },
        "entity": {
            "lei": lei_norm,
            "reported_name": filing.entity_name,
            "country": filing.country,
        },
        "filing": filing.to_dict(),
        "xbrl_activity": inventory,
        "narrative_evidence": [row.to_dict() for row in narrative],
        "narrative_status": narrative_status,
        "provenance": {
            "discovery": filing.api_source_url,
            "xbrl_json": filing.json_url,
            "xhtml": filing.xhtml_url,
            "original_report_package": filing.package_url,
            "reported_package_sha256": filing.package_sha256,
        },
    }


def write_manifest(payload: dict[str, Any], path: Path) -> Path:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, target)
    return target


def _print_summary(payload: dict[str, Any]) -> None:
    entity = payload["entity"]
    filing = payload["filing"]
    activity = payload["xbrl_activity"]
    dimensions = activity["custom_dimensions"]
    relevant = [d for d in dimensions if d["activity_relevance"]]
    print(f"Entity: {entity.get('reported_name') or '-'}")
    print(f"LEI: {entity['lei']}")
    print(f"Country: {entity.get('country') or '-'}")
    print(f"Filing period: {filing.get('period_end') or '-'}")
    print(f"Language: {filing.get('language') or '-'}")
    print(f"XBRL facts: {activity['fact_count']}")
    print(f"Custom dimensions: {len(dimensions)} ({len(relevant)} activity-relevant heuristic)")
    print(f"Numeric activity facts: {len(activity['numeric_activity_facts'])}")
    print(f"Tagged text activity facts: {len(activity['text_activity_facts'])}")
    print(f"Narrative evidence windows: {len(payload['narrative_evidence'])}")
    print(f"Narrative status: {payload.get('narrative_status', {}).get('state', 'unknown')}")
    print()
    print("Read-only: no entity, peer, financial-fact, ingestion-job or raw-artifact database write was performed.")
    print("No peer conclusion was made; heuristic text windows are leads, not reported segment facts.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m zse_tool.esef_activity",
        description="Build a bounded, read-only activity evidence pack from the latest ESEF filing for an exact LEI.",
    )
    parser.add_argument("--lei", required=True)
    parser.add_argument("--latest", action="store_true", help="Required semantic marker; v0.4.2 analyzes latest ESEF only")
    parser.add_argument("--prefer-language", default="en")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--json-max-mib", type=int, default=25)
    parser.add_argument("--xhtml-max-mib", type=int, default=80)
    parser.add_argument("--output")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not args.latest:
        parser.error("v0.4.2 requires --latest; historical activity packs come later")
    if args.json_max_mib < 1 or args.xhtml_max_mib < 1:
        parser.error("download byte limits must be positive")

    payload = build_activity_evidence(
        args.lei,
        timeout=args.timeout,
        limit=args.limit,
        prefer_language=args.prefer_language,
        json_limit=args.json_max_mib * 1024 * 1024,
        xhtml_limit=args.xhtml_max_mib * 1024 * 1024,
    )
    output_path = write_manifest(payload, Path(args.output)) if args.output else None
    if args.json:
        if output_path:
            payload = dict(payload)
            payload["manifest_path"] = str(output_path)
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_summary(payload)
        if output_path:
            print(f"Activity evidence manifest: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
