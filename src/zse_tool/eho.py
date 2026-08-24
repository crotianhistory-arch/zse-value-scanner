from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any, Iterable
from urllib.parse import urljoin

from .errors import RemoteDataError
from .http_client import RespectfulHttpClient
from .models import EhoItem

EHO_JSON_URL = "https://eho.zse.hr/feed/json"
EHO_DICTIONARY_URL = "https://eho.zse.hr/feed/json/dictionary"
VALID_VARIANTS = {
    "issuerNews",
    "financialReports",
    "suspensions",
    "tradingNews",
    "observationSegment",
}


def _looks_like_document_url(value: str) -> bool:
    lower = value.lower().split("?", 1)[0]
    return lower.endswith((".xlsx", ".xls", ".pdf", ".zip", ".xhtml", ".html", ".xml"))


def extract_document_urls(obj: Any) -> list[str]:
    """Recursively find direct document URLs in an EHO item.

    The financialReports JSON schema can evolve, so this deliberately preserves
    raw JSON and discovers file URLs instead of depending on one field name.
    """
    found: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)
        elif isinstance(value, str):
            candidate = value.strip()
            if _looks_like_document_url(candidate):
                if candidate.startswith(("http://", "https://")):
                    url = candidate
                elif candidate.startswith("/"):
                    url = urljoin("https://eho.zse.hr/", candidate)
                else:
                    return
                found.append(url.replace("https://eho.zse.hr//", "https://eho.zse.hr/"))

    walk(obj)
    # stable order + deduplicate
    return list(dict.fromkeys(found))



def stable_fallback_source_id(raw: dict[str, Any], variant: str) -> str:
    """Deterministic ID for EHO items that do not expose a native ID.

    Older code used ``unknown-<list index>``.  That index restarts on every
    request, so a later historical sync could overwrite metadata belonging to
    an earlier report.  Hashing the canonical item JSON makes the fallback
    stable across request windows and independent of list position.
    """
    payload = {"variant": variant, "item": raw}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    return f"auto-{digest}"


def _first(raw: dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        if key in raw and raw[key] not in (None, ""):
            return raw[key]
    return None


class EhoClient:
    def __init__(self, http: RespectfulHttpClient):
        self.http = http

    def fetch(
        self,
        *,
        variant: str = "issuerNews",
        ticker: str | None = None,
        date_value: str | date | None = None,
        date_from: str | date | None = None,
        date_to: str | date | None = None,
        news_type_id: int | None = None,
    ) -> dict[str, Any]:
        if variant not in VALID_VARIANTS:
            raise ValueError(f"Unknown EHO variant: {variant}")
        params: dict[str, str | int] = {"variant": variant}
        if ticker:
            params["ticker"] = ticker.upper()
        if date_value:
            params["date"] = str(date_value)
        if date_from:
            params["dateFrom"] = str(date_from)
        if date_to:
            params["dateTo"] = str(date_to)
        if news_type_id is not None:
            params["newsTypeId"] = int(news_type_id)
        return self.http.get_json(EHO_JSON_URL, params=params)

    def fetch_items(self, **kwargs: Any) -> list[EhoItem]:
        payload = self.fetch(**kwargs)
        raw_items = payload.get("items", [])
        if raw_items is None:
            raw_items = []
        if not isinstance(raw_items, list):
            raise RemoteDataError("EHO JSON 'items' is not a list")
        variant = str(payload.get("type") or kwargs.get("variant") or "unknown")
        result: list[EhoItem] = []
        for index, raw in enumerate(raw_items):
            if not isinstance(raw, dict):
                continue
            source_id = str(_first(raw, ("id", "reportId", "publicationId")) or stable_fallback_source_id(raw, variant))
            result.append(
                EhoItem(
                    source_id=source_id,
                    variant=variant,
                    issuer_code=_first(raw, ("issuerCode", "ticker", "symbol")),
                    issuer_name=_first(raw, ("issuerName", "issuer", "companyName")),
                    title=_first(raw, ("title", "name")),
                    publish_date=_first(raw, ("publishDate", "publishedAt", "date")),
                    item_link=_first(raw, ("link", "url", "viewUrl")),
                    raw=raw,
                    document_urls=extract_document_urls(raw),
                )
            )
        return result

    def dictionary(self) -> dict[str, Any]:
        return self.http.get_json(EHO_DICTIONARY_URL)

    def probe(self, ticker: str = "KOEI") -> dict[str, Any]:
        base = self.fetch(variant="issuerNews")
        financial = self.fetch(variant="financialReports", ticker=ticker)
        return {
            "issuerNews": {
                "type": base.get("type"),
                "count": base.get("count"),
                "timestamp": base.get("timestamp"),
            },
            "financialReports": {
                "type": financial.get("type"),
                "count": financial.get("count"),
                "timestamp": financial.get("timestamp"),
                "ticker": ticker.upper(),
            },
        }


def pretty_item(item: EhoItem) -> str:
    docs = ", ".join(item.document_urls) if item.document_urls else "(no direct file URL found)"
    return (
        f"[{item.source_id}] {item.publish_date or '?'} "
        f"{item.issuer_code or '?'} - {item.title or '(no title)'}\n"
        f"  documents: {docs}"
    )


def raw_json(item: EhoItem) -> str:
    return json.dumps(item.raw, ensure_ascii=False, indent=2, sort_keys=True)
