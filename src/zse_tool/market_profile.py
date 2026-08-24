from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

SCHEMA_VERSION = "commercial-market-evidence-v0.1"
PROFILE_VERSION = "commercial-search-profile-v0.1"
ALLOWED_KINDS = {
    "business_scope",
    "geography_revenue",
    "order_intake",
    "contract_project",
    "customer_evidence",
}
ALLOWED_EVIDENCE_CLASSES = {
    "R1_REPORTED_NUMERIC",
    "R2_REPORTED_TEXT",
    "D1_DERIVED",
}
DEFAULT_INPUT_LIMIT = 2 * 1024 * 1024
CORE_SCOPE_SHARE_PCT = 50.0


def _nonempty(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"missing required field: {field}")
    return text


def _validate_https_url(value: Any, field: str) -> str:
    text = _nonempty(value, field)
    parsed = urlparse(text)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError(f"{field} must be an absolute HTTPS URL")
    if parsed.username or parsed.password:
        raise ValueError(f"{field} must not contain credentials")
    return text


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"expected numeric value, got {value!r}") from None


def validate_evidence_document(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {SCHEMA_VERSION!r}")
    company = payload.get("company")
    if not isinstance(company, dict):
        raise ValueError("company must be an object")
    _nonempty(company.get("name"), "company.name")
    _nonempty(company.get("ticker"), "company.ticker")

    evidence = payload.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise ValueError("evidence must be a non-empty list")

    ids: set[str] = set()
    for idx, row in enumerate(evidence):
        if not isinstance(row, dict):
            raise ValueError(f"evidence[{idx}] must be an object")
        evidence_id = _nonempty(row.get("evidence_id"), f"evidence[{idx}].evidence_id")
        if evidence_id in ids:
            raise ValueError(f"duplicate evidence_id: {evidence_id}")
        ids.add(evidence_id)
        kind = _nonempty(row.get("kind"), f"evidence[{idx}].kind")
        if kind not in ALLOWED_KINDS:
            raise ValueError(f"unsupported evidence kind: {kind}")
        evidence_class = _nonempty(row.get("evidence_class"), f"evidence[{idx}].evidence_class")
        if evidence_class not in ALLOWED_EVIDENCE_CLASSES:
            raise ValueError(f"unsupported evidence_class: {evidence_class}")
        _nonempty(row.get("period"), f"evidence[{idx}].period")

        source = row.get("source")
        if not isinstance(source, dict):
            raise ValueError(f"evidence[{idx}].source must be an object")
        _validate_https_url(source.get("url"), f"evidence[{idx}].source.url")
        _nonempty(source.get("title"), f"evidence[{idx}].source.title")
        _nonempty(source.get("published"), f"evidence[{idx}].source.published")

        scope = row.get("scope")
        if not isinstance(scope, dict):
            raise ValueError(f"evidence[{idx}].scope must be an object")
        _nonempty(scope.get("level"), f"evidence[{idx}].scope.level")

        if kind in {"geography_revenue", "order_intake", "contract_project", "customer_evidence"}:
            market = row.get("market")
            if not isinstance(market, dict):
                raise ValueError(f"evidence[{idx}].market must be an object")
            iso2 = _nonempty(market.get("iso2"), f"evidence[{idx}].market.iso2").upper()
            if len(iso2) != 2 or not iso2.isalpha():
                raise ValueError(f"invalid ISO2 market code: {iso2}")
            _nonempty(market.get("name"), f"evidence[{idx}].market.name")

        metrics = row.get("metrics") or {}
        if not isinstance(metrics, dict):
            raise ValueError(f"evidence[{idx}].metrics must be an object")
        for key in (
            "revenue_eur_m",
            "share_total_exports_pct",
            "share_total_revenue_pct",
            "order_intake_eur_m",
            "contract_value_eur_m",
        ):
            if key in metrics:
                _as_float(metrics[key])

        terms = row.get("activity_terms") or []
        if not isinstance(terms, list) or not all(isinstance(x, str) and x.strip() for x in terms):
            raise ValueError(f"evidence[{idx}].activity_terms must be a list of non-empty strings")

    return payload


def read_json_bounded(path: Path, *, max_bytes: int = DEFAULT_INPUT_LIMIT) -> dict[str, Any]:
    target = Path(path).expanduser().resolve()
    size = target.stat().st_size
    if size > max_bytes:
        raise ValueError(f"market evidence input exceeds byte limit: {size} > {max_bytes}")
    payload = json.loads(target.read_text())
    if not isinstance(payload, dict):
        raise ValueError("market evidence root must be an object")
    return validate_evidence_document(payload)


def _source_ref(row: dict[str, Any]) -> dict[str, Any]:
    source = row["source"]
    return {
        "evidence_id": row["evidence_id"],
        "evidence_class": row["evidence_class"],
        "kind": row["kind"],
        "period": row["period"],
        "scope": row["scope"],
        "source": {
            "url": source["url"],
            "title": source["title"],
            "published": source["published"],
        },
    }


def build_commercial_search_profile(payload: dict[str, Any]) -> dict[str, Any]:
    validate_evidence_document(payload)
    evidence: list[dict[str, Any]] = payload["evidence"]

    business_anchors: list[dict[str, Any]] = []
    core_scope_terms: list[str] = []
    for row in evidence:
        if row["kind"] != "business_scope":
            continue
        metrics = row.get("metrics") or {}
        share = _as_float(metrics.get("share_total_revenue_pct"))
        terms = sorted({str(x).strip() for x in row.get("activity_terms") or [] if str(x).strip()})
        anchor = {
            "evidence_class": row["evidence_class"],
            "scope": row["scope"],
            "period": row["period"],
            "metrics": metrics,
            "activity_terms": terms,
            "is_core_scope": bool(share is not None and share >= CORE_SCOPE_SHARE_PCT),
            "core_scope_threshold_pct": CORE_SCOPE_SHARE_PCT,
            "source_ref": _source_ref(row),
        }
        business_anchors.append(anchor)
        if anchor["is_core_scope"]:
            core_scope_terms.extend(terms)
    core_scope_terms = sorted(set(core_scope_terms))

    by_market: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in evidence:
        market = row.get("market")
        if isinstance(market, dict):
            by_market[str(market["iso2"]).upper()].append(row)

    market_anchors: list[dict[str, Any]] = []
    confirmed_examples: list[dict[str, Any]] = []
    search_hypotheses: list[dict[str, Any]] = []

    for iso2 in sorted(by_market):
        rows = by_market[iso2]
        name = rows[0]["market"]["name"]
        kinds = sorted({row["kind"] for row in rows})
        periods = sorted({row["period"] for row in rows})
        revenue_periods = sorted({row["period"] for row in rows if row["kind"] == "geography_revenue"})
        direct_rows = [row for row in rows if row["kind"] == "contract_project" and row.get("activity_terms")]
        direct_terms = sorted({term.strip() for row in direct_rows for term in row.get("activity_terms") or [] if term.strip()})

        if direct_rows:
            status = "DIRECT_ACTIVITY_MARKET_EVIDENCE"
        elif len(revenue_periods) >= 2:
            status = "REPEATED_REPORTED_MARKET"
        else:
            status = "REPORTED_MARKET"

        market_anchors.append({
            "market": {"iso2": iso2, "name": name},
            "status": status,
            "evidence_kinds": kinds,
            "periods": periods,
            "reported_revenue_periods": revenue_periods,
            "direct_activity_terms": direct_terms,
            "direct_activity_market_link": bool(direct_rows),
            "source_refs": [_source_ref(row) for row in rows],
        })

        for row in direct_rows:
            confirmed_examples.append({
                "evidence_class": row["evidence_class"],
                "market": row["market"],
                "period": row["period"],
                "scope": row["scope"],
                "activity_terms": sorted(set(row.get("activity_terms") or [])),
                "customer": row.get("customer"),
                "project": row.get("project"),
                "metrics": row.get("metrics") or {},
                "source_ref": _source_ref(row),
            })

        if core_scope_terms:
            search_hypotheses.append({
                "evidence_class": "H1_SEARCH_HYPOTHESIS",
                "market": {"iso2": iso2, "name": name},
                "activity_terms": core_scope_terms,
                "basis": (
                    "reported market evidence combined with a >=50% group revenue business scope; "
                    "this does not prove that the business scope generated the reported market revenue"
                ),
                "direct_activity_market_link_available": bool(direct_rows),
                "supporting_market_evidence_ids": [row["evidence_id"] for row in rows],
                "supporting_business_scope_evidence_ids": [
                    anchor["source_ref"]["evidence_id"] for anchor in business_anchors if anchor["is_core_scope"]
                ],
            })

    return {
        "profile_version": PROFILE_VERSION,
        "source_schema_version": payload["schema_version"],
        "mode": "read-only-commercial-search-profile",
        "policy": {
            "automatic_database_writes": False,
            "automatic_competitor_assignment": False,
            "automatic_peer_assignment": False,
            "automatic_similarity_scoring": False,
            "llm_used": False,
            "group_geography_is_not_segment_geography": True,
            "search_hypotheses_are_not_reported_facts": True,
        },
        "company": payload["company"],
        "business_anchors": business_anchors,
        "market_anchors": market_anchors,
        "confirmed_activity_market_examples": confirmed_examples,
        "search_hypotheses": search_hypotheses,
        "evidence_count": len(evidence),
    }


def write_json_atomic(payload: dict[str, Any], path: Path) -> Path:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, target)
    return target


def _print_profile(profile: dict[str, Any]) -> None:
    company = profile["company"]
    print(f"Company: {company.get('name')} ({company.get('ticker')})")
    print(f"Evidence rows: {profile['evidence_count']}")
    print("Business anchors:")
    for row in profile["business_anchors"]:
        scope = row["scope"].get("name") or row["scope"].get("level")
        print(f"  {scope}: core={row['is_core_scope']} terms={', '.join(row['activity_terms']) or '-'}")
    print("Market anchors:")
    for row in profile["market_anchors"]:
        market = row["market"]
        print(
            f"  {market['iso2']} {market['name']}: {row['status']} "
            f"direct_terms={', '.join(row['direct_activity_terms']) or '-'}"
        )
    print()
    print("Read-only profile: no competitor, peer or database decision was persisted.")
    print("Group-level geography combined with a dominant business scope is only a search hypothesis unless directly linked by project evidence.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m zse_tool.market_profile",
        description="Build a provenance-preserving commercial-market search profile from bounded reported evidence.",
    )
    parser.add_argument("--input", required=True, help="Commercial market evidence JSON")
    parser.add_argument("--output", help="Optional output profile JSON")
    parser.add_argument("--input-max-mib", type=int, default=2)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.input_max_mib < 1:
        parser.error("--input-max-mib must be positive")
    payload = read_json_bounded(Path(args.input), max_bytes=args.input_max_mib * 1024 * 1024)
    profile = build_commercial_search_profile(payload)
    output = write_json_atomic(profile, Path(args.output)) if args.output else None
    if args.json:
        print(json.dumps(profile, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_profile(profile)
        if output:
            print(f"Commercial search profile: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
