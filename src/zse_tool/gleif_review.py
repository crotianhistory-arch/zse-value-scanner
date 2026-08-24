from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from .gleif import GLEIFCandidate, search_legal_name
from .storage import Database, utc_now

TICKER_SCHEME = "TICKER:ZSE"


@dataclass(frozen=True)
class GLEIFReviewRow:
    ticker: str
    state: str
    disposition: str
    entity_id: str | None
    local_legal_name: str | None
    local_country: str | None
    existing_lei: str | None
    candidate_rank: int | None
    lei: str | None
    gleif_legal_name: str | None
    candidate_country: str | None
    registration_status: str | None
    entity_status: str | None
    name_similarity: float | None
    match_class: str | None
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


def _entity_row_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    return dict(row)


def _existing_leis(db: Database, entity_id: str) -> list[str]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT value FROM entity_identifiers WHERE entity_id=? AND scheme='LEI' ORDER BY value",
            (entity_id,),
        ).fetchall()
    return [str(row["value"]).strip().upper() for row in rows]


def unidentified_zse_tickers(db: Database) -> list[str]:
    """Return ZSE tickers in the entity master that do not yet have an LEI."""
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT i.value AS ticker
            FROM entity_identifiers i
            WHERE i.scheme='TICKER:ZSE'
              AND NOT EXISTS (
                  SELECT 1
                  FROM entity_identifiers lei
                  WHERE lei.entity_id=i.entity_id
                    AND lei.scheme='LEI'
              )
            ORDER BY UPPER(i.value)
            """
        ).fetchall()
    return [str(row["ticker"]).strip().upper() for row in rows]


def _candidate_disposition(candidate: GLEIFCandidate) -> tuple[str, str]:
    if candidate.country_match is False:
        return "REJECT", "GLEIF country conflicts with the local entity country"
    if candidate.registration_status != "ISSUED":
        return "REJECT", f"GLEIF registration status is {candidate.registration_status!r}, not 'ISSUED'"
    if candidate.entity_status and candidate.entity_status != "ACTIVE":
        return "REJECT", f"GLEIF entity status is {candidate.entity_status!r}, not 'ACTIVE'"
    return (
        "REVIEW_CONFIRM",
        "passes hard country/status gates; legal-name similarity remains review evidence only",
    )


def review_ticker(
    db: Database,
    ticker: str,
    *,
    limit: int = 5,
    timeout: float = 20.0,
    searcher: Callable[..., list[GLEIFCandidate]] = search_legal_name,
) -> list[GLEIFReviewRow]:
    """Build a read-only GLEIF review set for one ZSE entity."""
    ticker_norm = _normalize_ticker(ticker)
    if not 1 <= int(limit) <= 25:
        raise ValueError("limit must be between 1 and 25")

    entity = _entity_row_dict(db.research_entity_by_identifier(TICKER_SCHEME, ticker_norm))
    if not entity:
        return [
            GLEIFReviewRow(
                ticker=ticker_norm,
                state="UNKNOWN_ENTITY",
                disposition="BLOCK",
                entity_id=None,
                local_legal_name=None,
                local_country=None,
                existing_lei=None,
                candidate_rank=None,
                lei=None,
                gleif_legal_name=None,
                candidate_country=None,
                registration_status=None,
                entity_status=None,
                name_similarity=None,
                match_class=None,
                source_url=None,
                note=f"no research entity found for {TICKER_SCHEME}={ticker_norm}",
                confirmation_command=None,
            )
        ]

    entity_id = str(entity["entity_id"])
    local_name = str(entity["legal_name"])
    local_country = (str(entity.get("country_code")).upper() if entity.get("country_code") else None)
    existing = _existing_leis(db, entity_id)
    if existing:
        return [
            GLEIFReviewRow(
                ticker=ticker_norm,
                state="ALREADY_IDENTIFIED",
                disposition="SKIP",
                entity_id=entity_id,
                local_legal_name=local_name,
                local_country=local_country,
                existing_lei=";".join(existing),
                candidate_rank=None,
                lei=None,
                gleif_legal_name=None,
                candidate_country=None,
                registration_status=None,
                entity_status=None,
                name_similarity=None,
                match_class=None,
                source_url=None,
                note="entity already has an LEI; discovery was skipped",
                confirmation_command=None,
            )
        ]

    try:
        candidates = searcher(
            local_name,
            country=local_country,
            limit=int(limit),
            timeout=timeout,
        )
    except Exception as exc:
        return [
            GLEIFReviewRow(
                ticker=ticker_norm,
                state="SEARCH_ERROR",
                disposition="BLOCK",
                entity_id=entity_id,
                local_legal_name=local_name,
                local_country=local_country,
                existing_lei=None,
                candidate_rank=None,
                lei=None,
                gleif_legal_name=None,
                candidate_country=None,
                registration_status=None,
                entity_status=None,
                name_similarity=None,
                match_class=None,
                source_url=None,
                note=f"{type(exc).__name__}: {exc}",
                confirmation_command=None,
            )
        ]

    if not candidates:
        return [
            GLEIFReviewRow(
                ticker=ticker_norm,
                state="NO_CANDIDATE",
                disposition="REVIEW",
                entity_id=entity_id,
                local_legal_name=local_name,
                local_country=local_country,
                existing_lei=None,
                candidate_rank=None,
                lei=None,
                gleif_legal_name=None,
                candidate_country=None,
                registration_status=None,
                entity_status=None,
                name_similarity=None,
                match_class=None,
                source_url=None,
                note="GLEIF legal-name search returned no candidates",
                confirmation_command=None,
            )
        ]

    rows: list[GLEIFReviewRow] = []
    for rank, candidate in enumerate(candidates, 1):
        disposition, note = _candidate_disposition(candidate)
        confirmation_command = None
        if disposition == "REVIEW_CONFIRM":
            confirmation_command = (
                "python -m zse_tool.gleif_ingest "
                f"--ticker {ticker_norm} --lei {candidate.lei} --yes-confirm --json"
            )
        rows.append(
            GLEIFReviewRow(
                ticker=ticker_norm,
                state="CANDIDATE",
                disposition=disposition,
                entity_id=entity_id,
                local_legal_name=local_name,
                local_country=local_country,
                existing_lei=None,
                candidate_rank=rank,
                lei=candidate.lei,
                gleif_legal_name=candidate.legal_name,
                candidate_country=candidate.legal_address_country,
                registration_status=candidate.registration_status,
                entity_status=candidate.entity_status,
                name_similarity=candidate.name_similarity,
                match_class=candidate.match_class,
                source_url=candidate.source_url,
                note=note,
                confirmation_command=confirmation_command,
            )
        )
    return rows


def review_batch(
    db: Database,
    tickers: Iterable[str],
    *,
    limit: int = 5,
    timeout: float = 20.0,
    searcher: Callable[..., list[GLEIFCandidate]] = search_legal_name,
) -> list[GLEIFReviewRow]:
    """Review multiple tickers without mutating identity metadata."""
    seen: set[str] = set()
    ordered: list[str] = []
    for value in tickers:
        ticker = _normalize_ticker(value)
        if ticker not in seen:
            seen.add(ticker)
            ordered.append(ticker)

    rows: list[GLEIFReviewRow] = []
    for ticker in ordered:
        rows.extend(
            review_ticker(
                db,
                ticker,
                limit=limit,
                timeout=timeout,
                searcher=searcher,
            )
        )
    return rows


def _manifest_payload(rows: Iterable[GLEIFReviewRow]) -> dict[str, Any]:
    return {
        "generated_at": utc_now(),
        "mode": "read-only-gleif-review",
        "policy": {
            "automatic_identity_writes": False,
            "name_similarity_is_write_gate": False,
            "confirmation_required": True,
        },
        "rows": [row.to_dict() for row in rows],
    }


def write_manifest(rows: Iterable[GLEIFReviewRow], path: Path) -> Path:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = _manifest_payload(rows)
    data = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, target)
    return target


def _default_db_path(data_dir: str | None = None) -> Path:
    root = Path(data_dir or os.getenv("ZSE_DATA_DIR", "data")).expanduser().resolve()
    return root / "zse.sqlite"


def _parse_tickers(raw: str | None) -> list[str]:
    if not raw:
        return []
    values = [part.strip() for part in raw.split(",") if part.strip()]
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        ticker = _normalize_ticker(value)
        if ticker not in seen:
            seen.add(ticker)
            out.append(ticker)
    return out


def _print_rows(rows: Iterable[GLEIFReviewRow]) -> None:
    rows = list(rows)
    if not rows:
        print("No review rows.")
        return
    print("TICKER  STATE               ACTION          RANK  SIMILAR  REG       ENTITY     LEI                   GLEIF LEGAL NAME")
    for row in rows:
        rank = "-" if row.candidate_rank is None else str(row.candidate_rank)
        sim = "-" if row.name_similarity is None else f"{row.name_similarity:.1%}"
        reg = row.registration_status or "-"
        entity_status = row.entity_status or "-"
        lei = row.lei or row.existing_lei or "-"
        gleif_name = row.gleif_legal_name or row.note or "-"
        print(
            f"{row.ticker:<7} {row.state:<19} {row.disposition:<15} "
            f"{rank:>4}  {sim:>7}  {reg:<8}  {entity_status:<9}  {lei:<20}  {gleif_name}"
        )
    print()
    print("Read-only review: this command never attaches an LEI.")
    print("Only rows marked REVIEW_CONFIRM are eligible for explicit human confirmation.")
    print("Name similarity is evidence only, never an automatic identity-write gate.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m zse_tool.gleif_review",
        description=(
            "Batch-review GLEIF candidates for ZSE entities without mutating identity metadata."
        ),
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--tickers", help="Comma-separated ZSE tickers, e.g. HT,GRNL")
    source.add_argument(
        "--all-unidentified",
        action="store_true",
        help="Review every ZSE entity in the research master that currently lacks an LEI",
    )
    parser.add_argument("--data-dir", help="Override scanner data directory; otherwise ZSE_DATA_DIR or ./data")
    parser.add_argument("--limit", type=int, default=5, help="Candidate limit per ticker (1-25; default 5)")
    parser.add_argument("--timeout", type=float, default=20.0, help="GLEIF HTTP timeout seconds")
    parser.add_argument("--output", help="Optional JSON review-manifest path")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db = Database(_default_db_path(args.data_dir))
    db.init()

    if args.all_unidentified:
        tickers = unidentified_zse_tickers(db)
    else:
        tickers = _parse_tickers(args.tickers)

    rows = review_batch(
        db,
        tickers,
        limit=args.limit,
        timeout=args.timeout,
    )

    output_path = None
    if args.output:
        output_path = write_manifest(rows, Path(args.output))

    if args.json:
        payload = _manifest_payload(rows)
        if output_path is not None:
            payload["manifest_path"] = str(output_path)
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_rows(rows)
        if output_path is not None:
            print(f"Review manifest: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
