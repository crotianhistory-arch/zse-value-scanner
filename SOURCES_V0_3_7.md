# Sources and policy — v0.3.7

## Purpose

v0.3.7 introduces an ISIN-first deterministic identity-resolution ladder. It is read-only with respect to entity identifiers.

## Evidence ladder

- **A_OFFICIAL_ISIN_MAPPING** — GLEIF API `filter[isin]`, backed by the GLEIF/ANNA ISIN-to-LEI mapping process. Strongest discovery path in this patch. Human confirmation is still required before persistence.
- **B_CORROBORATED_OFFICIAL_EVIDENCE** — reserved for future multi-source corroboration (issuer/ZSE/regulator/registry evidence).
- **C_NAME_CANDIDATE** — GLEIF legal-name search candidate. Name similarity is evidence only.
- **D_RESEARCH_LEAD_REQUIRED** — deterministic paths exhausted. Official web research should be attempted next; LLM-assisted internet research may help generate leads, but a lead cannot become a stored identifier without official corroboration.

## Official references

- GLEIF API documentation: https://documenter.getpostman.com/view/7679680/SVYrrxuU
  - documents `filter[isin]` for finding LEI records by ISIN.
- GLEIF API overview: https://www.gleif.org/en/lei-data/gleif-api
- GLEIF ISIN-to-LEI mapping downloads: https://www.gleif.org/en/lei-data/lei-mapping/download-isin-to-lei-relationship-files
  - describes the mapping process established by ANNA and certified by GLEIF.

## Safety properties

- no automatic identity writes;
- existing LEIs skip external discovery;
- a failure of the stronger ISIN path blocks rather than silently weakening to name search;
- multiple distinct eligible mapped LEIs are treated as an ambiguity conflict;
- country and current GLEIF registration/entity status are hard review gates;
- web/LLM output remains research-lead evidence until corroborated.
