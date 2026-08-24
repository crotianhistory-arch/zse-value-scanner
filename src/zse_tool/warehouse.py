from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .storage import Database, utc_now


WAREHOUSE_SUBDIRS = (
    "raw/zse",
    "raw/esef",
    "raw/gleif",
    "raw/ted",
    "raw/macro/eurostat",
    "raw/macro/ecb",
    "raw/sec",
    "raw/news",
    "staging/entities",
    "staging/financials",
    "staging/contracts",
    "parquet/entities",
    "parquet/financials",
    "parquet/prices",
    "parquet/contracts",
    "parquet/macro",
    "manifests",
    "tmp",
)


SOURCE_SEEDS = (
    {
        "source_key": "zse_eho",
        "name": "Zagreb Stock Exchange / EHO",
        "authority": "Zagreb Stock Exchange",
        "base_url": "https://eho.zse.hr/",
        "source_kind": "official-exchange",
        "access_method": "JSON feeds + issuer documents",
        "notes": "Existing Croatian source used by the scanner.",
    },
    {
        "source_key": "esma_esef",
        "name": "European Single Electronic Format (ESEF)",
        "authority": "European Securities and Markets Authority",
        "base_url": "https://www.esma.europa.eu/issuer-disclosure/electronic-reporting",
        "source_kind": "official-standard",
        "access_method": "ESEF/iXBRL reports via national OAMs, exchanges/issuers; ESAP later",
        "notes": "Schema/format authority. Report discovery adapter remains to be implemented.",
    },
    {
        "source_key": "gleif",
        "name": "Global LEI Index / Golden Copy",
        "authority": "Global Legal Entity Identifier Foundation",
        "base_url": "https://www.gleif.org/en/lei-data/access-and-use-lei-data",
        "source_kind": "official-reference-data",
        "access_method": "API + bulk Golden Copy/delta files",
        "notes": "Entity identity, LEI reference data, identifier mappings and ownership relationships.",
    },
    {
        "source_key": "ted",
        "name": "Tenders Electronic Daily (TED)",
        "authority": "Publications Office of the European Union",
        "base_url": "https://docs.ted.europa.eu/api/latest/search.html",
        "source_kind": "official-procurement",
        "access_method": "Search API + notice XML downloads",
        "notes": "Published procurement notices; suitable for contract/tender research.",
    },
    {
        "source_key": "eurostat",
        "name": "Eurostat",
        "authority": "European Commission / Eurostat",
        "base_url": "https://ec.europa.eu/eurostat/web/user-guides/data-browser/api-data-access/api-introduction",
        "source_kind": "official-statistics",
        "access_method": "REST/SDMX APIs",
        "notes": "Industry, trade, labour, prices, production and macroeconomic series.",
    },
    {
        "source_key": "ecb",
        "name": "ECB Data Portal",
        "authority": "European Central Bank",
        "base_url": "https://data.ecb.europa.eu/",
        "source_kind": "official-statistics",
        "access_method": "SDMX data services",
        "notes": "Interest rates, monetary/financial and other ECB statistical series.",
    },
    {
        "source_key": "sec_edgar",
        "name": "SEC EDGAR structured data",
        "authority": "U.S. Securities and Exchange Commission",
        "base_url": "https://www.sec.gov/search-filings/edgar-application-programming-interfaces",
        "source_kind": "official-filings",
        "access_method": "data.sec.gov JSON APIs + bulk ZIP archives",
        "notes": "Optional U.S. comparable-company financial universe.",
    },
)


DATASET_SEEDS = (
    {
        "dataset_key": "zse_financial_reports",
        "source_key": "zse_eho",
        "name": "ZSE/EHO financial reports",
        "category": "financials",
        "format": "JSON metadata + XLS/XLSX/PDF",
        "storage_kind": "raw+sqlite",
        "update_policy": "incremental",
        "status": "active",
        "notes": "Current scanner source; kept separate from future external-company normalization.",
    },
    {
        "dataset_key": "eu_esef_annual_reports",
        "source_key": "esma_esef",
        "name": "EU listed-company ESEF annual reports",
        "category": "financials",
        "format": "XHTML/iXBRL + taxonomy packages",
        "storage_kind": "raw+normalized",
        "update_policy": "incremental",
        "status": "planned",
        "notes": "Discovery/collection adapter will be tested on a small peer universe before bulk ingestion.",
    },
    {
        "dataset_key": "gleif_lei_golden_copy",
        "source_key": "gleif",
        "name": "GLEIF LEI Golden Copy",
        "category": "entities",
        "format": "CSV/JSON/XML",
        "storage_kind": "raw+normalized",
        "update_policy": "snapshot+delta",
        "status": "planned",
        "notes": "Primary external entity-master feed for LEI/legal-name/relationship data.",
    },
    {
        "dataset_key": "ted_procurement_notices",
        "source_key": "ted",
        "name": "TED procurement notices",
        "category": "contracts",
        "format": "JSON metadata + XML notices",
        "storage_kind": "raw+normalized",
        "update_policy": "incremental",
        "status": "planned",
        "notes": "Contract/tender evidence; entity matching must remain evidence-based.",
    },
    {
        "dataset_key": "eurostat_series",
        "source_key": "eurostat",
        "name": "Eurostat industry and macro series",
        "category": "macro",
        "format": "SDMX/JSON-stat",
        "storage_kind": "raw+normalized",
        "update_policy": "incremental",
        "status": "planned",
        "notes": "Only selected series should be mirrored initially; avoid indiscriminate bulk downloads.",
    },
    {
        "dataset_key": "ecb_series",
        "source_key": "ecb",
        "name": "ECB financial and macro series",
        "category": "macro",
        "format": "SDMX",
        "storage_kind": "raw+normalized",
        "update_policy": "incremental",
        "status": "planned",
        "notes": "Interest-rate and monetary/financial variables for company exposure models.",
    },
    {
        "dataset_key": "sec_companyfacts",
        "source_key": "sec_edgar",
        "name": "SEC XBRL company facts",
        "category": "financials",
        "format": "JSON + bulk ZIP",
        "storage_kind": "raw+normalized",
        "update_policy": "snapshot+incremental",
        "status": "planned",
        "notes": "Optional U.S. comparable universe; not required for European peer work.",
    },
)


@dataclass(frozen=True, slots=True)
class WarehouseLayout:
    root: Path

    @property
    def raw(self) -> Path:
        return self.root / "raw"

    @property
    def staging(self) -> Path:
        return self.root / "staging"

    @property
    def parquet(self) -> Path:
        return self.root / "parquet"

    @property
    def manifests(self) -> Path:
        return self.root / "manifests"

    @property
    def tmp(self) -> Path:
        return self.root / "tmp"

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for rel in WAREHOUSE_SUBDIRS:
            (self.root / rel).mkdir(parents=True, exist_ok=True)

    def paths(self) -> dict[str, str]:
        return {
            "root": str(self.root),
            "raw": str(self.raw),
            "staging": str(self.staging),
            "parquet": str(self.parquet),
            "manifests": str(self.manifests),
            "tmp": str(self.tmp),
        }


def seed_source_registry(db: Database) -> None:
    for row in SOURCE_SEEDS:
        db.upsert_research_source(row)
    for row in DATASET_SEEDS:
        db.upsert_research_dataset(row)


def _clean_identifier(value: str | None) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _entity_id(*, lei: str | None = None, isin: str | None = None, ticker: str | None = None, exchange: str | None = None, name: str | None = None, country: str | None = None) -> str:
    lei = _clean_identifier(lei)
    isin = _clean_identifier(isin)
    ticker = _clean_identifier(ticker)
    exchange = _clean_identifier(exchange)
    if lei:
        return f"LEI:{lei.upper()}"
    if isin:
        return f"ISIN:{isin.upper()}"
    if ticker:
        return f"{(exchange or 'TICKER').upper()}:{ticker.upper()}"
    raw = f"{(country or '').upper()}|{(name or '').strip().casefold()}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    return f"LOCAL:{digest}"


def register_entity(
    db: Database,
    *,
    legal_name: str,
    country_code: str | None = None,
    lei: str | None = None,
    isin: str | None = None,
    ticker: str | None = None,
    exchange: str | None = None,
    source_key: str = "manual",
    entity_type: str = "company",
) -> dict[str, Any]:
    legal_name = str(legal_name).strip()
    if not legal_name:
        raise ValueError("legal_name is required")
    country_code = _clean_identifier(country_code)
    if country_code:
        country_code = country_code.upper()
    lei = _clean_identifier(lei)
    isin = _clean_identifier(isin)
    ticker = _clean_identifier(ticker)
    exchange = _clean_identifier(exchange)

    existing = None
    for scheme, value in (("LEI", lei), ("ISIN", isin), (f"TICKER:{(exchange or '').upper()}" if exchange else "TICKER", ticker)):
        if value:
            existing = db.research_entity_by_identifier(scheme, value)
            if existing:
                break
    entity_id = existing["entity_id"] if existing else _entity_id(
        lei=lei, isin=isin, ticker=ticker, exchange=exchange, name=legal_name, country=country_code
    )
    db.upsert_research_entity({
        "entity_id": entity_id,
        "legal_name": legal_name,
        "country_code": country_code,
        "entity_type": entity_type,
        "status": "active",
        "source_key": source_key,
    })
    if lei:
        db.upsert_entity_identifier(entity_id, "LEI", lei.upper(), source_key=source_key, is_primary=True)
    if isin:
        db.upsert_entity_identifier(entity_id, "ISIN", isin.upper(), source_key=source_key, is_primary=False)
    if ticker:
        scheme = f"TICKER:{exchange.upper()}" if exchange else "TICKER"
        db.upsert_entity_identifier(entity_id, scheme, ticker.upper(), source_key=source_key, is_primary=False)
    return db.research_entity_as_dict(entity_id)


def bootstrap_local_entities(db: Database) -> list[dict[str, Any]]:
    """Seed the entity master from information already present locally.

    No network access is performed. Security records contribute ISINs; company
    profiles contribute evidence-grounded legal names when no security name is
    available. Existing identifiers are reused so repeated runs are idempotent.
    """
    rows: dict[str, dict[str, Any]] = {}
    with db.connect() as conn:
        for r in conn.execute("SELECT ticker, isin, name FROM securities ORDER BY ticker"):
            t = str(r["ticker"]).upper()
            rows.setdefault(t, {}).update({"ticker": t, "isin": r["isin"], "name": r["name"]})
        for r in conn.execute(
            "SELECT cp.ticker, cp.legal_name FROM company_profiles cp "
            "JOIN (SELECT ticker, MAX(profile_date) d FROM company_profiles GROUP BY ticker) x "
            "ON x.ticker=cp.ticker AND x.d=cp.profile_date ORDER BY cp.ticker"
        ):
            t = str(r["ticker"]).upper()
            rows.setdefault(t, {}).update({"ticker": t, "profile_name": r["legal_name"]})
        for r in conn.execute("SELECT DISTINCT issuer_code, issuer_name FROM parsed_reports WHERE issuer_code IS NOT NULL"):
            t = str(r["issuer_code"]).upper()
            rows.setdefault(t, {}).setdefault("report_name", r["issuer_name"])

    out = []
    for ticker, r in sorted(rows.items()):
        name = r.get("name") or r.get("profile_name") or r.get("report_name") or ticker
        out.append(register_entity(
            db,
            legal_name=name,
            country_code="HR",
            isin=r.get("isin"),
            ticker=ticker,
            exchange="ZSE",
            source_key="local-zse",
        ))
    return out


def init_warehouse(db: Database, layout: WarehouseLayout, *, bootstrap_local: bool = False) -> dict[str, Any]:
    layout.ensure()
    seed_source_registry(db)
    entities = bootstrap_local_entities(db) if bootstrap_local else []
    return {
        "warehouse": layout.paths(),
        "sources": db.research_source_count(),
        "datasets": db.research_dataset_count(),
        "bootstrapped_entities": len(entities),
    }


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def warehouse_status(db: Database, layout: WarehouseLayout, *, data_dir: Path, db_path: Path) -> dict[str, Any]:
    paths = layout.paths()
    required = {rel: (layout.root / rel).is_dir() for rel in WAREHOUSE_SUBDIRS}
    optional = {
        "duckdb": importlib.util.find_spec("duckdb") is not None,
        "pyarrow": importlib.util.find_spec("pyarrow") is not None,
    }
    return {
        "data_dir": str(data_dir),
        "database": str(db_path),
        "warehouse": paths,
        "initialized": layout.root.exists() and all(required.values()),
        "directories": required,
        "bytes": {
            "raw": _dir_size(layout.raw),
            "staging": _dir_size(layout.staging),
            "parquet": _dir_size(layout.parquet),
        },
        "backend": {
            "metadata": "sqlite",
            "large_analytics": "duckdb+parquet" if optional["duckdb"] and optional["pyarrow"] else "optional-not-installed",
            "duckdb_available": optional["duckdb"],
            "pyarrow_available": optional["pyarrow"],
        },
        "counts": db.warehouse_counts(),
    }


def dataset_list(db: Database, *, category: str | None = None) -> list[dict[str, Any]]:
    return [dict(r) for r in db.research_datasets(category=category)]


def entity_lookup(db: Database, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
    return [db.research_entity_as_dict(r["entity_id"]) for r in db.research_entity_search(query, limit=limit)]
