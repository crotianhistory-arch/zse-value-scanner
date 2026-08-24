# Classification translation v0.1 — controlled activity pilot

v0.4.12 adds a read-only translation layer from the existing
`energy-electrical-v0.1` controlled activity taxonomy into official statistical
classification targets.

The purpose is not to assign one permanent industry code to a company. A company
may have several explicit controlled activities, and each activity can map to
several classification systems at different levels of detail.

## Evidence boundary

The translation chain is:

1. issuer-reported filing text — `R2_REPORTED_TEXT`;
2. controlled activity mapping — `A1_ANALYTICAL_ACTIVITY_MAPPING`;
3. activity-to-classification mapping — `A3_ANALYTICAL_CLASSIFICATION_MAPPING`;
4. official target code / label / URI — validated against the v0.4.11 NACE/CPA
   reference DB or pinned to an official external classification source.

No mapping in v0.4.12 is labelled an official crosswalk unless an official
correspondence table directly supports that exact relationship.

## Relation semantics

- `close_activity_match`: the external classification is a comparatively tight
  activity description for the controlled node, but it is still not treated as
  company-level identity.
- `product_family_match`: closest official product-family target.
- `contains_activity`: the target classification explicitly contains the
  controlled activity but is broader.
- `broader_activity_match`: the translation intentionally loses material detail.

## Pilot nodes

The first pilot covers:

- Transformers
- Switchgear
- Power cables
- High-voltage power cables
- Submarine power cables

The mapping catalog validates NACE Rev. 2.1 and CPA 2.2 targets against the
standalone v0.4.11 reference DB.

It also embeds official reference targets for:

- ISIC Rev. 5 (United Nations)
- NAICS 2022 (U.S. Census Bureau)

This gives an immediate cross-system sanity test without modifying the
classification reference DB schema.

## Why the external comparison is useful

The transformer domain demonstrates why a translation layer must preserve
relationship type and detail loss:

- controlled `transformers` -> NACE Rev. 2.1 `27.11`
- controlled `transformers` -> CPA 2.2 `27.11.4`
- controlled `transformers` -> ISIC Rev. 5 `2710`
- controlled `transformers` -> NAICS 2022 `335311`

NACE `27.11` also contains motors and generators, while ISIC `2710` is broader
again because it also includes electricity distribution/control apparatus.
NAICS `335311` is much narrower and is specifically the U.S. transformer
manufacturing industry.

Similarly, NACE distinguishes `27.11` (motors/generators/transformers) from
`27.12` (distribution/control apparatus), while ISIC Rev. 5 class `2710`
combines those domains. That coarsening is surfaced explicitly rather than
silently treated as equality.

## Safety

- translation reads the controlled activity profile and standalone
  classification reference DB;
- translation does not write `data/zse.sqlite`;
- derived taxonomy ancestors are not automatically translated;
- no exposure percentage is inferred;
- no whole-company primary industry code is inferred;
- no peer or competitor decision is created;
- no network request is required by the translation command itself.

The external ISIC/NAICS reference labels are pinned in the mapping catalog with
their official source URLs so that provenance remains reviewable.
