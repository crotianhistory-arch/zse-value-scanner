from pathlib import Path

from zse_tool.cli import build_parser
from zse_tool.config import Settings
from zse_tool.storage import Database
from zse_tool.warehouse import (
    WarehouseLayout,
    bootstrap_local_entities,
    entity_lookup,
    init_warehouse,
    register_entity,
    seed_source_registry,
    warehouse_status,
)


def test_warehouse_init_creates_layout_and_seed_registry(tmp_path: Path):
    db = Database(tmp_path / "zse.sqlite")
    layout = WarehouseLayout(tmp_path / "warehouse")
    result = init_warehouse(db, layout)
    assert result["sources"] >= 7
    assert result["datasets"] >= 7
    for rel in ("raw/esef", "raw/ted", "staging/financials", "parquet/financials", "manifests", "tmp"):
        assert (layout.root / rel).is_dir()
    assert db.warehouse_counts()["research_entities"] == 0


def test_settings_support_separate_warehouse_root_without_moving_existing_db(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ZSE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ZSE_WAREHOUSE_DIR", str(tmp_path / "big-warehouse"))
    s = Settings.from_env()
    assert s.db_path == (tmp_path / "data" / "zse.sqlite").resolve()
    assert s.resolved_warehouse_dir == (tmp_path / "big-warehouse").resolve()


def test_bootstrap_local_entities_is_idempotent_and_preserves_zse_identifiers(tmp_path: Path):
    db = Database(tmp_path / "zse.sqlite")
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO securities(ticker, isin, name, source_url, updated_at) VALUES (?,?,?,?,?)",
            ("GRNL", "HRGRNLRA0006", "Granolio d.d.", "https://example.invalid", "2026-08-20T00:00:00Z"),
        )
    first = bootstrap_local_entities(db)
    second = bootstrap_local_entities(db)
    assert len(first) == 1 and len(second) == 1
    assert db.warehouse_counts()["research_entities"] == 1
    e = entity_lookup(db, "GRNL")[0]
    ids = {(x["scheme"], x["value"]) for x in e["identifiers"]}
    assert ("ISIN", "HRGRNLRA0006") in ids
    assert ("TICKER:ZSE", "GRNL") in ids


def test_entity_register_merges_when_stable_identifier_already_exists(tmp_path: Path):
    db = Database(tmp_path / "zse.sqlite")
    a = register_entity(db, legal_name="Example A", country_code="HR", isin="HRTEST000001", ticker="AAA", exchange="ZSE")
    b = register_entity(db, legal_name="Example A renamed", country_code="HR", isin="HRTEST000001", lei="12345678901234567890")
    assert a["entity_id"] == b["entity_id"]
    assert b["legal_name"] == "Example A renamed"
    ids = {(x["scheme"], x["value"]) for x in b["identifiers"]}
    assert ("LEI", "12345678901234567890") in ids


def test_ingestion_job_state_is_resumable(tmp_path: Path):
    db = Database(tmp_path / "zse.sqlite")
    seed_source_registry(db)
    job = db.start_ingestion_job("gleif_lei_golden_copy", "2026-08-20-full", cursor={"page": 1})
    db.update_ingestion_job(job["job_id"], cursor={"page": 17}, items_seen=4000, items_downloaded=3995, items_failed=5, bytes_downloaded=12_345_678)
    resumed = db.start_ingestion_job("gleif_lei_golden_copy", "2026-08-20-full")
    assert resumed["job_id"] == job["job_id"]
    assert resumed["items_seen"] == 4000
    assert '17' in resumed["cursor_json"]
    db.finish_ingestion_job(job["job_id"], success=True)
    complete = db.ingestion_jobs("gleif_lei_golden_copy", limit=1)[0]
    assert complete["state"] == "complete"


def test_raw_artifact_registration_is_deduplicated_by_dataset_url_and_hash(tmp_path: Path):
    db = Database(tmp_path / "zse.sqlite")
    seed_source_registry(db)
    row = {
        "dataset_key": "eu_esef_annual_reports",
        "source_url": "https://example.invalid/report.zip",
        "local_path": str(tmp_path / "raw" / "report.zip"),
        "sha256": "a" * 64,
        "byte_size": 123,
        "media_type": "application/zip",
    }
    a = db.register_raw_artifact(row)
    b = db.register_raw_artifact(row)
    assert a == b
    assert db.warehouse_counts()["raw_artifacts"] == 1


def test_external_financial_fact_staging_preserves_source_and_updates_same_context(tmp_path: Path):
    db = Database(tmp_path / "zse.sqlite")
    seed_source_registry(db)
    e = register_entity(db, legal_name="Peer AG", country_code="DE", lei="529900TESTPEER00001")
    artifact = db.register_raw_artifact({
        "dataset_key": "eu_esef_annual_reports",
        "entity_id": e["entity_id"],
        "source_url": "https://example.invalid/peer-2025.zip",
        "local_path": str(tmp_path / "peer-2025.zip"),
        "sha256": "b" * 64,
    })
    fact = {
        "entity_id": e["entity_id"],
        "dataset_key": "eu_esef_annual_reports",
        "source_artifact_id": artifact,
        "period_end": "2025-12-31",
        "fiscal_year": 2025,
        "period_type": "FY",
        "taxonomy": "ifrs-full",
        "concept": "Revenue",
        "statement": "income_statement",
        "value": 100.0,
        "unit": "EUR",
        "context_key": "consolidated-fy",
    }
    fid1 = db.save_external_financial_fact(fact)
    fact["value"] = 101.0
    fid2 = db.save_external_financial_fact(fact)
    assert fid1 == fid2
    with db.connect() as conn:
        r = conn.execute("SELECT value, source_artifact_id FROM external_financial_facts WHERE fact_id=?", (fid1,)).fetchone()
    assert r["value"] == 101.0
    assert r["source_artifact_id"] == artifact


def test_warehouse_status_is_read_only_and_reports_optional_backend(tmp_path: Path):
    db = Database(tmp_path / "zse.sqlite")
    layout = WarehouseLayout(tmp_path / "warehouse")
    init_warehouse(db, layout)
    status = warehouse_status(db, layout, data_dir=tmp_path, db_path=db.path)
    assert status["initialized"] is True
    assert status["backend"]["metadata"] == "sqlite"
    assert "duckdb_available" in status["backend"]


def test_cli_exposes_warehouse_foundation_commands():
    parser = build_parser()
    assert parser.parse_args(["warehouse-init", "--bootstrap-local"]).cmd == "warehouse-init"
    assert parser.parse_args(["warehouse-status"]).cmd == "warehouse-status"
    assert parser.parse_args(["dataset-list", "--category", "financials"]).category == "financials"
    assert parser.parse_args(["entity-lookup", "GRNL"]).query == "GRNL"
    args = parser.parse_args(["entity-register", "--name", "Peer AG", "--country", "DE", "--lei", "529900TESTPEER00001"])
    assert args.lei == "529900TESTPEER00001"
