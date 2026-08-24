# Sources / provenance — v0.3.9

The parser correction is based on the live official Zagreb Stock Exchange
issuer page structure observed on 2026-08-22.

For HT (ISIN HRHT00RA0005), the page contains:
- an earlier navigation tab named `Issuer` followed by `Announcements`;
- the issuer-detail legal name `Hrvatski Telekom d.d.` immediately before
  `Home Member State`;
- LEI `097900BFHJ0000029454`;
- Tax Number `81793146560`.

This patch changes only HTML parsing/provenance extraction. Identity persistence
remains explicit and separate.
