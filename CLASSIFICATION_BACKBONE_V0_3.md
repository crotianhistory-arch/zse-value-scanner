# Official classification backbone v0.3

v0.4.11 adds a strict Eurostat ShowVoc transport for the full NACE Rev. 2.1
and CPA 2.2 hierarchies. The older v0.1 Cellar/SPARQL and v0.2 SDMX
catalogs remain readable for historical reproducibility, but the v0.3 catalog
is the supported live full-classification path.

## Why this change

The v0.4.10 SDMX resolver could select `NACE_R2_1`, whose dissemination
codelist exposes only 22 top-level sections. The integrity gate correctly
rejected that result because the complete NACE Rev. 2.1 hierarchy contains
1,047 concepts.

A read-only live diagnostic against Eurostat ShowVoc on 2026-08-24 confirmed
complete project-scoped concept sets, exact parent relationships, unique
notations, and complete English/French/German `skos:prefLabel` coverage.

## Frozen integrity gates

NACE Rev. 2.1:

- level 1: 22
- level 2: 87
- level 3: 287
- level 4: 651
- total: 1,047

CPA 2.2:

- level 1: 22
- level 2: 87
- level 3: 284
- level 4: 644
- level 5: 1,433
- level 6: 3,354
- total: 5,824

The sync aborts before replacing the reference DB if any total, level count,
explicit parent relation, code uniqueness rule, or required-language label
coverage check fails.

## Acquisition

For each configured ShowVoc project the sync:

1. sends project-scoped SPARQL POST requests to the official ShowVoc service;
2. pages through concepts and explicit `skos:broader` relationships;
3. pages through EN/FR/DE `skos:prefLabel` values;
4. preserves every raw response content-addressed by SHA-256;
5. normalizes official concept URIs, codes, explicit parents, levels, and labels;
6. writes a content-addressed retrieval manifest;
7. atomically builds the standalone SQLite classification reference DB.

The installer itself performs no network request and no database write.

## Provenance boundary

- concept URI, code, `skos:broader`, `skos:prefLabel`: official source data;
- code-shape level number: deterministic derivation;
- integrity counts: hard validation gates frozen from the verified live projects;
- CPA -> NACE structural link: deterministic relationship already used by the
  reference schema;
- no company is assigned to any classification code by this patch;
- no peer, competitor, valuation, or LLM decision is created by this patch.

## Data isolation

The live sync writes only to the explicitly supplied reference DB and raw
directory. It does not write `data/zse.sqlite` and does not change warehouse,
financial, company, identity, peer, or competitor data.
