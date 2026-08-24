# Classification crosswalk framework v0.1

v0.4.13 introduces a generic crosswalk graph for official classification correspondences.
It deliberately does not add more transformer-specific matching rules.

## Architecture

System-specific acquisition/parsing stays at the edges. Normalized correspondence edges use a common representation:

- source system, version and code;
- target system, version and code;
- official source and raw SHA-256;
- official change type and note when supplied;
- evidence class;
- graph cardinality derived from the complete official edge set.

The first adapter ingests the UNSD ISIC Rev. 4 -> Rev. 5 correspondence workbook. The database and query engine are not ISIC-specific and can later hold Eurostat, Census, Statistics Canada, national extensions, or other official tables.

## Evidence boundary

`O1_OFFICIAL_CROSSWALK` means a row came from an official correspondence source. The core does not infer that every correspondence is exact equivalence.

`E1_EMPIRICAL_CROSS_SYSTEM_EVIDENCE` is reserved for a real entity observed under codes from two systems in cited source documents. Empirical observations are stored in a separate table and never upgrade themselves into official correspondence edges.

The existing `A3_ANALYTICAL_CLASSIFICATION_MAPPING` remains a separate analytical layer.

## Mapping shape

The graph derives local edge shape from the complete source table:

- `one_to_one`
- `one_to_many`
- `many_to_one`
- `many_to_many`

Official GSIM change type and source notes are retained verbatim. Mapping shape is deterministic graph metadata, not a replacement for the official semantics.

## Anti-overfitting rule

A new product or sector must not require changes to the crosswalk engine. New classification systems may require adapters, but they normalize into the same graph.

The v0.4.13 live validation checks both an electrical code and a food-manufacturing code and also discovers one-to-many/many-to-one examples directly from the official table.

## Data isolation

The crosswalk reference database is explicitly supplied by the caller and should live under the research warehouse/scratch area. The patch does not write `data/zse.sqlite`.
