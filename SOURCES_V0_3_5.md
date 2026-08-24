# Sources and provenance — v0.3.5

## GLEIF
- Official GLEIF API: https://api.gleif.org/api/v1
- GLEIF API overview: https://www.gleif.org/en/lei-data/gleif-api
- GLEIF Level 1 ("Who is Who") reference-data overview:
  https://www.gleif.org/en/lei-data/access-and-use-lei-data/level-1-data-who-is-who

Design note: GLEIF states that the API is based on the GLEIF Golden Copy and supports
entity-name searches and LEI reference-data retrieval. v0.3.5 uses the API only for
a bounded, explicitly confirmed entity-identity pilot; it does not bulk-download the
full Golden Copy.

## Local provenance
- Durable metadata remains in data/zse.sqlite.
- Rebuildable raw GLEIF responses are stored under ZSE_WAREHOUSE_DIR.
- No automatic fuzzy match is persisted in v0.3.5.
