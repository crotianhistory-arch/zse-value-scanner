# Sources — v0.4.8 Official Classification Backbone

Primary machine source:

- Eurostat classifications as Linked Open Data, queried through the Publications Office Cellar SPARQL endpoint:
  https://publications.europa.eu/webapi/rdf/sparql
- Eurostat / EU Vocabularies classification catalogue and ShowVoc:
  https://op.europa.eu/en/web/eu-vocabularies/eurostat
  https://showvoc.op.europa.eu/

NACE Rev. 2.1:

- Eurostat NACE overview and guidance:
  https://ec.europa.eu/eurostat/web/nace
  https://ec.europa.eu/eurostat/en/web/nace/guidance
- NACE Rev. 2.1 manual, 2025 edition:
  https://ec.europa.eu/eurostat/web/products-manuals-and-guidelines/w/ks-gq-24-007
- Legal basis: Commission Delegated Regulation (EU) 2023/137.

CPA 2.2:

- Eurostat CPA overview:
  https://ec.europa.eu/eurostat/web/cpa
- Legal basis: Commission Delegated Regulation (EU) 2024/3103.
- Current correction: Commission Delegated Regulation (EU) 2026/880, published 20 April 2026, correcting specific CPA positions.
  https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32026R0880

Interpretation rule used by the code:

- Eurostat documents that each CPA product is assigned to one NACE activity; CPA 2.2 is aligned with NACE Rev. 2.1; the structure corresponds through the fourth level (class). The resulting CPA->NACE links in this patch are deterministic derived links and are labeled accordingly.
