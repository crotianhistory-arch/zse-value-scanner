from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlencode

import requests

from .gleif import GLEIFCandidate, fetch_lei_record, normalize_lei
from .storage import Database, utc_now

ZSE_ISSUER_BASE = "https://zse.hr/en/papir/310"
TICKER_SCHEME = "TICKER:ZSE"
ISIN_SCHEME = "ISIN"
LEI_SCHEME = "LEI"


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"} and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        value = " ".join(data.split())
        if value:
            self.parts.append(value)


@dataclass(frozen=True)
class ZSEIssuerIdentity:
    isin: str
    issuer_name: str
    lei: str
    country_code: str | None
    tax_number: str | None
    source_url: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OfficialIdentityRow:
    ticker: str
    state: str
    evidence_level: str
    disposition: str
    entity_id: str | None
    local_legal_name: str | None
    local_country: str | None
    isin: str | None
    existing_lei: str | None
    lei: str | None
    zse_issuer_name: str | None
    zse_tax_number: str | None
    zse_source_url: str | None
    gleif_legal_name: str | None
    gleif_country: str | None
    registration_status: str | None
    entity_status: str | None
    gleif_source_url: str | None
    note: str | None
    confirmation_command: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalize_ticker(value: str) -> str:
    ticker = value.strip().upper()
    if not ticker or not re.fullmatch(r"[A-Z0-9._-]+", ticker):
        raise ValueError("ticker must contain only letters, digits, dot, underscore or hyphen")
    return ticker


def _normalize_isin(value: str) -> str:
    isin = value.strip().upper()
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]", isin):
        raise ValueError(f"invalid ISIN: {value!r}")
    return isin


def _entity_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    return dict(row)


def _identifiers(db: Database, entity_id: str, scheme: str) -> list[str]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT value FROM entity_identifiers WHERE entity_id=? AND scheme=? ORDER BY value",
            (entity_id, scheme.upper()),
        ).fetchall()
    return [str(row["value"]).strip().upper() for row in rows]


def unidentified_zse_tickers(db: Database) -> list[str]:
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT i.value AS ticker
            FROM entity_identifiers i
            WHERE i.scheme='TICKER:ZSE'
              AND NOT EXISTS (
                  SELECT 1 FROM entity_identifiers lei
                  WHERE lei.entity_id=i.entity_id AND lei.scheme='LEI'
              )
            ORDER BY UPPER(i.value)
            """
        ).fetchall()
    return [str(row["ticker"]).strip().upper() for row in rows]


def _visible_tokens(html: str) -> list[str]:
    parser = _VisibleTextParser()
    parser.feed(html)
    return parser.parts


def _next_token(tokens: list[str], labels: set[str]) -> str | None:
    lower_labels = {label.casefold() for label in labels}
    for idx, token in enumerate(tokens):
        if token.casefold() in lower_labels:
            for candidate in tokens[idx + 1 : idx + 5]:
                if candidate and candidate.casefold() not in lower_labels:
                    return candidate
    return None


_ZSE_NAVIGATION_TOKENS = {
    "announcements",
    "objave",
    "trading",
    "trgovanje",
    "instrument",
    "issuer",
    "izdavatelj",
    "historical data",
    "povijesni podaci",
}


def _issuer_name_from_tokens(tokens: list[str]) -> str | None:
    """Return the issuer legal name from the ZSE issuer-detail block.

    Current ZSE pages place the issuer legal name immediately before the
    Home Member State field. This avoids confusing the navigation-tab label
    "Issuer" with the issuer-detail content.
    """
    member_labels = {"home member state", "matična država članica"}
    for idx, token in enumerate(tokens):
        if token.casefold() in member_labels:
            for candidate in reversed(tokens[max(0, idx - 4) : idx]):
                folded = candidate.casefold()
                if candidate and folded not in member_labels and folded not in _ZSE_NAVIGATION_TOKENS:
                    return candidate

    # Compatibility fallback for older/simple ZSE layouts where an explicit
    # Issuer/Izdavatelj field directly precedes the legal name.
    fallback = _next_token(tokens, {"Issuer", "Izdavatelj"})
    if fallback and fallback.casefold() not in _ZSE_NAVIGATION_TOKENS:
        return fallback
    return None


def parse_zse_issuer_html(html: str, *, isin: str, source_url: str) -> ZSEIssuerIdentity:
    """Parse the official ZSE issuer page reached through the exact ISIN query."""
    isin_norm = _normalize_isin(isin)
    tokens = _visible_tokens(html)
    joined = "\n".join(tokens)

    lei = None
    for pattern in (
        r"(?:LEI(?:\s*\([^\n]*\))?)\s*\n\s*([A-Z0-9]{20})\b",
        r"\bLEI\b.{0,180}?\b([A-Z0-9]{20})\b",
    ):
        match = re.search(pattern, joined, flags=re.IGNORECASE | re.DOTALL)
        if match:
            lei = normalize_lei(match.group(1))
            break
    if not lei:
        raise ValueError("official ZSE issuer page did not expose a parseable LEI")

    issuer_name = _issuer_name_from_tokens(tokens)
    if not issuer_name:
        raise ValueError("official ZSE issuer page did not expose a parseable issuer name")

    tax_number = _next_token(tokens, {"Tax Number", "Porezni broj"})
    country = _next_token(tokens, {"Home Member State", "Matična država članica"})
    country_code = None
    if country:
        folded = country.casefold()
        if "croatia" in folded or "hrvatska" in folded:
            country_code = "HR"

    return ZSEIssuerIdentity(
        isin=isin_norm,
        issuer_name=issuer_name,
        lei=lei,
        country_code=country_code,
        tax_number=tax_number,
        source_url=source_url,
    )


def fetch_zse_issuer_identity(
    isin: str,
    *,
    timeout: float = 20.0,
    session: Any | None = None,
) -> ZSEIssuerIdentity:
    isin_norm = _normalize_isin(isin)
    client = session or requests
    response = client.get(
        ZSE_ISSUER_BASE,
        params={"isin": isin_norm},
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": "zse-value-scanner/0.3.8 (+official ZSE identity corroboration)",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    source_url = getattr(response, "url", None) or f"{ZSE_ISSUER_BASE}?{urlencode({'isin': isin_norm})}"
    return parse_zse_issuer_html(response.text, isin=isin_norm, source_url=str(source_url))


def _validate_gleif(candidate: GLEIFCandidate, *, lei: str, country: str | None) -> None:
    if normalize_lei(candidate.lei) != normalize_lei(lei):
        raise ValueError(f"GLEIF exact record mismatch: expected {lei}, received {candidate.lei}")
    expected_country = country.strip().upper() if country else None
    observed_country = candidate.legal_address_country.strip().upper() if candidate.legal_address_country else None
    if expected_country and observed_country and expected_country != observed_country:
        raise ValueError(f"country conflict: local={expected_country}, GLEIF={observed_country}")
    if candidate.registration_status != "ISSUED":
        raise ValueError(f"GLEIF registration status is {candidate.registration_status!r}, not ISSUED")
    if candidate.entity_status and candidate.entity_status != "ACTIVE":
        raise ValueError(f"GLEIF entity status is {candidate.entity_status!r}, not ACTIVE")


def corroborate_ticker(
    db: Database,
    ticker: str,
    *,
    timeout: float = 20.0,
    zse_fetcher: Callable[..., ZSEIssuerIdentity] = fetch_zse_issuer_identity,
    gleif_fetcher: Callable[..., tuple[GLEIFCandidate, dict[str, Any], bytes]] = fetch_lei_record,
) -> list[OfficialIdentityRow]:
    """Corroborate a ZSE entity with the official exchange issuer page plus exact GLEIF record.

    This function is strictly read-only with respect to identity metadata.
    """
    ticker_norm = _normalize_ticker(ticker)
    entity = _entity_dict(db.research_entity_by_identifier(TICKER_SCHEME, ticker_norm))
    if not entity:
        return [OfficialIdentityRow(
            ticker=ticker_norm, state="UNKNOWN_ENTITY", evidence_level="NONE", disposition="BLOCK",
            entity_id=None, local_legal_name=None, local_country=None, isin=None, existing_lei=None,
            lei=None, zse_issuer_name=None, zse_tax_number=None, zse_source_url=None,
            gleif_legal_name=None, gleif_country=None, registration_status=None, entity_status=None,
            gleif_source_url=None, note=f"no research entity found for {TICKER_SCHEME}={ticker_norm}",
            confirmation_command=None,
        )]

    entity_id = str(entity["entity_id"])
    local_name = str(entity["legal_name"])
    local_country = str(entity.get("country_code")).upper() if entity.get("country_code") else None
    existing = _identifiers(db, entity_id, LEI_SCHEME)
    if existing:
        return [OfficialIdentityRow(
            ticker=ticker_norm, state="ALREADY_IDENTIFIED", evidence_level="A_VERIFIED", disposition="SKIP",
            entity_id=entity_id, local_legal_name=local_name, local_country=local_country,
            isin=None, existing_lei=";".join(existing), lei=None, zse_issuer_name=None,
            zse_tax_number=None, zse_source_url=None, gleif_legal_name=None, gleif_country=None,
            registration_status=None, entity_status=None, gleif_source_url=None,
            note="entity already has an LEI; official corroboration skipped", confirmation_command=None,
        )]

    isins = _identifiers(db, entity_id, ISIN_SCHEME)
    if not isins:
        return [OfficialIdentityRow(
            ticker=ticker_norm, state="NO_ISIN", evidence_level="NONE", disposition="RESEARCH",
            entity_id=entity_id, local_legal_name=local_name, local_country=local_country,
            isin=None, existing_lei=None, lei=None, zse_issuer_name=None, zse_tax_number=None,
            zse_source_url=None, gleif_legal_name=None, gleif_country=None, registration_status=None,
            entity_status=None, gleif_source_url=None,
            note="no local ISIN available for exact ZSE issuer-page lookup", confirmation_command=None,
        )]

    rows: list[OfficialIdentityRow] = []
    good_leis: set[str] = set()
    for raw_isin in isins:
        isin = _normalize_isin(raw_isin)
        try:
            zse_record = zse_fetcher(isin, timeout=timeout)
            candidate, _payload, _canonical = gleif_fetcher(
                zse_record.lei,
                query_name=zse_record.issuer_name,
                country=local_country or zse_record.country_code,
                timeout=timeout,
            )
            _validate_gleif(candidate, lei=zse_record.lei, country=local_country or zse_record.country_code)
            good_leis.add(zse_record.lei)
            command = (
                "python -m zse_tool.gleif_ingest "
                f"--ticker {ticker_norm} --lei {zse_record.lei} --yes-confirm --json"
            )
            rows.append(OfficialIdentityRow(
                ticker=ticker_norm,
                state="CORROBORATED",
                evidence_level="B_CORROBORATED_OFFICIAL_EVIDENCE",
                disposition="REVIEW_CONFIRM",
                entity_id=entity_id,
                local_legal_name=local_name,
                local_country=local_country,
                isin=isin,
                existing_lei=None,
                lei=zse_record.lei,
                zse_issuer_name=zse_record.issuer_name,
                zse_tax_number=zse_record.tax_number,
                zse_source_url=zse_record.source_url,
                gleif_legal_name=candidate.legal_name,
                gleif_country=candidate.legal_address_country,
                registration_status=candidate.registration_status,
                entity_status=candidate.entity_status,
                gleif_source_url=candidate.source_url,
                note=(
                    "official ZSE issuer page links the local ISIN to this LEI; exact GLEIF record "
                    "independently validates country and active/issued status"
                ),
                confirmation_command=command,
            ))
        except Exception as exc:
            rows.append(OfficialIdentityRow(
                ticker=ticker_norm, state="OFFICIAL_CORROBORATION_ERROR",
                evidence_level="B_CORROBORATED_OFFICIAL_EVIDENCE", disposition="BLOCK",
                entity_id=entity_id, local_legal_name=local_name, local_country=local_country,
                isin=isin, existing_lei=None, lei=None, zse_issuer_name=None, zse_tax_number=None,
                zse_source_url=f"{ZSE_ISSUER_BASE}?{urlencode({'isin': isin})}", gleif_legal_name=None,
                gleif_country=None, registration_status=None, entity_status=None, gleif_source_url=None,
                note=f"{type(exc).__name__}: {exc}", confirmation_command=None,
            ))

    if len(good_leis) > 1:
        return [OfficialIdentityRow(
            ticker=ticker_norm, state="OFFICIAL_AMBIGUITY",
            evidence_level="B_CORROBORATED_OFFICIAL_EVIDENCE", disposition="BLOCK",
            entity_id=entity_id, local_legal_name=local_name, local_country=local_country,
            isin=";".join(isins), existing_lei=None, lei=";".join(sorted(good_leis)),
            zse_issuer_name=None, zse_tax_number=None, zse_source_url=None,
            gleif_legal_name=None, gleif_country=None, registration_status=None, entity_status=None,
            gleif_source_url=None, note="multiple local ISINs corroborated to distinct LEIs; manual review required",
            confirmation_command=None,
        )] + rows
    return rows


def corroborate_batch(db: Database, tickers: Iterable[str], **kwargs: Any) -> list[OfficialIdentityRow]:
    seen: set[str] = set()
    rows: list[OfficialIdentityRow] = []
    for value in tickers:
        ticker = _normalize_ticker(value)
        if ticker in seen:
            continue
        seen.add(ticker)
        rows.extend(corroborate_ticker(db, ticker, **kwargs))
    return rows


def _manifest(rows: Iterable[OfficialIdentityRow]) -> dict[str, Any]:
    return {
        "generated_at": utc_now(),
        "mode": "read-only-official-identity-corroboration",
        "policy": {
            "automatic_identity_writes": False,
            "human_confirmation_required": True,
            "primary_official_source": "Zagreb Stock Exchange issuer page scoped by exact ISIN",
            "independent_validation": "GLEIF exact LEI record",
            "llm_required": False,
        },
        "rows": [row.to_dict() for row in rows],
    }


def write_manifest(rows: Iterable[OfficialIdentityRow], path: Path) -> Path:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(_manifest(rows), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, target)
    return target


def _default_db(data_dir: str | None) -> Database:
    root = Path(data_dir or os.getenv("ZSE_DATA_DIR", "data")).expanduser().resolve()
    db = Database(root / "zse.sqlite")
    db.init()
    return db


def _parse_tickers(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [_normalize_ticker(x) for x in raw.split(",") if x.strip()]


def _print_rows(rows: Iterable[OfficialIdentityRow]) -> None:
    rows = list(rows)
    print("TICKER  LEVEL                              STATE         ACTION          ISIN          LEI                   ZSE ISSUER")
    for row in rows:
        print(
            f"{row.ticker:<7} {row.evidence_level:<34} {row.state:<13} {row.disposition:<15} "
            f"{(row.isin or '-'):13} {(row.lei or row.existing_lei or '-'):21} {row.zse_issuer_name or row.note or '-'}"
        )
    print()
    print("Read-only: no LEI is attached by this command.")
    print("B_CORROBORATED means the ZSE ISIN-scoped issuer page and exact GLEIF record agree.")
    print("Human confirmation is still required before persistence.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m zse_tool.zse_identity",
        description="Corroborate ZSE issuer identity using the official ISIN-scoped ZSE issuer page plus exact GLEIF validation.",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--tickers", help="Comma-separated ZSE tickers, e.g. HT,GRNL")
    source.add_argument("--all-unidentified", action="store_true", help="Corroborate all ZSE entities without an LEI")
    parser.add_argument("--data-dir", help="Override scanner data directory; otherwise ZSE_DATA_DIR or ./data")
    parser.add_argument("--timeout", type=float, default=20.0, help="HTTP timeout seconds")
    parser.add_argument("--output", help="Optional JSON manifest path")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db = _default_db(args.data_dir)
    tickers = unidentified_zse_tickers(db) if args.all_unidentified else _parse_tickers(args.tickers)
    rows = corroborate_batch(db, tickers, timeout=args.timeout)
    output_path = write_manifest(rows, Path(args.output)) if args.output else None
    if args.json:
        payload = _manifest(rows)
        if output_path:
            payload["manifest_path"] = str(output_path)
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_rows(rows)
        if output_path:
            print(f"Corroboration manifest: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
