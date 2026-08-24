from __future__ import annotations

import sqlite3
from pathlib import Path

from zse_tool.gleif import GLEIFCandidate
from zse_tool.gleif_resolve import resolve_batch, resolve_ticker, write_manifest
from zse_tool.storage import Database


def candidate(lei="097900BFHJ0000029454", name="Hrvatski Telekom d.d.", country="HR", reg="ISSUED", entity="ACTIVE"):
    return GLEIFCandidate(
        lei=lei,
        legal_name=name,
        legal_address_country=country,
        jurisdiction=country,
        registration_status=reg,
        entity_status=entity,
        registration_authority_id=None,
        registration_authority_entity_id=None,
        query_name="HT d.d.",
        query_country="HR",
        name_similarity=0.5,
        country_match=(country == "HR"),
        match_class="REVIEW",
        source_url=f"https://api.gleif.org/api/v1/lei-records/{lei}",
    )


def make_db(tmp_path: Path, *, ticker="HT", name="HT d.d.", isin="HRHT00RA0005", lei=None):
    db = Database(tmp_path / "zse.sqlite")
    db.init()
    entity_id = f"ISIN:{isin}"
    db.upsert_research_entity({
        "entity_id": entity_id,
        "legal_name": name,
        "country_code": "HR",
        "entity_type": "listed-company",
        "status": "active",
        "source_key": "zse",
    })
    db.upsert_entity_identifier(entity_id, "ISIN", isin, source_key="zse", is_primary=True)
    db.upsert_entity_identifier(entity_id, "TICKER:ZSE", ticker, source_key="zse", is_primary=True)
    if lei:
        db.upsert_entity_identifier(entity_id, "LEI", lei, source_key="gleif")
    return db


def test_isin_first_returns_level_a_and_confirmation(tmp_path):
    db = make_db(tmp_path)
    calls = []
    def isin_searcher(isin, **kwargs):
        calls.append(isin)
        return [candidate()]
    def name_searcher(*args, **kwargs):
        raise AssertionError("name fallback should not run when ISIN maps")
    rows = resolve_ticker(db, "HT", isin_searcher=isin_searcher, name_searcher=name_searcher)
    assert calls == ["HRHT00RA0005"]
    assert len(rows) == 1
    row = rows[0]
    assert row.evidence_level == "A_OFFICIAL_ISIN_MAPPING"
    assert row.disposition == "REVIEW_CONFIRM"
    assert row.lei == "097900BFHJ0000029454"
    assert "gleif_ingest --ticker HT --lei 097900BFHJ0000029454" in row.confirmation_command


def test_existing_lei_skips_all_external_search(tmp_path):
    db = make_db(tmp_path, lei="097900BFHJ0000029454")
    def fail(*args, **kwargs):
        raise AssertionError("external search should be skipped")
    rows = resolve_ticker(db, "HT", isin_searcher=fail, name_searcher=fail)
    assert rows[0].state == "ALREADY_IDENTIFIED"
    assert rows[0].evidence_level == "A_VERIFIED"


def test_country_conflict_rejected(tmp_path):
    db = make_db(tmp_path)
    rows = resolve_ticker(db, "HT", isin_searcher=lambda *a, **k: [candidate(country="DE")])
    assert rows[0].evidence_level == "A_OFFICIAL_ISIN_MAPPING"
    assert rows[0].disposition == "REJECT"
    assert rows[0].confirmation_command is None


def test_isin_search_error_blocks_and_does_not_weaken_to_name(tmp_path):
    db = make_db(tmp_path)
    def broken(*a, **k):
        raise TimeoutError("network")
    def name(*a, **k):
        raise AssertionError("must not silently weaken evidence after stronger-path error")
    rows = resolve_ticker(db, "HT", isin_searcher=broken, name_searcher=name)
    assert rows[0].state == "ISIN_SEARCH_ERROR"
    assert rows[0].disposition == "BLOCK"


def test_no_isin_mapping_falls_back_to_name_level_c(tmp_path):
    db = make_db(tmp_path)
    rows = resolve_ticker(
        db,
        "HT",
        isin_searcher=lambda *a, **k: [],
        name_searcher=lambda *a, **k: [candidate()],
    )
    assert rows[0].evidence_level == "C_NAME_CANDIDATE"
    assert rows[0].disposition == "REVIEW_CONFIRM"


def test_no_isin_and_no_name_becomes_level_d_research_lead(tmp_path):
    db = make_db(tmp_path)
    rows = resolve_ticker(
        db,
        "HT",
        isin_searcher=lambda *a, **k: [],
        name_searcher=lambda *a, **k: [],
    )
    assert rows[0].state == "UNRESOLVED"
    assert rows[0].evidence_level == "D_RESEARCH_LEAD_REQUIRED"
    assert rows[0].disposition == "RESEARCH"
    assert "LLM" in rows[0].note


def test_name_fallback_can_be_disabled(tmp_path):
    db = make_db(tmp_path)
    rows = resolve_ticker(
        db,
        "HT",
        isin_searcher=lambda *a, **k: [],
        name_searcher=lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
        allow_name_fallback=False,
    )
    assert rows[0].state == "NO_ISIN_MAPPING"


def test_distinct_valid_isin_mappings_are_blocked_as_ambiguous(tmp_path):
    db = make_db(tmp_path)
    entity_id = "ISIN:HRHT00RA0005"
    db.upsert_entity_identifier(entity_id, "ISIN", "HRHT00RB0004", source_key="zse")
    def lookup(isin, **kwargs):
        if isin == "HRHT00RA0005":
            return [candidate(lei="097900BFHJ0000029454")]
        return [candidate(lei="549300TMC6BYESPQ7W85", name="Other d.d.")]
    rows = resolve_ticker(db, "HT", isin_searcher=lookup)
    assert rows[0].state == "ISIN_AMBIGUOUS"
    assert rows[0].disposition == "BLOCK"


def test_batch_deduplicates_tickers(tmp_path):
    db = make_db(tmp_path)
    rows = resolve_batch(db, ["HT", "ht", "HT"], isin_searcher=lambda *a, **k: [candidate()])
    assert len(rows) == 1


def test_manifest_records_evidence_ladder_and_no_auto_write(tmp_path):
    db = make_db(tmp_path)
    rows = resolve_ticker(db, "HT", isin_searcher=lambda *a, **k: [candidate()])
    target = write_manifest(rows, tmp_path / "manifest.json")
    text = target.read_text()
    assert '"automatic_identity_writes": false' in text
    assert '"A_OFFICIAL_ISIN_MAPPING"' in text
    assert '"D_RESEARCH_LEAD_REQUIRED"' in text
    assert "web/LLM findings require official corroboration" in text
