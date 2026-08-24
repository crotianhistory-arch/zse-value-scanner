# Validation status — v0.1.0

Datum: 2026-08-13

## Provjereno

- `pytest`: 6/6 testova prolazi.
- Python syntax/bytecode compile: prolazi.
- CLI help i offline `inspect-xlsx`: prolaze.
- Editable install provjeren s `--no-build-isolation --no-deps` u runtimeu bez DNS-a.
- Parser provjeren na stvarnom EHO XLSX-u: KONČAR d.d., 2025 Q1, consolidated, unrevised, English.
- Na tom stvarnom fileu parser je pronašao 856 činjenica, bez validation warninga.
- Balance-sheet total se podudara s assets totalom.
- Cash-flow ending cash se podudara s balance-sheet cashom.

## Mrežna provjera

Službena EHO dokumentacija potvrđuje JSON feed i parametre `variant`, `date`, `dateFrom`, `dateTo`, `ticker`, `newsTypeId`.
Sam base JSON endpoint je tijekom razvoja dohvaćen i vraća `application/json` bez login/CAPTCHA zahtjeva.

Ovaj execution container nema normalan outbound DNS, pa puni `probe -> sync -> download` nije moguće end-to-end izvršiti iz containera. To je upravo razlog zašto ZIP sadrži `probe` i `list --raw`: prvi test na tvom lokalnom računalu/clustera provjerit će stvarni mrežni put i aktualnu financialReports JSON strukturu.

## Poznata ograničenja

- Financial report adapter čuva raw JSON i rekurzivno traži direktne XLSX/PDF URL-ove. Ako ZSE promijeni JSON strukturu i više ne šalje direktan file URL, metadata sync će i dalje raditi, ali downloader će trebati mali adapter update.
- Parser v0.1 podržava standardni `.xlsx`, ne legacy `.xls` i ne ESEF/XBRL.
- Dug ne uključuje generičke `other liabilities`; IFRS 16 leasing može biti izostavljen dok ne uvedemo note parser.
- Nema tržišne cijene, TTM-a, P/E/EV/EBITDA screenera ni DCF-a u ovoj verziji. Namjerno: prvo validacija ulaznih podataka.


## v0.3.0 validation — 2026-08-20

- Full regression suite: 69 passed, 1 harmless openpyxl warning.
- Existing financial/debt/valuation tests remain unchanged and pass.
- New tests verify official-code validation, rejection of invented NACE codes, GRNL segment mix, analytical-vs-official assignment status, historical profile date selection and deterministic peer ranking.
- Offline CLI smoke test passes for `profile-seed`, `company-profile`, `activities`, `segments`, `taxonomy`, and `peer-candidates`.
- Runtime company-intelligence commands require no internet and no LLM once profile evidence/taxonomies are cached.
