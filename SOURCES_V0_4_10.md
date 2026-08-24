# Sources — v0.4.10 Eurostat SDMX Classification Snapshot

Official Eurostat sources only:

- Eurostat, “API - Migrating from Bulk Download Listing urls to API urls”:
  documents codelist discovery via
  `/sdmx/2.1/codelist/ESTAT/all/latest?detail=allstubs` and per-codelist downloads
  via `/sdmx/2.1/codelist/ESTAT/<CODELIST_CODE>/latest?format=TSV&lang=en`.
- Eurostat, “API - Detailed guidelines - SDMX2.1 API - structure queries”:
  documents codelist structure queries plus TSV and language parameters.
- Eurostat, “Code lists - Metadata”:
  states Eurostat code lists are available in English, French and German.
- Eurostat NACE Rev. 2.1 documentation:
  22 sections, 87 divisions, 287 groups, 651 classes.
- Eurostat CPA 2.2 documentation:
  22 sections, 87 divisions, 284 groups, 644 classes, 1,432 categories,
  3,359 subcategories.
- Eurostat CPA overview/guidance:
  CPA 2.2 is aligned to NACE Rev. 2.1 and applicable from reference year 2025.

The previous Cellar/SPARQL source remains supported only for the legacy v0.1
catalog; it is not required by the v0.4.10 live pilot.
