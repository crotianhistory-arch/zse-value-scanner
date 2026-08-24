# Sources — v0.4.11 Eurostat ShowVoc full-classification snapshot

Primary source used by the live v0.4.11 sync:

- Eurostat ShowVoc / Semantic Turkey SPARQL service:
  `https://showvoc.op.europa.eu/semanticturkey/it.uniroma2.art.semanticturkey/st-core-services/SPARQL/evaluateQuery`

Project contexts verified read-only on 2026-08-24:

- NACE Rev. 2.1:
  `ESTAT_Statistical_Classification_of_Economic_Activities_in_the_European_Community_Rev._2.1._(NACE_2.1)`
- CPA 2.2:
  `ESTAT_Statistical_classification_of_products_by_activity,_2.2_(CPA_2.2)`

The diagnostic established the following directly from those project
contexts:

- NACE Rev. 2.1: 1,047 unique concepts with notation;
  level counts 22 / 87 / 287 / 651.
- CPA 2.2: 5,824 unique concepts with notation;
  level counts 22 / 87 / 284 / 644 / 1,433 / 3,354.
- Every observed concept had EN, FR, and DE `skos:prefLabel` coverage.
- Every non-root concept had one explicit `skos:broader` parent.
- No duplicate codes or multiple direct parents were observed.

The v0.4.10 SDMX path remains in the codebase only for historical catalog
compatibility. It is not the v0.4.11 full-classification acquisition path.
