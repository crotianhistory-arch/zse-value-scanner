from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
import json
import re
from typing import Any
from urllib.parse import quote

from .errors import RemoteDataError
from .http_client import RespectfulHttpClient

ZSE_SHARES_URL = "https://zse.hr/en/securities/26"
ZSE_INSTRUMENT_URL = "https://zse.hr/en/instrument/310"
ZSE_DELAY_NOTE = "Official ZSE trading/market data are displayed with a 15-minute delay."


@dataclass(slots=True)
class SecurityEntry:
    ticker: str
    isin: str
    name: str | None
    sector: str | None
    listed_quantity: int | None
    nominal_value: str | None
    listing_date: str | None
    delisting_date: str | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self._row is not None and self._cell is not None:
            text = " ".join(" ".join(self._cell).split())
            self._row.append(unescape(text))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None
            self._cell = None


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if text:
            self.parts.append(text)

    def text(self) -> str:
        return " ".join(self.parts)


def _parse_int(text: str | None) -> int | None:
    if not text:
        return None
    cleaned = re.sub(r"[^0-9]", "", text)
    return int(cleaned) if cleaned else None


def _parse_en_number(text: str | None) -> float | None:
    if not text:
        return None
    cleaned = text.replace("\xa0", " ").replace(" ", "").replace(",", "")
    m = re.search(r"[-+]?\d+(?:\.\d+)?", cleaned)
    return float(m.group(0)) if m else None


def parse_share_directory(html: str, ticker: str, isin: str | None = None) -> SecurityEntry:
    """Resolve one listed share from the official ZSE securities directory."""
    parser = _TableParser()
    parser.feed(html)
    wanted = ticker.upper().strip()
    wanted_isin = isin.upper().strip() if isin else None
    candidates: list[SecurityEntry] = []

    for row in parser.rows:
        if len(row) < 5 or row[0].upper().strip() != wanted:
            continue
        row_isin = row[1].upper().strip()
        if wanted_isin and row_isin != wanted_isin:
            continue
        if not re.fullmatch(r"[A-Z0-9]{12}", row_isin):
            continue
        candidates.append(
            SecurityEntry(
                ticker=wanted,
                isin=row_isin,
                name=row[2].strip() or None,
                sector=row[3].strip() or None,
                listed_quantity=_parse_int(row[4]),
                nominal_value=row[5].strip() if len(row) > 5 and row[5].strip() not in {"", "-"} else None,
                listing_date=row[6].strip() if len(row) > 6 and row[6].strip() not in {"", "-"} else None,
                delisting_date=row[7].strip() if len(row) > 7 and row[7].strip() not in {"", "-"} else None,
            )
        )

    if not candidates:
        suffix = f" / ISIN {wanted_isin}" if wanted_isin else ""
        raise RemoteDataError(f"Ticker {wanted}{suffix} not found in official ZSE listed-securities directory")

    active = [c for c in candidates if not c.delisting_date]
    if len(active) == 1:
        return active[0]
    if wanted_isin:
        matches = [c for c in candidates if c.isin == wanted_isin]
        if len(matches) == 1:
            return matches[0]
    if len(candidates) == 1:
        return candidates[0]
    raise RemoteDataError(
        f"Ticker {wanted} maps to multiple active ZSE instruments; rerun market-sync with --isin"
    )


def parse_instrument_market(html: str, *, ticker: str, isin: str, directory_quantity: int | None = None) -> dict[str, Any]:
    """Extract current listed quantity and official published market cap.

    The ZSE instrument page exposes market capitalization server-side even when
    the direct last-price widget is rendered client-side.  We therefore treat
    the published market cap as authoritative and derive a display-only implied
    price as market_cap / listed_quantity.
    """
    parser = _TextParser()
    parser.feed(html)
    text = parser.text()

    if isin.upper() not in text.upper():
        raise RemoteDataError(f"ZSE instrument page did not contain expected ISIN {isin}")

    q_match = re.search(r"Listed\s+Quantity\s+([0-9][0-9,\.\s]*)", text, flags=re.I)
    listed_quantity = _parse_int(q_match.group(1)) if q_match else directory_quantity

    cap_match = re.search(
        r"Market\s+Cap\s+([0-9][0-9,\.\s]*)\s*(?:mil\.?|million)\s*EUR",
        text,
        flags=re.I,
    )
    market_cap_eur = None
    if cap_match:
        cap_million = _parse_en_number(cap_match.group(1))
        if cap_million is not None:
            market_cap_eur = cap_million * 1_000_000.0

    implied_price_eur = None
    if market_cap_eur is not None and listed_quantity:
        implied_price_eur = market_cap_eur / listed_quantity

    return {
        "ticker": ticker.upper(),
        "isin": isin.upper(),
        "listed_quantity": listed_quantity,
        "market_cap_eur": market_cap_eur,
        "implied_price_eur": implied_price_eur,
        "price_basis": "ZSE published market cap / listed quantity" if implied_price_eur is not None else None,
        "quality": "official-zse-market-cap" if market_cap_eur is not None else "official-zse-no-market-cap",
        "note": ZSE_DELAY_NOTE,
    }


class ZseMarketClient:
    def __init__(self, http: RespectfulHttpClient):
        self.http = http

    def resolve_security(self, ticker: str, isin: str | None = None) -> SecurityEntry:
        response = self.http.get(
            ZSE_SHARES_URL,
            params={"status": "LISTED_SECURITIES", "model": "ALL", "type": "SHARE"},
        )
        return parse_share_directory(response.text, ticker, isin=isin)

    def fetch_snapshot(self, ticker: str, isin: str | None = None) -> tuple[SecurityEntry, dict[str, Any]]:
        security = self.resolve_security(ticker, isin=isin)
        source_url = f"{ZSE_INSTRUMENT_URL}?isin={quote(security.isin)}"
        response = self.http.get(ZSE_INSTRUMENT_URL, params={"isin": security.isin})
        snapshot = parse_instrument_market(
            response.text,
            ticker=security.ticker,
            isin=security.isin,
            directory_quantity=security.listed_quantity,
        )
        snapshot.update(
            {
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "source_url": source_url,
                "source_kind": "official_zse_instrument_page",
                "raw_json": json.dumps(
                    {
                        "security": security.as_dict(),
                        "market": {k: v for k, v in snapshot.items() if k != "raw_json"},
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            }
        )
        return security, snapshot
