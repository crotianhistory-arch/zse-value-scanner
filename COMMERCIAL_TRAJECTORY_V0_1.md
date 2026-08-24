# Commercial trajectory v0.1

This layer answers: **is the target company's reported commercial activity rising, decreasing, unchanged or mixed through time?**

It does not claim market share and does not infer competitor displacement.

## Evidence model

Reported observations retain company, period, scope, market where applicable, metric, amount, unit/currency/scale, source precision, evidence class and a provenance reference. Repeated official publications live in a top-level source registry and observations refer to them by `source_id`.

The first KOEI seed covers:
- Germany and Sweden annual market revenue for 2023–2025;
- Germany and Sweden H1 2025–2026 as a separate comparable series;
- Power Transmission and Distribution division revenue for 2023–2025;
- group order intake for FY 2022–2024 and H1 2025–2026;
- year-end backlog for 2023–2025.

## Comparability rules

- FY, H1, Q1 and Q1-Q3 are separate series.
- YoY is derived only when the preceding year exists in the same series.
- CAGR is derived only for FY series.
- Metric, scope, market, currency, unit and scale must all match for one series.
- Reported source precision is preserved (`exact`, `rounded`, bounds).
- `INCREASING`, `DECREASING`, `UNCHANGED`, `MIXED` are deterministic monotonic descriptions of the reported sequence, not market-share conclusions.

## Comparative revisions

A later report can publish a comparative prior-year segment figure that differs slightly from the number printed in the earlier report because of reclassification or presentation changes. The curated seed may use the later comparative figure when constructing one analytical series, but the exact source remains attached to that observation. The original historical report is not overwritten.

## Interpretation boundary

`market_revenue` means revenue KONCAR reported for that country. It does **not** mean transformer revenue in that country unless separately evidenced, and it does **not** mean market share.

Likewise, faster KOEI growth than a future peer would be evidence for relative commercial momentum, not proof that KOEI took business from that peer. Customer/project/tender evidence would be required to support displacement.
