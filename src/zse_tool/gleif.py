from __future__ import annotations

import argparse
import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from typing import Any, Iterable

import requests

GLEIF_API_BASE = "https://api.gleif.org/api/v1"
DEFAULT_TIMEOUT = 20.0
MAX_PAGE_SIZE = 25


def _text(value: Any) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", value).casefold()
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _name_similarity(left: str, right: str) -> float:
    a = _normalize_name(left)
    b = _normalize_name(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def normalize_lei(value: str) -> str:
    lei = value.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{20}", lei):
        raise ValueError("LEI must be exactly 20 ASCII letters/digits")
    return lei


@dataclass(frozen=True)
class GLEIFCandidate:
    lei: str
    legal_name: str
    legal_address_country: str | None
    jurisdiction: str | None
    registration_status: str | None
    entity_status: str | None
    registration_authority_id: str | None
    registration_authority_entity_id: str | None
    query_name: str
    query_country: str | None
    name_similarity: float
    country_match: bool | None
    match_class: str
    source_url: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["name_similarity"] = round(self.name_similarity, 6)
        return data


def _candidate_from_record(record: dict[str, Any], *, query_name: str, query_country: str | None) -> GLEIFCandidate:
    attrs = record.get("attributes") or {}
    entity = attrs.get("entity") or {}
    registration = attrs.get("registration") or {}
    legal_name_obj = entity.get("legalName") or {}
    legal_address = entity.get("legalAddress") or {}
    reg_auth = entity.get("registeredAt") or {}

    lei = _text(attrs.get("lei")) or _text(record.get("id")) or ""
    legal_name = _text(legal_name_obj.get("name")) or ""
    country = _text(legal_address.get("country"))
    jurisdiction = _text(entity.get("jurisdiction"))
    query_country_norm = query_country.upper() if query_country else None
    record_country_norm = country.upper() if country else None
    country_match = None if query_country_norm is None else record_country_norm == query_country_norm
    similarity = _name_similarity(query_name, legal_name)

    exact_name = _normalize_name(query_name) == _normalize_name(legal_name)
    if exact_name and country_match is not False:
        match_class = "EXACT"
    elif similarity >= 0.92 and country_match is not False:
        match_class = "STRONG_REVIEW"
    else:
        match_class = "REVIEW"

    return GLEIFCandidate(
        lei=lei,
        legal_name=legal_name,
        legal_address_country=country,
        jurisdiction=jurisdiction,
        registration_status=_text(registration.get("status")),
        entity_status=_text(entity.get("status")),
        registration_authority_id=_text(reg_auth.get("id")),
        registration_authority_entity_id=_text(entity.get("registeredAs")),
        query_name=query_name,
        query_country=query_country_norm,
        name_similarity=similarity,
        country_match=country_match,
        match_class=match_class,
        source_url=f"{GLEIF_API_BASE}/lei-records/{lei}" if lei else f"{GLEIF_API_BASE}/lei-records",
    )


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def search_legal_name(
    legal_name: str,
    *,
    country: str | None = None,
    limit: int = 10,
    timeout: float = DEFAULT_TIMEOUT,
    session: Any | None = None,
) -> list[GLEIFCandidate]:
    """Return a bounded, read-only candidate set from the official GLEIF API."""
    name = legal_name.strip()
    if not name:
        raise ValueError("legal_name must not be empty")
    if not 1 <= int(limit) <= MAX_PAGE_SIZE:
        raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
    if country is not None:
        country = country.strip().upper()
        if not re.fullmatch(r"[A-Z]{2}", country):
            raise ValueError("country must be a two-letter ISO country code, e.g. HR")

    client = session or requests
    params: dict[str, str | int] = {
        "filter[entity.legalName]": name,
        "page[number]": 1,
        "page[size]": int(limit),
    }
    headers = {
        "Accept": "application/vnd.api+json",
        "User-Agent": "zse-value-scanner/0.3.5 (+bounded GLEIF entity work)",
    }
    response = client.get(
        f"{GLEIF_API_BASE}/lei-records",
        params=params,
        headers=headers,
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    records = payload.get("data") or []
    if not isinstance(records, list):
        raise ValueError("unexpected GLEIF response: data is not a list")

    candidates = [
        _candidate_from_record(record, query_name=name, query_country=country)
        for record in records[: int(limit)]
        if isinstance(record, dict)
    ]
    candidates.sort(
        key=lambda c: (
            c.match_class == "EXACT",
            c.country_match is True,
            c.name_similarity,
            c.registration_status == "ISSUED",
        ),
        reverse=True,
    )
    return candidates


def fetch_lei_record(
    lei: str,
    *,
    query_name: str,
    country: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    session: Any | None = None,
) -> tuple[GLEIFCandidate, dict[str, Any], bytes]:
    """Fetch one exact LEI record and return candidate metadata plus canonical JSON bytes."""
    normalized_lei = normalize_lei(lei)
    name = query_name.strip()
    if not name:
        raise ValueError("query_name must not be empty")
    if country is not None:
        country = country.strip().upper()
        if not re.fullmatch(r"[A-Z]{2}", country):
            raise ValueError("country must be a two-letter ISO country code, e.g. HR")

    client = session or requests
    source_url = f"{GLEIF_API_BASE}/lei-records/{normalized_lei}"
    response = client.get(
        source_url,
        headers={
            "Accept": "application/vnd.api+json",
            "User-Agent": "zse-value-scanner/0.3.5 (+confirmed GLEIF identity)",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    record = payload.get("data")
    if not isinstance(record, dict):
        raise ValueError("unexpected GLEIF response: data is not an object")

    candidate = _candidate_from_record(record, query_name=name, query_country=country)
    if normalize_lei(candidate.lei) != normalized_lei:
        raise ValueError(
            f"GLEIF response LEI mismatch: requested {normalized_lei}, received {candidate.lei!r}"
        )
    return candidate, payload, _canonical_json_bytes(payload)


def _print_table(candidates: Iterable[GLEIFCandidate]) -> None:
    rows = list(candidates)
    if not rows:
        print("No GLEIF candidates returned.")
        return
    print("MATCH           SIMILAR  COUNTRY  REG       LEI                   LEGAL NAME")
    for c in rows:
        country = c.legal_address_country or "-"
        reg = c.registration_status or "-"
        print(
            f"{c.match_class:<15} {c.name_similarity:>7.1%}  {country:<7}  {reg:<8}  "
            f"{c.lei:<20}  {c.legal_name}"
        )
    print()
    print("Read-only preflight: no entity was registered or merged.")
    print("EXACT means normalized legal-name equality and no supplied-country conflict; it is still evidence to review.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m zse_tool.gleif",
        description="Bounded, read-only GLEIF legal-entity discovery preflight.",
    )
    parser.add_argument("--name", required=True, help="Legal entity name to search")
    parser.add_argument("--country", help="Optional two-letter ISO country code, e.g. HR")
    parser.add_argument("--limit", type=int, default=10, help=f"Candidate limit (1-{MAX_PAGE_SIZE}; default 10)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="HTTP timeout seconds")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a table")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    candidates = search_legal_name(
        args.name,
        country=args.country,
        limit=args.limit,
        timeout=args.timeout,
    )
    if args.json:
        print(json.dumps([candidate.to_dict() for candidate in candidates], ensure_ascii=False, indent=2))
    else:
        _print_table(candidates)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
