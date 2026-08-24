from __future__ import annotations

import pytest

from zse_tool.gleif import _normalize_name, search_legal_name


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, *, params, headers, timeout):
        self.calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        return FakeResponse(self.payload)


def _record(*, lei, name, country="HR", status="ISSUED"):
    return {
        "id": lei,
        "attributes": {
            "lei": lei,
            "entity": {
                "legalName": {"name": name},
                "legalAddress": {"country": country},
                "jurisdiction": country,
                "status": "ACTIVE",
                "registeredAt": {"id": "RA000000"},
                "registeredAs": "12345678",
            },
            "registration": {"status": status},
        },
    }


def test_normalize_name_is_diacritic_and_punctuation_stable():
    assert _normalize_name("KONČAR - Elektroindustrija d.d.") == "koncar elektroindustrija d d"


def test_search_is_bounded_and_read_only_candidate_classification():
    payload = {
        "data": [
            _record(lei="11111111111111111111", name="KONČAR - ELEKTROINDUSTRIJA d.d."),
            _record(lei="22222222222222222222", name="Koncar Example Holding", country="DE"),
        ]
    }
    session = FakeSession(payload)
    rows = search_legal_name("KONČAR - ELEKTROINDUSTRIJA d.d.", country="HR", limit=2, session=session)

    assert len(rows) == 2
    assert rows[0].lei == "11111111111111111111"
    assert rows[0].match_class == "EXACT"
    assert rows[0].country_match is True
    assert rows[1].country_match is False

    call = session.calls[0]
    assert call["params"]["page[size]"] == 2
    assert call["params"]["page[number]"] == 1
    assert call["params"]["filter[entity.legalName]"] == "KONČAR - ELEKTROINDUSTRIJA d.d."
    assert call["headers"]["Accept"] == "application/vnd.api+json"


def test_country_validation_is_conservative():
    with pytest.raises(ValueError, match="two-letter ISO"):
        search_legal_name("Example AG", country="Germany", session=FakeSession({"data": []}))


def test_limit_prevents_accidental_bulk_query():
    with pytest.raises(ValueError, match="between 1 and 25"):
        search_legal_name("Example AG", limit=1000, session=FakeSession({"data": []}))
