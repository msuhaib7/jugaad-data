# Jugaad Data Project Wiki

Last audited: 2026-09-28  
Local version: 0.35.13

## Purpose and boundary

This repository retrieves public Indian-market data from NSE, NSE Indices,
BSE and RBI. It is an acquisition utility, not an authoritative point-in-time
security master or corporate-action adjustment service. Endpoint availability,
retention and response formats remain controlled by the source websites.

## Network safety policy

The owner has reported exchange-side IP blacklisting. Do not run NSE,
NSE Indices or BSE network code, live tests, broad probes or batch downloads
without an explicit owner request naming the scope.

For an authorized download:

- Process one symbol or index at a time.
- Pause between symbols/indices and between requests.
- Cache resumable raw responses and avoid re-downloading completed data.
- Use bounded retries and stop on unexpected schemas or another symbol/series.
- Preserve raw inputs, normalized outputs, coverage checks and a manifest.
- Treat `tests/test_nse.py` as networked integration coverage even though all
  of its cases are not marked `live`; exclude it from offline test runs.
- Do not use the standard date-range bhavcopy CLI for a cautious batch: it
  uses `ThreadPoolExecutor` and therefore makes concurrent requests.
- Do not add recurring NSE/NSE Indices/BSE acquisition jobs. Manual official
  files remain preferred for membership and corporate-action evidence.

The current constituent downloads for Nifty Midcap 150 and Nifty Smallcap 250,
and the 2026-09-28 Phase 6 cash-bhavcopy run, were specific owner-authorized
exceptions. They do not authorize further automated exchange access. The cash
run encountered HTTP 403 responses; do not retry its missing dates or start
another NSE/BSE batch without a new explicit owner request.

## Command-line interface

The installed `jdata` CLI exposes four commands:

| Command | Data |
|---|---|
| `bhavcopy` | NSE equity, full, F&O or index bhavcopy; one date or a date range |
| `stock` | Security-wise NSE daily history for a symbol and series |
| `index` | NSE Indices daily price OHLC history |
| `derivatives` | Contract-specific stock/index futures or options history |

The Python API exposes additional datasets that have no direct CLI command.

## Downloadable historical and archive data

| Dataset | Interface | Contents and limits |
|---|---|---|
| Security-wise daily history | `stock_raw`, `stock_csv`, `stock_df` | Date, series, OHLC, previous close, LTP, VWAP, volume, value, trade count, delivery quantity/percentage and symbol. Availability depends on NSE retention. |
| Equity bhavcopy | `bhavcopy_raw`, `bhavcopy_save` | All securities for one session. Handles legacy and UDiff formats. |
| Full equity bhavcopy | `full_bhavcopy_raw`, `full_bhavcopy_save` | OHLCV, trades and delivery. The implementation warns that pre-2020 files may be unavailable or time out. |
| F&O bhavcopy | `bhavcopy_fo_raw`, `bhavcopy_fo_save` | All daily futures/options contracts, including settlement, volume and open interest; legacy and UDiff formats. |
| Contract history | `derivatives_raw`, `derivatives_csv`, `derivatives_df` | `FUTSTK`, `FUTIDX`, `OPTSTK` and `OPTIDX` for a specified expiry; options also require strike and type. Older availability is limited by NSE retention. |
| Index OHLC | `index_raw`, `index_csv`, `index_df` | Daily open, high, low and close from NSE Indices. |
| Index valuations | `index_pe_raw`, `index_pe_df` | Historical P/E, P/B and dividend yield. No built-in CSV helper. |
| Index total returns | `index_tri_raw` | Historical TRI and, when supplied, NTR. No built-in CSV helper; the validated project script writes CSV. |
| All-index daily snapshot | `bhavcopy_index_raw`, `bhavcopy_index_save` | The `ind_close_all_DDMMYYYY.csv` daily index file. |
| Bulk deals | `bulk_deals_raw`, `bulk_deals_save` | Current bulk-deal file exposed by the archive route. |

### NSE daily reports

`NSEArchives.list_available_reports()` discovers the report keys currently
advertised for a segment. `NSEArchives.download_report()` downloads one of
those files. The daily-reports endpoint exposes only the current and previous
trading day; it is not a historical backfill service.

The documentation identifies 39+ dynamic report types. Known keys include:

- `CM-UDIFF-BHAVCOPY-CSV`
- `CM-BHAVDATA-FULL`
- `CM-BHAVCOPY-DAT`
- `CM-VOLATILITY`
- `CM-BULK-DEAL`
- `CM-BLOCK-DEAL`
- `CM-SHORT-SELLING`
- `CM-CIRCUIT`
- `FO-UDIFF-BHAVCOPY-CSV`

The exact list must be discovered at runtime and may change. The callable API
is on an `NSEArchives` instance; documentation examples showing module-level
`list_available_reports` or `download_report` functions do not match the
current implementation.

## Current and live NSE data

`NSELive` can fetch:

- Stock quotes and trade/order-book information.
- Market status, cash-market turnover and equity-derivatives turnover.
- All-index quotes and a selected index's live rows.
- Current index constituent symbols through `live_index(index_name)`.
- Intraday chart/tick data and year-wise price/volume summaries.
- Index, equity and currency option chains, expiries and strikes.
- The current F&O securities list and pre-open market.
- Exchange holiday data.
- Corporate announcements and integrated financial filings.
- Current symbol metadata: company, active series, ISIN, market type,
  F&O/SLB/ETF/debt flags and delisting flag.
- Company name, current index memberships and regulatory details by symbol.
- Top gainers/losers, most-active names, volume spurts and 52-week highs/lows.
- Current block-deal session data and major-index quote data.

`live_index()` is sufficient to create a dated current constituent symbol
snapshot. It does not provide historical membership, official index weight,
industry or an authoritative constituent CSV. The Midcap/Smallcap snapshots
were enriched with ISIN and company name from the current Dhan master.

## BSE and RBI data

`BSELive` supports current BSE corporate announcements, attachment URLs, and
the current scrip list filtered by group, segment or status such as Active or
Delisted. Scrip rows may include code, symbol, company name, ISIN and market
metadata. It does not implement historical BSE prices or bhavcopies.

`RBI.current_rates()` parses the current RBI homepage for policy and related
rates, T-bill/government-security values and published index values. It does
not provide historical RBI series.

## Validated project downloaders

### Required security history

`scripts/download_required_ohlcv.py` is the cautious downloader used for the
bull-momentum work. It processes monthly windows and symbols sequentially,
requests exact series, caches raw JSON, validates dates/OHLC/volume, writes
atomically and records a manifest. Its 19 configured targets are:

`DHFL`, `EQUITAS`, `INOXLEISUR`, `MINDTREE`, `HDFC`, `UJJIVAN`, `FRETAIL`,
`TATAMTRDVR`, `IDFC`, `ISEC`, `PEL`, `GSPL`, `FCONSUMER`, `FLFL`, `HEG`,
`JBCHEPHARM`, `MTARTECH`, `RAJESHEXPO` and `STLTECH`.

All 19 are currently present according to
`data/required_daily_ohlcv/manifest.json`. Target dates, aliases, permitted
series and event descriptions in the script are the source of truth.

### Required index history

`scripts/download_required_indices.py` downloads price and TRI sequentially,
with configurable request/index delays, schema checks and a manifest. All 27
configured indices are currently present through 2026-09-25:

- Broad market: Nifty 50, 100, 200, 500, LargeMidcap 250, Midcap 150,
  Smallcap 100 and Smallcap 250.
- Volatility: India VIX price history; TRI is not applicable.
- Sector/thematic: Auto, Bank, Consumer Durables, Energy, Financial Services,
  FMCG, Healthcare, Infrastructure, IT, Media, Metal, Oil & Gas, Pharma,
  Private Bank, PSU Bank, PSE, Realty and Services Sector.

The manifest is `data/required_market_indices/manifest.json`.

### Current constituent snapshots

The validated 2026-09-25 exchange snapshots fetched on 2026-09-28 contain:

- Nifty Midcap 150: 150 constituents, all `EQ`.
- Nifty Smallcap 250: 250 constituents: 249 `EQ` and `HFCL` in `BE`.

Raw responses, adapter-ready CSVs and fingerprints are held by the consumer
project under
`/Users/suhaib/workspace/quant/data/bull_momentum_inputs/jugaad_data/constituents/current/2026-09-28/`.

### Phase 6 cash bhavcopy archive

`scripts/download_phase6_cash_bhavcopies.py` downloads exchange-wide cash
bhavcopies one session at a time from a supplied local calendar. It preserves
raw CSVs, validates the date and legacy/UDiff schema, writes atomically and
maintains a resumable checksum manifest.

The owner-authorized 2026-09-28 run requested 1,919 sessions from 2019-01-01
through 2026-09-25. Its validated result is:

- 1,898 sessions available through 2026-08-26; 21 failed with HTTP 403.
- 1,367 legacy files and 531 UDiff files.
- 4,649,828 data rows and 561,988,107 CSV bytes; no temporary files.
- Missing sessions: 2026-08-27, 2026-08-28, 2026-08-31, 2026-09-01 through
  2026-09-04, 2026-09-07 through 2026-09-11, 2026-09-15 through 2026-09-18,
  and 2026-09-21 through 2026-09-25.

The acquisition copy and manifest are under
`data/phase6_cash_bhavcopies/`. A byte-for-byte verified consumer copy is under
`/Users/suhaib/workspace/quant/data/bull_momentum_inputs/jugaad_data/phase6_cash_bhavcopies/`.
Do not treat the 21 failures as holidays or silently fill them.

`scripts/download_phase6_corporate_announcements.py` is a tested sequential,
resumable downloader for supporting announcement evidence. It was not run:
after the cash endpoint returned HTTP 403, making more NSE requests would have
violated the stop-on-block policy. Announcements would not replace an
authoritative corporate-action ledger even if downloaded.

## Data correctness cautions

- `NSEHistory._stock()` sends `series=ALL` when the caller requests `EQ`.
  Consumers requiring an exact series must validate/filter the response or
  use the exact-series project downloader.
- Standard `stock_raw`, `derivatives_raw` and index-history methods split date
  ranges and may use two worker threads. They are not the cautious sequential
  path required by the network policy.
- Historical API responses use a persistent local cache; live methods cache
  values for approximately five seconds.
- NSE changed bhavcopy formats in July 2024. Downstream readers must recognize
  legacy and UDiff schemas rather than concatenate them blindly.
- A successful HTTP response does not establish completeness, adjustment
  correctness or point-in-time validity. Coverage and schema checks remain
  mandatory.

## Unsupported or insufficient for research acceptance

The repository does not reliably provide:

- Historical index constituents or dated membership snapshots.
- Complete scheduled and ad-hoc reconstitution history.
- Historical index weights.
- Authoritative historical symbol/series/ISIN chains.
- A complete structured corporate-action ledger.
- Official stock adjustment factors for splits, bonuses, rights, mergers and
  demergers, or a reliably adjusted stock-price series.
- Complete historical ASM/GSM/surveillance status.
- Historical sector classifications.
- Historical BSE OHLCV/bhavcopies or historical RBI time series.

Corporate announcements can supply supporting evidence, but they are not a
substitute for a complete, normalized corporate-action ledger.

## Bull-momentum Phase 6 relevance

This project can help Phase 6 by supplying raw daily bhavcopies, price/volume/
trade/delivery history for known securities, index benchmarks and supporting
announcement evidence. It can improve price reconciliation and fill known
historical-symbol gaps.

It cannot resolve the primary point-in-time opportunity-set blocker. Complete
historical Nifty membership, delisted/suspended identities and reconstitution
events still require owner-supplied official files or a licensed point-in-time
dataset. Corporate-action adjustments must also be independently reconciled.
Current constituent snapshots and current symbol metadata remain
survivorship-biased when backdated.
