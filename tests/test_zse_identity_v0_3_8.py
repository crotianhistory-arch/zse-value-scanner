from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from zse_tool.gleif import GLEIFCandidate
from zse_tool.zse_identity import (
    ZSEIssuerIdentity,
    corroborate_ticker,
    fetch_zse_issuer_identity,
    parse_zse_issuer_html,
    unidentified_zse_tickers,
)


class FakeDB:
    def __init__(self, path: Path):
        self.path = path
        with self.connect() as conn:
            conn.execute("CREATE TABLE research_entities(entity_id TEXT PRIMARY KEY, legal_name TEXT, country_code TEXT)")
            conn.execute("CREATE TABLE entity_identifiers(entity_id TEXT, scheme TEXT, value TEXT, PRIMARY KEY(scheme,value))")

    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def add_entity(self, entity_id: str, name: str, country: str, ticker: str, isins: list[str], leis: list[str] | None = None):
        with self.connect() as conn:
            conn.execute("INSERT INTO research_entities VALUES(?,?,?)", (entity_id, name, country))
            conn.execute("INSERT INTO entity_identifiers VALUES(?,?,?)", (entity_id, "TICKER:ZSE", ticker))
            for isin in isins:
                conn.execute("INSERT INTO entity_identifiers VALUES(?,?,?)", (entity_id, "ISIN", isin))
            for lei in leis or []:
                conn.execute("INSERT INTO entity_identifiers VALUES(?,?,?)", (entity_id, "LEI", lei))

    def research_entity_by_identifier(self, scheme: str, value: str):
        with self.connect() as conn:
            return conn.execute(
                "SELECT e.* FROM research_entities e JOIN entity_identifiers i ON i.entity_id=e.entity_id "
                "WHERE i.scheme=? AND UPPER(i.value)=UPPER(?) LIMIT 1",
                (scheme, value),
            ).fetchone()


def _candidate(lei: str, *, country: str = "HR", reg: str = "ISSUED", status: str = "ACTIVE", name: str = "Issuer d.d."):
    return GLEIFCandidate(
        lei=lei,
        legal_name=name,
        legal_address_country=country,
        jurisdiction=country,
        registration_status=reg,
        entity_status=status,
        registration_authority_id="RA000000",
        registration_authority_entity_id="123",
        query_name=name,
        query_country=country,
        name_similarity=1.0,
        country_match=True,
        match_class="EXACT",
        source_url=f"https://api.gleif.org/api/v1/lei-records/{lei}",
    )


def _english_html(name="Hrvatski Telekom d.d.", lei="097900BFHJ0000029454", tax="81793146560"):
    return f"""
    <html><body>
      <div>Issuer</div><div>{name}</div>
      <div>Home Member State</div><div>Hrvatska (Croatia)</div>
      <div>LEI (Legal Entity Identifier (ISO 17442))</div><div>{lei}</div>
      <div>Tax Number</div><div>{tax}</div>
    </body></html>
    """


def test_parse_zse_english_page():
    row = parse_zse_issuer_html(_english_html(), isin="HRHT00RA0005", source_url="https://zse.hr/en/papir/310?isin=HRHT00RA0005")
    assert row.issuer_name == "Hrvatski Telekom d.d."
    assert row.lei == "097900BFHJ0000029454"
    assert row.country_code == "HR"
    assert row.tax_number == "81793146560"


def test_parse_zse_croatian_page():
    html = """
    <div>Izdavatelj</div><div>Granolio d.d. za proizvodnju, trgovinu i usluge</div>
    <div>Matična država članica</div><div>Hrvatska</div>
    <div>LEI</div><div>213800O3Z6ZSDBAKG321</div>
    <div>Porezni broj</div><div>59064993527</div>
    """
    row = parse_zse_issuer_html(html, isin="HRGRNLRA0006", source_url="x")
    assert row.issuer_name.startswith("Granolio")
    assert row.lei == "213800O3Z6ZSDBAKG321"
    assert row.country_code == "HR"


def test_parse_requires_lei():
    with pytest.raises(ValueError, match="LEI"):
        parse_zse_issuer_html("<div>Issuer</div><div>Example d.d.</div>", isin="HRHT00RA0005", source_url="x")


def test_fetch_uses_exact_isin_query():
    seen = {}
    class Response:
        url = "https://zse.hr/en/papir/310?isin=HRHT00RA0005"
        text = _english_html()
        def raise_for_status(self): pass
    class Session:
        def get(self, url, **kwargs):
            seen["url"] = url
            seen.update(kwargs)
            return Response()
    row = fetch_zse_issuer_identity("HRHT00RA0005", session=Session())
    assert seen["params"] == {"isin": "HRHT00RA0005"}
    assert row.lei == "097900BFHJ0000029454"


def test_already_identified_skips_external_fetch(tmp_path):
    db = FakeDB(tmp_path / "x.sqlite")
    db.add_entity("E1", "KOEI d.d.", "HR", "KOEI", ["HRKOEIRA0009"], ["74780000H0SHMRAW0I15"])
    def boom(*args, **kwargs):
        raise AssertionError("should not fetch")
    rows = corroborate_ticker(db, "KOEI", zse_fetcher=boom, gleif_fetcher=boom)
    assert rows[0].state == "ALREADY_IDENTIFIED"
    assert rows[0].disposition == "SKIP"


def test_successful_official_corroboration(tmp_path):
    db = FakeDB(tmp_path / "x.sqlite")
    db.add_entity("E1", "HT d.d.", "HR", "HT", ["HRHT00RA0005"])
    zse = ZSEIssuerIdentity("HRHT00RA0005", "Hrvatski Telekom d.d.", "097900BFHJ0000029454", "HR", "81793146560", "https://zse.hr/en/papir/310?isin=HRHT00RA0005")
    rows = corroborate_ticker(
        db, "HT",
        zse_fetcher=lambda *a, **k: zse,
        gleif_fetcher=lambda *a, **k: (_candidate(zse.lei, name="Hrvatski Telekom d.d."), {}, b"{}"),
    )
    row = rows[0]
    assert row.state == "CORROBORATED"
    assert row.evidence_level == "B_CORROBORATED_OFFICIAL_EVIDENCE"
    assert row.disposition == "REVIEW_CONFIRM"
    assert row.lei == "097900BFHJ0000029454"
    assert "--ticker HT --lei 097900BFHJ0000029454" in row.confirmation_command


def test_country_conflict_blocks(tmp_path):
    db = FakeDB(tmp_path / "x.sqlite")
    db.add_entity("E1", "HT d.d.", "HR", "HT", ["HRHT00RA0005"])
    zse = ZSEIssuerIdentity("HRHT00RA0005", "Hrvatski Telekom d.d.", "097900BFHJ0000029454", "HR", None, "zse")
    rows = corroborate_ticker(
        db, "HT", zse_fetcher=lambda *a, **k: zse,
        gleif_fetcher=lambda *a, **k: (_candidate(zse.lei, country="DE"), {}, b"{}"),
    )
    assert rows[0].state == "OFFICIAL_CORROBORATION_ERROR"
    assert rows[0].disposition == "BLOCK"
    assert "country conflict" in rows[0].note


def test_non_issued_blocks(tmp_path):
    db = FakeDB(tmp_path / "x.sqlite")
    db.add_entity("E1", "GRNL d.d.", "HR", "GRNL", ["HRGRNLRA0006"])
    zse = ZSEIssuerIdentity("HRGRNLRA0006", "Granolio d.d.", "213800O3Z6ZSDBAKG321", "HR", None, "zse")
    rows = corroborate_ticker(
        db, "GRNL", zse_fetcher=lambda *a, **k: zse,
        gleif_fetcher=lambda *a, **k: (_candidate(zse.lei, reg="LAPSED"), {}, b"{}"),
    )
    assert rows[0].disposition == "BLOCK"
    assert "not ISSUED" in rows[0].note


def test_multiple_isins_to_distinct_leis_block(tmp_path):
    db = FakeDB(tmp_path / "x.sqlite")
    db.add_entity("E1", "Example d.d.", "HR", "EX", ["HREX00000001", "HREX00000019"])
    mapping = {
        "HREX00000001": "11111111111111111111",
        "HREX00000019": "22222222222222222222",
    }
    def zse_fetch(isin, **kwargs):
        return ZSEIssuerIdentity(isin, "Example d.d.", mapping[isin], "HR", None, "zse:" + isin)
    rows = corroborate_ticker(
        db, "EX", zse_fetcher=zse_fetch,
        gleif_fetcher=lambda lei, **k: (_candidate(lei), {}, b"{}"),
    )
    assert rows[0].state == "OFFICIAL_AMBIGUITY"
    assert rows[0].disposition == "BLOCK"


def test_unidentified_tickers_excludes_existing_lei(tmp_path):
    db = FakeDB(tmp_path / "x.sqlite")
    db.add_entity("E1", "A", "HR", "AAA", ["HRAA00000001"], ["11111111111111111111"])
    db.add_entity("E2", "B", "HR", "BBB", ["HRBB00000002"])
    assert unidentified_zse_tickers(db) == ["BBB"]
