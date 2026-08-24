from __future__ import annotations

import pytest

from zse_tool.zse_identity import parse_zse_issuer_html


def test_live_like_navigation_does_not_become_issuer_name():
    html = """
    <html><body>
      <nav>
        <span>Trading</span><span>Instrument</span><span>Issuer</span>
        <span>Announcements</span><span>Historical Data</span>
      </nav>
      <h1>HT d.d.</h1>
      <section>
        <div>Hrvatski Telekom d.d.</div>
        <div>Home Member State</div><div>Hrvatska (Croatia)</div>
        <div>LEI</div><div>097900BFHJ0000029454</div>
        <div>Tax Number</div><div>81793146560</div>
      </section>
    </body></html>
    """
    row = parse_zse_issuer_html(
        html,
        isin="HRHT00RA0005",
        source_url="https://zse.hr/en/papir/310?isin=HRHT00RA0005",
    )
    assert row.issuer_name == "Hrvatski Telekom d.d."
    assert row.issuer_name != "Announcements"


def test_live_like_croatian_detail_block_uses_name_before_member_state():
    html = """
    <html><body>
      <nav><span>Izdavatelj</span><span>Objave</span></nav>
      <section>
        <div>Granolio d.d. za proizvodnju, trgovinu i usluge</div>
        <div>Matična država članica</div><div>Hrvatska</div>
        <div>LEI</div><div>213800O3Z6ZSDBAKG321</div>
        <div>Porezni broj</div><div>59064993527</div>
      </section>
    </body></html>
    """
    row = parse_zse_issuer_html(html, isin="HRGRNLRA0006", source_url="x")
    assert row.issuer_name == "Granolio d.d. za proizvodnju, trgovinu i usluge"


def test_navigation_only_issuer_label_is_rejected_instead_of_false_provenance():
    html = """
    <nav><span>Issuer</span><span>Announcements</span><span>Historical Data</span></nav>
    <div>LEI</div><div>097900BFHJ0000029454</div>
    """
    with pytest.raises(ValueError, match="issuer name"):
        parse_zse_issuer_html(html, isin="HRHT00RA0005", source_url="x")
