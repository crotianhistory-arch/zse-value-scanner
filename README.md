# ZSE Value Scanner v0.3.3

Prva, konzervativna verzija privatnog istraživačkog alata za Zagrebačku burzu i EHO.

## v0.3.0 — Company Intelligence Foundation

Ova verzija dodaje novi deterministički sloj iznad financijskih podataka. Cilj je prvo razumjeti **što tvrtka stvarno radi**, a tek zatim birati peerove i modele vrednovanja.

Novi sloj namjerno razlikuje:

- **službenu klasifikaciju** (npr. NACE Rev. 2.1 / NKD 2025 code list);
- **službenu registriranu klasifikaciju konkretne tvrtke** (samo kada imamo registry evidence);
- **analitičko mapiranje** reported segmenta/proizvoda na službeni kod.

LLM nije potreban i nije uključen u ovaj workflow. Budući LLM može predložiti profil JSON, ali `profile-validate` prihvaća samo poznate službene kodove i zahtijeva source/evidence za svaku materijalnu aktivnost.

Prvi grounded fixture je GRNL, napravljen iz revidiranog konsolidiranog godišnjeg izvještaja 2024. Uključuje segmentni miks, proizvode, geografske prihode, kapacitete, podružnice te NACE/NKD mapiranja.

```bash
zse-tool profile-seed --ticker GRNL
zse-tool company-profile --ticker GRNL
zse-tool activities --ticker GRNL
zse-tool segments --ticker GRNL
zse-tool taxonomy --scheme NACE --version 2.1
zse-tool peer-candidates --ticker GRNL
```

`peer-candidates` u v0.3.0 radi samo nad lokalno profiliranim kompanijama. Dok postoji samo GRNL profil, ispravno javlja da nema drugih kandidata. Sljedeće verzije mogu dodati druge ZSE profile i europske peer baze bez promjene sheme.


## Promjene u v0.1.1

- parser sada prepoznaje hrvatske EHO nazive sheetova (`Opći podaci`, `Bilanca`, `RDG`, `NT_I`, `NT_D`) uz engleske nazive iz početnog fixturea;
- metadata parser prepoznaje hrvatske oznake `Godina`, `Kvartal`, `Tvrtka izdavatelja`, `Konsolidirani izvještaj` i `Revidirano`;
- provjereno na stvarnom KOEI Q1 2025 XLSX-u s EHO-a: 856 činjenica, 0 validation warninga.

## Što ova verzija radi

- koristi službeni EHO JSON feed (`https://eho.zse.hr/feed/json`) umjesto HTML scrapinga;
- testira pristup s malim brojem zahtjeva (`probe`);
- sprema metadata objava u lokalni SQLite;
- skida samo dokumente koje je feed već otkrio i nikad ih ne skida ponovno;
- parsira standardizirane EHO/HANFA XLSX obrasce po **ADP kodovima**;
- sprema sve sirove financijske činjenice i izvedene metrike;
- provjerava balans bilance i podudaranje casha između bilance i cash-flowa;
- računa prvi skup metrika: cash, financijski dug, net debt, prihod, EBIT, jednostavni EBITDA, neto dobit, CFO, CAPEX, FCF, interest coverage, debt/equity i run-rate ROE.

## Što namjerno još NE radi

- ne radi automatski investicijski score niti preporuku kupnje;
- nema TTM rekonstrukciju kroz više kvartala;
- nema automatski povijesni ZSE price downloader;
- ne parsira ESEF/XBRL niti legacy `.xls`;
- ne čita PDF bilješke LLM-om;
- `gross_financial_debt_ex_other` namjerno ne tretira generičke "other liabilities" kao dug. IFRS 16 leasing može zato nedostajati dok ne uvedemo parsiranje bilješki.

To su planirani odvojeni moduli, da jezgra ostane provjerljiva.

## Zašto Python

Glavni problem ovdje nije CPU nego pouzdan dohvat, normalizacija računovodstvenih obrazaca i provjera podataka. Python + `requests` + `openpyxl` + SQLite je za ovu količinu podataka više nego dovoljan i puno lakše ga je mijenjati.

## Instalacija

Preporuka: Python 3.11+.

### Windows

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -e .
```

### Linux / macOS / cluster

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
pip install -e .
```

Ako je cluster bez pristupa PyPI-ju, ali već ima `setuptools`, `requests` i `openpyxl`, možeš probati:

```bash
pip install -e . --no-build-isolation --no-deps
```

Nakon toga možeš koristiti `zse-tool ...` ili bez instaliranog console entrypointa `python -m zse_tool ...`.

## Predloženi prvi test lokalno

```bash
zse-tool probe --ticker KOEI
```

Ovo radi samo dva mala GET zahtjeva prema službenom EHO JSON feedu. Ako dobiješ HTTP 403/429 ili anti-bot challenge, alat prekida rad i javlja grešku umjesto agresivnog retryanja.

Zatim samo pogledaj metadata, bez skidanja fileova:

```bash
zse-tool list --ticker KOEI --date-from 2025-04-01 --date-to 2025-04-30 --limit 10
```

Spremi metadata u bazu:

```bash
zse-tool sync --ticker KOEI --date-from 2025-04-01 --date-to 2025-04-30
zse-tool db-stats
```

Skini najviše 3 XLSX-a koja nedostaju. Ako `sync` ne pronađe direktne file URL-ove zbog buduće promjene EHO JSON sheme, pokreni `list --raw` i spremi izlaz; sirovi JSON je namjerno sačuvan upravo da adapter možemo brzo prilagoditi bez promjene baze:


```bash
zse-tool download --ticker KOEI --types xlsx --limit 3
```

Parsiranje i metrike:

```bash
zse-tool parse --ticker KOEI
zse-tool metrics --ticker KOEI
```

Ili sve u jednom:

```bash
zse-tool pipeline --ticker KOEI --date-from 2025-04-01 --date-to 2025-04-30 --limit 3
```

## Analiza već skinutog XLSX-a bez interneta

```bash
zse-tool inspect-xlsx putanja/do/report.xlsx
zse-tool inspect-xlsx putanja/do/report.xlsx --json
```

To je korisno prije clustera: možeš skinuti jedan izvještaj ručno i testirati parser potpuno offline.

## Gdje se spremaju podaci

Default:

```text
data/
  zse.sqlite
  files/
    KOEI/
      <EHO-id>/
        report.xlsx
```

Drugi direktorij:

```bash
zse-tool --data-dir /scratch/moj_user/zse probe
```

ili env:

```bash
export ZSE_DATA_DIR=/scratch/moj_user/zse
```

## Cluster

Projekt nema posebne cluster zahtjeve. Kopiraš cijeli folder ili ZIP, raspakiraš ga, napraviš virtualenv i `pip install -e .`.

Primjer SLURM interactive noda:

```bash
module load python   # samo ako vaš cluster to traži
python -m venv .venv
source .venv/bin/activate
pip install -e .
zse-tool probe --ticker KOEI
```

Ako compute nodovi nemaju outbound internet, `probe` će to odmah pokazati; tada treba koristiti login/data-transfer node prema pravilima clustera.

## Mrežna pristojnost / anti-robot

EHO javno dokumentira JSON/XML/RSS feedove i parametre `variant`, `date`, `dateFrom`, `dateTo`, `ticker` i `newsTypeId`. Ovaj alat zato **ne crawl-a web stranice** kao glavni mehanizam.

HTTP sloj ima:

- default minimalni razmak 1 s između zahtjeva;
- retry/backoff za 429/5xx;
- poštivanje `Retry-After`;
- detekciju 403/429 i tipičnih CAPTCHA/Cloudflare HTML odgovora;
- lokalni cache: već skinuti dokument ne skida se ponovno.

Za privatnu uporabu i povijesne tržišne podatke treba i dalje poštivati uvjete ZSE-a; nemoj redistribuirati dataset bez provjere prava/licence.

## Kako parser radi

Standardni XLSX koji smo provjerili s EHO-a sadrži sheetove poput:

```text
General data
Balance sheet
P&L
CF_I
CF_D
SOCE
Notes
```

Bilanca/P&L/CF imaju ADP kod u stupcu G. Parser sprema npr.:

```text
statement=balance_sheet
adp_code=63
label=IV CASH AT BANK AND IN HAND
column=current_period
value=...
```

Iz toga `metrics.py` radi normalizirane metrike. To je deterministički sloj; LLM nije potreban za osnovne brojke.

## Testovi

```bash
pip install -e '.[dev]'
pytest -q
```

Testovi koriste sintetički XLSX fixture i ne trebaju internet.

## Arhitektura

```text
src/zse_tool/
  cli.py                 CLI i pipeline
  config.py              direktoriji, timeout, rate limit
  http_client.py         HTTP, retry, backoff, block detection, download
  eho.py                 službeni EHO JSON adapter
  storage.py             SQLite schema/cache/facts/metrics
  parsers/
    xlsx_financial.py    XLSX + ADP parser
  validation.py          računovodstvene provjere
  metrics.py             deterministički izračuni
```

## Plan za v0.2+

1. testirati više izdavatelja i hrvatske/engleske varijante obrazaca;
2. robustan izbor konsolidiranog i najnovijeg/ispravljenog izvještaja;
3. TTM rekonstrukcija Q1/Q2/Q3/Q4;
4. modul za službene povijesne ZSE cijene i broj dionica;
5. market cap, EV, P/E, EV/EBITDA, FCF yield;
6. note parser za bankovne kredite + IFRS 16 leasing;
7. ESEF/XBRL;
8. tek onda value/quality screener i DCF;
9. opcionalni LLM samo za bilješke i kvalitativne rizike, uz citiranje izvora/stranice.

## Napomena

Ovo je istraživački alat, ne investicijski savjet. Prije stvarne odluke treba provjeriti izvorni izvještaj i osobito neuobičajene stavke duga, leasinga, jednokratnih prihoda/troškova i promjene broja dionica.

## Debt decomposition and enterprise value (v0.2.4)

The scanner keeps debt definitions explicit instead of treating every liability as financial debt.

```bash
zse-tool debt --ticker PODR
zse-tool valuation --ticker PODR
zse-tool compare-md --ticker PODR --periods 2026-Q1,2025-FY,2024-FY
```

`gross_financial_debt_standardized` sums standardized financing detail rows (loans/deposits,
banks/financial institutions, and securities liabilities, including related-party loan rows).
Generic other liabilities are excluded, and the scanner does not claim that IFRS 16 lease debt is
complete unless it is separately disclosed.

For cross-checking, `md_financial_debt` follows the public MojeDionice methodology: all long-term
liabilities + short-term liabilities to banks/financial institutions + short-term securities
liabilities. If short-term liability types are unavailable, total short-term liabilities are used.
`enterprise_value_md_eur` then subtracts cash and short-term financial assets. This compatibility
measure is kept separate from the scanner's own standardized financing-debt EV.

### v0.2.5 Phase-1 parity additions

- MojeDionice-compatible EBITDA now keeps standardized **value adjustments** and **provisions** as separate inputs and adds both in the comparison definition. Company-style simple EBITDA is unchanged.
- Adds parent comprehensive income, share capital, and the MojeDionice-style `retained earnings + reserves = parent equity - share capital` summary field.
- `compare-md` now shows consolidated/audited status for each selected reporting period.
- Employee count remains intentionally deferred to a dedicated metadata/notes extractor rather than guessed from arbitrary workbook cells.


### v0.2.6 Phase-1 summary parity

- Extracts explicitly reported employee statistics from `Bilješke` / `Notes` / general-data sheets.
- Keeps `employees_average_current_period` and `employees_period_end` separate; `employees_reported`
  prefers the explicitly required average-current-period disclosure and falls back only to an
  explicit period-end count.
- Adds a Q1 comprehensive-income cross-check using the redundant quarter-only column. Q2/Q3 TTM
  calculations continue to use cumulative values.
- `compare-md` shows employee count plus its measure.
- New `parity` command reports which MojeDionice financial-summary fields are present for each period:

```bash
zse-tool parity --ticker HT --periods 2026-Q1,2025-FY,2024-FY
```

Annual filings that are available only as ESEF are still outside the XLSX parser. In that case the
scanner may use the consolidated Q4 report for the year-end financial values and will label it as
such rather than claiming it is the audited FY filing.

## v0.3.1 company-intelligence profiles

v0.3.1 adds evidence-grounded bundled profiles for KOEI, PODR and HT alongside GRNL, while
preserving each dated profile as a historical snapshot. Use `zse-tool profile-seed --ticker ALL`
to load all bundled profiles. `profile-quality` reports freshness/completeness and research gaps;
`profile-history` exposes stored profile versions. Peer matching is deliberately conservative:
exact NACE matches are stronger than group/division matches, and candidate scores are not yet
valuation inputs.


## v0.3.2 Peer Architecture v2

v0.3.2 separates three meanings of "peer" so an unrelated company can no longer
become a business peer merely because it has similar revenue or market value.

```bash
zse-tool peer-candidates --ticker GRNL --type business
zse-tool peer-candidates --ticker GRNL --type business-model
zse-tool peer-candidates --ticker GRNL --type investment
zse-tool peer-candidates --ticker GRNL --type all
```

`business` (alias `product`) is the only peer type intended to screen whole-company
product/activity comparables. It has a **hard NACE activity gate**. Exact classes are
strongest, same group is weaker, and same two-digit division can produce only a weak
research lead. Size and financial similarity refine ranking only after the activity gate;
they can never rescue an activity-unrelated issuer.

`business-model` compares the latest deterministic financial fingerprint using available
margins, cash conversion, capex intensity, liquidity, leverage, net-debt/EBITDA and
interest coverage. It deliberately permits cross-industry economic analogues and must not
be treated as proof of product comparability.

`investment` compares the available market-cap size, sales size and leverage profile.
It is useful for identifying similarly sized/risk-shaped listed companies, but is explicitly
not a product/business or valuation-peer signal. Trading liquidity, ownership concentration
and country-risk dimensions are not yet included.

The JSON output keeps activity, business-model, investment, size and market-cap scores
separate, plus eligibility/status and the evidence behind the activity match. This is
intended to make future European-peer ingestion auditable before peer multiples are ever
used in fair-value calculations.

## v0.3.3 Research Warehouse Foundation

v0.3.3 prepares the scanner for a later cluster-scale research warehouse without
changing the existing ZSE storage or requiring a network connection at runtime.
The current operational database remains `ZSE_DB_PATH` (default
`$ZSE_DATA_DIR/zse.sqlite`). A separate warehouse root can be configured with
`ZSE_WAREHOUSE_DIR`; the default is `$ZSE_DATA_DIR/warehouse`.

Initialize only directories and metadata registries:

```bash
zse-tool warehouse-init
```

Initialize and also copy already-known ZSE identities/ISINs/tickers into the
new entity master (no internet access):

```bash
zse-tool warehouse-init --bootstrap-local
```

Inspect the result:

```bash
zse-tool warehouse-status
zse-tool dataset-list
zse-tool entity-lookup GRNL
zse-tool ingestion-jobs
```

The warehouse layout reserves separate locations for immutable raw evidence,
staging files, future Parquet analytical tables, manifests and temporary files:

```text
warehouse/
  raw/
    zse/ esef/ gleif/ ted/ sec/ news/
    macro/eurostat/ macro/ecb/
  staging/
    entities/ financials/ contracts/
  parquet/
    entities/ financials/ prices/ contracts/ macro/
  manifests/
  tmp/
```

SQLite remains the metadata/job-state backend. v0.3.3 adds an entity master,
identifier table, source/dataset registry, dataset-version records, resumable
ingestion jobs, raw-artifact provenance and a generic external-financial-fact
staging table. These are deliberately separate from the proven Croatian
`parsed_reports`/`facts`/`metrics` tables.

DuckDB and PyArrow are **optional** extras in v0.3.3. Applying this patch does
not install or download them. They are reserved for the later cluster phase,
when large Parquet datasets become useful:

```bash
pip install -e '.[warehouse]'
```

Do not run that optional installation on a bandwidth-constrained machine merely
to use v0.3.3; all warehouse metadata commands work with the normal dependencies.

The bundled dataset registry contains only plans/metadata for future adapters
(GLEIF, EU ESEF, TED, Eurostat, ECB and SEC structured data). `warehouse-init`
does **not** bulk-download any of them. The intended rollout is small external
peer samples first, then cluster-scale ingestion only after the schemas and
entity matching are validated.
