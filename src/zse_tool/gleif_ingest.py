from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from .gleif import GLEIFCandidate, fetch_lei_record, normalize_lei
from .storage import Database, utc_now

DATASET_KEY = "gleif_lei_golden_copy"
SOURCE_KEY = "gleif"
TICKER_SCHEME = "TICKER:ZSE"


class GLEIFIdentityConflict(RuntimeError):
    """Raised when a requested LEI would conflict with existing deterministic identity data."""


@dataclass(frozen=True)
class GLEIFPersistResult:
    state: str
    ticker: str
    entity_id: str
    entity_legal_name: str
    lei: str
    gleif_legal_name: str | None
    country: str | None
    registration_status: str | None
    entity_status: str | None
    name_similarity: float | None
    artifact_path: str | None
    artifact_sha256: str | None
    artifact_id: int | None
    ingestion_job_id: int | None

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
    try:
        return dict(row)
    except Exception as exc:
        raise TypeError("unexpected research entity row type") from exc


def _existing_leis(db: Database, entity_id: str) -> list[str]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT value FROM entity_identifiers WHERE entity_id=? AND scheme='LEI' ORDER BY value",
            (entity_id,),
        ).fetchall()
    return [str(row["value"]).strip().upper() for row in rows]


def _ensure_identity_preconditions(
    db: Database,
    *,
    ticker: str,
    lei: str,
) -> tuple[dict[str, Any], bool]:
    entity = _entity_row_dict(db.research_entity_by_identifier(TICKER_SCHEME, ticker))
    if not entity:
        raise GLEIFIdentityConflict(f"no research entity found for {TICKER_SCHEME}={ticker}")

    entity_id = str(entity["entity_id"])
    current = _existing_leis(db, entity_id)
    if current:
        if current == [lei] or (lei in current and len(set(current)) == 1):
            return entity, True
        raise GLEIFIdentityConflict(
            f"{ticker} already has different LEI identifier(s): {', '.join(current)}"
        )

    holder = _entity_row_dict(db.research_entity_by_identifier("LEI", lei))
    if holder and str(holder.get("entity_id")) != entity_id:
        raise GLEIFIdentityConflict(
            f"LEI {lei} is already attached to another entity: "
            f"{holder.get('legal_name') or holder.get('entity_id')}"
        )
    return entity, False


def _validate_live_record(
    candidate: GLEIFCandidate,
    *,
    requested_lei: str,
    entity_country: str | None,
) -> None:
    if normalize_lei(candidate.lei) != requested_lei:
        raise GLEIFIdentityConflict(
            f"live GLEIF record returned {candidate.lei!r}, expected {requested_lei}"
        )

    expected_country = (entity_country or "").strip().upper() or None
    observed_country = (candidate.legal_address_country or "").strip().upper() or None
    if expected_country and observed_country and observed_country != expected_country:
        raise GLEIFIdentityConflict(
            f"country conflict for {requested_lei}: local={expected_country}, GLEIF={observed_country}"
        )

    if candidate.registration_status != "ISSUED":
        raise GLEIFIdentityConflict(
            f"GLEIF registration status is {candidate.registration_status!r}, not 'ISSUED'; review manually"
        )

    if candidate.entity_status and candidate.entity_status != "ACTIVE":
        raise GLEIFIdentityConflict(
            f"GLEIF entity status is {candidate.entity_status!r}, not 'ACTIVE'; review manually"
        )


def _write_raw_snapshot(
    warehouse_root: Path,
    *,
    lei: str,
    canonical_json: bytes,
) -> tuple[Path, str]:
    sha256 = hashlib.sha256(canonical_json).hexdigest()
    target_dir = warehouse_root / "raw" / "gleif" / "api" / "lei-records" / lei
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{sha256}.json"

    if not target.exists():
        tmp = target.with_suffix(".json.tmp")
        tmp.write_bytes(canonical_json)
        os.replace(tmp, target)
    return target, sha256


def persist_confirmed_lei(
    db: Database,
    warehouse_root: Path,
    *,
    ticker: str,
    lei: str,
    timeout: float = 20.0,
    fetcher: Callable[..., tuple[GLEIFCandidate, dict[str, Any], bytes]] = fetch_lei_record,
) -> GLEIFPersistResult:
    """Persist one explicitly confirmed LEI for one existing ZSE research entity.

    This is intentionally not an automatic fuzzy matcher. The caller must provide
    the LEI explicitly. Country, live GLEIF status, and identifier conflicts are
    treated as hard gates before the LEI identifier is written.
    """
    ticker_norm = _normalize_ticker(ticker)
    lei_norm = normalize_lei(lei)
    warehouse_root = Path(warehouse_root).expanduser().resolve()

    entity, already_attached = _ensure_identity_preconditions(
        db,
        ticker=ticker_norm,
        lei=lei_norm,
    )
    entity_id = str(entity["entity_id"])
    local_name = str(entity["legal_name"])
    local_country = entity.get("country_code")

    if already_attached:
        return GLEIFPersistResult(
            state="already_attached",
            ticker=ticker_norm,
            entity_id=entity_id,
            entity_legal_name=local_name,
            lei=lei_norm,
            gleif_legal_name=None,
            country=(str(local_country).upper() if local_country else None),
            registration_status=None,
            entity_status=None,
            name_similarity=None,
            artifact_path=None,
            artifact_sha256=None,
            artifact_id=None,
            ingestion_job_id=None,
        )

    run_key = f"confirm:{ticker_norm}:{lei_norm}"
    job = db.start_ingestion_job(
        DATASET_KEY,
        run_key,
        cursor={"ticker": ticker_norm, "lei": lei_norm, "mode": "explicit-confirmation"},
    )
    job_id = int(job["job_id"])

    try:
        candidate, payload, canonical_json = fetcher(
            lei_norm,
            query_name=local_name,
            country=(str(local_country) if local_country else None),
            timeout=timeout,
        )
        _validate_live_record(
            candidate,
            requested_lei=lei_norm,
            entity_country=(str(local_country) if local_country else None),
        )

        artifact_path, artifact_sha = _write_raw_snapshot(
            warehouse_root,
            lei=lei_norm,
            canonical_json=canonical_json,
        )
        retrieved_at = utc_now()
        metadata = {
            "retrieval_method": "GLEIF API exact LEI record",
            "confirmation_mode": "explicit",
            "ticker": ticker_norm,
            "requested_lei": lei_norm,
            "local_legal_name": local_name,
            "gleif_candidate": candidate.to_dict(),
        }
        artifact_id = db.register_raw_artifact(
            {
                "dataset_key": DATASET_KEY,
                "entity_id": entity_id,
                "source_url": candidate.source_url,
                "publication_date": None,
                "retrieved_at": retrieved_at,
                "local_path": str(artifact_path),
                "sha256": artifact_sha,
                "byte_size": len(canonical_json),
                "media_type": "application/vnd.api+json",
                "parser_status": "validated-identity",
                "metadata_json": json.dumps(metadata, ensure_ascii=False, sort_keys=True),
            }
        )

        db.upsert_entity_identifier(
            entity_id,
            "LEI",
            lei_norm,
            source_key=SOURCE_KEY,
            is_primary=False,
        )
        db.update_ingestion_job(
            job_id,
            cursor={
                "ticker": ticker_norm,
                "lei": lei_norm,
                "artifact_sha256": artifact_sha,
                "artifact_id": artifact_id,
            },
            items_seen=1,
            items_downloaded=1,
            items_skipped=0,
            items_failed=0,
            bytes_downloaded=len(canonical_json),
        )
        db.finish_ingestion_job(job_id, success=True)

        return GLEIFPersistResult(
            state="attached",
            ticker=ticker_norm,
            entity_id=entity_id,
            entity_legal_name=local_name,
            lei=lei_norm,
            gleif_legal_name=candidate.legal_name,
            country=candidate.legal_address_country,
            registration_status=candidate.registration_status,
            entity_status=candidate.entity_status,
            name_similarity=candidate.name_similarity,
            artifact_path=str(artifact_path),
            artifact_sha256=artifact_sha,
            artifact_id=artifact_id,
            ingestion_job_id=job_id,
        )
    except Exception as exc:
        try:
            db.update_ingestion_job(job_id, items_seen=1, items_failed=1, last_error=str(exc))
            db.finish_ingestion_job(job_id, success=False, last_error=str(exc))
        except Exception:
            pass
        raise


def _default_paths(data_dir: str | None = None) -> tuple[Path, Path]:
    root = Path(data_dir or os.getenv("ZSE_DATA_DIR", "data")).expanduser().resolve()
    warehouse = Path(os.getenv("ZSE_WAREHOUSE_DIR", root / "warehouse")).expanduser().resolve()
    return root / "zse.sqlite", warehouse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m zse_tool.gleif_ingest",
        description=(
            "Persist one explicitly confirmed GLEIF LEI for an existing ZSE research entity. "
            "No automatic fuzzy merge is performed."
        ),
    )
    parser.add_argument("--ticker", required=True, help="Existing ZSE ticker, e.g. KOEI")
    parser.add_argument("--lei", required=True, help="Explicitly confirmed 20-character LEI")
    parser.add_argument("--data-dir", help="Override scanner data directory; otherwise ZSE_DATA_DIR or ./data")
    parser.add_argument("--timeout", type=float, default=20.0, help="GLEIF HTTP timeout seconds")
    parser.add_argument("--yes-confirm", action="store_true", help="Required explicit acknowledgement before any LEI write")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.yes_confirm:
        raise SystemExit("Refusing write: add --yes-confirm after reviewing the exact ticker and LEI.")

    db_path, warehouse_root = _default_paths(args.data_dir)
    db = Database(db_path)
    db.init()
    result = persist_confirmed_lei(
        db,
        warehouse_root,
        ticker=args.ticker,
        lei=args.lei,
        timeout=args.timeout,
    )

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"GLEIF identity {result.state}: {result.ticker} -> {result.lei}")
        print(f"local entity: {result.entity_legal_name}")
        if result.gleif_legal_name:
            print(f"GLEIF legal name: {result.gleif_legal_name}")
            print(f"country/status: {result.country or '-'} / {result.registration_status or '-'} / {result.entity_status or '-'}")
            print(f"name similarity (evidence only): {result.name_similarity:.1%}")
            print(f"raw artifact: {result.artifact_path}")
            print(f"sha256: {result.artifact_sha256}")
            print(f"artifact/job: {result.artifact_id} / {result.ingestion_job_id}")
        else:
            print("No write was needed; the same LEI is already attached.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
