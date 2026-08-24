from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

from .gleif import normalize_lei

FILINGS_API_BASE = "https://filings.xbrl.org/api"
FILINGS_REPOSITORY_ORIGIN = "https://filings.xbrl.org"
MAX_PAGE_SIZE = 50


@dataclass(frozen=True)
class ESEFFiling:
    api_id: str
    filing_index: str | None
    country: str | None
    period_end: str | None
    language: str | None
    date_added: str | None
    processed: str | None
    entity_name: str | None
    entity_identifier: str | None
    error_count: int | None
    warning_count: int | None
    inconsistency_count: int | None
    package_sha256: str | None
    json_url: str | None
    package_url: str | None
    xhtml_url: str | None
    viewer_url: str | None
    api_source_url: str

    @property
    def is_esef(self) -> bool:
        return bool(self.filing_index and "-ESEF-" in self.filing_index.upper())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _canonical_repository_url(value: Any) -> str | None:
    """Resolve a filings.xbrl.org link to an absolute HTTPS repository URL.

    The JSON:API may return repository-relative link fields. Resolve only paths
    belonging to the trusted filings.xbrl.org repository. Do not downgrade the
    downloader's HTTPS-only policy and do not accept arbitrary external hosts.
    """
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None

    resolved = urljoin(FILINGS_REPOSITORY_ORIGIN + "/", raw)
    parsed = urlparse(resolved)
    if parsed.scheme.lower() != "https":
        raise ValueError(f"filings repository URL must use HTTPS: {raw!r}")
    if parsed.hostname is None or parsed.hostname.lower() != "filings.xbrl.org":
        raise ValueError(f"unexpected filings repository host: {raw!r}")
    if parsed.username or parsed.password:
        raise ValueError("filings repository URL must not contain credentials")
    if parsed.port not in (None, 443):
        raise ValueError(f"unexpected filings repository port: {parsed.port}")
    return resolved


def _infer_language(*urls: str | None) -> str | None:
    for url in urls:
        if not url:
            continue
        path = url.split("?", 1)[0]
        match = re.search(r"[-_]([a-z]{2})(?:-[A-Z]{2})?(?:\.json\.gz|\.xhtml(?:\.gz)?|\.zip)$", path)
        if match:
            return match.group(1).lower()
        match = re.search(r"[-_]([a-z]{2})(?=/|$)", path)
        if match:
            return match.group(1).lower()
    return None


def _entity_lookup(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    entities: dict[str, dict[str, Any]] = {}
    for item in payload.get("included") or []:
        if not isinstance(item, dict) or item.get("type") != "entity":
            continue
        entities[str(item.get("id"))] = item.get("attributes") or {}
    return entities


def _filing_from_resource(item: dict[str, Any], entities: dict[str, dict[str, Any]], source_url: str) -> ESEFFiling:
    attrs = item.get("attributes") or {}
    rel = ((item.get("relationships") or {}).get("entity") or {}).get("data") or {}
    entity = entities.get(str(rel.get("id")), {})
    json_url = _canonical_repository_url(attrs.get("json_url"))
    package_url = _canonical_repository_url(attrs.get("package_url"))
    xhtml_url = _canonical_repository_url(attrs.get("report_url"))
    viewer_url = _canonical_repository_url(attrs.get("viewer_url"))
    return ESEFFiling(
        api_id=str(item.get("id") or ""),
        filing_index=attrs.get("fxo_id"),
        country=attrs.get("country"),
        period_end=attrs.get("period_end"),
        language=_infer_language(json_url, xhtml_url, package_url),
        date_added=attrs.get("date_added"),
        processed=attrs.get("processed"),
        entity_name=entity.get("name"),
        entity_identifier=entity.get("identifier"),
        error_count=_as_int(attrs.get("error_count")),
        warning_count=_as_int(attrs.get("warning_count")),
        inconsistency_count=_as_int(attrs.get("inconsistency_count")),
        package_sha256=attrs.get("sha256"),
        json_url=json_url,
        package_url=package_url,
        xhtml_url=xhtml_url,
        viewer_url=viewer_url,
        api_source_url=source_url,
    )


def discover_filings(
    lei: str,
    *,
    limit: int = 25,
    timeout: float = 20.0,
    session: Any | None = None,
) -> list[ESEFFiling]:
    """Discover filings for exactly one LEI using the public filings.xbrl.org API.

    This is intentionally bounded and will never issue an unfiltered Europe-wide query.
    """
    lei_norm = normalize_lei(lei)
    if not 1 <= int(limit) <= MAX_PAGE_SIZE:
        raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
    client = session or requests
    response = client.get(
        f"{FILINGS_API_BASE}/filings",
        params={
            "filter[entity.identifier]": lei_norm,
            "include": "entity",
            "sort": "-processed",
            "page[size]": int(limit),
            "page[number]": 1,
        },
        headers={
            "Accept": "application/vnd.api+json",
            "User-Agent": "zse-value-scanner/0.4.1 (+bounded ESEF repository URL normalization)",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data") or []
    if not isinstance(data, list):
        raise ValueError("unexpected filings.xbrl.org response: data is not a list")
    source_url = str(getattr(response, "url", None) or f"{FILINGS_API_BASE}/filings")
    entities = _entity_lookup(payload)
    filings = [
        _filing_from_resource(item, entities, source_url)
        for item in data
        if isinstance(item, dict)
    ]
    for filing in filings:
        if filing.entity_identifier and normalize_lei(filing.entity_identifier) != lei_norm:
            raise ValueError(
                f"filings API entity mismatch: requested {lei_norm}, received {filing.entity_identifier}"
            )
    return filings


def select_latest_esef(filings: list[ESEFFiling], *, prefer_language: str = "en") -> ESEFFiling:
    candidates = [f for f in filings if f.is_esef]
    if not candidates:
        raise ValueError("no ESEF filing found for requested entity")
    preferred = prefer_language.strip().lower() if prefer_language else ""

    def key(f: ESEFFiling) -> tuple[str, int, str, str]:
        return (
            f.period_end or "",
            1 if preferred and f.language == preferred else 0,
            f.processed or "",
            f.api_id,
        )

    return max(candidates, key=key)


def _print_filings(filings: list[ESEFFiling]) -> None:
    print("PERIOD      COUNTRY LANG  ERR WARN INC  JSON PACKAGE XHTML  ENTITY")
    for f in filings:
        print(
            f"{(f.period_end or '-'):11} {(f.country or '-'):7} {(f.language or '-'):5} "
            f"{str(f.error_count if f.error_count is not None else '-'):>3} "
            f"{str(f.warning_count if f.warning_count is not None else '-'):>4} "
            f"{str(f.inconsistency_count if f.inconsistency_count is not None else '-'):>3}  "
            f"{'yes' if f.json_url else 'no ':4} {'yes' if f.package_url else 'no ':7} "
            f"{'yes' if f.xhtml_url else 'no ':5}  {f.entity_name or '-'}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m zse_tool.esef",
        description="Bounded, read-only ESEF filing discovery by exact LEI.",
    )
    parser.add_argument("--lei", required=True, help="Exact Legal Entity Identifier")
    parser.add_argument("--limit", type=int, default=25, help=f"Maximum API rows, 1..{MAX_PAGE_SIZE}")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--latest", action="store_true", help="Show only the latest ESEF filing")
    parser.add_argument("--prefer-language", default="en")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    filings = discover_filings(args.lei, limit=args.limit, timeout=args.timeout)
    rows = [select_latest_esef(filings, prefer_language=args.prefer_language)] if args.latest else filings
    if args.json:
        print(json.dumps([f.to_dict() for f in rows], ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_filings(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
