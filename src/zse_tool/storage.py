from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .models import EhoItem, Metric, ParsedReport

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS feed_items (
    source_id TEXT NOT NULL,
    variant TEXT NOT NULL,
    issuer_code TEXT,
    issuer_name TEXT,
    title TEXT,
    publish_date TEXT,
    item_link TEXT,
    raw_json TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY (source_id, variant)
);

CREATE TABLE IF NOT EXISTS documents (
    source_id TEXT NOT NULL,
    variant TEXT NOT NULL,
    url TEXT NOT NULL,
    document_type TEXT,
    local_path TEXT,
    sha256 TEXT,
    byte_size INTEGER,
    downloaded_at TEXT,
    content_status TEXT,
    content_note TEXT,
    PRIMARY KEY (source_id, variant, url),
    FOREIGN KEY (source_id, variant) REFERENCES feed_items(source_id, variant)
);

CREATE TABLE IF NOT EXISTS parsed_reports (
    local_path TEXT PRIMARY KEY,
    source_id TEXT,
    source_variant TEXT,
    source_url TEXT,
    source_publish_date TEXT,
    source_sha256 TEXT,
    issuer_code TEXT,
    issuer_name TEXT,
    period_start TEXT,
    period_end TEXT,
    year INTEGER,
    quarter INTEGER,
    consolidated INTEGER,
    audited INTEGER,
    currency TEXT,
    scale REAL,
    warnings_json TEXT,
    parsed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS facts (
    local_path TEXT NOT NULL,
    statement TEXT NOT NULL,
    adp_code INTEGER NOT NULL,
    label TEXT NOT NULL,
    column_name TEXT NOT NULL,
    value REAL,
    unit TEXT NOT NULL,
    source_sheet TEXT NOT NULL,
    source_row INTEGER NOT NULL,
    PRIMARY KEY (local_path, statement, adp_code, column_name, source_sheet),
    FOREIGN KEY (local_path) REFERENCES parsed_reports(local_path) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS metrics (
    local_path TEXT NOT NULL,
    issuer_code TEXT,
    period_end TEXT,
    metric_name TEXT NOT NULL,
    value REAL,
    unit TEXT NOT NULL,
    quality TEXT NOT NULL,
    note TEXT,
    PRIMARY KEY (local_path, metric_name),
    FOREIGN KEY (local_path) REFERENCES parsed_reports(local_path) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS securities (
    ticker TEXT PRIMARY KEY,
    isin TEXT NOT NULL,
    name TEXT,
    sector TEXT,
    listed_quantity INTEGER,
    nominal_value TEXT,
    listing_date TEXT,
    delisting_date TEXT,
    source_url TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_snapshots (
    ticker TEXT NOT NULL,
    isin TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    listed_quantity INTEGER,
    market_cap_eur REAL,
    implied_price_eur REAL,
    price_basis TEXT,
    quality TEXT NOT NULL,
    note TEXT,
    raw_json TEXT,
    PRIMARY KEY (ticker, observed_at)
);

CREATE TABLE IF NOT EXISTS classification_schemes (
    scheme TEXT NOT NULL,
    version TEXT NOT NULL,
    title TEXT NOT NULL,
    authority TEXT,
    source_url TEXT NOT NULL,
    language TEXT,
    effective_from TEXT,
    notes TEXT,
    PRIMARY KEY (scheme, version)
);

CREATE TABLE IF NOT EXISTS classification_codes (
    scheme TEXT NOT NULL,
    version TEXT NOT NULL,
    code TEXT NOT NULL,
    label TEXT NOT NULL,
    level TEXT,
    parent_code TEXT,
    language TEXT,
    source_url TEXT NOT NULL,
    PRIMARY KEY (scheme, version, code),
    FOREIGN KEY (scheme, version) REFERENCES classification_schemes(scheme, version) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS company_profiles (
    ticker TEXT NOT NULL,
    profile_date TEXT NOT NULL,
    legal_name TEXT NOT NULL,
    summary TEXT,
    method TEXT NOT NULL,
    confidence REAL NOT NULL,
    source_url TEXT NOT NULL,
    source_date TEXT NOT NULL,
    notes TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (ticker, profile_date)
);

CREATE TABLE IF NOT EXISTS company_activities (
    ticker TEXT NOT NULL,
    profile_date TEXT NOT NULL,
    activity_key TEXT NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    weight REAL,
    weight_basis TEXT,
    method TEXT NOT NULL,
    confidence REAL NOT NULL,
    source_url TEXT NOT NULL,
    source_date TEXT NOT NULL,
    evidence TEXT NOT NULL,
    valid_from TEXT,
    valid_to TEXT,
    PRIMARY KEY (ticker, profile_date, activity_key),
    FOREIGN KEY (ticker, profile_date) REFERENCES company_profiles(ticker, profile_date) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS activity_classifications (
    ticker TEXT NOT NULL,
    profile_date TEXT NOT NULL,
    activity_key TEXT NOT NULL,
    scheme TEXT NOT NULL,
    version TEXT NOT NULL,
    code TEXT NOT NULL,
    assignment_status TEXT NOT NULL,
    method TEXT NOT NULL,
    confidence REAL NOT NULL,
    source_url TEXT NOT NULL,
    source_date TEXT NOT NULL,
    evidence TEXT NOT NULL,
    PRIMARY KEY (ticker, profile_date, activity_key, scheme, version, code),
    FOREIGN KEY (ticker, profile_date, activity_key) REFERENCES company_activities(ticker, profile_date, activity_key) ON DELETE CASCADE,
    FOREIGN KEY (scheme, version, code) REFERENCES classification_codes(scheme, version, code)
);

CREATE TABLE IF NOT EXISTS company_segments (
    ticker TEXT NOT NULL,
    profile_date TEXT NOT NULL,
    segment_key TEXT NOT NULL,
    name TEXT NOT NULL,
    period_end TEXT NOT NULL,
    revenue_eur REAL,
    revenue_share REAL,
    measure TEXT,
    source_url TEXT NOT NULL,
    source_date TEXT NOT NULL,
    evidence TEXT NOT NULL,
    PRIMARY KEY (ticker, profile_date, segment_key),
    FOREIGN KEY (ticker, profile_date) REFERENCES company_profiles(ticker, profile_date) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS company_products (
    ticker TEXT NOT NULL,
    profile_date TEXT NOT NULL,
    product_key TEXT NOT NULL,
    name TEXT NOT NULL,
    segment_key TEXT,
    source_url TEXT NOT NULL,
    source_date TEXT NOT NULL,
    evidence TEXT NOT NULL,
    PRIMARY KEY (ticker, profile_date, product_key),
    FOREIGN KEY (ticker, profile_date) REFERENCES company_profiles(ticker, profile_date) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS company_geographies (
    ticker TEXT NOT NULL,
    profile_date TEXT NOT NULL,
    geography_key TEXT NOT NULL,
    name TEXT NOT NULL,
    period_end TEXT NOT NULL,
    revenue_eur REAL,
    revenue_share REAL,
    source_url TEXT NOT NULL,
    source_date TEXT NOT NULL,
    evidence TEXT NOT NULL,
    PRIMARY KEY (ticker, profile_date, geography_key),
    FOREIGN KEY (ticker, profile_date) REFERENCES company_profiles(ticker, profile_date) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS company_capacities (
    ticker TEXT NOT NULL,
    profile_date TEXT NOT NULL,
    capacity_key TEXT NOT NULL,
    name TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    segment_key TEXT,
    source_url TEXT NOT NULL,
    source_date TEXT NOT NULL,
    evidence TEXT NOT NULL,
    PRIMARY KEY (ticker, profile_date, capacity_key),
    FOREIGN KEY (ticker, profile_date) REFERENCES company_profiles(ticker, profile_date) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS company_subsidiaries (
    ticker TEXT NOT NULL,
    profile_date TEXT NOT NULL,
    subsidiary_key TEXT NOT NULL,
    name TEXT NOT NULL,
    ownership_pct REAL,
    control TEXT,
    activity TEXT,
    source_url TEXT NOT NULL,
    source_date TEXT NOT NULL,
    evidence TEXT NOT NULL,
    PRIMARY KEY (ticker, profile_date, subsidiary_key),
    FOREIGN KEY (ticker, profile_date) REFERENCES company_profiles(ticker, profile_date) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS research_sources (
    source_key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    authority TEXT,
    base_url TEXT,
    source_kind TEXT NOT NULL,
    access_method TEXT,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_datasets (
    dataset_key TEXT PRIMARY KEY,
    source_key TEXT NOT NULL,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    format TEXT,
    storage_kind TEXT NOT NULL,
    update_policy TEXT,
    status TEXT NOT NULL,
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (source_key) REFERENCES research_sources(source_key)
);

CREATE TABLE IF NOT EXISTS research_entities (
    entity_id TEXT PRIMARY KEY,
    legal_name TEXT NOT NULL,
    country_code TEXT,
    entity_type TEXT NOT NULL,
    status TEXT NOT NULL,
    source_key TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entity_identifiers (
    entity_id TEXT NOT NULL,
    scheme TEXT NOT NULL,
    value TEXT NOT NULL,
    source_key TEXT,
    is_primary INTEGER NOT NULL DEFAULT 0,
    valid_from TEXT,
    valid_to TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (scheme, value),
    FOREIGN KEY (entity_id) REFERENCES research_entities(entity_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS dataset_versions (
    dataset_key TEXT NOT NULL,
    version_id TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    source_url TEXT,
    local_path TEXT,
    sha256 TEXT,
    byte_size INTEGER,
    row_count INTEGER,
    status TEXT NOT NULL,
    notes TEXT,
    PRIMARY KEY (dataset_key, version_id),
    FOREIGN KEY (dataset_key) REFERENCES research_datasets(dataset_key) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ingestion_jobs (
    job_id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_key TEXT NOT NULL,
    run_key TEXT NOT NULL,
    state TEXT NOT NULL,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    cursor_json TEXT,
    items_seen INTEGER NOT NULL DEFAULT 0,
    items_downloaded INTEGER NOT NULL DEFAULT 0,
    items_skipped INTEGER NOT NULL DEFAULT 0,
    items_failed INTEGER NOT NULL DEFAULT 0,
    bytes_downloaded INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    UNIQUE(dataset_key, run_key),
    FOREIGN KEY (dataset_key) REFERENCES research_datasets(dataset_key) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS raw_artifacts (
    artifact_id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_key TEXT NOT NULL,
    entity_id TEXT,
    source_url TEXT NOT NULL,
    publication_date TEXT,
    retrieved_at TEXT NOT NULL,
    local_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    byte_size INTEGER,
    media_type TEXT,
    parser_status TEXT NOT NULL DEFAULT 'unparsed',
    metadata_json TEXT,
    UNIQUE(dataset_key, source_url, sha256),
    FOREIGN KEY (dataset_key) REFERENCES research_datasets(dataset_key) ON DELETE CASCADE,
    FOREIGN KEY (entity_id) REFERENCES research_entities(entity_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS external_financial_facts (
    fact_id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id TEXT NOT NULL,
    dataset_key TEXT NOT NULL,
    source_artifact_id INTEGER,
    period_start TEXT,
    period_end TEXT NOT NULL,
    fiscal_year INTEGER,
    period_type TEXT,
    consolidated INTEGER,
    audited INTEGER,
    taxonomy TEXT,
    concept TEXT NOT NULL,
    label TEXT,
    statement TEXT,
    value REAL,
    unit TEXT,
    currency TEXT,
    reported_at TEXT,
    quality TEXT NOT NULL DEFAULT 'raw',
    context_key TEXT,
    metadata_json TEXT,
    UNIQUE(entity_id, dataset_key, period_end, concept, context_key, source_artifact_id),
    FOREIGN KEY (entity_id) REFERENCES research_entities(entity_id) ON DELETE CASCADE,
    FOREIGN KEY (dataset_key) REFERENCES research_datasets(dataset_key) ON DELETE CASCADE,
    FOREIGN KEY (source_artifact_id) REFERENCES raw_artifacts(artifact_id) ON DELETE SET NULL
);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# SQL fragments are kept centralized so the reports view and metrics view use
# exactly the same definition of "preferred report".
_PERIOD_KEY_SQL = """
CASE
  WHEN p.period_end IS NOT NULL AND p.period_end <> '' THEN p.period_end
  WHEN p.year IS NOT NULL AND p.quarter IN (1,2,3,4)
       THEN printf('%04d-Q%d', p.year, p.quarter)
  WHEN p.year IS NOT NULL THEN printf('%04d-FY', p.year)
  ELSE p.local_path
END
"""

_PREFERENCE_ORDER_SQL = """
CASE p.consolidated WHEN 1 THEN 3 WHEN 0 THEN 1 ELSE 2 END DESC,
COALESCE(f.publish_date, '') DESC,
CASE p.audited WHEN 1 THEN 2 ELSE 1 END DESC,
COALESCE(p.parsed_at, '') DESC,
p.local_path DESC
"""


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        # SQLite foreign-key enforcement is connection-local.  Enable it on
        # every connection so cleanup cannot leave orphan facts/metrics.
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            self._migrate_schema(conn)

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, declaration: str) -> None:
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        # v0.1.5 adds immutable report provenance and persistent content status.
        # ALTER TABLE keeps all existing downloaded documents/facts/metrics intact.
        for column in ("content_status", "content_note"):
            self._ensure_column(conn, "documents", column, "TEXT")
        for column in ("source_variant", "source_url", "source_publish_date", "source_sha256"):
            self._ensure_column(conn, "parsed_reports", column, "TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_variant_url ON documents(variant, url)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_local_path ON documents(local_path)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_parsed_reports_source_id ON parsed_reports(source_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_market_snapshots_ticker_time ON market_snapshots(ticker, observed_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_securities_isin ON securities(isin)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_classification_codes_lookup ON classification_codes(scheme, version, code)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_company_profiles_ticker_date ON company_profiles(ticker, profile_date DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_company_activities_ticker_date ON company_activities(ticker, profile_date DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_classifications_code ON activity_classifications(scheme, version, code)")

    def upsert_items(self, items: Iterable[EhoItem]) -> int:
        """Upsert EHO metadata and repair legacy ``unknown-N`` associations.

        Before v0.1.5 fallback IDs were list positions and could collide between
        sync windows.  New EHO items use deterministic IDs.  When the same
        document URL is already stored under a legacy ID, preserve its downloaded
        file/hash and move its parsed-report provenance to the new stable item.
        """
        now = utc_now()
        count = 0
        with self.connect() as conn:
            for item in items:
                conn.execute(
                    """
                    INSERT INTO feed_items (
                        source_id, variant, issuer_code, issuer_name, title,
                        publish_date, item_link, raw_json, first_seen_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_id, variant) DO UPDATE SET
                        issuer_code=excluded.issuer_code,
                        issuer_name=excluded.issuer_name,
                        title=excluded.title,
                        publish_date=excluded.publish_date,
                        item_link=excluded.item_link,
                        raw_json=excluded.raw_json,
                        last_seen_at=excluded.last_seen_at
                    """,
                    (
                        item.source_id, item.variant, item.issuer_code, item.issuer_name,
                        item.title, item.publish_date, item.item_link,
                        json.dumps(item.raw, ensure_ascii=False, sort_keys=True), now, now,
                    ),
                )

                for url in item.document_urls:
                    dtype = document_type_from_url(url)
                    existing = conn.execute(
                        """
                        SELECT * FROM documents
                        WHERE variant=? AND url=?
                        ORDER BY CASE WHEN source_id=? THEN 0 ELSE 1 END,
                                 CASE WHEN local_path IS NOT NULL THEN 0 ELSE 1 END,
                                 COALESCE(downloaded_at, '') DESC
                        """,
                        (item.variant, url, item.source_id),
                    ).fetchall()
                    downloaded = next((r for r in existing if r["local_path"]), None)

                    conn.execute(
                        """
                        INSERT INTO documents(
                            source_id, variant, url, document_type, local_path, sha256,
                            byte_size, downloaded_at, content_status, content_note
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(source_id, variant, url) DO UPDATE SET
                            document_type=excluded.document_type,
                            local_path=COALESCE(documents.local_path, excluded.local_path),
                            sha256=COALESCE(documents.sha256, excluded.sha256),
                            byte_size=COALESCE(documents.byte_size, excluded.byte_size),
                            downloaded_at=COALESCE(documents.downloaded_at, excluded.downloaded_at),
                            content_status=COALESCE(documents.content_status, excluded.content_status),
                            content_note=COALESCE(documents.content_note, excluded.content_note)
                        """,
                        (
                            item.source_id, item.variant, url, dtype,
                            downloaded["local_path"] if downloaded else None,
                            downloaded["sha256"] if downloaded else None,
                            downloaded["byte_size"] if downloaded else None,
                            downloaded["downloaded_at"] if downloaded else None,
                            downloaded["content_status"] if downloaded else None,
                            downloaded["content_note"] if downloaded else None,
                        ),
                    )

                    # Repair only the old positional IDs.  Stable/native IDs are
                    # never merged just because two EHO items happen to share a URL.
                    for old in existing:
                        old_id = str(old["source_id"] or "")
                        if old_id == item.source_id or not re.fullmatch(r"unknown-\d+", old_id):
                            continue
                        if old["local_path"]:
                            conn.execute(
                                """
                                UPDATE parsed_reports
                                SET source_id=?, source_variant=?, source_url=?,
                                    source_publish_date=?,
                                    source_sha256=COALESCE(?, source_sha256)
                                WHERE local_path=?
                                """,
                                (
                                    item.source_id, item.variant, url, item.publish_date,
                                    old["sha256"], old["local_path"],
                                ),
                            )
                        conn.execute(
                            "DELETE FROM documents WHERE source_id=? AND variant=? AND url=?",
                            (old_id, item.variant, url),
                        )
                count += 1

            # Remove only legacy feed rows that no longer own any documents.
            candidates = conn.execute(
                "SELECT source_id, variant FROM feed_items WHERE source_id LIKE 'unknown-%'"
            ).fetchall()
            for row in candidates:
                if not re.fullmatch(r"unknown-\d+", str(row["source_id"])):
                    continue
                used = conn.execute(
                    "SELECT 1 FROM documents WHERE source_id=? AND variant=? LIMIT 1",
                    (row["source_id"], row["variant"]),
                ).fetchone()
                if not used:
                    conn.execute(
                        "DELETE FROM feed_items WHERE source_id=? AND variant=?",
                        (row["source_id"], row["variant"]),
                    )
        return count

    def pending_documents(
        self,
        *,
        ticker: str | None = None,
        document_types: tuple[str, ...] = ("xlsx",),
        limit: int = 20,
    ) -> list[sqlite3.Row]:
        placeholders = ",".join("?" for _ in document_types)
        sql = f"""
            SELECT d.*, f.issuer_code, f.publish_date
            FROM documents d
            JOIN feed_items f USING(source_id, variant)
            WHERE d.local_path IS NULL
              AND d.document_type IN ({placeholders})
        """
        params: list[object] = list(document_types)
        if ticker:
            sql += " AND UPPER(f.issuer_code) = ?"
            params.append(ticker.upper())
        sql += " ORDER BY f.publish_date DESC LIMIT ?"
        params.append(limit)
        with self.connect() as conn:
            return conn.execute(sql, params).fetchall()

    def mark_downloaded(
        self,
        *,
        source_id: str,
        variant: str,
        url: str,
        local_path: Path,
        sha256: str,
        byte_size: int,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE documents
                SET local_path=?, sha256=?, byte_size=?, downloaded_at=?
                WHERE source_id=? AND variant=? AND url=?
                """,
                (str(local_path), sha256, byte_size, utc_now(), source_id, variant, url),
            )

    def mark_document_content_status(
        self, *, source_id: str, variant: str, url: str, status: str, note: str | None = None
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE documents SET content_status=?, content_note=?
                WHERE source_id=? AND variant=? AND url=?
                """,
                (status, note, source_id, variant, url),
            )

    def downloaded_xlsx(self, ticker: str | None = None) -> list[sqlite3.Row]:
        # Deduplicate by local file path and skip files already proven not to be
        # valid XLSX containers.  A historical EHO URL can occasionally carry
        # an .xlsx suffix while serving non-XLSX content.
        sql = """
            WITH candidates AS (
                SELECT d.*, f.issuer_code, f.issuer_name, f.publish_date,
                       ROW_NUMBER() OVER (
                           PARTITION BY d.local_path
                           ORDER BY CASE WHEN d.source_id LIKE 'auto-%' THEN 0
                                         WHEN d.source_id LIKE 'unknown-%' THEN 2 ELSE 1 END,
                                    COALESCE(f.publish_date, '') DESC, d.source_id
                       ) AS path_rank
                FROM documents d JOIN feed_items f USING(source_id, variant)
                WHERE d.local_path IS NOT NULL AND d.document_type='xlsx'
                  AND COALESCE(d.content_status, '') <> 'invalid'
        """
        params: list[object] = []
        if ticker:
            sql += " AND UPPER(f.issuer_code)=?"
            params.append(ticker.upper())
        sql += """
            )
            SELECT * FROM candidates WHERE path_rank=1
            ORDER BY publish_date DESC, local_path
        """
        with self.connect() as conn:
            return conn.execute(sql, params).fetchall()

    @staticmethod
    def _delete_parsed_report_path(conn: sqlite3.Connection, local_path: str) -> None:
        # Delete children explicitly as well as relying on FK cascades.  This is
        # safe for databases created before v0.1.6, when some connections did
        # not have PRAGMA foreign_keys enabled.
        conn.execute("DELETE FROM facts WHERE local_path=?", (local_path,))
        conn.execute("DELETE FROM metrics WHERE local_path=?", (local_path,))
        conn.execute("DELETE FROM parsed_reports WHERE local_path=?", (local_path,))

    def cleanup_report_integrity(self, ticker: str | None = None) -> dict[str, int]:
        """Remove only provably stale parsed-report rows.

        Safe repair rules:
        1. Parsed reports whose local_path is no longer referenced by any
           downloaded document are stale migration leftovers.
        2. If several parsed rows claim the exact same stable source_id + URL,
           keep the row referenced by the current document record (otherwise
           the newest parsed row) and remove the rest.

        Original files on disk and EHO metadata are never deleted.
        """
        removed_orphans = 0
        removed_duplicates = 0
        with self.connect() as conn:
            params: list[object] = []
            ticker_sql = ""
            if ticker:
                ticker_sql = " AND UPPER(p.issuer_code)=?"
                params.append(ticker.upper())

            orphan_rows = conn.execute(
                f"""
                SELECT p.local_path
                FROM parsed_reports p
                WHERE NOT EXISTS (
                    SELECT 1 FROM documents d WHERE d.local_path=p.local_path
                )
                {ticker_sql}
                """,
                params,
            ).fetchall()
            for row in orphan_rows:
                self._delete_parsed_report_path(conn, row["local_path"])
                removed_orphans += 1

            # Re-evaluate after orphan removal.  Duplicate stable provenance is
            # not useful history: one EHO URL identifies one downloaded report.
            group_params: list[object] = []
            group_ticker_sql = ""
            if ticker:
                group_ticker_sql = " AND UPPER(issuer_code)=?"
                group_params.append(ticker.upper())
            groups = conn.execute(
                f"""
                SELECT source_id, COALESCE(source_variant, 'financialReports') AS variant,
                       source_url, COUNT(*) AS n
                FROM parsed_reports
                WHERE source_id IS NOT NULL AND source_url IS NOT NULL
                  {group_ticker_sql}
                GROUP BY source_id, COALESCE(source_variant, 'financialReports'), source_url
                HAVING COUNT(*) > 1
                """,
                group_params,
            ).fetchall()
            for group in groups:
                rows = conn.execute(
                    """
                    SELECT p.*,
                           EXISTS(SELECT 1 FROM documents d WHERE d.local_path=p.local_path) AS referenced
                    FROM parsed_reports p
                    WHERE p.source_id=?
                      AND COALESCE(p.source_variant, 'financialReports')=?
                      AND p.source_url=?
                    ORDER BY referenced DESC, COALESCE(p.parsed_at, '') DESC, p.local_path DESC
                    """,
                    (group["source_id"], group["variant"], group["source_url"]),
                ).fetchall()
                for row in rows[1:]:
                    self._delete_parsed_report_path(conn, row["local_path"])
                    removed_duplicates += 1

        return {
            "removed_orphan_reports": removed_orphans,
            "removed_duplicate_reports": removed_duplicates,
            "removed_total": removed_orphans + removed_duplicates,
        }

    def integrity_report(self, ticker: str | None = None) -> dict:
        """Return database-integrity diagnostics without modifying data."""
        ticker_u = ticker.upper() if ticker else None
        with self.connect() as conn:
            parsed_filter = "" if not ticker_u else " AND UPPER(p.issuer_code)=?"
            parsed_params: list[object] = [] if not ticker_u else [ticker_u]
            doc_filter = "" if not ticker_u else " AND UPPER(f.issuer_code)=?"
            doc_params: list[object] = [] if not ticker_u else [ticker_u]

            orphan_rows = conn.execute(
                f"""
                SELECT p.local_path, p.source_id, p.source_url
                FROM parsed_reports p
                WHERE NOT EXISTS (SELECT 1 FROM documents d WHERE d.local_path=p.local_path)
                {parsed_filter}
                ORDER BY p.local_path
                """, parsed_params
            ).fetchall()

            duplicate_reports = conn.execute(
                f"""
                SELECT p.source_id, COALESCE(p.source_variant, 'financialReports') AS variant,
                       p.source_url, COUNT(*) AS count
                FROM parsed_reports p
                WHERE p.source_id IS NOT NULL AND p.source_url IS NOT NULL
                {parsed_filter}
                GROUP BY p.source_id, COALESCE(p.source_variant, 'financialReports'), p.source_url
                HAVING COUNT(*) > 1
                ORDER BY count DESC, p.source_id
                """, parsed_params
            ).fetchall()

            duplicate_urls = conn.execute(
                f"""
                SELECT d.variant, d.url, COUNT(*) AS count
                FROM documents d JOIN feed_items f USING(source_id, variant)
                WHERE 1=1 {doc_filter}
                GROUP BY d.variant, d.url HAVING COUNT(*) > 1
                ORDER BY count DESC, d.url
                """, doc_params
            ).fetchall()

            duplicate_hashes = conn.execute(
                f"""
                SELECT d.sha256, COUNT(*) AS count, GROUP_CONCAT(DISTINCT d.url) AS urls
                FROM documents d JOIN feed_items f USING(source_id, variant)
                WHERE d.sha256 IS NOT NULL AND d.sha256 <> '' {doc_filter}
                GROUP BY d.sha256 HAVING COUNT(*) > 1
                ORDER BY count DESC, d.sha256
                """, doc_params
            ).fetchall()

            invalid_docs = conn.execute(
                f"""
                SELECT d.source_id, d.url, d.content_note
                FROM documents d JOIN feed_items f USING(source_id, variant)
                WHERE d.content_status='invalid' {doc_filter}
                ORDER BY f.publish_date DESC, d.url
                """, doc_params
            ).fetchall()

            unparsed = conn.execute(
                f"""
                SELECT d.source_id, d.url, d.local_path
                FROM documents d JOIN feed_items f USING(source_id, variant)
                WHERE d.document_type='xlsx' AND d.local_path IS NOT NULL
                  AND COALESCE(d.content_status, '') <> 'invalid'
                  AND NOT EXISTS (SELECT 1 FROM parsed_reports p WHERE p.local_path=d.local_path)
                  {doc_filter}
                ORDER BY f.publish_date DESC, d.url
                """, doc_params
            ).fetchall()

        return {
            "ticker": ticker_u,
            "ok": not orphan_rows and not duplicate_reports and not duplicate_urls and not unparsed,
            "orphan_parsed_reports": [dict(r) for r in orphan_rows],
            "duplicate_parsed_sources": [dict(r) for r in duplicate_reports],
            "duplicate_document_urls": [dict(r) for r in duplicate_urls],
            # Same-content files can be legitimate historical EHO quirks, so
            # hash duplicates are informational and do not make ok=False.
            "duplicate_document_hashes": [dict(r) for r in duplicate_hashes],
            "invalid_documents": [dict(r) for r in invalid_docs],
            "unparsed_valid_xlsx": [dict(r) for r in unparsed],
        }

    def save_parsed_report(
        self,
        parsed: ParsedReport,
        *,
        source_id: str | None = None,
        source_variant: str | None = None,
        source_url: str | None = None,
        source_publish_date: str | None = None,
        source_sha256: str | None = None,
        issuer_code: str | None = None,
        metrics: Iterable[Metric] = (),
    ) -> None:
        path = str(parsed.source_path.resolve())
        with self.connect() as conn:
            # A stable EHO source+URL must map to one parsed row.  v0.1.5
            # migrations could leave an older copy under a legacy directory.
            if source_id and source_url:
                stale = conn.execute(
                    """
                    SELECT local_path FROM parsed_reports
                    WHERE source_id=?
                      AND COALESCE(source_variant, 'financialReports')=?
                      AND source_url=? AND local_path<>?
                    """,
                    (source_id, source_variant or "financialReports", source_url, path),
                ).fetchall()
                for row in stale:
                    self._delete_parsed_report_path(conn, row["local_path"])
            conn.execute("DELETE FROM facts WHERE local_path=?", (path,))
            conn.execute("DELETE FROM metrics WHERE local_path=?", (path,))
            conn.execute(
                """
                INSERT INTO parsed_reports(
                    local_path, source_id, source_variant, source_url,
                    source_publish_date, source_sha256, issuer_code, issuer_name, period_start,
                    period_end, year, quarter, consolidated, audited, currency,
                    scale, warnings_json, parsed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(local_path) DO UPDATE SET
                    source_id=excluded.source_id,
                    source_variant=excluded.source_variant,
                    source_url=excluded.source_url,
                    source_publish_date=excluded.source_publish_date,
                    source_sha256=excluded.source_sha256,
                    issuer_code=excluded.issuer_code,
                    issuer_name=excluded.issuer_name,
                    period_start=excluded.period_start,
                    period_end=excluded.period_end,
                    year=excluded.year,
                    quarter=excluded.quarter,
                    consolidated=excluded.consolidated,
                    audited=excluded.audited,
                    currency=excluded.currency,
                    scale=excluded.scale,
                    warnings_json=excluded.warnings_json,
                    parsed_at=excluded.parsed_at
                """,
                (
                    path,
                    source_id,
                    source_variant,
                    source_url,
                    source_publish_date,
                    source_sha256,
                    issuer_code,
                    parsed.issuer_name,
                    parsed.period_start.isoformat() if parsed.period_start else None,
                    parsed.period_end.isoformat() if parsed.period_end else None,
                    parsed.year,
                    parsed.quarter,
                    None if parsed.consolidated is None else int(parsed.consolidated),
                    None if parsed.audited is None else int(parsed.audited),
                    parsed.currency,
                    parsed.scale,
                    json.dumps(parsed.warnings, ensure_ascii=False),
                    utc_now(),
                ),
            )
            conn.executemany(
                """
                INSERT INTO facts(
                    local_path, statement, adp_code, label, column_name, value,
                    unit, source_sheet, source_row
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        path,
                        fact.statement,
                        fact.adp_code,
                        fact.label,
                        fact.column_name,
                        fact.value,
                        fact.unit,
                        fact.source_sheet,
                        fact.source_row,
                    )
                    for fact in parsed.facts
                ],
            )
            conn.executemany(
                """
                INSERT INTO metrics(
                    local_path, issuer_code, period_end, metric_name, value,
                    unit, quality, note
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        path,
                        issuer_code,
                        parsed.period_end.isoformat() if parsed.period_end else None,
                        metric.name,
                        metric.value,
                        metric.unit,
                        metric.quality,
                        metric.note,
                    )
                    for metric in metrics
                ],
            )

    def report_metrics(self, local_path: str) -> list[sqlite3.Row]:
        """Return all stored base metrics for one parsed report."""
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM metrics WHERE local_path=? ORDER BY metric_name",
                (str(local_path),),
            ).fetchall()

    def upsert_security(self, security, *, source_url: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO securities(
                    ticker, isin, name, sector, listed_quantity, nominal_value,
                    listing_date, delisting_date, source_url, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    isin=excluded.isin,
                    name=excluded.name,
                    sector=excluded.sector,
                    listed_quantity=excluded.listed_quantity,
                    nominal_value=excluded.nominal_value,
                    listing_date=excluded.listing_date,
                    delisting_date=excluded.delisting_date,
                    source_url=excluded.source_url,
                    updated_at=excluded.updated_at
                """,
                (
                    security.ticker.upper(), security.isin.upper(), security.name, security.sector,
                    security.listed_quantity, security.nominal_value, security.listing_date,
                    security.delisting_date, source_url, utc_now(),
                ),
            )

    def save_market_snapshot(self, snapshot: dict) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO market_snapshots(
                    ticker, isin, observed_at, source_url, source_kind, listed_quantity,
                    market_cap_eur, implied_price_eur, price_basis, quality, note, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(snapshot["ticker"]).upper(), str(snapshot["isin"]).upper(),
                    snapshot["observed_at"], snapshot["source_url"], snapshot["source_kind"],
                    snapshot.get("listed_quantity"), snapshot.get("market_cap_eur"),
                    snapshot.get("implied_price_eur"), snapshot.get("price_basis"),
                    snapshot.get("quality") or "unknown", snapshot.get("note"), snapshot.get("raw_json"),
                ),
            )

    def latest_market_snapshot(self, ticker: str):
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT * FROM market_snapshots
                WHERE UPPER(ticker)=?
                ORDER BY observed_at DESC
                LIMIT 1
                """,
                (ticker.upper(),),
            ).fetchone()

    def market_history(self, ticker: str, limit: int = 20):
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT * FROM market_snapshots
                WHERE UPPER(ticker)=?
                ORDER BY observed_at DESC
                LIMIT ?
                """,
                (ticker.upper(), int(limit)),
            ).fetchall()

    def upsert_classification_scheme(self, scheme: dict) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO classification_schemes(
                    scheme, version, title, authority, source_url, language,
                    effective_from, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scheme, version) DO UPDATE SET
                    title=excluded.title,
                    authority=excluded.authority,
                    source_url=excluded.source_url,
                    language=excluded.language,
                    effective_from=excluded.effective_from,
                    notes=excluded.notes
                """,
                (
                    str(scheme["scheme"]).upper(), str(scheme["version"]), scheme["title"],
                    scheme.get("authority"), scheme["source_url"], scheme.get("language"),
                    scheme.get("effective_from"), scheme.get("notes"),
                ),
            )

    def upsert_classification_code(self, code: dict) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO classification_codes(
                    scheme, version, code, label, level, parent_code, language, source_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scheme, version, code) DO UPDATE SET
                    label=excluded.label,
                    level=excluded.level,
                    parent_code=excluded.parent_code,
                    language=excluded.language,
                    source_url=excluded.source_url
                """,
                (
                    str(code["scheme"]).upper(), str(code["version"]), str(code["code"]),
                    code["label"], code.get("level"), code.get("parent_code"),
                    code.get("language"), code["source_url"],
                ),
            )

    def classification_code_exists(self, scheme: str, version: str, code: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM classification_codes WHERE scheme=? AND version=? AND code=?",
                (scheme.upper(), str(version), str(code)),
            ).fetchone()
            return row is not None

    def classification_codes(self, scheme: str | None = None, version: str | None = None, query: str | None = None):
        sql = "SELECT * FROM classification_codes WHERE 1=1"
        params: list[object] = []
        if scheme:
            sql += " AND scheme=?"
            params.append(scheme.upper())
        if version:
            sql += " AND version=?"
            params.append(str(version))
        if query:
            sql += " AND (LOWER(code) LIKE ? OR LOWER(label) LIKE ?)"
            q = f"%{query.lower()}%"
            params.extend([q, q])
        sql += " ORDER BY scheme, version, code"
        with self.connect() as conn:
            return conn.execute(sql, params).fetchall()

    def classification_schemes(self):
        with self.connect() as conn:
            return conn.execute("SELECT * FROM classification_schemes ORDER BY scheme, version").fetchall()

    def save_company_profile_bundle(self, payload: dict) -> None:
        profile = payload["profile"]
        ticker = str(profile["ticker"]).upper()
        profile_date = str(profile["profile_date"])
        with self.connect() as conn:
            # Re-importing the same dated profile is an explicit replacement of
            # that evidence snapshot; older/newer profile dates remain intact.
            conn.execute(
                "INSERT INTO company_profiles(ticker, profile_date, legal_name, summary, method, confidence, source_url, source_date, notes, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(ticker, profile_date) DO UPDATE SET legal_name=excluded.legal_name, summary=excluded.summary, "
                "method=excluded.method, confidence=excluded.confidence, source_url=excluded.source_url, source_date=excluded.source_date, "
                "notes=excluded.notes, created_at=excluded.created_at",
                (
                    ticker, profile_date, profile["legal_name"], profile.get("summary"), profile["method"],
                    float(profile["confidence"]), profile["source_url"], profile["source_date"],
                    profile.get("notes"), utc_now(),
                ),
            )
            for table in (
                "activity_classifications", "company_activities", "company_segments", "company_products",
                "company_geographies", "company_capacities", "company_subsidiaries",
            ):
                conn.execute(f"DELETE FROM {table} WHERE ticker=? AND profile_date=?", (ticker, profile_date))

            for a in payload.get("activities", []):
                conn.execute(
                    """
                    INSERT INTO company_activities(
                        ticker, profile_date, activity_key, name, role, weight, weight_basis,
                        method, confidence, source_url, source_date, evidence, valid_from, valid_to
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ticker, profile_date, a["key"], a["name"], a["role"], a.get("weight"), a.get("weight_basis"),
                        a["method"], float(a["confidence"]), a["source_url"], a["source_date"], a["evidence"],
                        a.get("valid_from"), a.get("valid_to"),
                    ),
                )
                for c in a.get("classifications", []):
                    conn.execute(
                        """
                        INSERT INTO activity_classifications(
                            ticker, profile_date, activity_key, scheme, version, code, assignment_status,
                            method, confidence, source_url, source_date, evidence
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            ticker, profile_date, a["key"], c["scheme"].upper(), str(c["version"]), str(c["code"]),
                            c["assignment_status"], c["method"], float(c["confidence"]), c["source_url"],
                            c["source_date"], c["evidence"],
                        ),
                    )

            for r in payload.get("segments", []):
                conn.execute(
                    "INSERT INTO company_segments(ticker, profile_date, segment_key, name, period_end, revenue_eur, revenue_share, measure, source_url, source_date, evidence) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (ticker, profile_date, r["key"], r["name"], r["period_end"], r.get("revenue_eur"), r.get("revenue_share"),
                     r.get("measure"), r["source_url"], r["source_date"], r["evidence"]),
                )
            for r in payload.get("products", []):
                conn.execute(
                    "INSERT INTO company_products(ticker, profile_date, product_key, name, segment_key, source_url, source_date, evidence) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (ticker, profile_date, r["key"], r["name"], r.get("segment_key"), r["source_url"], r["source_date"], r["evidence"]),
                )
            for r in payload.get("geographies", []):
                conn.execute(
                    "INSERT INTO company_geographies(ticker, profile_date, geography_key, name, period_end, revenue_eur, revenue_share, source_url, source_date, evidence) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (ticker, profile_date, r["key"], r["name"], r["period_end"], r.get("revenue_eur"), r.get("revenue_share"),
                     r["source_url"], r["source_date"], r["evidence"]),
                )
            for r in payload.get("capacities", []):
                conn.execute(
                    "INSERT INTO company_capacities(ticker, profile_date, capacity_key, name, value, unit, segment_key, source_url, source_date, evidence) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (ticker, profile_date, r["key"], r["name"], float(r["value"]), r["unit"], r.get("segment_key"),
                     r["source_url"], r["source_date"], r["evidence"]),
                )
            for r in payload.get("subsidiaries", []):
                conn.execute(
                    "INSERT INTO company_subsidiaries(ticker, profile_date, subsidiary_key, name, ownership_pct, control, activity, source_url, source_date, evidence) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (ticker, profile_date, r["key"], r["name"], r.get("ownership_pct"), r.get("control"), r.get("activity"),
                     r["source_url"], r["source_date"], r["evidence"]),
                )

    def company_profile(self, ticker: str, *, as_of: str | None = None):
        sql = "SELECT * FROM company_profiles WHERE UPPER(ticker)=?"
        params: list[object] = [ticker.upper()]
        if as_of:
            sql += " AND profile_date<=?"
            params.append(as_of)
        sql += " ORDER BY profile_date DESC LIMIT 1"
        with self.connect() as conn:
            return conn.execute(sql, params).fetchone()

    def _profile_date(self, ticker: str, profile_date: str | None = None) -> str | None:
        if profile_date:
            return profile_date
        row = self.company_profile(ticker)
        return row["profile_date"] if row else None

    def company_activities(self, ticker: str, *, profile_date: str | None = None):
        profile_date = self._profile_date(ticker, profile_date)
        if not profile_date:
            return []
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM company_activities WHERE UPPER(ticker)=? AND profile_date=? "
                "ORDER BY CASE WHEN weight IS NULL THEN 1 ELSE 0 END, weight DESC, activity_key",
                (ticker.upper(), profile_date),
            ).fetchall()

    def activity_classifications(self, ticker: str, profile_date: str, activity_key: str, *, scheme: str | None = None):
        sql = "SELECT ac.*, cc.label, cc.level FROM activity_classifications ac JOIN classification_codes cc "
        sql += "ON cc.scheme=ac.scheme AND cc.version=ac.version AND cc.code=ac.code "
        sql += "WHERE UPPER(ac.ticker)=? AND ac.profile_date=? AND ac.activity_key=?"
        params: list[object] = [ticker.upper(), profile_date, activity_key]
        if scheme:
            sql += " AND ac.scheme=?"
            params.append(scheme.upper())
        sql += " ORDER BY ac.scheme, ac.version, ac.code"
        with self.connect() as conn:
            return conn.execute(sql, params).fetchall()

    def _profile_rows(self, table: str, ticker: str, profile_date: str | None, order_by: str):
        profile_date = self._profile_date(ticker, profile_date)
        if not profile_date:
            return []
        with self.connect() as conn:
            return conn.execute(
                f"SELECT * FROM {table} WHERE UPPER(ticker)=? AND profile_date=? ORDER BY {order_by}",
                (ticker.upper(), profile_date),
            ).fetchall()

    def company_segments(self, ticker: str, *, profile_date: str | None = None):
        return self._profile_rows("company_segments", ticker, profile_date, "COALESCE(revenue_share, -1) DESC, segment_key")

    def company_products(self, ticker: str, *, profile_date: str | None = None):
        return self._profile_rows("company_products", ticker, profile_date, "segment_key, product_key")

    def company_geographies(self, ticker: str, *, profile_date: str | None = None):
        return self._profile_rows("company_geographies", ticker, profile_date, "COALESCE(revenue_share, -1) DESC, geography_key")

    def company_capacities(self, ticker: str, *, profile_date: str | None = None):
        return self._profile_rows("company_capacities", ticker, profile_date, "segment_key, capacity_key")

    def company_subsidiaries(self, ticker: str, *, profile_date: str | None = None):
        return self._profile_rows("company_subsidiaries", ticker, profile_date, "subsidiary_key")

    def profiled_tickers(self) -> list[str]:
        with self.connect() as conn:
            return [r[0] for r in conn.execute("SELECT DISTINCT UPPER(ticker) FROM company_profiles ORDER BY 1")]

    def company_profile_history(self, ticker: str):
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM company_profiles WHERE UPPER(ticker)=? ORDER BY profile_date DESC",
                (ticker.upper(),),
            ).fetchall()

    def latest_preferred_metric_value(self, ticker: str, metric_name: str) -> float | None:
        rows = self.latest_metrics(ticker)
        for row in rows:
            if row["metric_name"] == metric_name and row["value"] is not None:
                return float(row["value"])
        return None

    # --- Research warehouse metadata (v0.3.3) ---
    def upsert_research_source(self, row: dict) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO research_sources(source_key, name, authority, base_url, source_kind, access_method, notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_key) DO UPDATE SET
                    name=excluded.name, authority=excluded.authority, base_url=excluded.base_url,
                    source_kind=excluded.source_kind, access_method=excluded.access_method,
                    notes=excluded.notes, updated_at=excluded.updated_at
                """,
                (row["source_key"], row["name"], row.get("authority"), row.get("base_url"), row["source_kind"],
                 row.get("access_method"), row.get("notes"), now, now),
            )

    def upsert_research_dataset(self, row: dict) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO research_datasets(dataset_key, source_key, name, category, format, storage_kind, update_policy, status, notes, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(dataset_key) DO UPDATE SET
                    source_key=excluded.source_key, name=excluded.name, category=excluded.category,
                    format=excluded.format, storage_kind=excluded.storage_kind, update_policy=excluded.update_policy,
                    status=excluded.status, notes=excluded.notes, updated_at=excluded.updated_at
                """,
                (row["dataset_key"], row["source_key"], row["name"], row["category"], row.get("format"),
                 row["storage_kind"], row.get("update_policy"), row["status"], row.get("notes"), now, now),
            )

    def research_source_count(self) -> int:
        with self.connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM research_sources").fetchone()[0])

    def research_dataset_count(self) -> int:
        with self.connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM research_datasets").fetchone()[0])

    def research_datasets(self, *, category: str | None = None):
        sql = """
            SELECT d.*, s.authority, s.base_url,
                   (SELECT COUNT(*) FROM dataset_versions v WHERE v.dataset_key=d.dataset_key) AS versions,
                   (SELECT COUNT(*) FROM raw_artifacts a WHERE a.dataset_key=d.dataset_key) AS artifacts,
                   (SELECT COUNT(*) FROM ingestion_jobs j WHERE j.dataset_key=d.dataset_key) AS jobs
            FROM research_datasets d JOIN research_sources s USING(source_key)
        """
        params: list[object] = []
        if category:
            sql += " WHERE LOWER(d.category)=LOWER(?)"
            params.append(category)
        sql += " ORDER BY d.category, d.dataset_key"
        with self.connect() as conn:
            return conn.execute(sql, params).fetchall()

    def upsert_research_entity(self, row: dict) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO research_entities(entity_id, legal_name, country_code, entity_type, status, source_key, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(entity_id) DO UPDATE SET
                    legal_name=excluded.legal_name, country_code=COALESCE(excluded.country_code, research_entities.country_code),
                    entity_type=excluded.entity_type, status=excluded.status, source_key=COALESCE(excluded.source_key, research_entities.source_key),
                    updated_at=excluded.updated_at
                """,
                (row["entity_id"], row["legal_name"], row.get("country_code"), row.get("entity_type", "company"),
                 row.get("status", "active"), row.get("source_key"), now, now),
            )

    def upsert_entity_identifier(self, entity_id: str, scheme: str, value: str, *, source_key: str | None = None, is_primary: bool = False, valid_from: str | None = None, valid_to: str | None = None) -> None:
        with self.connect() as conn:
            existing = conn.execute("SELECT entity_id FROM entity_identifiers WHERE scheme=? AND value=?", (scheme.upper(), value)).fetchone()
            if existing and existing["entity_id"] != entity_id:
                raise ValueError(f"identifier {scheme}:{value} already belongs to {existing['entity_id']}")
            conn.execute(
                """
                INSERT INTO entity_identifiers(entity_id, scheme, value, source_key, is_primary, valid_from, valid_to, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scheme, value) DO UPDATE SET
                    source_key=COALESCE(excluded.source_key, entity_identifiers.source_key),
                    is_primary=MAX(entity_identifiers.is_primary, excluded.is_primary),
                    valid_from=COALESCE(excluded.valid_from, entity_identifiers.valid_from),
                    valid_to=excluded.valid_to
                """,
                (entity_id, scheme.upper(), value, source_key, int(bool(is_primary)), valid_from, valid_to, utc_now()),
            )

    def research_entity_by_identifier(self, scheme: str, value: str):
        with self.connect() as conn:
            return conn.execute(
                "SELECT e.* FROM research_entities e JOIN entity_identifiers i USING(entity_id) WHERE i.scheme=? AND UPPER(i.value)=UPPER(?) LIMIT 1",
                (scheme.upper(), value),
            ).fetchone()

    def research_entity_as_dict(self, entity_id: str) -> dict:
        with self.connect() as conn:
            e = conn.execute("SELECT * FROM research_entities WHERE entity_id=?", (entity_id,)).fetchone()
            if e is None:
                raise ValueError(f"unknown entity_id {entity_id}")
            ids = conn.execute("SELECT scheme, value, source_key, is_primary, valid_from, valid_to FROM entity_identifiers WHERE entity_id=? ORDER BY scheme, value", (entity_id,)).fetchall()
        out = dict(e)
        out["identifiers"] = [dict(r) for r in ids]
        return out

    def research_entity_search(self, query: str, *, limit: int = 20):
        q = f"%{query.strip()}%"
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT DISTINCT e.* FROM research_entities e
                LEFT JOIN entity_identifiers i USING(entity_id)
                WHERE e.legal_name LIKE ? COLLATE NOCASE OR i.value LIKE ? COLLATE NOCASE OR e.entity_id LIKE ? COLLATE NOCASE
                ORDER BY e.legal_name LIMIT ?
                """, (q, q, q, int(limit))
            ).fetchall()

    def save_dataset_version(self, row: dict) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO dataset_versions(dataset_key, version_id, retrieved_at, source_url, local_path, sha256, byte_size, row_count, status, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(dataset_key, version_id) DO UPDATE SET
                    retrieved_at=excluded.retrieved_at, source_url=excluded.source_url, local_path=excluded.local_path,
                    sha256=excluded.sha256, byte_size=excluded.byte_size, row_count=excluded.row_count,
                    status=excluded.status, notes=excluded.notes
                """,
                (row["dataset_key"], row["version_id"], row.get("retrieved_at") or utc_now(), row.get("source_url"),
                 row.get("local_path"), row.get("sha256"), row.get("byte_size"), row.get("row_count"),
                 row.get("status", "complete"), row.get("notes")),
            )

    def start_ingestion_job(self, dataset_key: str, run_key: str, *, cursor: dict | None = None) -> dict:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO ingestion_jobs(dataset_key, run_key, state, started_at, updated_at, cursor_json)
                VALUES (?, ?, 'running', ?, ?, ?)
                ON CONFLICT(dataset_key, run_key) DO UPDATE SET
                    state=CASE WHEN ingestion_jobs.state='complete' THEN ingestion_jobs.state ELSE 'running' END,
                    updated_at=excluded.updated_at,
                    cursor_json=COALESCE(ingestion_jobs.cursor_json, excluded.cursor_json),
                    last_error=CASE WHEN ingestion_jobs.state='complete' THEN ingestion_jobs.last_error ELSE NULL END
                """,
                (dataset_key, run_key, now, now, json.dumps(cursor) if cursor is not None else None),
            )
            row = conn.execute("SELECT * FROM ingestion_jobs WHERE dataset_key=? AND run_key=?", (dataset_key, run_key)).fetchone()
        return dict(row)

    def update_ingestion_job(self, job_id: int, *, cursor: dict | None = None, items_seen: int | None = None, items_downloaded: int | None = None, items_skipped: int | None = None, items_failed: int | None = None, bytes_downloaded: int | None = None, state: str | None = None, last_error: str | None = None) -> None:
        fields = ["updated_at=?"]
        params: list[object] = [utc_now()]
        mapping = {
            "items_seen": items_seen, "items_downloaded": items_downloaded, "items_skipped": items_skipped,
            "items_failed": items_failed, "bytes_downloaded": bytes_downloaded, "state": state, "last_error": last_error,
        }
        if cursor is not None:
            fields.append("cursor_json=?")
            params.append(json.dumps(cursor))
        for name, value in mapping.items():
            if value is not None:
                fields.append(f"{name}=?")
                params.append(value)
        params.append(job_id)
        with self.connect() as conn:
            conn.execute(f"UPDATE ingestion_jobs SET {', '.join(fields)} WHERE job_id=?", params)

    def finish_ingestion_job(self, job_id: int, *, success: bool = True, last_error: str | None = None) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                "UPDATE ingestion_jobs SET state=?, updated_at=?, completed_at=?, last_error=? WHERE job_id=?",
                ("complete" if success else "failed", now, now, last_error, job_id),
            )

    def ingestion_jobs(self, dataset_key: str | None = None, *, limit: int = 20):
        sql = "SELECT * FROM ingestion_jobs"
        params: list[object] = []
        if dataset_key:
            sql += " WHERE dataset_key=?"
            params.append(dataset_key)
        sql += " ORDER BY job_id DESC LIMIT ?"
        params.append(int(limit))
        with self.connect() as conn:
            return conn.execute(sql, params).fetchall()

    def register_raw_artifact(self, row: dict) -> int:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO raw_artifacts(dataset_key, entity_id, source_url, publication_date, retrieved_at, local_path, sha256, byte_size, media_type, parser_status, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(dataset_key, source_url, sha256) DO UPDATE SET
                    entity_id=COALESCE(excluded.entity_id, raw_artifacts.entity_id), publication_date=COALESCE(excluded.publication_date, raw_artifacts.publication_date),
                    local_path=excluded.local_path, byte_size=COALESCE(excluded.byte_size, raw_artifacts.byte_size),
                    media_type=COALESCE(excluded.media_type, raw_artifacts.media_type), parser_status=excluded.parser_status,
                    metadata_json=COALESCE(excluded.metadata_json, raw_artifacts.metadata_json)
                """,
                (row["dataset_key"], row.get("entity_id"), row["source_url"], row.get("publication_date"),
                 row.get("retrieved_at") or utc_now(), row["local_path"], row["sha256"], row.get("byte_size"),
                 row.get("media_type"), row.get("parser_status", "unparsed"), json.dumps(row.get("metadata")) if row.get("metadata") is not None else None),
            )
            found = conn.execute("SELECT artifact_id FROM raw_artifacts WHERE dataset_key=? AND source_url=? AND sha256=?", (row["dataset_key"], row["source_url"], row["sha256"])).fetchone()
            return int(found[0])

    def save_external_financial_fact(self, row: dict) -> int:
        context_key = row.get("context_key") or ""
        source_artifact_id = row.get("source_artifact_id")
        with self.connect() as conn:
            existing = conn.execute(
                "SELECT fact_id FROM external_financial_facts WHERE entity_id=? AND dataset_key=? AND period_end=? AND concept=? AND context_key=? AND source_artifact_id IS ?",
                (row["entity_id"], row["dataset_key"], row["period_end"], row["concept"], context_key, source_artifact_id),
            ).fetchone()
            if existing:
                fact_id = int(existing[0])
                conn.execute(
                    "UPDATE external_financial_facts SET value=?, unit=?, currency=?, label=?, statement=?, quality=?, metadata_json=? WHERE fact_id=?",
                    (row.get("value"), row.get("unit"), row.get("currency"), row.get("label"), row.get("statement"), row.get("quality", "raw"), json.dumps(row.get("metadata")) if row.get("metadata") is not None else None, fact_id),
                )
                return fact_id
            cur = conn.execute(
                """
                INSERT INTO external_financial_facts(entity_id, dataset_key, source_artifact_id, period_start, period_end, fiscal_year, period_type, consolidated, audited, taxonomy, concept, label, statement, value, unit, currency, reported_at, quality, context_key, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (row["entity_id"], row["dataset_key"], source_artifact_id, row.get("period_start"), row["period_end"], row.get("fiscal_year"), row.get("period_type"),
                 row.get("consolidated"), row.get("audited"), row.get("taxonomy"), row["concept"], row.get("label"), row.get("statement"), row.get("value"), row.get("unit"),
                 row.get("currency"), row.get("reported_at"), row.get("quality", "raw"), context_key, json.dumps(row.get("metadata")) if row.get("metadata") is not None else None),
            )
            return int(cur.lastrowid)

    def warehouse_counts(self) -> dict[str, int]:
        tables = ("research_sources", "research_datasets", "research_entities", "entity_identifiers", "dataset_versions", "ingestion_jobs", "raw_artifacts", "external_financial_facts")
        with self.connect() as conn:
            return {t: int(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]) for t in tables}

    def stats(self) -> dict[str, int]:
        tables = ("feed_items", "documents", "parsed_reports", "facts", "metrics", "securities", "market_snapshots", "classification_schemes", "classification_codes", "company_profiles", "company_activities", "activity_classifications", "company_segments", "company_products", "company_geographies", "company_capacities", "company_subsidiaries", "research_sources", "research_datasets", "research_entities", "entity_identifiers", "dataset_versions", "ingestion_jobs", "raw_artifacts", "external_financial_facts")
        with self.connect() as conn:
            return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}

    def report_inventory(self, ticker: str | None = None, *, preferred_only: bool = False) -> list[sqlite3.Row]:
        """Return parsed reports with deterministic preferred-report ranking.

        One preferred report is selected per issuer/reporting period. Group
        analysis prefers consolidated over unknown over unconsolidated scope;
        within the same scope it prefers the newest EHO publication and uses
        audited status as a tie-breaker. This makes later corrections naturally supersede
        older versions without deleting history.
        """
        sql = f"""
        WITH base AS (
            SELECT p.*,
                   COALESCE(p.source_url, (
                       SELECT d.url FROM documents d
                       WHERE d.local_path=p.local_path LIMIT 1
                   )) AS document_url,
                   COALESCE(p.source_publish_date, f.publish_date) AS publish_date,
                   f.title AS feed_title,
                   {_PERIOD_KEY_SQL} AS period_key
            FROM parsed_reports p
            LEFT JOIN feed_items f
              ON f.source_id=p.source_id
             AND f.variant=COALESCE(p.source_variant, 'financialReports')
            WHERE 1=1
        """
        params: list[object] = []
        if ticker:
            sql += " AND UPPER(p.issuer_code)=?"
            params.append(ticker.upper())
        sql += f"""
        ), ranked AS (
            SELECT base.*,
                   ROW_NUMBER() OVER (
                     PARTITION BY COALESCE(issuer_code, ''), period_key
                     ORDER BY
                       CASE consolidated WHEN 1 THEN 3 WHEN 0 THEN 1 ELSE 2 END DESC,
                       COALESCE(publish_date, '') DESC,
                       CASE audited WHEN 1 THEN 2 ELSE 1 END DESC,
                       COALESCE(parsed_at, '') DESC,
                       local_path DESC
                   ) AS preference_rank,
                   COUNT(*) OVER (
                     PARTITION BY COALESCE(issuer_code, ''), period_key
                   ) AS period_candidates
            FROM base
        )
        SELECT * FROM ranked
        """
        if preferred_only:
            sql += " WHERE preference_rank=1"
        sql += " ORDER BY COALESCE(period_end, period_key) DESC, preference_rank ASC, publish_date DESC"
        with self.connect() as conn:
            return conn.execute(sql, params).fetchall()

    def latest_metrics(self, ticker: str | None = None) -> list[sqlite3.Row]:
        """Latest metrics using only the preferred report for each period.

        This prevents an unconsolidated report from silently winning merely
        because its filename sorts after the consolidated report.
        """
        sql = f"""
        WITH report_base AS (
            SELECT p.*,
                   COALESCE(p.source_publish_date, f.publish_date) AS publish_date,
                   {_PERIOD_KEY_SQL} AS period_key
            FROM parsed_reports p
            LEFT JOIN feed_items f
              ON f.source_id=p.source_id
             AND f.variant=COALESCE(p.source_variant, 'financialReports')
            WHERE 1=1
        """
        params: list[object] = []
        if ticker:
            sql += " AND UPPER(p.issuer_code)=?"
            params.append(ticker.upper())
        sql += f"""
        ), preferred_reports AS (
            SELECT report_base.*,
                   ROW_NUMBER() OVER (
                     PARTITION BY COALESCE(issuer_code, ''), period_key
                     ORDER BY
                       CASE consolidated WHEN 1 THEN 3 WHEN 0 THEN 1 ELSE 2 END DESC,
                       COALESCE(publish_date, '') DESC,
                       CASE audited WHEN 1 THEN 2 ELSE 1 END DESC,
                       COALESCE(parsed_at, '') DESC,
                       local_path DESC
                   ) AS report_rank
            FROM report_base
        ), preferred_metrics AS (
            SELECT m.*, pr.year, pr.quarter, pr.consolidated, pr.audited,
                   ROW_NUMBER() OVER (
                     PARTITION BY COALESCE(m.issuer_code, ''), m.metric_name
                     ORDER BY COALESCE(m.period_end, pr.period_key, '') DESC,
                              COALESCE(pr.publish_date, '') DESC,
                              m.local_path DESC
                   ) AS metric_rank
            FROM metrics m
            JOIN preferred_reports pr ON pr.local_path=m.local_path
            WHERE pr.report_rank=1
        )
        SELECT * FROM preferred_metrics
        WHERE metric_rank=1
        ORDER BY issuer_code, metric_name
        """
        with self.connect() as conn:
            return conn.execute(sql, params).fetchall()


def document_type_from_url(url: str) -> str:
    path = url.lower().split("?", 1)[0]
    for ext in ("xlsx", "xls", "pdf", "zip", "xhtml", "html", "xml"):
        if path.endswith("." + ext):
            return ext
    return "unknown"
