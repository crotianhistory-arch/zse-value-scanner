# Official classification backbone v0.2

The core snapshot now uses Eurostat's SDMX 2.1 codelist dissemination API rather
than remote Cellar SPARQL queries.

## Acquisition

1. Download all Eurostat codelist stubs from the official SDMX structure API.
2. Resolve the requested NACE Rev. 2.1 and CPA 2.2 codelist artifacts.
3. Download each selected codelist independently in English, French and German TSV.
4. Preserve the raw XML/TSV responses by SHA-256 under the configured raw directory.
5. Filter out dissemination-only aggregate codes (`TOTAL`, ranges, special aggregates).
6. Accept only valid official classification code shapes.
7. Require the exact official total and per-level counts before writing the DB.
8. Reconstruct parent/child relationships from the official ordered code list.
9. Build the standalone SQLite reference DB atomically.

## Language scope

Eurostat documents dissemination code lists in English, French and German. These
three official labels are the first reference-language layer. National
classification adapters can later add Croatian, Serbian, Albanian, Italian,
Polish, etc. without rewriting the common NACE/CPA concepts.

## Notes

The SDMX codelist snapshot supplies codes and labels, not the richer ShowVoc/XKOS
explanatory-note layer used by the original v0.4.8 design. v0.4.10 therefore keeps
`classification_notes` empty for this core snapshot. Notes can be added later as
an optional official enrichment step; their absence must never block the stable
classification backbone.

## Provenance boundary

- SDMX code and label: official reported reference data.
- Parent hierarchy reconstructed from ordered official codes: deterministic derivation.
- CPA -> NACE link: deterministic relationship based on the aligned official structure.
- No company is assigned to a code by this patch.
