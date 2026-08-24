# v0.3.2 peer-architecture sources

v0.3.2 adds no new external company facts or taxonomies. The peer engine operates only on data already stored locally by earlier versions:

- evidence-grounded company profiles and official NACE mappings from v0.3.0/v0.3.1;
- parsed ZSE/EHO financial metrics;
- stored official ZSE market-cap snapshots when available.

The scoring thresholds and feature weights are internal research heuristics. They are deliberately exposed in deterministic code and are not presented as an official industry standard or a validated return-prediction model. They must be backtested before use in investment ranking or valuation.
