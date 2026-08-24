# Službeni izvori provjereni za v0.1

Provjereno 2026-08-13.

- EHO feed dokumentacija: https://eho.zse.hr/feed
- EHO JSON: https://eho.zse.hr/feed/json
- EHO news-type dictionary: https://eho.zse.hr/feed/json/dictionary
- ZSE cijene vrijednosnih papira: https://zse.hr/hr/cijene-vrijednosnih-papira/36
- ZSE historical data: https://zse.hr/en/historical-data/2453
- HANFA transparentnost / ESEF: https://www.hanfa.hr/podrucja-nadzora/trziste-kapitala/izdavatelji/transparentnost/

Za parser je tijekom razvoja provjeren stvarni EHO XLSX: KONČAR d.d., 2025 Q1, consolidated, unrevised, English. Taj dokument nije uključen u ZIP; testovi koriste sintetički fixture iste ključne strukture.

Napomena: ZSE službeno dokumentira EHO JSON/XML/RSS kao izvore podataka i parametre `variant`, `date`, `dateFrom`, `dateTo`, `ticker` i `newsTypeId`. Projekt stoga ne koristi HTML crawling kao normalni način rada.


## Company intelligence — v0.3.0

- Eurostat NACE Rev. 2.1 overview: https://ec.europa.eu/eurostat/web/nace
- Eurostat NACE Rev. 2.1 manual / explanatory notes: https://ec.europa.eu/eurostat/web/products-manuals-and-guidelines/w/ks-gq-24-007
- NKD 2025, Narodne novine 47/2024: https://narodne-novine.nn.hr/eli/sluzbeni/2024/47/pdf
- Granolio Group audited consolidated annual report 2024: https://www.granolio.hr/wp-content/uploads/2025/04/Granolio-Group-revised-consolidated-31.12.2024_ENG.pdf

v0.3.0 bundles only a curated subset of official NACE/NKD codes needed by the first grounded GRNL profile. The database schema is designed for full official lists later.
