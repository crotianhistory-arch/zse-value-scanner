# Sources — v0.3.6 GLEIF batch review

- GLEIF API: https://www.gleif.org/en/lei-data/gleif-api
  - Used for bounded legal-name candidate discovery.
  - GLEIF documents filters, single-field/full-text search and fuzzy matching.
- GLEIF Level 1 Data ("Who is Who"):
  https://www.gleif.org/en/lei-data/access-and-use-lei-data/level-1-data-who-is-who
  - Used as the authoritative identity-reference layer after explicit confirmation.

Policy:
- Candidate discovery is read-only.
- Legal-name similarity is evidence, never an automatic write gate.
- Country and GLEIF status conflicts block confirmation suggestions.
- Only the already-tested `zse_tool.gleif_ingest` explicit-confirmation path may attach an LEI.
