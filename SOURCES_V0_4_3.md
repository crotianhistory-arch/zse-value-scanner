# Sources — v0.4.3 Structured Tagged Activity Extraction

## Technical source
- filings.xbrl.org public API and repository: https://filings.xbrl.org/docs/api
- filings.xbrl.org explains that each filing may provide tagged xBRL-JSON and a copy of the original report package: https://filings.xbrl.org/docs/about
- xBRL-JSON specification: https://www.xbrl.org/Specification/xbrl-json/REC-2021-10-13/xbrl-json-REC-2021-10-13.html

## Live Alfen regression evidence
The v0.4.2 live pilot on 2026-08-22 reported 358 XBRL facts and 21 tagged text activity facts for Alfen N.V. (LEI 724500HDW6IWR9J5YT90). The tagged principal-activity fact explicitly identified Smart Grid Solutions, EV Charging and Energy Storage Systems. Tagged reportable-segment and revenue explanatory facts also contained embedded HTML tables. The full XHTML exceeded the 80 MiB cap, motivating a compact xBRL-JSON-first extraction path.

## Initial bounded pilot candidates
- Schneider Electric SE — LEI 969500A1YF1XUYYXS284; filings.xbrl.org indexes ESEF filings for this LEI.
- Prysmian S.p.A. — LEI 529900X0H1IO3RS1A464; filings.xbrl.org indexes English and Italian ESEF filings.
- NKT A/S — LEI 529900197LKWCEQ0NL18; NKT investor materials publish an ESEF annual report.
- Vestas Wind Systems A/S — LEI 549300DYMC8BGZZC8844; filings.xbrl.org indexes annual and interim ESEF filings, including 2025 annual.

These companies are pilot evidence targets only. Inclusion does not mean they are already approved peers for any Croatian issuer.
