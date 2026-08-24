from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

import requests

from .gleif import GLEIF_API_BASE, GLEIFCandidate, _candidate_from_record, search_legal_name
from .storage import Database, utc_now

TICKER_SCHEME = "TICKER:ZSE"
ISIN_SCHEME = "ISIN"
LEI_SCHEME = "LEI"
MAX_PAGE_SIZE = 25


@dataclass(frozen=True)
class IdentityResolutionRow:
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
    gleif_legal_name: str | None
    candidate_country: str | None
    registration_status: str | None
    entity_status: str | None
    name_similarity: float | None
    source_method: str
    source_url: str | None
    note: str | None
    confirmation_command: str | None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.name_similarity is not None:
            data["name_similarity"] = round(self.name_similarity, 6)
        return data


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


def search_isin(
    isin: str,
    *,
    query_name: str,
    country: str | None = None,
    limit: int = 5,
    timeout: float = 20.0,
    session: Any | None = None,
) -> list[GLEIFCandidate]:
    """Search the official GLEIF API using its certified ISIN-to-LEI mapping filter."""
    isin_norm = _normalize_isin(isin)
    if not 1 <= int(limit) <= MAX_PAGE_SIZE:
        raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
    country_norm = country.strip().upper() if country else None
    if country_norm and not re.fullmatch(r"[A-Z]{2}", country_norm):
        raise ValueError("country must be a two-letter ISO country code")

    client = session or requests
    response = client.get(
        f"{GLEIF_API_BASE}/lei-records",
        params={
            "filter[isin]": isin_norm,
            "page[number]": 1,
            "page[size]": int(limit),
        },
        headers={
            "Accept": "application/vnd.api+json",
            "User-Agent": "zse-value-scanner/0.3.7 (+ISIN-first identity resolution)",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    records = payload.get("data") or []
    if not isinstance(records, list):
        raise ValueError("unexpected GLEIF response: data is not a list")
    return [
        _candidate_from_record(record, query_name=query_name, query_country=country_norm)
        for record in records[: int(limit)]
        if isinstance(record, dict)
    ]


def _candidate_gate(candidate: GLEIFCandidate) -> tuple[str, str]:
    if candidate.country_match is False:
        return "REJECT", "country conflicts with local entity"
    if candidate.registration_status != "ISSUED":
        return "REJECT", f"registration status {candidate.registration_status!r} is not ISSUED"
    if candidate.entity_status and candidate.entity_status != "ACTIVE":
        return "REJECT", f"entity status {candidate.entity_status!r} is not ACTIVE"
    return "REVIEW_CONFIRM", "passes country/status hard gates"


def _row_from_candidate(
    *,
    ticker: str,
    entity: dict[str, Any],
    isin: str | None,
    candidate: GLEIFCandidate,
    evidence_level: str,
    source_method: str,
    disposition: str,
    note: str,
) -> IdentityResolutionRow:
    command = None
    if disposition == "REVIEW_CONFIRM":
        command = (
            "python -m zse_tool.gleif_ingest "
            f"--ticker {ticker} --lei {candidate.lei} --yes-confirm --json"
        )
    return IdentityResolutionRow(
        ticker=ticker,
        state="CANDIDATE",
        evidence_level=evidence_level,
        disposition=disposition,
        entity_id=str(entity["entity_id"]),
        local_legal_name=str(entity["legal_name"]),
        local_country=(str(entity.get("country_code")).upper() if entity.get("country_code") else None),
        isin=isin,
        existing_lei=None,
        lei=candidate.lei,
        gleif_legal_name=candidate.legal_name,
        candidate_country=candidate.legal_address_country,
        registration_status=candidate.registration_status,
        entity_status=candidate.entity_status,
        name_similarity=candidate.name_similarity,
        source_method=source_method,
        source_url=candidate.source_url,
        note=note,
        confirmation_command=command,
    )


def resolve_ticker(
    db: Database,
    ticker: str,
    *,
    limit: int = 5,
    timeout: float = 20.0,
    isin_searcher: Callable[..., list[GLEIFCandidate]] = search_isin,
    name_searcher: Callable[..., list[GLEIFCandidate]] = search_legal_name,
    allow_name_fallback: bool = True,
) -> list[IdentityResolutionRow]:
    """Resolve identity evidence deterministically, preferring official ISIN mapping over names.

    No identifier is written. Level A is official mapped-identifier evidence. Level C is
    name-search candidate evidence. Level B (multi-source official corroboration) and Level D
    (web/LLM research leads) are reserved for later layers and never silently promoted here.
    """
    ticker_norm = _normalize_ticker(ticker)
    entity = _entity_dict(db.research_entity_by_identifier(TICKER_SCHEME, ticker_norm))
    if not entity:
        return [IdentityResolutionRow(
            ticker=ticker_norm, state="UNKNOWN_ENTITY", evidence_level="NONE", disposition="BLOCK",
            entity_id=None, local_legal_name=None, local_country=None, isin=None, existing_lei=None,
            lei=None, gleif_legal_name=None, candidate_country=None, registration_status=None,
            entity_status=None, name_similarity=None, source_method="local-entity-master", source_url=None,
            note=f"no research entity found for {TICKER_SCHEME}={ticker_norm}", confirmation_command=None,
        )]

    entity_id = str(entity["entity_id"])
    local_name = str(entity["legal_name"])
    local_country = (str(entity.get("country_code")).upper() if entity.get("country_code") else None)
    existing = _identifiers(db, entity_id, LEI_SCHEME)
    if existing:
        return [IdentityResolutionRow(
            ticker=ticker_norm, state="ALREADY_IDENTIFIED", evidence_level="A_VERIFIED", disposition="SKIP",
            entity_id=entity_id, local_legal_name=local_name, local_country=local_country, isin=None,
            existing_lei=";".join(existing), lei=None, gleif_legal_name=None, candidate_country=None,
            registration_status=None, entity_status=None, name_similarity=None,
            source_method="local-entity-master", source_url=None,
            note="entity already has an LEI; external resolution skipped", confirmation_command=None,
        )]

    isins = _identifiers(db, entity_id, ISIN_SCHEME)
    isin_rows: list[IdentityResolutionRow] = []
    mapped_good_leis: set[str] = set()
    any_isin_results = False

    for raw_isin in isins:
        try:
            isin = _normalize_isin(raw_isin)
            candidates = isin_searcher(
                isin,
                query_name=local_name,
                country=local_country,
                limit=limit,
                timeout=timeout,
            )
        except Exception as exc:
            return [IdentityResolutionRow(
                ticker=ticker_norm, state="ISIN_SEARCH_ERROR", evidence_level="A_OFFICIAL_ISIN_MAPPING",
                disposition="BLOCK", entity_id=entity_id, local_legal_name=local_name,
                local_country=local_country, isin=raw_isin, existing_lei=None, lei=None,
                gleif_legal_name=None, candidate_country=None, registration_status=None,
                entity_status=None, name_similarity=None, source_method="GLEIF filter[isin]",
                source_url=None, note=f"{type(exc).__name__}: {exc}; stronger identifier path failed",
                confirmation_command=None,
            )]

        if candidates:
            any_isin_results = True
        for candidate in candidates:
            disposition, gate_note = _candidate_gate(candidate)
            if disposition == "REVIEW_CONFIRM":
                mapped_good_leis.add(candidate.lei)
            isin_rows.append(_row_from_candidate(
                ticker=ticker_norm,
                entity=entity,
                isin=isin,
                candidate=candidate,
                evidence_level="A_OFFICIAL_ISIN_MAPPING",
                source_method="GLEIF filter[isin] / ANNA ISIN-to-LEI mapping",
                disposition=disposition,
                note=(
                    f"official ISIN-to-LEI mapped candidate; {gate_note}; "
                    "human confirmation still required before persistence"
                ),
            ))

    if any_isin_results:
        if len(mapped_good_leis) > 1:
            return [IdentityResolutionRow(
                ticker=ticker_norm, state="ISIN_AMBIGUOUS", evidence_level="A_OFFICIAL_ISIN_MAPPING",
                disposition="BLOCK", entity_id=entity_id, local_legal_name=local_name,
                local_country=local_country, isin=";".join(isins) if isins else None,
                existing_lei=None, lei=";".join(sorted(mapped_good_leis)), gleif_legal_name=None,
                candidate_country=None, registration_status=None, entity_status=None,
                name_similarity=None, source_method="GLEIF filter[isin] / ANNA ISIN-to-LEI mapping",
                source_url=None, note="multiple distinct eligible LEIs mapped from local ISINs; manual investigation required",
                confirmation_command=None,
            )] + isin_rows
        return isin_rows

    if not allow_name_fallback:
        return [IdentityResolutionRow(
            ticker=ticker_norm, state="NO_ISIN_MAPPING", evidence_level="NONE", disposition="REVIEW",
            entity_id=entity_id, local_legal_name=local_name, local_country=local_country,
            isin=";".join(isins) if isins else None, existing_lei=None, lei=None,
            gleif_legal_name=None, candidate_country=None, registration_status=None,
            entity_status=None, name_similarity=None, source_method="GLEIF filter[isin]",
            source_url=None, note="no official ISIN mapping returned; name fallback disabled",
            confirmation_command=None,
        )]

    try:
        candidates = name_searcher(local_name, country=local_country, limit=limit, timeout=timeout)
    except Exception as exc:
        return [IdentityResolutionRow(
            ticker=ticker_norm, state="NAME_SEARCH_ERROR", evidence_level="C_NAME_CANDIDATE", disposition="BLOCK",
            entity_id=entity_id, local_legal_name=local_name, local_country=local_country,
            isin=";".join(isins) if isins else None, existing_lei=None, lei=None,
            gleif_legal_name=None, candidate_country=None, registration_status=None,
            entity_status=None, name_similarity=None, source_method="GLEIF legal-name search",
            source_url=None, note=f"{type(exc).__name__}: {exc}", confirmation_command=None,
        )]

    if not candidates:
        return [IdentityResolutionRow(
            ticker=ticker_norm, state="UNRESOLVED", evidence_level="D_RESEARCH_LEAD_REQUIRED", disposition="RESEARCH",
            entity_id=entity_id, local_legal_name=local_name, local_country=local_country,
            isin=";".join(isins) if isins else None, existing_lei=None, lei=None,
            gleif_legal_name=None, candidate_country=None, registration_status=None,
            entity_status=None, name_similarity=None, source_method="deterministic paths exhausted",
            source_url=None,
            note="ISIN mapping and legal-name search returned no candidate; escalate to official web research, then optionally LLM-assisted research",
            confirmation_command=None,
        )]

    rows: list[IdentityResolutionRow] = []
    for candidate in candidates:
        disposition, gate_note = _candidate_gate(candidate)
        rows.append(_row_from_candidate(
            ticker=ticker_norm,
            entity=entity,
            isin=";".join(isins) if isins else None,
            candidate=candidate,
            evidence_level="C_NAME_CANDIDATE",
            source_method="GLEIF legal-name search",
            disposition=disposition,
            note=f"name-search candidate only; {gate_note}; further corroboration recommended",
        ))
    return rows


def resolve_batch(
    db: Database,
    tickers: Iterable[str],
    **kwargs: Any,
) -> list[IdentityResolutionRow]:
    seen: set[str] = set()
    rows: list[IdentityResolutionRow] = []
    for value in tickers:
        ticker = _normalize_ticker(value)
        if ticker in seen:
            continue
        seen.add(ticker)
        rows.extend(resolve_ticker(db, ticker, **kwargs))
    return rows


def _manifest(rows: Iterable[IdentityResolutionRow]) -> dict[str, Any]:
    return {
        "generated_at": utc_now(),
        "mode": "read-only-identity-resolution",
        "policy": {
            "automatic_identity_writes": False,
            "priority": [
                "A_OFFICIAL_ISIN_MAPPING",
                "B_CORROBORATED_OFFICIAL_EVIDENCE",
                "C_NAME_CANDIDATE",
                "D_RESEARCH_LEAD_REQUIRED",
            ],
            "level_b_status": "reserved for multi-source official corroboration",
            "level_d_status": "research leads only; web/LLM findings require official corroboration before persistence",
            "human_confirmation_required": True,
        },
        "rows": [row.to_dict() for row in rows],
    }


def write_manifest(rows: Iterable[IdentityResolutionRow], path: Path) -> Path:
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
    return [_normalize_ticker(x) for x in (raw or "").split(",") if x.strip()]


def _print(rows: Iterable[IdentityResolutionRow]) -> None:
    rows = list(rows)
    print("TICKER  LEVEL                     STATE              ACTION          ISIN          LEI                   GLEIF LEGAL NAME")
    for row in rows:
        print(
            f"{row.ticker:<7} {row.evidence_level:<25} {row.state:<18} {row.disposition:<15} "
            f"{(row.isin or '-'):13} {(row.lei or row.existing_lei or '-'):<20} "
            f"{row.gleif_legal_name or row.note or '-'}"
        )
    print()
    print("Read-only: this resolver never attaches an LEI.")
    print("Priority: official ISIN mapping -> official corroboration -> name candidate -> research lead.")
    print("Web/LLM research may generate leads later, but never bypasses official-evidence confirmation.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m zse_tool.gleif_resolve",
        description="ISIN-first deterministic GLEIF identity resolution with safe escalation policy.",
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--tickers", help="Comma-separated ZSE tickers, e.g. HT,GRNL")
    src.add_argument("--all-unidentified", action="store_true", help="Resolve every ZSE entity lacking an LEI")
    p.add_argument("--data-dir")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--timeout", type=float, default=20.0)
    p.add_argument("--no-name-fallback", action="store_true")
    p.add_argument("--output", help="Optional JSON manifest path")
    p.add_argument("--json", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 1 <= args.limit <= MAX_PAGE_SIZE:
        raise SystemExit(f"--limit must be between 1 and {MAX_PAGE_SIZE}")
    db = _default_db(args.data_dir)
    tickers = unidentified_zse_tickers(db) if args.all_unidentified else _parse_tickers(args.tickers)
    rows = resolve_batch(
        db,
        tickers,
        limit=args.limit,
        timeout=args.timeout,
        allow_name_fallback=not args.no_name_fallback,
    )
    out = write_manifest(rows, Path(args.output)) if args.output else None
    if args.json:
        payload = _manifest(rows)
        if out:
            payload["manifest_path"] = str(out)
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print(rows)
        if out:
            print(f"Resolution manifest: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
