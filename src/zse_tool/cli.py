from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
import zipfile
from pathlib import Path

from .analytics import build_trends, build_ttm_snapshot
from .company_intelligence import (
    build_peer_candidates,
    ensure_bundled_taxonomies,
    import_profile_file,
    profile_as_dict,
    profile_history,
    profile_quality,
    seed_all_bundled_profiles,
    seed_bundled_profile,
    validate_profile_file,
)
from .config import Settings
from .eho import EhoClient, pretty_item
from .errors import ZseToolError
from .http_client import RespectfulHttpClient, safe_filename_from_url
from .llm import OllamaManager, OllamaSchemaMapper, detect_nvidia_gpus
from .metrics import calculate_metrics
from .market import ZSE_SHARES_URL, ZseMarketClient
from .parsers import parse_xlsx_report
from .reporting import describe_report
from .storage import Database
from .validation import validate_report
from .valuation import build_debt_snapshot, build_md_comparison, build_valuation
from .warehouse import (
    WarehouseLayout,
    dataset_list,
    entity_lookup,
    init_warehouse,
    register_entity,
    seed_source_registry,
    warehouse_status,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="zse-tool", description="ZSE/EHO private research tool")
    p.add_argument("--data-dir", default=None, help="Data directory (default: ./data or ZSE_DATA_DIR)")
    llm_group = p.add_mutually_exclusive_group()
    llm_group.add_argument("--use-llm", dest="use_llm", action="store_true", help="Enable optional local Ollama fallback")
    llm_group.add_argument("--no-llm", dest="use_llm", action="store_false", help="Force deterministic mode; never call Ollama")
    p.set_defaults(use_llm=None)
    sp = p.add_subparsers(dest="cmd", required=True)

    a = sp.add_parser("probe", help="Small live access test against official EHO JSON feeds")
    a.add_argument("--ticker", default="KOEI")

    a = sp.add_parser("list", help="List EHO feed items without downloading documents")
    a.add_argument("--variant", default="financialReports")
    a.add_argument("--ticker")
    a.add_argument("--date-from")
    a.add_argument("--date-to")
    a.add_argument("--limit", type=int, default=10)
    a.add_argument("--raw", action="store_true")

    a = sp.add_parser("sync", help="Save EHO metadata into SQLite; no report download")
    a.add_argument("--variant", default="financialReports")
    a.add_argument("--ticker")
    a.add_argument("--date-from")
    a.add_argument("--date-to")

    a = sp.add_parser("download", help="Download only documents already discovered by sync")
    a.add_argument("--ticker")
    a.add_argument("--types", default="xlsx", help="Comma-separated: xlsx,pdf,...")
    a.add_argument("--limit", type=int, default=5)
    a.add_argument("--max-mb", type=float, default=25.0)

    a = sp.add_parser("parse", help="Parse downloaded XLSX files and save facts + metrics")
    a.add_argument("--ticker")

    a = sp.add_parser("inspect-xlsx", help="Parse a local XLSX without database/network")
    a.add_argument("file")
    a.add_argument("--json", action="store_true")

    sp.add_parser("db-stats", help="Show local database counts")

    a = sp.add_parser("metrics", help="Show latest calculated metrics from preferred reports")
    a.add_argument("--ticker")

    a = sp.add_parser("reports", help="Show parsed report inventory and preferred-report selection")
    a.add_argument("--ticker")
    a.add_argument("--preferred-only", action="store_true")
    a.add_argument("--json", action="store_true")

    a = sp.add_parser("coverage", help="Show historical preferred-report coverage by year")
    a.add_argument("--ticker", required=True)
    a.add_argument("--json", action="store_true")

    a = sp.add_parser("integrity", help="Check/repair duplicate and orphan database records")
    a.add_argument("--ticker")
    a.add_argument("--repair", action="store_true", help="Remove only provably stale/duplicate parsed-report rows")
    a.add_argument("--json", action="store_true")

    a = sp.add_parser("ttm", help="Calculate true trailing-12-month metrics from preferred historical reports")
    a.add_argument("--ticker", required=True)
    a.add_argument("--as-of", help="Reporting period such as 2026-Q2 or 2025-FY; default: latest")
    a.add_argument("--json", action="store_true")

    a = sp.add_parser("trends", help="Show annual financial history, latest TTM and growth rates")
    a.add_argument("--ticker", required=True)
    a.add_argument("--years", type=int, default=7, help="Annual history rows to show (default: 7)")
    a.add_argument("--json", action="store_true")

    a = sp.add_parser("market-sync", help="Fetch/store current official ZSE listed quantity and market cap")
    a.add_argument("--ticker", required=True)
    a.add_argument("--isin", help="Optional ISIN override if a symbol maps to multiple instruments")
    a.add_argument("--json", action="store_true")

    a = sp.add_parser("market", help="Show stored official ZSE market snapshot(s)")
    a.add_argument("--ticker", required=True)
    a.add_argument("--history", type=int, default=1, help="Number of stored snapshots to show (default: 1)")
    a.add_argument("--json", action="store_true")

    a = sp.add_parser("valuation", help="Combine latest official ZSE market cap with TTM financials")
    a.add_argument("--ticker", required=True)
    a.add_argument("--as-of", help="Financial period such as 2026-Q1 or 2025-FY; market snapshot remains current")
    a.add_argument("--json", action="store_true")

    a = sp.add_parser("debt", help="Show standardized debt decomposition and MojeDionice EV debt construction")
    a.add_argument("--ticker", required=True)
    a.add_argument("--as-of", help="Financial period such as 2026-Q1 or 2025-FY; default: latest")
    a.add_argument("--json", action="store_true")

    a = sp.add_parser("compare-md", help="Show scanner metrics aligned to MojeDionice-style definitions for selected periods")
    a.add_argument("--ticker", required=True)
    a.add_argument("--periods", required=True, help="Comma-separated periods, e.g. 2026-Q1,2025-FY,2024-FY")
    a.add_argument("--json", action="store_true")

    a = sp.add_parser("parity", help="Check MojeDionice financial-summary field coverage for selected periods")
    a.add_argument("--ticker", required=True)
    a.add_argument("--periods", required=True, help="Comma-separated periods, e.g. 2026-Q1,2025-FY,2024-FY")
    a.add_argument("--json", action="store_true")

    a = sp.add_parser("taxonomy", help="List/search cached official activity-classification codes")
    a.add_argument("--scheme", help="Classification scheme, e.g. NACE or NKD")
    a.add_argument("--version", help="Scheme version, e.g. 2.1 or 2025")
    a.add_argument("--query", help="Search code or label")
    a.add_argument("--json", action="store_true")

    a = sp.add_parser("profile-seed", help="Load a bundled evidence-grounded company profile into SQLite")
    a.add_argument("--ticker", required=True, help="Ticker or ALL to seed every bundled profile")
    a.add_argument("--json", action="store_true")

    a = sp.add_parser("profile-validate", help="Validate a company-intelligence JSON file without writing it")
    a.add_argument("file")
    a.add_argument("--json", action="store_true")

    a = sp.add_parser("profile-import", help="Validate and import a company-intelligence JSON file")
    a.add_argument("file")
    a.add_argument("--json", action="store_true")

    a = sp.add_parser("company-profile", help="Show evidence-grounded activities, segments, products and exposures")
    a.add_argument("--ticker", required=True)
    a.add_argument("--as-of", help="Profile date cutoff, YYYY-MM-DD")
    a.add_argument("--json", action="store_true")

    a = sp.add_parser("activities", help="Show material activities and official taxonomy mappings")
    a.add_argument("--ticker", required=True)
    a.add_argument("--json", action="store_true")

    a = sp.add_parser("segments", help="Show latest stored operating-segment revenue mix")
    a.add_argument("--ticker", required=True)
    a.add_argument("--json", action="store_true")


    a = sp.add_parser("profile-history", help="Show dated company-intelligence profile versions")
    a.add_argument("--ticker", required=True)
    a.add_argument("--json", action="store_true")

    a = sp.add_parser("profile-quality", help="Show company-profile freshness and evidence completeness")
    a.add_argument("--ticker", required=True)
    a.add_argument("--as-of", help="Profile date cutoff, YYYY-MM-DD")
    a.add_argument("--json", action="store_true")

    a = sp.add_parser("peer-candidates", help="Rank deterministic business, business-model or investment peer candidates")
    a.add_argument("--ticker", required=True)
    a.add_argument(
        "--type", default="business",
        choices=("business", "product", "business-model", "model", "investment", "all"),
        help="Peer meaning: product/business activity, cross-industry business model, investment characteristics, or all",
    )
    a.add_argument("--limit", type=int, default=10)
    a.add_argument("--eligible-only", action="store_true", help="Hide rejected/insufficient candidates")
    a.add_argument("--json", action="store_true")

    a = sp.add_parser("warehouse-init", help="Initialize the local research-warehouse layout and source registry")
    a.add_argument("--bootstrap-local", action="store_true", help="Seed entity master from already-stored ZSE securities/profiles")
    a.add_argument("--json", action="store_true")

    a = sp.add_parser("warehouse-status", help="Show warehouse paths, backend availability, sizes and metadata counts")
    a.add_argument("--json", action="store_true")

    a = sp.add_parser("dataset-list", help="List registered research datasets and ingestion state")
    a.add_argument("--category", help="Optional category filter, e.g. entities, financials, contracts, macro")
    a.add_argument("--json", action="store_true")

    a = sp.add_parser("entity-register", help="Register/merge one entity in the local research entity master")
    a.add_argument("--name", required=True, help="Legal/company name")
    a.add_argument("--country", help="ISO country code, e.g. HR, DE")
    a.add_argument("--lei")
    a.add_argument("--isin")
    a.add_argument("--ticker")
    a.add_argument("--exchange", help="Exchange code used to scope ticker, e.g. ZSE")
    a.add_argument("--json", action="store_true")

    a = sp.add_parser("entity-lookup", help="Search the local entity master by name, LEI, ISIN, ticker or entity id")
    a.add_argument("query")
    a.add_argument("--limit", type=int, default=20)
    a.add_argument("--json", action="store_true")

    a = sp.add_parser("ingestion-jobs", help="Show resumable ingestion-job state recorded in the warehouse metadata")
    a.add_argument("--dataset")
    a.add_argument("--limit", type=int, default=20)
    a.add_argument("--json", action="store_true")

    a = sp.add_parser("pipeline", help="sync -> download XLSX -> parse")
    a.add_argument("--ticker", required=True)
    a.add_argument("--date-from")
    a.add_argument("--date-to")
    a.add_argument("--limit", type=int, default=5)

    sp.add_parser("llm-status", help="Inspect local GPU/Ollama without starting or loading a model")
    a = sp.add_parser("llm-test", help="Start Ollama if needed, auto-select a GPU-fit model, and run one schema test")
    a.add_argument("--model", default=None, help="Override model for this test; default is configured/auto")

    return p


def _settings(args) -> Settings:
    settings = Settings.from_env(args.data_dir)
    if args.use_llm is not None:
        settings.llm.enabled = bool(args.use_llm)
    return settings


def _ctx(args):
    settings = _settings(args)
    settings.ensure_dirs()
    db = Database(settings.db_path)
    http = RespectfulHttpClient(settings)
    return settings, db, EhoClient(http), http


def _sync(db, eho, args):
    items = eho.fetch_items(variant=args.variant, ticker=args.ticker, date_from=args.date_from, date_to=args.date_to)
    n = db.upsert_items(items)
    print(f"Saved/updated {n} feed items.")
    return items



def _xlsx_container_ok(path: Path) -> tuple[bool, str | None]:
    """Check that an .xlsx download is really an OOXML workbook container."""
    try:
        if not zipfile.is_zipfile(path):
            return False, "file has .xlsx suffix but is not a ZIP/OOXML container"
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            required = {"[Content_Types].xml", "xl/workbook.xml"}
            missing = required - names
            if missing:
                return False, "OOXML workbook members missing: " + ", ".join(sorted(missing))
    except (OSError, zipfile.BadZipFile) as exc:
        return False, str(exc)
    return True, None


def _download(settings, db, http, ticker, types, limit, max_mb):
    rows = db.pending_documents(ticker=ticker, document_types=tuple(types), limit=limit)
    if not rows:
        print("No pending matching documents.")
        return 0
    n = 0
    for r in rows:
        ticker_code = (r["issuer_code"] or "UNKNOWN").upper()
        dest = settings.data_dir / "files" / ticker_code / r["source_id"] / safe_filename_from_url(r["url"])
        print(f"Downloading {r['url']} -> {dest}")
        size, sha = http.download(r["url"], dest, max_bytes=int(max_mb * 1024 * 1024))
        db.mark_downloaded(
            source_id=r["source_id"],
            variant=r["variant"],
            url=r["url"],
            local_path=dest,
            sha256=sha,
            byte_size=size,
        )
        if r["document_type"] == "xlsx":
            ok, note = _xlsx_container_ok(dest)
            db.mark_document_content_status(
                source_id=r["source_id"], variant=r["variant"], url=r["url"],
                status="valid" if ok else "invalid", note=note,
            )
            if not ok:
                print(f"  WARNING: retained but will skip parsing: {note}")
        print(f"  {size / 1024:.1f} KiB sha256={sha[:12]}...")
        n += 1
    return n


def _llm_mapper(settings: Settings):
    if not settings.llm.enabled:
        return None
    if settings.llm.provider.casefold() != "ollama":
        print(f"LLM disabled: unsupported provider {settings.llm.provider!r}", file=sys.stderr)
        return None

    manager = OllamaManager(settings.llm, settings.data_dir)
    status = manager.prepare()
    if status.selected_model:
        gpu_text = "CPU/unknown"
        if status.gpu:
            gpu_text = f"{status.gpu.name} ({status.gpu.free_vram_gib:.1f}/{status.gpu.total_vram_gib:.1f} GiB free before load)"
        print(f"LLM ready: model={status.selected_model} processor={status.processor or '?'} gpu={gpu_text}")
        return OllamaSchemaMapper(
            manager,
            status,
            settings.data_dir,
            min_confidence=settings.llm.mapping_min_confidence,
        )

    print(f"LLM unavailable: {status.reason or 'unknown reason'}", file=sys.stderr)
    print("Continuing with deterministic parser only.", file=sys.stderr)
    return None


def _parse_downloaded(db, settings: Settings, ticker=None):
    rows = db.downloaded_xlsx(ticker)
    mapper = _llm_mapper(settings)
    n = 0
    for r in rows:
        path = Path(r["local_path"])
        ok, note = _xlsx_container_ok(path)
        if not ok:
            db.mark_document_content_status(
                source_id=r["source_id"], variant=r["variant"], url=r["url"],
                status="invalid", note=note,
            )
            print(f"SKIP invalid XLSX {path.name}: {note}")
            continue
        try:
            parsed = parse_xlsx_report(path, schema_mapper=mapper)
            parsed.warnings = validate_report(parsed)
            metrics = calculate_metrics(parsed)
            db.save_parsed_report(
                parsed,
                source_id=r["source_id"],
                source_variant=r["variant"],
                source_url=r["url"],
                source_publish_date=r["publish_date"],
                source_sha256=r["sha256"],
                issuer_code=r["issuer_code"],
                metrics=metrics,
            )
            desc = describe_report(parsed)
            print(
                f"Parsed {path.name}: {desc.period_label} {desc.scope} {desc.audit} | "
                f"{len(parsed.facts)} facts, {len(metrics)} metrics, {len(parsed.warnings)} warnings"
            )
            for w in parsed.warnings:
                print(f"  WARNING: {w}")
            n += 1
        except Exception as exc:
            print(f"ERROR parsing {path}: {exc}", file=sys.stderr)
    return n



def _coverage_data(db: Database, ticker: str) -> dict:
    # Coverage is about what exists, not only which Dec-31 report wins the
    # calculation preference.  Therefore Q4 and the later audited FY filing can
    # both appear for the same year.
    rows = db.report_inventory(ticker, preferred_only=False)
    years: dict[int, dict[str, tuple[tuple, str]]] = {}
    for row in rows:
        year = row["year"]
        if year is None:
            continue
        desc = describe_report(row)
        slot = "FY" if desc.period_label.endswith("-FY") else (f"Q{row['quarter']}" if row["quarter"] in (1, 2, 3, 4) else "OTHER")
        if slot == "OTHER":
            continue
        scope = "C" if row["consolidated"] == 1 else ("U" if row["consolidated"] == 0 else "?")
        audit = "A" if row["audited"] == 1 else ""
        rank = (
            2 if row["consolidated"] == 1 else (0 if row["consolidated"] == 0 else 1),
            1 if row["audited"] == 1 else 0,
            row["publish_date"] or "",
        )
        current = years.setdefault(int(year), {}).get(slot)
        if current is None or rank > current[0]:
            years[int(year)][slot] = (rank, scope + audit)
    rendered = {
        str(y): {slot: value for slot, (_, value) in slots.items()}
        for y, slots in sorted(years.items(), reverse=True)
    }
    return {
        "ticker": ticker.upper(),
        "legend": {"C": "consolidated", "U": "unconsolidated", "A": "audited"},
        "years": rendered,
    }


def _print_coverage(db: Database, ticker: str, as_json: bool = False) -> None:
    data = _coverage_data(db, ticker)
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    print(f"{data['ticker']} preferred-report coverage")
    print("YEAR   Q1   Q2   Q3   Q4   FY")
    for year, slots in data["years"].items():
        values = [slots.get(x, ".") for x in ("Q1", "Q2", "Q3", "Q4", "FY")]
        print(f"{year}  " + "  ".join(f"{v:>3}" for v in values))
    print("C=consolidated  U=unconsolidated  A=audited  .=missing/not yet available")


def _print_integrity(db: Database, ticker: str | None, *, repair: bool = False, as_json: bool = False) -> int:
    repaired = db.cleanup_report_integrity(ticker) if repair else None
    data = db.integrity_report(ticker)
    if repaired is not None:
        data["repair"] = repaired
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0 if data["ok"] else 1

    scope = ticker.upper() if ticker else "database"
    print(f"Integrity check: {scope}")
    if repaired is not None:
        print(
            "Repair: "
            f"removed {repaired['removed_orphan_reports']} orphan + "
            f"{repaired['removed_duplicate_reports']} duplicate parsed reports "
            f"({repaired['removed_total']} total)"
        )
    checks = [
        ("orphan parsed reports", data["orphan_parsed_reports"]),
        ("duplicate parsed sources", data["duplicate_parsed_sources"]),
        ("duplicate document URLs", data["duplicate_document_urls"]),
        ("unparsed valid XLSX", data["unparsed_valid_xlsx"]),
        ("invalid documents (informational)", data["invalid_documents"]),
        ("duplicate SHA256 content (informational)", data["duplicate_document_hashes"]),
    ]
    for label, rows in checks:
        print(f"{label:42} {len(rows)}")
    print("status".ljust(42), "OK" if data["ok"] else "NEEDS ATTENTION")

    # Show only a few concrete records; --json exposes the complete details.
    for key in ("orphan_parsed_reports", "duplicate_parsed_sources", "unparsed_valid_xlsx", "invalid_documents"):
        rows = data[key]
        if rows:
            print(f"\n{key}:")
            for row in rows[:5]:
                print("  " + json.dumps(row, ensure_ascii=False, sort_keys=True))
            if len(rows) > 5:
                print(f"  ... {len(rows) - 5} more (use --json for all)")
    return 0 if data["ok"] else 1


def _fmt_money(value: float | None) -> str:
    if value is None:
        return "-"
    sign = "-" if value < 0 else ""
    x = abs(value)
    if x >= 1_000_000_000:
        return f"{sign}{x/1_000_000_000:.2f}b"
    if x >= 1_000_000:
        return f"{sign}{x/1_000_000:.2f}m"
    if x >= 1_000:
        return f"{sign}{x/1_000:.1f}k"
    return f"{value:.2f}"


def _fmt_ratio(value: float | None, *, pct: bool = True) -> str:
    if value is None:
        return "-"
    return f"{value*100:.1f}%" if pct else f"{value:.2f}x"


def _print_ttm(db: Database, ticker: str, as_of: str | None, as_json: bool) -> int:
    data = build_ttm_snapshot(db, ticker, as_of)
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0 if data.get("ok") else 2
    if not data.get("ok"):
        print(f"TTM unavailable: {data.get('error')}")
        return 2
    print(f"{data['ticker']} TTM snapshot as of {data['as_of']} ({data['period_end']})")
    print(data["currency_note"])
    money = (
        "sales_revenue_ttm", "ebit_ttm", "ebitda_simple_ttm",
        "net_income_parent_ttm", "operating_cash_flow_ttm", "free_cash_flow_ttm",
        "cash", "gross_financial_debt_ex_other", "net_debt_ex_other", "equity_parent",
    )
    for name in money:
        m = data["metrics"].get(name, {})
        suffix = "  !suspect" if str(m.get("quality", "")).startswith("suspect") else ""
        print(f"{name:34} {_fmt_money(m.get('value')):>12} EUR{suffix}")
    print()
    for name in ("ebit_margin_ttm", "ebitda_margin_ttm", "net_margin_parent_ttm", "fcf_margin_ttm", "roe_ttm"):
        m = data["metrics"].get(name, {})
        print(f"{name:34} {_fmt_ratio(m.get('value')):>12}")
    for name in ("net_debt_to_ebitda_ttm", "interest_coverage_ebit_ttm", "debt_to_equity_parent"):
        m = data["metrics"].get(name, {})
        print(f"{name:34} {_fmt_ratio(m.get('value'), pct=False):>12}")
    if data.get("equity_attribution_warning"):
        print(f"\nWARNING: {data['equity_attribution_warning']}")
    fallbacks = [m for m in data["metrics"].values() if "fallback" in str(m.get("quality", ""))]
    if fallbacks:
        print(f"\nQ4 flow fallback used for: {', '.join(m['name'] for m in fallbacks)}")
        print("Audited FY flow metric was unavailable; same-year consolidated Q4 cumulative value was used. Use --json for provenance.")
    unavailable = [m for m in data["metrics"].values() if m.get("value") is None]
    if unavailable:
        print(f"\nUnavailable metrics: {', '.join(m['name'] for m in unavailable)}")
        print("Use --json to see exact missing inputs and source provenance.")
    return 0


def _print_trends(db: Database, ticker: str, years: int, as_json: bool) -> int:
    data = build_trends(db, ticker, years)
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    annual = data["annual"]
    if not annual:
        print("No year-end history available.")
        return 2
    print(f"{data['ticker']} annual history (nominal EUR)")
    print("YEAR      REVENUE       EBIT     EBITDA    NET INC        FCF   EBIT%     ROE    NET DEBT")
    for r in reversed(annual):
        print(
            f"{r['year']:4}  {_fmt_money(r['sales_revenue']):>11} {_fmt_money(r['ebit']):>10} "
            f"{_fmt_money(r['ebitda_simple']):>10} {_fmt_money(r['net_income_parent']):>10} "
            f"{_fmt_money(r['free_cash_flow']):>10} {_fmt_ratio(r['ebit_margin']):>7} "
            f"{_fmt_ratio(r['roe']):>7} {_fmt_money(r['net_debt_ex_other']):>11}"
        )
    suspect_equity_years = [str(r["year"]) for r in annual if r.get("equity_parent_suspect")]
    if suspect_equity_years:
        print(f"\nROE suppressed for suspect parent-equity attribution in: {', '.join(suspect_equity_years)}")
    fallback_years = [str(r["year"]) for r in annual if r.get("flow_fallback_metrics")]
    if fallback_years:
        print(f"\nQ4 cumulative flow fallback used for audited-FY gaps in: {', '.join(fallback_years)}")
        print("Point-in-time balance-sheet values still come from the audited FY report.")
    print("\nGrowth (positive endpoints only for CAGR):")
    for name, g in data["growth"].items():
        print(f"{name:24} 3y={_fmt_ratio(g.get('cagr_3y')):>8}  5y={_fmt_ratio(g.get('cagr_5y')):>8}")
    if data.get("yoy"):
        print("\nLatest TTM YoY:")
        for name, value in data["yoy"].items():
            print(f"{name:34} {_fmt_ratio(value):>12}")
    latest = data.get("latest_ttm", {})
    if latest.get("ok"):
        print(f"\nLatest TTM period: {latest['as_of']}")
    print(data["currency_note"])
    return 0


def _market_sync(db: Database, http, ticker: str, isin: str | None, as_json: bool) -> int:
    client = ZseMarketClient(http)
    security, snapshot = client.fetch_snapshot(ticker, isin=isin)
    directory_source = f"{ZSE_SHARES_URL}?status=LISTED_SECURITIES&model=ALL&type=SHARE"
    db.upsert_security(security, source_url=directory_source)
    db.save_market_snapshot(snapshot)
    if as_json:
        print(json.dumps({"security": security.as_dict(), "snapshot": snapshot}, ensure_ascii=False, indent=2))
        return 0
    print(f"{security.ticker} market snapshot stored")
    print(f"ISIN                 {security.isin}")
    print(f"listed_quantity      {snapshot.get('listed_quantity') or '-':>12}")
    print(f"market_cap           {_fmt_money(snapshot.get('market_cap_eur')):>12} EUR")
    print(f"implied_price        {_fmt_money(snapshot.get('implied_price_eur')):>12} EUR/share")
    print(f"observed_at          {snapshot.get('observed_at')}")
    print("Price is display-only: ZSE published market cap / listed quantity, not a separately fetched last trade.")
    print(snapshot.get("note") or "")
    return 0 if snapshot.get("market_cap_eur") is not None else 2


def _print_market(db: Database, ticker: str, history: int, as_json: bool) -> int:
    rows = db.market_history(ticker, max(1, history))
    if not rows:
        print(f"No market snapshot stored for {ticker.upper()}. Run: zse-tool market-sync --ticker {ticker.upper()}")
        return 2
    data = [dict(r) for r in rows]
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    for i, r in enumerate(data):
        if i:
            print()
        print(f"{ticker.upper()} market snapshot observed {r['observed_at']}")
        print(f"ISIN                 {r['isin']}")
        print(f"listed_quantity      {r.get('listed_quantity') or '-':>12}")
        print(f"market_cap           {_fmt_money(r.get('market_cap_eur')):>12} EUR")
        print(f"implied_price        {_fmt_money(r.get('implied_price_eur')):>12} EUR/share")
        print(f"quality              {r.get('quality') or '?'}")
        print(r.get("note") or "")
    return 0


def _print_valuation(db: Database, ticker: str, as_of: str | None, as_json: bool) -> int:
    data = build_valuation(db, ticker, as_of)
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0 if data.get("ok") else 2
    if not data.get("ok"):
        print(data.get("error") or "Valuation unavailable")
        return 2
    market = data["market"]
    print(f"{data['ticker']} current valuation")
    print(f"market observed      {market.get('observed_at')}")
    print(f"financials as of     {data['financials'].get('as_of')} ({data['financials'].get('period_end')})")
    print(f"listed_quantity      {market.get('listed_quantity') or '-':>12}")
    print(f"implied_price        {_fmt_money(market.get('implied_price_eur')):>12} EUR/share")
    print(f"market_cap           {_fmt_money(market.get('market_cap_eur')):>12} EUR")
    print()
    money_names = ("enterprise_value_eur", "enterprise_value_md_eur", "enterprise_value_md_observed_eur")
    for name in money_names:
        m = data["metrics"][name]
        print(f"{name:34} {_fmt_money(m.get('value')):>12} EUR")
    for name in ("pe_ttm", "price_to_sales_ttm", "ev_to_ebitda_simple_ttm", "ev_to_ebit_ttm", "ev_md_to_md_ebitda_ttm", "ev_md_observed_to_md_ebitda_ttm"):
        m = data["metrics"][name]
        print(f"{name:34} {_fmt_ratio(m.get('value'), pct=False):>12}")
    for name in ("earnings_yield_ttm", "fcf_yield_ttm", "cfo_yield_ttm", "net_cash_to_market_cap"):
        m = data["metrics"][name]
        print(f"{name:34} {_fmt_ratio(m.get('value')):>12}")
    if data["metrics"]["price_to_book_parent"].get("value") is not None:
        print(f"{'price_to_book_parent':34} {_fmt_ratio(data['metrics']['price_to_book_parent'].get('value'), pct=False):>12}")
    print("\nMarket source: official ZSE published market cap. Implied price is market cap / listed quantity.")
    print(market.get("note") or "")
    ev_note = data["metrics"]["enterprise_value_eur"].get("note")
    if ev_note:
        print(f"Debt note: {ev_note}")
    if data["financials"].get("equity_attribution_warning"):
        print("Equity warning remains active; P/B and ROE-based valuation are intentionally not included.")
    return 0


def _print_debt(db: Database, ticker: str, as_of: str | None, as_json: bool) -> int:
    data = build_debt_snapshot(db, ticker, as_of)
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0 if data.get("ok") else 2
    if not data.get("ok"):
        print(data.get("error") or "Debt snapshot unavailable")
        return 2

    m = data["metrics"]
    def val(name):
        return m.get(name, {}).get("value")

    print(f"{data['ticker']} debt decomposition as of {data.get('as_of')} ({data.get('period_end')})")
    print("Standardized financing detail rows:")
    rows = (
        ("LT group loans/deposits", "debt_lt_group_loans"),
        ("LT participating loans", "debt_lt_participating_loans"),
        ("LT loans/deposits", "debt_lt_loans_deposits"),
        ("LT banks/financial inst.", "debt_lt_banks_financial_institutions"),
        ("LT securities", "debt_lt_securities"),
        ("ST group loans/deposits", "debt_st_group_loans"),
        ("ST participating loans", "debt_st_participating_loans"),
        ("ST loans/deposits", "debt_st_loans_deposits"),
        ("ST banks/financial inst.", "debt_st_banks_financial_institutions"),
        ("ST securities", "debt_st_securities"),
    )
    for label, name in rows:
        print(f"{label:30} {_fmt_money(val(name)):>12} EUR")
    print(f"{'explicit financing debt':30} {_fmt_money(val('gross_financial_debt_standardized')):>12} EUR")
    print(f"{'  long-term explicit':30} {_fmt_money(val('explicit_long_term_financing_debt')):>12} EUR")
    print(f"{'  short-term explicit':30} {_fmt_money(val('explicit_short_term_financing_debt')):>12} EUR")
    print(f"{'external financing':30} {_fmt_money(val('external_financing_debt')):>12} EUR")
    print(f"{'related-party financing':30} {_fmt_money(val('related_party_financing_debt')):>12} EUR")
    print(f"{'cash':30} {_fmt_money(val('cash')):>12} EUR")
    print(f"{'short-term fin. assets':30} {_fmt_money(val('short_term_financial_assets')):>12} EUR")
    print(f"{'net debt vs liquid assets':30} {_fmt_money(val('net_debt_liquid_assets')):>12} EUR")
    print()
    print("Liability bridge (kept unclassified until note-level evidence):")
    print(f"{'LT liability residual':30} {_fmt_money(val('unclassified_long_term_liabilities_residual')):>12} EUR")
    print(f"{'ST liability residual':30} {_fmt_money(val('unclassified_current_liabilities_residual')):>12} EUR")
    print(f"{'total liabilities':30} {_fmt_money(val('total_liabilities')):>12} EUR")
    print()
    print("MojeDionice published-method view:")
    print(f"{'all long-term liabilities':30} {_fmt_money(val('long_term_liabilities')):>12} EUR")
    print(f"{'published ST debt leg':30} {_fmt_money(val('md_short_term_financial_debt')):>12} EUR")
    print(f"{'published financial debt':30} {_fmt_money(val('md_financial_debt')):>12} EUR")
    print(f"{'published net debt':30} {_fmt_money(val('md_net_debt_liquid_assets')):>12} EUR")
    print()
    print("MojeDionice observed-compatibility view:")
    print(f"{'observed ST debt leg':30} {_fmt_money(val('md_observed_short_term_financial_debt')):>12} EUR")
    print(f"{'observed financial debt':30} {_fmt_money(val('md_observed_financial_debt')):>12} EUR")
    print(f"{'observed net debt':30} {_fmt_money(val('md_observed_net_debt_liquid_assets')):>12} EUR")
    print()
    print(data.get("scanner_note") or "")
    print(data.get("md_note") or "")
    print(data.get("analysis_note") or "")
    print("Lease note: the standardized issuer balance sheet does not guarantee a separate IFRS 16 lease-liability row; lease-inclusive EV remains unclaimed until such disclosure is explicitly extracted.")
    return 0


def _print_md_comparison(db: Database, ticker: str, periods_csv: str, as_json: bool) -> int:
    periods = [p.strip() for p in periods_csv.split(",") if p.strip()]
    data = build_md_comparison(db, ticker, periods)
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0 if all(r.get("ok") for r in data.get("periods", [])) else 2

    market = data.get("market") or {}
    print(f"{data['ticker']} MojeDionice-style comparison")
    if market:
        print(f"Market numerator: latest stored ZSE market cap {_fmt_money(market.get('market_cap_eur'))} EUR observed {market.get('observed_at')}")
    print(data.get("note") or "")
    print()

    rows = data.get("periods", [])
    if not rows:
        print("No periods requested")
        return 2
    bad = [r for r in rows if not r.get("ok")]
    for r in bad:
        print(f"{r.get('period')}: {r.get('error') or 'unavailable'}")
    good = [r for r in rows if r.get("ok")]
    if not good:
        return 2

    labels = [r["period"] for r in good]
    width = 14
    print(f"{'METRIC':34}" + ''.join(f"{x:>{width}}" for x in labels))

    def money_line(label, key):
        print(f"{label:34}" + ''.join(f"{_fmt_money(r.get(key)):>{width}}" for r in good))
    def x_line(label, key):
        print(f"{label:34}" + ''.join(f"{_fmt_ratio(r.get(key), pct=False):>{width}}" for r in good))
    def pct_line(label, key):
        print(f"{label:34}" + ''.join(f"{_fmt_ratio(r.get(key)):>{width}}" for r in good))
    def text_line(label, key, yes="yes", no="no"):
        def fmt(v):
            if v is True:
                return yes
            if v is False:
                return no
            return "-"
        print(f"{label:34}" + ''.join(f"{fmt(r.get(key)):>{width}}" for r in good))
    def count_line(label, key):
        def fmt(v):
            if v is None:
                return "-"
            try:
                return f"{int(round(float(v))):,}"
            except (TypeError, ValueError):
                return str(v)
        print(f"{label:34}" + ''.join(f"{fmt(r.get(key)):>{width}}" for r in good))
    def string_line(label, key):
        print(f"{label:34}" + ''.join(f"{str(r.get(key) or '-'):>{width}}" for r in good))

    money_line("sales revenue", "sales_revenue")
    money_line("operating revenue", "operating_revenue")
    money_line("MD-style EBIT", "md_ebit")
    money_line("MD-style EBITDA", "md_ebitda")
    money_line("parent net income", "net_income_parent")
    money_line("comprehensive income", "comprehensive_income_parent")
    money_line("parent equity", "equity_parent")
    money_line("retained earnings + reserves", "retained_earnings_reserves_parent")
    money_line("total assets", "total_assets")
    count_line("employees (reported)", "employees")
    string_line("employee measure", "employee_measure")
    text_line("consolidated", "consolidated")
    text_line("audited", "audited")
    print()
    x_line("P/S", "p_s")
    x_line("P/E", "p_e")
    x_line("P/B", "p_b")
    money_line("BVPS EUR/share", "bvps")
    money_line("EPS EUR/share", "eps")
    print()
    pct_line("ROE ending equity", "roe")
    pct_line("ROA ending assets", "roa")
    pct_line("EBIT margin (MD)", "ebit_margin")
    pct_line("EBITDA margin (MD)", "ebitda_margin")
    pct_line("NPM (MD)", "npm")
    pct_line("ROCE (MD)", "roce")
    x_line("current ratio", "current_ratio")
    money_line("cash+short fin / share", "cash_plus_short_fin_per_share")
    pct_line("debt / parent equity", "debt_to_equity")
    print()
    x_line("EV/EBITDA (MD)", "ev_to_ebitda")
    x_line("P/EBITDA", "p_ebitda")
    x_line("P/EBIT", "p_ebit")
    x_line("P/EA", "p_ea")
    x_line("P/CF", "p_cf")
    x_line("P/FCF net capex", "p_fcf")

    if any(r.get("equity_warning") for r in good):
        print("\nWARNING: parent-equity attribution is still unavailable/suspect in at least one period.")
    print("\n" + (data.get("ev_note") or ""))
    return 0 if not bad else 2


def _print_parity(db: Database, ticker: str, periods_csv: str, as_json: bool) -> int:
    """Report financial-summary parity coverage without comparing to scraped values."""
    periods = [p.strip() for p in periods_csv.split(",") if p.strip()]
    data = build_md_comparison(db, ticker, periods)
    fields = (
        ("sales", "sales_revenue"),
        ("operating revenue", "operating_revenue"),
        ("EBIT", "md_ebit"),
        ("EBITDA", "md_ebitda"),
        ("parent net income", "net_income_parent"),
        ("comprehensive income", "comprehensive_income_parent"),
        ("parent equity", "equity_parent"),
        ("retained earnings + reserves", "retained_earnings_reserves_parent"),
        ("assets", "total_assets"),
        ("employees", "employees"),
        ("consolidated status", "consolidated"),
        ("audited status", "audited"),
        ("P/S", "p_s"),
        ("P/E", "p_e"),
        ("P/B", "p_b"),
        ("BVPS", "bvps"),
        ("EPS", "eps"),
        ("ROE", "roe"),
        ("ROA", "roa"),
        ("EBIT margin", "ebit_margin"),
        ("EBITDA margin", "ebitda_margin"),
        ("NPM", "npm"),
        ("ROCE", "roce"),
        ("current ratio", "current_ratio"),
        ("cash/share", "cash_plus_short_fin_per_share"),
        ("debt/equity", "debt_to_equity"),
        ("EV/EBITDA", "ev_to_ebitda"),
        ("P/EBITDA", "p_ebitda"),
        ("P/EBIT", "p_ebit"),
        ("P/EA", "p_ea"),
        ("P/CF", "p_cf"),
        ("P/FCF", "p_fcf"),
    )

    rows = []
    for period in data.get("periods", []):
        if not period.get("ok"):
            rows.append({"period": period.get("period"), "ok": False, "error": period.get("error")})
            continue
        available = {label: period.get(key) is not None for label, key in fields}
        missing = [label for label, ok in available.items() if not ok]
        rows.append({
            "requested_period": period.get("requested_period"),
            "period": period.get("period"),
            "ok": True,
            "available": sum(available.values()),
            "total": len(fields),
            "missing": missing,
            "fields": available,
            "employee_measure": period.get("employee_measure"),
        })

    out = {"ticker": ticker.upper(), "periods": rows}
    if as_json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"{ticker.upper()} MojeDionice financial-summary field availability")
        for row in rows:
            if not row.get("ok"):
                print(f"{row.get('period')}: unavailable - {row.get('error')}")
                continue
            requested = row.get("requested_period")
            source_label = row["period"]
            suffix = f" (requested {requested})" if requested and requested != source_label else ""
            print(f"{source_label}{suffix}: {row['available']}/{row['total']} fields available")
            if row["missing"]:
                print("  missing: " + ", ".join(row["missing"]))
            if row.get("employee_measure"):
                print("  employees: " + row["employee_measure"])
        print("Note: this command measures field availability, not numerical equality with MojeDionice.")
        print("Audited annual ESEF-only filings are not yet parsed by the XLSX pipeline; a Q4 filing may therefore be used for that year.")
    return 0 if rows and all(r.get("ok") for r in rows) else 2


def _status_dict(status) -> dict:
    gpu = None
    if status.gpu:
        gpu = {
            "index": status.gpu.index,
            "uuid": status.gpu.uuid,
            "name": status.gpu.name,
            "total_vram_gib": round(status.gpu.total_vram_gib, 2),
            "free_vram_gib": round(status.gpu.free_vram_gib, 2),
        }
    return {
        "enabled": status.enabled,
        "reachable": status.reachable,
        "started_by_scanner": status.started_by_scanner,
        "gpu": gpu,
        "selected_model": status.selected_model,
        "processor": status.processor,
        "reason": status.reason,
    }


def _llm_status(settings: Settings) -> int:
    manager = OllamaManager(settings.llm, settings.data_dir)
    status = manager.inspect()
    data = _status_dict(status)
    data["gpus"] = [
        {
            "index": g.index,
            "uuid": g.uuid,
            "name": g.name,
            "total_vram_gib": round(g.total_vram_gib, 2),
            "free_vram_gib": round(g.free_vram_gib, 2),
        }
        for g in detect_nvidia_gpus()
    ]
    if status.reachable:
        try:
            data["installed_models"] = [
                {
                    "name": m.name,
                    "size_gib": round(m.size_bytes / (1024 ** 3), 2),
                    "parameters": m.parameter_size,
                    "quantization": m.quantization,
                }
                for m in manager.client.list_models()
            ]
        except Exception as exc:
            data["installed_models_error"] = str(exc)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def _llm_test(settings: Settings, model_override: str | None) -> int:
    settings.llm.enabled = True
    if model_override:
        settings.llm.model = model_override
    manager = OllamaManager(settings.llm, settings.data_dir)
    status = manager.prepare()
    print(json.dumps(_status_dict(status), ensure_ascii=False, indent=2))
    if not status.selected_model:
        return 2
    mapper = OllamaSchemaMapper(manager, status, settings.data_dir, settings.llm.mapping_min_confidence)
    sample = "Izvještaj o financijskom položaju Grupe"
    mapped = mapper.map_sheet_name(sample)
    print(json.dumps({"sample": sample, "mapping": mapped}, ensure_ascii=False, indent=2))
    return 0 if mapped else 3



def _fmt_eur(value) -> str:
    if value is None:
        return "-"
    value = float(value)
    if abs(value) >= 1_000_000_000:
        return f"{value/1_000_000_000:.2f}b"
    if abs(value) >= 1_000_000:
        return f"{value/1_000_000:.2f}m"
    if abs(value) >= 1_000:
        return f"{value/1_000:.1f}k"
    return f"{value:.0f}"


def _print_taxonomy(db: Database, scheme: str | None, version: str | None, query: str | None, as_json: bool) -> int:
    ensure_bundled_taxonomies(db)
    rows = db.classification_codes(scheme, version, query)
    if as_json:
        print(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("No cached classification codes matched. v0.3.1 bundles a curated official subset; full-list sync/import comes later.")
        return 0
    print("Cached official classification codes")
    for r in rows:
        print(f"{r['scheme']:5} {r['version']:6} {r['code']:9} {r['label']}")
    print("Note: an official taxonomy code does not imply that the issuer's assignment to that code is official-registry data.")
    return 0


def _profile_seed(db: Database, ticker: str, as_json: bool) -> int:
    if ticker.upper() == "ALL":
        payloads = seed_all_bundled_profiles(db)
        if as_json:
            print(json.dumps(payloads, ensure_ascii=False, indent=2))
        else:
            for payload in payloads:
                p = payload["profile"]
                print(f"Loaded {p['ticker']} profile as of {p['profile_date']}: activities={len(payload.get('activities', []))} segments={len(payload.get('segments', []))}")
            print("Taxonomy mappings are evidence-grounded analytical assignments unless explicitly marked official-registry.")
        return 0
    payload = seed_bundled_profile(db, ticker)
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        p = payload["profile"]
        print(f"Loaded company-intelligence profile: {p['ticker']} as of {p['profile_date']}")
        print(f"Source: {p['source_url']}")
        print(f"Activities: {len(payload.get('activities', []))}; segments: {len(payload.get('segments', []))}")
        print("Taxonomy mappings are evidence-grounded analytical assignments unless explicitly marked official-registry.")
    return 0


def _profile_file_action(db: Database, file: str, *, write: bool, as_json: bool) -> int:
    payload = import_profile_file(db, Path(file)) if write else validate_profile_file(db, Path(file))
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        p = payload["profile"]
        verb = "Imported" if write else "Valid"
        print(f"{verb} company-intelligence profile: {p['ticker']} as of {p['profile_date']}")
        print(f"activities={len(payload.get('activities', []))} segments={len(payload.get('segments', []))}")
    return 0


def _print_activities(db: Database, ticker: str, as_json: bool) -> int:
    ensure_bundled_taxonomies(db)
    data = profile_as_dict(db, ticker)
    rows = data["activities"]
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    p = data["profile"]
    print(f"{ticker.upper()} activities - profile {p['profile_date']}")
    for a in rows:
        weight = "-" if a["weight"] is None else f"{float(a['weight'])*100:5.1f}%"
        codes = ", ".join(
            f"{c['scheme']} {c['code']} ({c['assignment_status']})" for c in a["classifications"]
        )
        print(f"{weight:>7}  {a['name']} [{a['role']}]\n         {codes}")
    return 0


def _print_segments(db: Database, ticker: str, as_json: bool) -> int:
    ensure_bundled_taxonomies(db)
    data = profile_as_dict(db, ticker)
    rows = data["segments"]
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    print(f"{ticker.upper()} operating segments - profile {data['profile']['profile_date']}")
    print(f"{'SEGMENT':24} {'REVENUE':>12} {'SHARE':>8}  MEASURE")
    for r in rows:
        share = "-" if r["revenue_share"] is None else f"{float(r['revenue_share'])*100:.1f}%"
        print(f"{r['name'][:24]:24} {_fmt_eur(r['revenue_eur']):>12} {share:>8}  {r['measure'] or '-'}")
    return 0


def _print_company_profile(db: Database, ticker: str, as_of: str | None, as_json: bool) -> int:
    ensure_bundled_taxonomies(db)
    data = profile_as_dict(db, ticker, as_of=as_of)
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    p = data["profile"]
    print(f"{p['ticker']} company intelligence profile as of {p['profile_date']}")
    print(p["legal_name"])
    if p["summary"]:
        print(p["summary"])
    print(f"method={p['method']} confidence={float(p['confidence'])*100:.0f}% source_date={p['source_date']}")
    print(f"source={p['source_url']}")

    print("\nMaterial activity map")
    for a in data["activities"]:
        weight = "-" if a["weight"] is None else f"{float(a['weight'])*100:.1f}%"
        nace = next((c for c in a["classifications"] if c["scheme"] == "NACE"), None)
        nkd = next((c for c in a["classifications"] if c["scheme"] == "NKD"), None)
        code_text = []
        if nace:
            code_text.append(f"NACE {nace['code']} {nace['label']}")
        if nkd:
            code_text.append(f"NKD {nkd['code']} {nkd['label']}")
        print(f"  {weight:>6}  {a['name']} ({a['role']})")
        for c in code_text:
            print(f"          {c}")

    if data["segments"]:
        print("\nReported segment revenue")
        for r in data["segments"]:
            share = "-" if r["revenue_share"] is None else f"{float(r['revenue_share'])*100:.1f}%"
            revenue = "-" if r["revenue_eur"] is None else f"{_fmt_eur(r['revenue_eur'])} EUR"
            print(f"  {r['name']:<28} {revenue:>14}  {share:>6}")

    if data["products"]:
        print("\nProducts / services")
        for r in data["products"]:
            print(f"  {r['name']} ({r['segment_key'] or 'unassigned'})")

    if data["geographies"]:
        print("\nTop stored geographies")
        for r in data["geographies"][:5]:
            share = "-" if r["revenue_share"] is None else f"{float(r['revenue_share'])*100:.1f}%"
            print(f"  {r['name']:<24} {_fmt_eur(r['revenue_eur']):>10} EUR  {share:>6}")

    if data["capacities"]:
        print("\nReported capacities")
        for r in data["capacities"]:
            v = float(r["value"])
            value = str(int(v)) if v.is_integer() else f"{v:g}"
            print(f"  {r['name']}: {value} {r['unit']}")

    if data["subsidiaries"]:
        print("\nMaterial subsidiaries")
        for r in data["subsidiaries"]:
            ownership = "?" if r["ownership_pct"] is None else f"{float(r['ownership_pct']):.1f}%"
            print(f"  {r['name']} ownership={ownership} control={r['control'] or '-'} - {r['activity'] or '-'}")

    print("\nClassification rule: official taxonomy + cited evidence. 'Analytical-mapping' is not represented as an official DZS registry assignment.")
    return 0


def _print_profile_history(db: Database, ticker: str, as_json: bool) -> int:
    rows = profile_history(db, ticker)
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    print(f"{ticker.upper()} company-profile history")
    if not rows:
        print("No company-intelligence profiles stored.")
        return 0
    for r in rows:
        print(f"  {r['profile_date']}  method={r['method']} confidence={float(r['confidence'])*100:.0f}% source_date={r['source_date']}")
    print("Historical profile versions are preserved; importing a new date does not overwrite older dates.")
    return 0


def _print_profile_quality(db: Database, ticker: str, as_of: str | None, as_json: bool) -> int:
    q = profile_quality(db, ticker, as_of=as_of)
    if as_json:
        print(json.dumps(q, ensure_ascii=False, indent=2))
        return 0
    print(f"{ticker.upper()} company-profile quality")
    print(f"profile date                 {q['profile_date']}")
    print(f"latest financial date       {q['latest_financial_period_end'] or '-'}")
    print(f"profile lag                  {q['profile_lag_days'] if q['profile_lag_days'] is not None else '-'} days")
    print(f"freshness                    {q['freshness']}")
    print(f"activity classifications     {q['activity_classification_coverage']*100:.0f}%")
    print(f"weighted activity coverage   {q['weighted_activity_coverage']*100:.1f}%")
    print(f"segment share coverage       {q['segment_share_coverage']*100:.1f}%")
    print(f"geographic share coverage    {q['geography_share_coverage']*100:.1f}%")
    print(f"products / capacities        {q['products_count']} / {q['capacities_count']}")
    print(f"stored subsidiaries          {q['subsidiaries_count']}")
    print(f"official registry mappings   {q['official_registry_assignments']}")
    print(f"evidence completeness score  {q['score']:.1f}/100")
    if q['gaps']:
        print("Gaps / next research tasks:")
        for gap in q['gaps']:
            print(f"  - {gap}")
    return 0


def _print_peer_candidates(
    db: Database,
    ticker: str,
    limit: int,
    peer_type: str,
    eligible_only: bool,
    as_json: bool,
) -> int:
    ensure_bundled_taxonomies(db)

    types = ["business", "business-model", "investment"] if peer_type == "all" else [peer_type]
    results = {
        kind: build_peer_candidates(
            db, ticker, limit=limit, peer_type=kind, include_rejected=not eligible_only
        )
        for kind in types
    }
    if as_json:
        payload = {
            kind: [asdict(r) for r in rows]
            for kind, rows in results.items()
        }
        print(json.dumps(payload if peer_type == "all" else payload[types[0]], ensure_ascii=False, indent=2))
        return 0

    title = {
        "business": "business/product peers",
        "product": "business/product peers",
        "business-model": "business-model peers",
        "model": "business-model peers",
        "investment": "investment-characteristic peers",
    }
    for section_i, kind in enumerate(types):
        canonical = "business" if kind == "product" else ("business-model" if kind == "model" else kind)
        if section_i:
            print()
        print(f"{ticker.upper()} deterministic local {title.get(kind, title.get(canonical, canonical))}")
        rows = results[kind]
        if not rows:
            print("No candidates with enough stored evidence.")
            continue
        for r in rows:
            score = "-" if r.score is None else f"{r.score:5.1f}"
            model = "-" if r.business_model_score is None else f"{r.business_model_score:4.0f}"
            inv = "-" if r.investment_score is None else f"{r.investment_score:4.0f}"
            size = "-" if r.size_score is None else f"{r.size_score:4.0f}"
            print(
                f"{r.ticker:8} {r.status:17} score={score:>5} "
                f"activity={r.activity_score:5.1f} model={model:>4} invest={inv:>4} size={size:>4}"
            )
            print(f"         {r.explanation}")
            if r.feature_notes:
                print(f"         evidence: {'; '.join(r.feature_notes)}")
        if canonical == "business":
            print("Business/product peers use a hard activity gate: size or financial similarity cannot rescue an unrelated company.")
        elif canonical == "business-model":
            print("Business-model peers may cross industries; they are economic analogues, not automatically valuation peers.")
        else:
            print("Investment peers compare available market/size/leverage characteristics; they are not product/business peers.")
    return 0


def _print_warehouse_status(settings: Settings, db: Database, as_json: bool) -> int:
    data = warehouse_status(
        db,
        WarehouseLayout(settings.resolved_warehouse_dir),
        data_dir=settings.data_dir,
        db_path=settings.db_path,
    )
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    print("Research warehouse")
    print(f"data root                    {data['data_dir']}")
    print(f"metadata database            {data['database']}")
    print(f"warehouse root               {data['warehouse']['root']}")
    print(f"initialized                  {'yes' if data['initialized'] else 'no'}")
    print(f"large analytics backend      {data['backend']['large_analytics']}")
    print(f"raw / staging / parquet      {_fmt_bytes(data['bytes']['raw'])} / {_fmt_bytes(data['bytes']['staging'])} / {_fmt_bytes(data['bytes']['parquet'])}")
    for k, v in data['counts'].items():
        print(f"{k:30} {v}")
    if not data['initialized']:
        print("Run: zse-tool warehouse-init --bootstrap-local")
    if data['backend']['large_analytics'] == 'optional-not-installed':
        print("DuckDB/PyArrow are optional in v0.3.3; no installation is required until large-scale cluster ingestion.")
    return 0


def _fmt_bytes(value: int) -> str:
    x = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if x < 1024.0 or unit == "TiB":
            return f"{x:.1f}{unit}" if unit != "B" else f"{int(x)}B"
        x /= 1024.0
    return f"{value}B"


def _print_dataset_list(db: Database, category: str | None, as_json: bool) -> int:
    seed_source_registry(db)
    rows = dataset_list(db, category=category)
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("No research datasets matched.")
        return 0
    print(f"{'DATASET':28} {'CATEGORY':11} {'STATUS':9} {'VERS':>4} {'ART':>4} {'JOBS':>4}  SOURCE")
    for r in rows:
        print(f"{r['dataset_key'][:28]:28} {r['category'][:11]:11} {r['status'][:9]:9} {r['versions']:4} {r['artifacts']:4} {r['jobs']:4}  {r['authority'] or r['source_key']}")
    print("Planned datasets are registry entries only; v0.3.3 does not bulk-download them.")
    return 0


def _print_entity_lookup(db: Database, query: str, limit: int, as_json: bool) -> int:
    rows = entity_lookup(db, query, limit=limit)
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("No local research entities matched.")
        return 0
    for e in rows:
        ids = ", ".join(f"{i['scheme']}={i['value']}" for i in e['identifiers'])
        print(f"{e['entity_id']:28} {e['legal_name']} [{e['country_code'] or '?'}]")
        if ids:
            print(f"  {ids}")
    return 0


def _print_ingestion_jobs(db: Database, dataset: str | None, limit: int, as_json: bool) -> int:
    rows = [dict(r) for r in db.ingestion_jobs(dataset, limit=limit)]
    for r in rows:
        if r.get('cursor_json'):
            try:
                r['cursor'] = json.loads(r['cursor_json'])
            except json.JSONDecodeError:
                r['cursor'] = r['cursor_json']
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("No ingestion jobs recorded yet.")
        return 0
    print(f"{'JOB':>5} {'DATASET':28} {'STATE':9} {'SEEN':>7} {'DOWN':>7} {'FAIL':>5}  RUN KEY")
    for r in rows:
        print(f"{r['job_id']:5} {r['dataset_key'][:28]:28} {r['state'][:9]:9} {r['items_seen']:7} {r['items_downloaded']:7} {r['items_failed']:5}  {r['run_key']}")
    print("Cursor/counters are persisted so future bulk ingestors can resume without restarting from zero.")
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = _settings(args)
        settings.ensure_dirs()

        if args.cmd == "llm-status":
            return _llm_status(settings)
        if args.cmd == "llm-test":
            return _llm_test(settings, args.model)

        if args.cmd == "inspect-xlsx":
            mapper = _llm_mapper(settings)
            parsed = parse_xlsx_report(args.file, schema_mapper=mapper)
            parsed.warnings = validate_report(parsed)
            metrics = calculate_metrics(parsed)
            data = {
                "issuer": parsed.issuer_name,
                "period_end": str(parsed.period_end) if parsed.period_end else None,
                "year": parsed.year,
                "quarter": parsed.quarter,
                "consolidated": parsed.consolidated,
                "facts": len(parsed.facts),
                "warnings": parsed.warnings,
                "metrics": {m.name: m.value for m in metrics},
            }
            if args.json:
                print(json.dumps(data, ensure_ascii=False, indent=2))
            else:
                print(f"Issuer: {data['issuer']} | period: {data['period_end']} | Q{data['quarter']} | consolidated={data['consolidated']}")
                print(f"Facts: {data['facts']}")
                for m in metrics:
                    print(f"{m.name:34} {m.value} {m.unit}")
                for w in parsed.warnings:
                    print(f"WARNING: {w}")
            return 0

        settings, db, eho, http = _ctx(args)
        if args.cmd == "probe":
            print(json.dumps(eho.probe(args.ticker), ensure_ascii=False, indent=2))
            return 0
        if args.cmd == "list":
            items = eho.fetch_items(variant=args.variant, ticker=args.ticker, date_from=args.date_from, date_to=args.date_to)
            for item in items[: args.limit]:
                print(json.dumps(item.raw, ensure_ascii=False, indent=2) if args.raw else pretty_item(item))
            print(f"Items returned: {len(items)}")
            return 0
        if args.cmd == "sync":
            _sync(db, eho, args)
            return 0
        if args.cmd == "download":
            _download(settings, db, http, args.ticker, [x.strip() for x in args.types.split(",")], args.limit, args.max_mb)
            return 0
        if args.cmd == "parse":
            _parse_downloaded(db, settings, args.ticker)
            return 0
        if args.cmd == "db-stats":
            print(json.dumps(db.stats(), indent=2))
            return 0
        if args.cmd == "metrics":
            for r in db.latest_metrics(args.ticker):
                desc = describe_report(r)
                print(
                    f"{r['issuer_code'] or '?':6} {desc.period_label:8} "
                    f"{r['metric_name']:34} {r['value']} {r['unit']}"
                )
            return 0
        if args.cmd == "reports":
            rows = db.report_inventory(args.ticker, preferred_only=args.preferred_only)
            if args.json:
                out = []
                for r in rows:
                    desc = describe_report(r)
                    out.append({
                        "preferred": r["preference_rank"] == 1,
                        "ticker": r["issuer_code"],
                        "period": desc.period_label,
                        "period_end": r["period_end"],
                        "scope": desc.scope,
                        "audit": desc.audit,
                        "publish_date": r["publish_date"],
                        "source_id": r["source_id"],
                        "candidates_for_period": r["period_candidates"],
                        "local_path": r["local_path"],
                    })
                print(json.dumps(out, ensure_ascii=False, indent=2))
            else:
                if not rows:
                    print("No parsed reports found.")
                for r in rows:
                    desc = describe_report(r)
                    marker = "*" if r["preference_rank"] == 1 else " "
                    published = r["publish_date"] or "?"
                    print(
                        f"{marker} {r['issuer_code'] or '?':6} {desc.period_label:8} "
                        f"{desc.scope:14} {desc.audit:11} published={published} "
                        f"source={r['source_id'] or '?'}"
                    )
                print("* = preferred report used for group-level latest metrics")
            return 0
        if args.cmd == "coverage":
            _print_coverage(db, args.ticker, args.json)
            return 0
        if args.cmd == "integrity":
            return _print_integrity(db, args.ticker, repair=args.repair, as_json=args.json)
        if args.cmd == "ttm":
            return _print_ttm(db, args.ticker, args.as_of, args.json)
        if args.cmd == "trends":
            return _print_trends(db, args.ticker, args.years, args.json)
        if args.cmd == "market-sync":
            return _market_sync(db, http, args.ticker, args.isin, args.json)
        if args.cmd == "market":
            return _print_market(db, args.ticker, args.history, args.json)
        if args.cmd == "valuation":
            return _print_valuation(db, args.ticker, args.as_of, args.json)
        if args.cmd == "debt":
            return _print_debt(db, args.ticker, args.as_of, args.json)
        if args.cmd == "compare-md":
            return _print_md_comparison(db, args.ticker, args.periods, args.json)
        if args.cmd == "parity":
            return _print_parity(db, args.ticker, args.periods, args.json)
        if args.cmd == "taxonomy":
            return _print_taxonomy(db, args.scheme, args.version, args.query, args.json)
        if args.cmd == "profile-seed":
            return _profile_seed(db, args.ticker, args.json)
        if args.cmd == "profile-validate":
            return _profile_file_action(db, args.file, write=False, as_json=args.json)
        if args.cmd == "profile-import":
            return _profile_file_action(db, args.file, write=True, as_json=args.json)
        if args.cmd == "company-profile":
            return _print_company_profile(db, args.ticker, args.as_of, args.json)
        if args.cmd == "activities":
            return _print_activities(db, args.ticker, args.json)
        if args.cmd == "segments":
            return _print_segments(db, args.ticker, args.json)
        if args.cmd == "profile-history":
            return _print_profile_history(db, args.ticker, args.json)
        if args.cmd == "profile-quality":
            return _print_profile_quality(db, args.ticker, args.as_of, args.json)
        if args.cmd == "peer-candidates":
            return _print_peer_candidates(db, args.ticker, args.limit, args.type, args.eligible_only, args.json)
        if args.cmd == "warehouse-init":
            data = init_warehouse(db, WarehouseLayout(settings.resolved_warehouse_dir), bootstrap_local=args.bootstrap_local)
            if args.json:
                print(json.dumps(data, ensure_ascii=False, indent=2))
            else:
                print(f"Initialized research warehouse: {data['warehouse']['root']}")
                print(f"Registered sources/datasets: {data['sources']} / {data['datasets']}")
                print(f"Bootstrapped local entities: {data['bootstrapped_entities']}")
                print("No external datasets were downloaded.")
            return 0
        if args.cmd == "warehouse-status":
            return _print_warehouse_status(settings, db, args.json)
        if args.cmd == "dataset-list":
            return _print_dataset_list(db, args.category, args.json)
        if args.cmd == "entity-register":
            seed_source_registry(db)
            e = register_entity(db, legal_name=args.name, country_code=args.country, lei=args.lei, isin=args.isin, ticker=args.ticker, exchange=args.exchange)
            if args.json:
                print(json.dumps(e, ensure_ascii=False, indent=2))
            else:
                print(f"Registered entity {e['entity_id']}: {e['legal_name']}")
                for i in e['identifiers']:
                    print(f"  {i['scheme']}={i['value']}")
            return 0
        if args.cmd == "entity-lookup":
            return _print_entity_lookup(db, args.query, args.limit, args.json)
        if args.cmd == "ingestion-jobs":
            return _print_ingestion_jobs(db, args.dataset, args.limit, args.json)
        if args.cmd == "pipeline":
            class A:
                pass

            a = A()
            a.variant = "financialReports"
            a.ticker = args.ticker
            a.date_from = args.date_from
            a.date_to = args.date_to
            _sync(db, eho, a)
            _download(settings, db, http, args.ticker, ["xlsx"], args.limit, 25.0)
            _parse_downloaded(db, settings, args.ticker)
            return 0
    except (ZseToolError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 1
