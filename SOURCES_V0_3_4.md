# Sources v0.3.4 — GLEIF discovery preflight

This milestone introduces only bounded, read-only Legal Entity Identifier discovery.
It does **not** bulk-download GLEIF data and does **not** register or merge entities in SQLite.

Primary source:

- Global Legal Entity Identifier Foundation (GLEIF), GLEIF API / LEI records.
  Base endpoint: `https://api.gleif.org/api/v1/lei-records`
  Legal-name filter: `filter[entity.legalName]`

Important interpretation guardrail:

GLEIF documents fuzzy/name-search results as candidate discovery, not proof that an LEI belongs to the intended entity. Potential matches must be corroborated against known attributes such as legal name, address/country, registration number and other identifiers before any entity-master merge.

The v0.3.4 code therefore labels candidates for review and performs no automatic merge.
