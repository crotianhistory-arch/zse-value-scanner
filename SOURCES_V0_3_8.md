# Sources and policy — v0.3.8

This patch adds a read-only identity-corroboration layer for ZSE-listed issuers.

Primary source:
- Zagreb Stock Exchange issuer/security page reached with the exact local ISIN: `https://zse.hr/en/papir/310?isin=<ISIN>`.
- The page exposes the issuer legal name and LEI for the tested Croatian issuers.

Independent validation:
- GLEIF exact LEI record (`https://api.gleif.org/api/v1/lei-records/<LEI>`).
- Country consistency plus GLEIF `ISSUED` registration status and `ACTIVE` entity status are hard gates.

Evidence policy:
- A ZSE page result is not silently written to identity metadata.
- `B_CORROBORATED_OFFICIAL_EVIDENCE` means the exact-ISIN ZSE issuer page and exact GLEIF LEI record agree sufficiently to present the identity for human confirmation.
- Name similarity is not a persistence gate.
- General web search or LLM output remains a research lead only until corroborated by official evidence.

The patch does not change the SQLite schema and does not attach any LEI automatically.
