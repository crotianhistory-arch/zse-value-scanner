from __future__ import annotations

import json
from urllib.error import HTTPError

import pytest

import zse_tool.classification_backbone as cb


class _Response:
    status = 200

    def __init__(self, payload: bytes):
        self.payload = payload
        self.offset = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, n: int = -1) -> bytes:
        if self.offset >= len(self.payload):
            return b""
        if n < 0:
            n = len(self.payload) - self.offset
        out = self.payload[self.offset:self.offset + n]
        self.offset += len(out)
        return out


def _ok_payload() -> bytes:
    return json.dumps({"results": {"bindings": []}}).encode("utf-8")


def test_sparql_transport_uses_get_first(monkeypatch):
    seen = []

    def fake_urlopen(request, timeout):
        seen.append((request.get_method(), request.full_url, request.data))
        return _Response(_ok_payload())

    monkeypatch.setattr(cb, "urlopen", fake_urlopen)
    out = cb._sparql_request("https://publications.europa.eu/webapi/rdf/sparql", "SELECT * WHERE {?s ?p ?o} LIMIT 1")
    assert out == _ok_payload()
    assert [x[0] for x in seen] == ["GET"]
    assert "query=" in seen[0][1]
    assert seen[0][2] is None


def test_sparql_transport_falls_back_to_post(monkeypatch):
    seen = []

    def fake_urlopen(request, timeout):
        seen.append(request.get_method())
        if request.get_method() == "GET":
            raise HTTPError(request.full_url, 500, "boom", hdrs=None, fp=None)
        return _Response(_ok_payload())

    monkeypatch.setattr(cb, "urlopen", fake_urlopen)
    monkeypatch.setattr(cb.time, "sleep", lambda _: None)
    out = cb._sparql_request("https://publications.europa.eu/webapi/rdf/sparql", "SELECT * WHERE {?s ?p ?o} LIMIT 1")
    assert out == _ok_payload()
    assert seen == ["GET", "GET", "GET", "POST"]


def test_sparql_transport_reports_both_failures(monkeypatch):
    def fake_urlopen(request, timeout):
        raise HTTPError(request.full_url, 500, "boom", hdrs=None, fp=None)

    monkeypatch.setattr(cb, "urlopen", fake_urlopen)
    monkeypatch.setattr(cb.time, "sleep", lambda _: None)
    with pytest.raises(cb.ClassificationError) as exc:
        cb._sparql_request("https://publications.europa.eu/webapi/rdf/sparql", "SELECT * WHERE {?s ?p ?o} LIMIT 1")
    msg = str(exc.value)
    assert "GET:" in msg
    assert "POST:" in msg
