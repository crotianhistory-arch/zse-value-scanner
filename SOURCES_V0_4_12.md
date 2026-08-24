# Sources — v0.4.12 activity/classification translation pilot

Official sources used for the external-system pilot:

## European targets

- Eurostat NACE Rev. 2.1 official classification / v0.4.11 ShowVoc snapshot.
- Eurostat CPA 2.2 official classification / v0.4.11 ShowVoc snapshot.
- Eurostat NACE correspondence guidance explains that correspondence tables
  describe relationships between classification positions and should be used
  when comparing different classifications.

The installed translator validates NACE/CPA target codes against the local
standalone v0.4.11 reference DB, preserving the official concept URI and
English `skos:prefLabel`.

## United Nations ISIC Rev. 5

United Nations Statistics Division:

- ISIC Rev. 5 class `2710` — Manufacture of electric motors, generators,
  transformers and electricity distribution and control apparatus.
- ISIC Rev. 5 class `2732` — Manufacture of other electronic and electric
  wires and cables.
- UNSD classification resources publish the ISIC Rev. 5 structure and the
  official ISIC Rev. 4 ↔ Rev. 5 correspondence table.

## United States NAICS 2022

U.S. Census Bureau:

- `335311` — Power, Distribution, and Specialty Transformer Manufacturing.
- `335313` — Switchgear and Switchboard Apparatus Manufacturing.
- `335929` — Other Communication and Energy Wire Manufacturing.
- Census publishes NAICS concordance tables to ISIC for older NAICS revisions;
  v0.4.12 does not silently promote those older concordances into a direct
  NACE Rev. 2.1 ↔ NAICS 2022 official crosswalk.

## Mapping policy

The controlled-taxonomy → NACE/CPA/ISIC/NAICS mappings in this pilot are
analytical mappings with explicit relationship semantics. Official target
codes, labels and source URLs remain separate from the analytical claim that a
controlled activity belongs within or closely matches a classification
position.
