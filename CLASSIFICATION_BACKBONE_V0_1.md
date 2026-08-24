# Official classification backbone v0.1

Purpose: build a rebuildable, versioned reference database of official statistical classifications before company discovery.

## v0.4.8 scope

Current European backbone only:

- NACE Rev. 2.1 — economic activities, applicable to European statistics from 2025 onwards.
- CPA 2.2 — products/services by activity, applicable from 2025 onwards.

The module resolves the official concept schemes from Eurostat's Linked Open Data service, downloads the full hierarchy, all available language-tagged preferred/alternative labels, and English explanatory notes. It writes a standalone SQLite file under a caller-selected path. It does **not** modify the ZSE operational SQLite database.

## Provenance model

Raw SPARQL response pages are retained content-addressed under the selected raw directory. A retrieval manifest records endpoint, resolved scheme URI, per-page SHA-256, item/label/note counts, languages, retrieval time and normalized SHA-256.

The normalized SQLite database separates:

- official schemes
- official items and hierarchy
- multilingual labels
- English explanatory notes
- derived cross-classification links

Reported/official material is never overwritten by analytical mappings.

## CPA -> NACE structural links

Eurostat documents that CPA 2.2 is aligned with NACE Rev. 2.1 and that, through the fourth level (class), the structures correspond. The module therefore creates deterministic links from CPA items to their originating NACE class. These are labeled:

`D1_DETERMINISTIC_FROM_OFFICIAL_STRUCTURE`

They are not represented as separately reported company classifications.

## Integrity gates

The sync aborts unless the official item counts match the current published structures:

- NACE Rev. 2.1: 22 sections + 87 divisions + 287 groups + 651 classes = 1,047 items.
- CPA 2.2: 22 sections + 87 divisions + 284 groups + 644 classes + 1,432 categories + 3,359 subcategories = 5,828 items.

Every official code must also have an English label. The resulting SQLite file is built through a temporary file, checked with `PRAGMA integrity_check`, then atomically moved into place. Existing reference DBs are not replaced unless `--replace` is explicit.

## Language strategy

The database stores all language-tagged labels returned by Eurostat. This is the translation backbone: a French, German, Croatian, Swedish, Italian, etc. official label is attached to the same classification code rather than machine-translated into a new fact.

Machine translation / LLM interpretation remains optional for company narrative evidence that is more detailed than official classification labels.

## What v0.4.8 does not do

- no company classification assignment
- no competitor discovery
- no peer ranking
- no LLM
- no writes to `data/zse.sqlite`
- no national classification adapters yet
- no NACE Rev. 2 -> Rev. 2.1 correspondence ingestion yet
- no ISIC/CPC global layer yet

Those can be added on top of this reference schema without changing the provenance model.
