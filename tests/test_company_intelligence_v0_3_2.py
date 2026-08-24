from pathlib import Path

from zse_tool.cli import build_parser
from zse_tool.company_intelligence import build_peer_candidates, seed_all_bundled_profiles
from zse_tool.storage import Database


def _add_financial_fingerprint(db: Database, ticker: str, *, sales: float = 100_000_000.0) -> None:
    path = f"/tmp/{ticker.lower()}-2026-q2.xlsx"
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO parsed_reports(local_path, issuer_code, period_end, year, quarter, consolidated, audited, parsed_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (path, ticker, "2026-06-30", 2026, 2, 1, 0, "2026-07-31T00:00:00+00:00"),
        )
        values = {
            "sales_revenue_ytd": sales,
            "ebit_ytd": sales * 0.10,
            "ebitda_simple_ytd": sales * 0.15,
            "free_cash_flow_ytd": sales * 0.08,
            "operating_cash_flow_ytd": sales * 0.14,
            "capex_ytd": sales * 0.05,
            "current_ratio": 1.5,
            "debt_to_equity_parent": 0.8,
            "net_debt_to_ebitda_run_rate": 1.0,
            "interest_coverage_ebit": 5.0,
        }
        for name, value in values.items():
            unit = "EUR" if name.endswith("_ytd") else "x"
            conn.execute(
                "INSERT INTO metrics(local_path, issuer_code, period_end, metric_name, value, unit, quality) "
                "VALUES (?,?,?,?,?,?,?)",
                (path, ticker, "2026-06-30", name, value, unit, "test"),
            )


def _add_market(db: Database, ticker: str, market_cap: float = 100_000_000.0) -> None:
    db.save_market_snapshot(
        {
            "ticker": ticker,
            "isin": f"TEST{ticker}0000",
            "observed_at": "2026-08-20T12:00:00+00:00",
            "source_url": "https://example.invalid/zse",
            "source_kind": "test",
            "listed_quantity": 1_000_000,
            "market_cap_eur": market_cap,
            "implied_price_eur": market_cap / 1_000_000,
            "price_basis": "test",
            "quality": "test",
        }
    )


def test_business_peer_gate_rejects_unrelated_company_even_when_financials_match(tmp_path: Path):
    db = Database(tmp_path / "zse.sqlite")
    seed_all_bundled_profiles(db)
    _add_financial_fingerprint(db, "GRNL")
    _add_financial_fingerprint(db, "HT")

    rows = build_peer_candidates(db, "GRNL", peer_type="business")
    ht = next(r for r in rows if r.ticker == "HT")
    assert ht.activity_score == 0.0
    assert ht.size_score == 100.0
    assert ht.business_model_score is not None and ht.business_model_score > 99
    assert ht.eligible is False
    assert ht.status == "REJECTED"
    assert ht.score is None


def test_business_model_peer_can_be_cross_industry_without_becoming_business_peer(tmp_path: Path):
    db = Database(tmp_path / "zse.sqlite")
    seed_all_bundled_profiles(db)
    _add_financial_fingerprint(db, "GRNL")
    _add_financial_fingerprint(db, "HT")

    rows = build_peer_candidates(db, "GRNL", peer_type="business-model")
    ht = next(r for r in rows if r.ticker == "HT")
    assert ht.activity_score == 0.0
    assert ht.eligible is True
    assert ht.status == "STRONG"
    assert ht.score is not None and ht.score > 99
    assert ht.features_compared >= 8


def test_investment_peer_score_does_not_imply_business_comparability(tmp_path: Path):
    db = Database(tmp_path / "zse.sqlite")
    seed_all_bundled_profiles(db)
    _add_financial_fingerprint(db, "GRNL")
    _add_financial_fingerprint(db, "HT")
    _add_market(db, "GRNL")
    _add_market(db, "HT")

    investment = next(r for r in build_peer_candidates(db, "GRNL", peer_type="investment") if r.ticker == "HT")
    business = next(r for r in build_peer_candidates(db, "GRNL", peer_type="business") if r.ticker == "HT")
    assert investment.eligible is True
    assert investment.score is not None and investment.score > 99
    assert investment.market_cap_score == 100.0
    assert business.eligible is False


def test_product_alias_maps_to_business_peer_semantics(tmp_path: Path):
    db = Database(tmp_path / "zse.sqlite")
    seed_all_bundled_profiles(db)
    business = build_peer_candidates(db, "GRNL", peer_type="business")
    product = build_peer_candidates(db, "GRNL", peer_type="product")
    assert [(r.ticker, r.status, r.eligible) for r in product] == [
        (r.ticker, r.status, r.eligible) for r in business
    ]


def test_peer_cli_accepts_separate_peer_types():
    parser = build_parser()
    for peer_type in ("business", "product", "business-model", "model", "investment", "all"):
        args = parser.parse_args(["peer-candidates", "--ticker", "GRNL", "--type", peer_type])
        assert args.type == peer_type
