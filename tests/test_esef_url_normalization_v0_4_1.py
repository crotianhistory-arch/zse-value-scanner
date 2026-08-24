from __future__ import annotations

import pytest

from zse_tool.esef import _canonical_repository_url, discover_filings

LEI = "724500HDW6IWR9J5YT90"


def _payload(urls: dict[str, str]):
    return {
        "data": [{
            "type": "filing",
            "id": "f1",
            "attributes": {
                "country": "NL",
                "fxo_id": f"{LEI}-2025-12-31-ESEF-NL-0",
                "period_end": "2025-12-31",
                "processed": "2026-02-20T00:00:00",
                "json_url": urls["json_url"],
                "package_url": urls["package_url"],
                "report_url": urls["report_url"],
                "viewer_url": urls["viewer_url"],
            },
            "relationships": {"entity": {"data": {"type": "entity", "id": "e1"}}},
        }],
        "included": [{
            "type": "entity",
            "id": "e1",
            "attributes": {"identifier": LEI, "name": "Alfen N.V."},
        }],
    }


def test_relative_repository_url_becomes_absolute_https():
    assert _canonical_repository_url("/a/b.json.gz") == "https://filings.xbrl.org/a/b.json.gz"


def test_scheme_relative_repository_url_inherits_https():
    assert _canonical_repository_url("//filings.xbrl.org/a/b.zip") == "https://filings.xbrl.org/a/b.zip"


def test_plain_relative_repository_url_becomes_absolute_https():
    assert _canonical_repository_url("a/b.xhtml.gz") == "https://filings.xbrl.org/a/b.xhtml.gz"


def test_existing_repository_https_url_is_preserved():
    url = "https://filings.xbrl.org/a/b.json.gz"
    assert _canonical_repository_url(url) == url


def test_http_repository_url_is_rejected_not_silently_upgraded():
    with pytest.raises(ValueError, match="must use HTTPS"):
        _canonical_repository_url("http://filings.xbrl.org/a/b.json.gz")


def test_external_host_is_rejected():
    with pytest.raises(ValueError, match="unexpected filings repository host"):
        _canonical_repository_url("https://example.com/a/b.json.gz")


def test_discovery_normalizes_all_api_repository_links():
    payload = _payload({
        "json_url": "/x/alfen-2025-12-31-en.json.gz",
        "package_url": "/x/alfen-2025-12-31-en.zip",
        "report_url": "/x/reports/alfen-2025-12-31-en.xhtml.gz",
        "viewer_url": "/filing/example",
    })

    class Response:
        url = "https://filings.xbrl.org/api/filings?filtered"
        def raise_for_status(self):
            pass
        def json(self):
            return payload

    class Session:
        def get(self, *args, **kwargs):
            return Response()

    row = discover_filings(LEI, session=Session())[0]
    assert row.json_url == "https://filings.xbrl.org/x/alfen-2025-12-31-en.json.gz"
    assert row.package_url == "https://filings.xbrl.org/x/alfen-2025-12-31-en.zip"
    assert row.xhtml_url == "https://filings.xbrl.org/x/reports/alfen-2025-12-31-en.xhtml.gz"
    assert row.viewer_url == "https://filings.xbrl.org/filing/example"
    assert row.language == "en"
