# v0.3.3 Research Warehouse source registry

This release registers future official/open research sources but does not bulk-download them.
The URLs below are metadata/configuration anchors for later source-specific adapters.

- ZSE/EHO: existing official Croatian exchange/issuer feed and report source used by the scanner.
- ESMA ESEF: https://www.esma.europa.eu/issuer-disclosure/electronic-reporting
  - ESEF is the EU regulated-market annual-report electronic format. Actual issuer-report discovery
    remains source-specific until a collection adapter is implemented.
- GLEIF LEI data: https://www.gleif.org/en/lei-data/access-and-use-lei-data
  - API plus Golden Copy and delta files for legal-entity identity/reference/relationship data.
- TED Search API: https://docs.ted.europa.eu/api/latest/search.html
  - Published EU procurement notices; the Search API supports reuse/bulk retrieval.
- Eurostat API: https://ec.europa.eu/eurostat/web/user-guides/data-browser/api-data-access/api-introduction
  - Official statistical REST/SDMX access.
- ECB Data Portal: https://data.ecb.europa.eu/
  - Official ECB statistical data services.
- SEC EDGAR APIs: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
  - `data.sec.gov` submissions/XBRL APIs plus official bulk archives.

The source registry distinguishes "active" sources already used by the scanner from "planned"
warehouse datasets. Registration is not evidence that a dataset has been downloaded or parsed.
