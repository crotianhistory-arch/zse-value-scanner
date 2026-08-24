# Sources — v0.4.0 European Activity Evidence Discovery

This patch is a bounded, read-only discovery/extraction layer. It does not assign peers, persist external financial facts, or use an LLM.

Primary technical sources checked 2026-08-22:

1. XBRL International, filings.xbrl.org API documentation
   - https://filings.xbrl.org/docs/api
   - Public JSON:API resources for filings/entities/validation messages.
   - Supports pagination, filters, `include=entity`, and sorting.

2. XBRL International, filings.xbrl.org repository documentation
   - https://filings.xbrl.org/docs/about
   - Repository provides an Inline XBRL viewer, xBRL-JSON, and a copy of the filed XBRL Report Package.
   - Repository is not complete; absence from the index must not be interpreted as absence of an ESEF filing.

3. XBRL International, xBRL-JSON tutorial / Recommendation
   - https://www.xbrl.org/guidance/xbrl-json-tutorial/
   - https://www.xbrl.org/Specification/xbrl-json/REC-2021-10-13/xbrl-json-REC-2021-10-13.html
   - Facts contain a `dimensions` object; taxonomy-defined dimensions appear as QName properties and can carry explicit or typed values.

4. Lauri Salmela, xbrl-filings-api documentation (independent client documentation used to cross-check current filings.xbrl.org field names)
   - https://lsalmela.github.io/xbrl-filings-api/getting-started.html
   - Documents filtering by `entity.identifier` (LEI) and current fields including `fxo_id`, `period_end`, `json_url`, `package_url`, `report_url`, `viewer_url`, and `sha256`.

5. IFRS Foundation, IFRS 8 Operating Segments
   - https://www.ifrs.org/issued-standards/list-of-standards/ifrs-8-operating-segments/
   - Conceptual basis for later activity interpretation: operating segments plus entity-wide disclosures about products/services, geographical areas, and major customers.

Evidence policy in this patch:
- R1_REPORTED_XBRL_FACT: directly reported XBRL fact.
- R2_REPORTED_TEXT: reserved for curated verbatim narrative evidence in a later patch.
- D1_DETERMINISTIC_EXTRACTION: deterministic inventory derived from XBRL concepts/dimensions.
- H1_HEURISTIC_ACTIVITY_CANDIDATE: bounded keyword window from reported XHTML; a research lead, not a peer conclusion.
