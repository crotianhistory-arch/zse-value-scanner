from __future__ import annotations

import json
from pathlib import Path

import pytest

from zse_tool.gleif import GLEIFCandidate, normalize_lei
from zse_tool.gleif_ingest import GLEIFIdentityConflict, persist_confirmed_lei
from zse_tool.storage import Database
from zse_tool.warehouse import seed_source_registry


def _db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "zse.sqlite")
    db.init()
    seed_source_registry(db)
    db.upsert_research_entity(
        {
            "entity_id": "ISIN:HRKOEIRA0009",
            "legal_name": "KONČAR d.d.",
            "country_code": "HR",
            "entity_type": "issuer",
            "status": "active",
            "source_key": "zse",
        }
    )
    db.upsert_entity_identifier("ISIN:HRKOEIRA0009", "ISIN", "HRKOEIRA0009", source_key="zse", is_primary=True)
    db.upsert_entity_identifier("ISIN:HRKOEIRA0009", "TICKER:ZSE", "KOEI", source_key="zse")
    return db


def _candidate(lei: str = "74780000H0SHMRAW0I15", country: str = "HR", reg: str = "ISSUED") -> GLEIFCandidate:
    return GLEIFCandidate(
        lei=lei,
        legal_name="KONČAR - Elektroindustrija d.d. za proizvodnju i usluge",
        legal_address_country=country,
        jurisdiction="HR",
        registration_status=reg,
        entity_status="ACTIVE",
        registration_authority_id="RA000365",
        registration_authority_entity_id="080040936",
        query_name="KONČAR d.d.",
        query_country="HR",
        name_similarity=0.70,
        country_match=(country == "HR"),
        match_class="REVIEW",
        source_url=f"https://api.gleif.org/api/v1/lei-records/{lei}",
    )


def _fetcher(candidate: GLEIFCandidate):
    payload = {"data": {"id": candidate.lei, "attributes": {"lei": candidate.lei}}}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    def fetch(*args, **kwargs):
        return candidate, payload, raw
    return fetch


def test_normalize_lei_rejects_bad_values():
    assert normalize_lei("74780000h0shmraw0i15") == "74780000H0SHMRAW0I15"
    with pytest.raises(ValueError):
        normalize_lei("too-short")


def test_confirmed_lei_persists_identifier_artifact_and_job(tmp_path):
    db = _db(tmp_path)
    warehouse = tmp_path / "warehouse"
    lei = "74780000H0SHMRAW0I15"
    result = persist_confirmed_lei(
        db,
        warehouse,
        ticker="KOEI",
        lei=lei,
        fetcher=_fetcher(_candidate()),
    )
    assert result.state == "attached"
    assert db.research_entity_by_identifier("LEI", lei)["entity_id"] == "ISIN:HRKOEIRA0009"
    assert result.artifact_path and Path(result.artifact_path).exists()
    counts = db.warehouse_counts()
    assert counts["raw_artifacts"] == 1
    assert counts["ingestion_jobs"] == 1
    job = db.ingestion_jobs(limit=5)[0]
    assert job["state"] == "complete"


def test_rerun_is_idempotent_and_does_not_fetch_again(tmp_path):
    db = _db(tmp_path)
    warehouse = tmp_path / "warehouse"
    lei = "74780000H0SHMRAW0I15"
    first = persist_confirmed_lei(db, warehouse, ticker="KOEI", lei=lei, fetcher=_fetcher(_candidate()))
    assert first.state == "attached"

    def should_not_fetch(*args, **kwargs):
        raise AssertionError("idempotent rerun must not call GLEIF")

    second = persist_confirmed_lei(db, warehouse, ticker="KOEI", lei=lei, fetcher=should_not_fetch)
    assert second.state == "already_attached"
    with db.connect() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM entity_identifiers WHERE entity_id=? AND scheme='LEI'",
            ("ISIN:HRKOEIRA0009",),
        ).fetchone()["n"]
    assert n == 1
    assert db.warehouse_counts()["raw_artifacts"] == 1


def test_country_conflict_records_failed_job_but_does_not_attach(tmp_path):
    db = _db(tmp_path)
    warehouse = tmp_path / "warehouse"
    lei = "74780000H0SHMRAW0I15"
    with pytest.raises(GLEIFIdentityConflict, match="country conflict"):
        persist_confirmed_lei(
            db,
            warehouse,
            ticker="KOEI",
            lei=lei,
            fetcher=_fetcher(_candidate(country="DE")),
        )
    assert db.research_entity_by_identifier("LEI", lei) is None
    assert db.warehouse_counts()["raw_artifacts"] == 0
    assert db.ingestion_jobs(limit=5)[0]["state"] == "failed"


def test_existing_different_lei_blocks_before_network(tmp_path):
    db = _db(tmp_path)
    db.upsert_entity_identifier("ISIN:HRKOEIRA0009", "LEI", "529900T8BM49AURSDO55", source_key="gleif")

    def should_not_fetch(*args, **kwargs):
        raise AssertionError("conflict must be detected before network")

    with pytest.raises(GLEIFIdentityConflict, match="already has different LEI"):
        persist_confirmed_lei(
            db,
            tmp_path / "warehouse",
            ticker="KOEI",
            lei="74780000H0SHMRAW0I15",
            fetcher=should_not_fetch,
        )


def test_lei_already_belongs_to_other_entity_is_blocked(tmp_path):
    db = _db(tmp_path)
    db.upsert_research_entity(
        {
            "entity_id": "OTHER",
            "legal_name": "Other d.d.",
            "country_code": "HR",
            "entity_type": "issuer",
            "status": "active",
            "source_key": "zse",
        }
    )
    db.upsert_entity_identifier("OTHER", "LEI", "74780000H0SHMRAW0I15", source_key="gleif")

    with pytest.raises(GLEIFIdentityConflict, match="already attached to another entity"):
        persist_confirmed_lei(
            db,
            tmp_path / "warehouse",
            ticker="KOEI",
            lei="74780000H0SHMRAW0I15",
            fetcher=_fetcher(_candidate()),
        )
