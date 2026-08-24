# Sources — v0.4.5 Reviewable Activity Taxonomy Mapping

## Project evidence

The v0.4.4 five-company annual-baseline pilot established stable annual activity evidence for:
- Alfen N.V.
- Schneider Electric SE
- Prysmian S.p.A.
- NKT A/S
- Vestas Wind Systems A/S

The pilot showed materially different activities that should not be collapsed into a single generic "energy" industry label:
- Alfen: electricity-grid products/systems/services, Smart Grid Solutions, EV Charging, Energy Storage Systems.
- Schneider Electric: energy management, electrical distribution, secure power, industrial/building automation, data-centre/infrastructure activities.
- Prysmian: power and telecom cables; Transmission, Power Grid, Electrification and Digital Solutions.
- NKT: high-voltage power cable market; Solutions, Applications, Service & Accessories.
- Vestas: wind turbines, wind power plants, project development and wind service activities.

## Design consequence

v0.4.5 adds a controlled multi-label analytical taxonomy with explicit deterministic phrase rules. Every mapping preserves the source XBRL fact provenance. Taxonomy ancestors are separately labelled as derived. Pairwise output contains only exact node intersections; it does not calculate a similarity score, assign peer status, infer segment exposure or rank companies.

This module consumes already-produced v0.4.4 annual-baseline JSON manifests and performs no network access.
