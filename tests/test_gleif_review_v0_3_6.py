from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from zse_tool.gleif import GLEIFCandidate
from zse_tool.gleif_review import (
    _parse_tickers,
    review_batch,
    review_ticker,
    unidentified_zse_tickers,
    write_manifest,
)


class FakeDB:
    def __init__(self, tmp_path: Path):
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.path = tmp_path / "db.sqlite"
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE research_entities(
                entity_id TEXT PRIMARY KEY,
                legal_name TEXT NOT NULL,
                country_code TEXT
            );
            CREATE TABLE entity_identifiers(
                entity_id TEXT NOT NULL,
                scheme TEXT NOT NULL,
                value TEXT NOT NULL,
                PRIMARY KEY(scheme, value)
            );
            """
        )
        conn.commit()
        conn.close()

    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def add_entity(self, ticker: str, name: str, country: str = "HR", lei: str | None = None):
        entity_id = f"ENTITY:{ticker}"
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO research_entities(entity_id, legal_name, country_code) VALUES(?,?,?)",
                (entity_id, name, country),
            )
            conn.execute(
                "INSERT INTO entity_identifiers(entity_id, scheme, value) VALUES(?,?,?)",
                (entity_id, "TICKER:ZSE", ticker),
            )
            if lei:
                conn.execute(
                    "INSERT INTO entity_identifiers(entity_id, scheme, value) VALUES(?,?,?)",
                    (entity_id, "LEI", lei),
                )

    def research_entity_by_identifier(self, scheme: str, value: str):
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT e.*
                FROM research_entities e
                JOIN entity_identifiers i ON i.entity_id=e.entity_id
                WHERE i.scheme=? AND UPPER(i.value)=UPPER(?)
                LIMIT 1
                """,
                (scheme, value),
            ).fetchone()


def candidate(
    *,
    lei="549300TMC6BYESPQ7W85",
    name="PODRAVKA prehrambena industrija, d.d.",
    country="HR",
    registration="ISSUED",
    entity_status="ACTIVE",
    similarity=0.51,
    country_match=True,
):
    return GLEIFCandidate(
        lei=lei,
        legal_name=name,
        legal_address_country=country,
        jurisdiction=country,
        registration_status=registration,
        entity_status=entity_status,
        registration_authority_id=None,
        registration_authority_entity_id=None,
        query_name="PODRAVKA d.d.",
        query_country="HR",
        name_similarity=similarity,
        country_match=country_match,
        match_class="REVIEW",
        source_url=f"https://api.gleif.org/api/v1/lei-records/{lei}",
    )


def test_already_identified_skips_network(tmp_path):
    db = FakeDB(tmp_path)
    db.add_entity("KOEI", "KONČAR d.d.", lei="74780000H0SHMRAW0I15")
    called = False

    def searcher(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("network must be skipped")

    rows = review_ticker(db, "KOEI", searcher=searcher)
    assert called is False
    assert rows[0].state == "ALREADY_IDENTIFIED"
    assert rows[0].disposition == "SKIP"
    assert rows[0].existing_lei == "74780000H0SHMRAW0I15"


def test_candidate_passing_hard_gates_requires_review_not_auto_write(tmp_path):
    db = FakeDB(tmp_path)
    db.add_entity("PODR", "PODRAVKA d.d.")

    rows = review_ticker(db, "PODR", searcher=lambda *a, **k: [candidate()])
    row = rows[0]
    assert row.state == "CANDIDATE"
    assert row.disposition == "REVIEW_CONFIRM"
    assert row.confirmation_command.endswith(
        "--ticker PODR --lei 549300TMC6BYESPQ7W85 --yes-confirm --json"
    )


@pytest.mark.parametrize(
    "cand, expected",
    [
        (candidate(country="DE", country_match=False), "REJECT"),
        (candidate(registration="LAPSED"), "REJECT"),
        (candidate(entity_status="INACTIVE"), "REJECT"),
    ],
)
def test_hard_conflicts_are_rejected(tmp_path, cand, expected):
    db = FakeDB(tmp_path)
    db.add_entity("PODR", "PODRAVKA d.d.")
    row = review_ticker(db, "PODR", searcher=lambda *a, **k: [cand])[0]
    assert row.disposition == expected
    assert row.confirmation_command is None


def test_no_candidate_is_review_state(tmp_path):
    db = FakeDB(tmp_path)
    db.add_entity("HT", "HT d.d.")
    row = review_ticker(db, "HT", searcher=lambda *a, **k: [])[0]
    assert row.state == "NO_CANDIDATE"
    assert row.disposition == "REVIEW"


def test_batch_continues_for_unknown_entity_and_deduplicates(tmp_path):
    db = FakeDB(tmp_path)
    db.add_entity("HT", "HT d.d.")
    rows = review_batch(db, ["HT", "MISSING", "ht"], searcher=lambda *a, **k: [])
    assert [row.ticker for row in rows] == ["HT", "MISSING"]
    assert rows[1].state == "UNKNOWN_ENTITY"


def test_unidentified_query_excludes_entities_with_lei(tmp_path):
    db = FakeDB(tmp_path)
    db.add_entity("KOEI", "KONČAR d.d.", lei="74780000H0SHMRAW0I15")
    db.add_entity("HT", "HT d.d.")
    db.add_entity("GRNL", "Granolio d.d.")
    assert unidentified_zse_tickers(db) == ["GRNL", "HT"]


def test_manifest_records_read_only_policy(tmp_path):
    rows = [
        review_ticker(
            (lambda db: (db.add_entity("HT", "HT d.d."), db)[1])(FakeDB(tmp_path / "db")),
            "HT",
            searcher=lambda *a, **k: [],
        )[0]
    ]
    target = write_manifest(rows, tmp_path / "review" / "manifest.json")
    payload = json.loads(target.read_text())
    assert payload["mode"] == "read-only-gleif-review"
    assert payload["policy"]["automatic_identity_writes"] is False
    assert payload["policy"]["confirmation_required"] is True


def test_parse_tickers_normalizes_and_deduplicates():
    assert _parse_tickers(" ht,GRNL,ht ") == ["HT", "GRNL"]
