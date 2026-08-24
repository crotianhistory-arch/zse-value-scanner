# Sources — v0.4.4 Annual Activity Baseline Selection

## Repository behavior
- filings.xbrl.org API: https://filings.xbrl.org/docs/api
- filings.xbrl.org repository/about: https://filings.xbrl.org/docs/about

The repository indexes ESEF filings by company identifier and period end. In Denmark, the repository can contain interim reporting periods as well as annual reports; therefore newest period end is not sufficient for annual business-profile selection.

## Live regression evidence
The v0.4.3 pilot on 2026-08-22 selected 2026-03-31 for NKT A/S and Vestas Wind Systems A/S and extracted zero activity facts for both. The same run extracted rich activity evidence from annual filings for Alfen N.V., Schneider Electric SE and Prysmian S.p.A.

Official issuer reporting pages separately identify:
- NKT Annual report 2025, published 25 February 2026, with XBRL/ESEF access: https://investors.nkt.com/financial-reports/
- Vestas Annual Report 2025 for financial year 1 January–31 December 2025, with ESEF ZIP, and separate Q1/Q2 2026 interim reports: https://www.vestas.com/en/investor

## Method
v0.4.4 does not hard-code December 31 as fiscal year end. It accepts a candidate as annual-like when XBRL evidence shows a reporting period of roughly one fiscal year (320–410 days) aligned with the filing period end, using explicit reporting-period facts when available and aligned duration facts as corroboration/fallback.
