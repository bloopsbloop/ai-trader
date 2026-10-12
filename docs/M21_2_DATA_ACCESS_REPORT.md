# M21.2 — Funding Data Access Report

## Decision

**Status: `CONDITIONAL_ON_PROVENANCE_OR_TERMS`.**

The three required public endpoint families were technically reachable from this computer for BTCUSDT and ETHUSDT in every predeclared short window. The fixed pagination and repeat checks passed, and the sampled premium/Spot bars aligned. The probe does not justify `READY_FOR_HISTORICAL_RESEARCH_PROTOCOL` because historical point-in-time provenance and correction behavior remain unproven, API-response retention and future production-use terms remain unclear, and a local post-fetch QA failure prevented exact response headers and exact pre-request timestamps from being flushed. This status does not authorize an economic experiment.

## Executed scope

- Retrieval date: 2026-10-12 UTC; raw-file writes span approximately `09:12:57.633Z` through `09:13:03.702Z`.
- HTTP attempts: **21** of the maximum 36; all were first attempts and entered the helper's HTTP-200 parse path.
- Raw response bodies: **87,009 bytes**. Complete namespace after offline QA: approximately **119 kB**, below the 50 MiB ceiling.
- Endpoints: public `GET /fapi/v1/fundingRate`, `GET /fapi/v1/premiumIndexKlines`, and `GET /api/v3/klines` only.
- Symbols: BTCUSDT and ETHUSDT only.
- No restriction response, retry, alternate host, account, API key, proxy, VPN or archive was used.

The initial helper fetched and saved all 21 raw bodies, then local QA raised because historical funding rows represented `markPrice` as an empty string. It had not yet flushed the in-memory headers/request log. The raw bodies were preserved unchanged and were analyzed offline; no market request was repeated. `request_log.json` records exact URL/parameters/body hashes and HTTP 200 as established by the helper's control flow, but it labels raw-file write time as a close timestamp proxy and records headers as unavailable. `probe_failure.json` preserves the failure. The helper now accepts and counts empty historical `markPrice` strings and writes an attempt log before later QA.

## Confirmed sampled coverage

These statements apply only to the requested two-day windows, not to full-history availability or the earliest available record.

| Window (half-open UTC) | Symbol | Funding rows | Premium 1h bars | Spot 1h bars | Common premium/Spot opens |
|---|---:|---:|---:|---:|---:|
| 2020-01-01 00:00 to 2020-01-03 00:00 | BTCUSDT | 6 | 48 | 48 | 48 |
| 2020-01-01 00:00 to 2020-01-03 00:00 | ETHUSDT | 6 | 48 | 48 | 48 |
| 2023-07-01 00:00 to 2023-07-03 00:00 | BTCUSDT | 6 | 48 | 48 | 48 |
| 2023-07-01 00:00 to 2023-07-03 00:00 | ETHUSDT | 6 | 48 | 48 | 48 |
| 2026-10-01 00:00 to 2026-10-03 00:00 | BTCUSDT | 6 | 48 | 48 | 48 |
| 2026-10-01 00:00 to 2026-10-03 00:00 | ETHUSDT | 6 | 48 | 48 | 48 |

All sampled kline open times were sorted and unique, all close times were exactly open plus one hour minus one millisecond, and no 1h gaps occurred inside the six symbol/window series. Premium and Spot open-time sets matched exactly in each series.

Funding rows were sorted and unique within each base request. The first/last timestamps were:

- beginning: `2020-01-01T00:00:00Z` through `2020-01-02T16:00:00Z` for both symbols;
- middle: `2023-07-01T00:00:00Z` through `2023-07-02T16:00:00.001Z` for both symbols;
- end: `2026-10-01T00:00:00.001Z` through `2026-10-02T16:00:00Z` for both symbols.

Adjacent funding timestamps were close to eight hours but not always exact: the observed deltas were 28,799,998–28,800,002 ms in the beginning window, 28,800,000–28,800,001 ms in the middle window, and 28,799,998–28,800,004 ms in the end window. This is evidence of an approximately eight-hour cadence only in these samples. A later feature implementation must tolerate millisecond jitter and still must not use a future settlement to infer a past interval.

## Schema and repeat findings

- Funding rows contained `symbol` (string), `fundingRate` (decimal string), `fundingTime` (integer milliseconds), `markPrice` (string) and `rateType` (string).
- All 36 sampled base funding rows reported `rateType="Regular"`; no claim is made about unrequested history or whether this field was available at the historical time.
- `markPrice` was an empty string in all 12 beginning rows and all 12 middle rows, but a decimal string in all 12 end rows. The current response shape is stable as a string field, while historical content does not satisfy a numeric-value assumption.
- Premium and Spot responses were 12-element tuples with integer open/close timestamps and documented string numeric fields.
- The fixed BTCUSDT middle-window funding pagination used `limit=2`; page 2 began at page 1's last `fundingTime + 1 ms`. Both pages had two rows, no timestamp overlap and a strict boundary.
- The repeated ETHUSDT middle-window funding request matched the original canonical records exactly; both canonical SHA-256 values are `e732c1a6ba682918096888ce19969c1bf178bc53b7278e157598e80cf50e9117`. One matching repeat does not establish immutable history.

## Design clarifications now controlling

The [design addendum](M21_2_DESIGN_ADDENDUM.md) requires an explicitly negative rate in addition to the expanding 5th-percentile condition; separates settlement rate from comparable interval intensity; separates funding time, premium close, assumed availability and entry; treats the one-hour embargo as an assumption; preserves eligible entries with missing exits as unresolved/censored; makes cash and a future-fixed passive portfolio the economic baselines; separates event means from full-calendar portfolio return; preserves joint BTC/ETH dependence; and excludes funding payments from Spot PnL.

The approximately eight-hour sampled cadence does not resolve the historical cadence design. Historical interval metadata available at the decision time has not been demonstrated. The current responses also do not prove revision-free point-in-time values.

## Sources and terms

The exact interfaces and limits were checked against the official [USDⓈ-M market-data reference](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data), official [Spot market-data reference](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/market), and [developer introduction](https://developers.binance.com/en/docs/introduction). They document technical historical-data access and public request schemas. They do not, in the reviewed text, guarantee complete or immutable history, exact retention, regional availability for other users, or all terms applicable to retained responses and a future production system.

No explicit prohibition of this small personal public-data probe was found in those pages. The applicable terms for retained API responses, derived research and a later production use remain **UNKNOWN**; API availability is not treated as legal permission for every use. Binance Vision terms were not used because no Vision data was requested. No broad legal conclusion is made.

## What remains unknown

- Complete-history coverage, the earliest available row, and gaps outside the six fixed windows.
- Whether any historical funding, premium or Spot values were corrected after original publication.
- Exact original publication latency and what was knowable at each historical decision time.
- Historical funding-interval metadata sufficient for leakage-safe cadence normalization.
- Why old `markPrice` values are empty and whether another documented point-in-time field can safely replace them; the proposed Spot rule does not require mark price.
- Exact response headers for this run and exact request-start times, due to the recorded local QA/log-flush defect.
- Product/contractual requirements for longer retention, derived research and any future production use.
- Statistical power or economic value. No returns, PnL, signals, correlations, profitable-event counts or parameter fitting were calculated.

M21.2 therefore supports only the statement that a bounded technical sample is currently retrievable. A later research protocol remains conditional on resolving or explicitly bounding provenance, cadence and terms; no 12-month prospective period, backtest, training or acquisition is activated.
