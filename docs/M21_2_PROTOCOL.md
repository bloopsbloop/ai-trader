# M21.2 — Funding Data Access Probe Protocol

## Purpose and boundary

This protocol was frozen before any M21.2 market-data request. It tests whether three documented public Binance REST inputs can be retrieved and checked in a small, non-economic sample. It does not estimate returns, PnL, signals, profitable-event counts, correlations, quantiles or parameters, and it does not authorize a historical experiment.

Source checkpoint: `0b9daa24f9b4e9dc8a2146512a2b6dfda0d574d5` on `main`. The six uncommitted M21.1 documents are the accepted baseline and remain unchanged:

| File | SHA-256 |
|---|---|
| `docs/M21_1_CANDIDATE_REVIEW.md` | `aea2472be46253c6481d4acfc2a375d6f19d08cea61a9f1b432dcdf9ff8fab50` |
| `docs/M21_1_DECISION.md` | `eb503db5a51654a05a1a037d0a1b34b740513ee961386c25017615cc0f929d40` |
| `docs/M21_1_PROPOSED_EXPERIMENT.md` | `9f1168fb1c08d50cb7dff8e69dd0f3d54fce98ced3654d78364df7c8770e61b0` |
| `docs/M21_1_REPORT.md` | `30dc3a735ef508d50a4cac5304bf20c2b22a3fd99433e5a5ec87a221f1939e4b` |
| `docs/M21_1_SCOPE.md` | `f6e111084cd9ec4661e1d0ef21fb3e96762871378fb47af791bb47b279528bcd` |
| `docs/M21_1_SOURCE_REGISTER.md` | `1dc798a3bf1eb7cc7fed07e0024a77f1e1d63539809b6522f5b8a86cdd4e44c6` |

## Official interfaces reviewed before access

- [USDⓈ-M funding-rate history](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data): public `GET /fapi/v1/fundingRate`; inclusive millisecond `startTime`/`endTime`; maximum `limit=1000`; ascending output; shared 500 requests per 5 minutes per IP limit. The documented row fields are `symbol`, `fundingRate`, `fundingTime`, `markPrice` and optional/current `rateType` (`Regular` or `Special`).
- [USDⓈ-M premium-index klines](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data): public `GET /fapi/v1/premiumIndexKlines`; `symbol`, `interval`, inclusive millisecond bounds and maximum `limit=1500`; the response is a 12-element tuple with open and close timestamps and premium OHLC values.
- [Spot klines](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/market): public `GET /api/v3/klines`; `symbol`, `interval`, millisecond bounds, UTC `timeZone=0`, maximum `limit=1000`; the response is a 12-element OHLCV tuple.
- [Binance developer introduction](https://developers.binance.com/en/docs/introduction): the documented interfaces support historical market-data access, while product requirements, regional eligibility and limits must be checked for a production integration.

No reviewed official page explicitly prohibits this small personal public-market-data research probe. The API documentation establishes technical interfaces, not a general legal entitlement. The exact contractual status of retained API responses, derived research artifacts and a future production use is **UNKNOWN** from the reviewed pages. Binance Vision dataset terms are not used to authorize this probe because no Vision archive is requested. No account, key, paid term or product enrollment is used.

## Frozen, nonadaptive request schedule

Only `BTCUSDT` and `ETHUSDT` are queried. All windows are half-open for analysis, represented to the APIs as `startTime` through `endTime = half_open_end - 1 ms`:

- beginning: `2020-01-01T00:00:00Z` to `2020-01-03T00:00:00Z`;
- middle: `2023-07-01T00:00:00Z` to `2023-07-03T00:00:00Z`;
- end: `2026-10-01T00:00:00Z` to `2026-10-03T00:00:00Z`.

For every symbol/window pair, make exactly one funding-history request (`limit=1000`), one 1h premium-index-kline request (`limit=1500`) and one 1h Spot-kline request (`limit=1000`, `timeZone=0`): 18 scheduled logical requests.

The fixed pagination example is BTCUSDT funding for the middle window with `limit=2`: page 1 starts at the window start; page 2 starts at the last returned `fundingTime + 1 ms`, while retaining the same window end. The second-page parameter is the only response-derived parameter and follows this frozen rule. These are two additional logical requests.

The fixed repeat is the exact ETHUSDT funding middle-window request, with byte-for-byte identical parameters to its scheduled original. This is one additional logical request. Canonical parsed records, not response headers or JSON whitespace, are compared.

Total planned logical requests: 21. No endpoint, symbol, window or fallback host may be added after seeing responses.

## Operational limits and evidence

- Maximum 36 HTTP attempts across the run, including retries.
- Maximum 50 MiB across all newly written probe outputs.
- Connect timeout 5 seconds and read timeout 15 seconds per attempt. At most one retry is permitted for a transport failure, HTTP 429 or HTTP 5xx, subject to the global attempt limit. Retry delay is bounded at 2 seconds.
- Each attempt records the endpoint URL, parameters, UTC retrieval time, status, selected response headers, raw-body byte length and SHA-256. Every raw body is retained, including errors and changed repeats.
- The output root `data/research/m21_2_funding_access_probe_v1` must not exist at start. The helper creates it once and never overwrites an existing run.
- HTTP 451, or an explicit location/restriction denial in an error body, stops all further calls to that endpoint family. No alternate host, VPN, proxy, account, other venue or Vision archive is used.
- Only public GET endpoints are permitted. There are no account, private, order or position requests.

## Frozen QA checks

The probe checks JSON shape and field types; millisecond timestamps; requested bounds; chronological ordering; duplicate timestamps; observed adjacent funding-time deltas; observed `rateType` values; 1h kline timestamp alignment and gaps; premium/Spot open-time overlap; page boundaries and pagination overlap; and canonical equality of the fixed repeat.

Observed short-window cadence is descriptive. It cannot prove the entire history has the same cadence. Empty or failed windows remain recorded and are not repaired with another source. Two matching responses do not prove immutable history, complete history or revision-free point-in-time availability.

## Decision rule

- `READY_FOR_HISTORICAL_RESEARCH_PROTOCOL`: all three endpoint families are technically accessible for both symbols in every fixed window, their sampled records pass the frozen structural QA, pagination and repeat checks pass, and no provenance or terms issue discovered in this review prevents preparing a separate protocol.
- `CONDITIONAL_ON_PROVENANCE_OR_TERMS`: the technical sample is usable, but source provenance, correction history, historical point-in-time availability or applicable use terms remain material and unresolved.
- `BLOCKED_BY_DATA_ACCESS`: a required endpoint family is inaccessible, restricted, consistently fails, or lacks usable common sampled data/schema for the proposed inputs.

The first status would permit only preparation of a later historical-research protocol. It would not establish an edge, independent evaluation data, complete history or production suitability. No prospective period is activated by M21.2.
