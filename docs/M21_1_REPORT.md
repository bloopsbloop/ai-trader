# M21.1 — Historical-Data Research Feasibility Report

## Result

**Status: `CONDITIONAL_ON_DATA_ACCESS`.** One economically distinct experiment has a plausible batch-data path, but documentation alone does not establish enough historical coverage, immutable provenance, regional access and permissible downstream use to preregister or execute it.

The conditional candidate is BTCUSDT/ETHUSDT Spot timing after extreme negative USD-M funding plus a negative premium-index state. It tests leveraged derivatives crowding, not another Spot indicator, classifier, ranker, news feature or Donchian threshold. The proposed action is long Spot or cash only; no futures trading or leverage is involved.

## Existing evidence in scope

- Candle classifiers and 4h/8h rankers produced weak predictive/ranking observations but negative trading results.
- Candidate filters and the clean gate did not establish an end-to-end economic improvement.
- Generic news evidence was mixed with partial provenance.
- Donchian 120/60 had positive base-cost point estimates in later work but remained statistically unresolved, cost-sensitive and provenance-limited.
- Rolling-window comparisons were mixed and do not prove that all adaptation fails.
- Cross-venue price discovery, depth resilience and listing drift were not run because the required point-in-time data was absent.

The viewed market periods are 2023–2025 and January–September 2026, with additional general awareness through this review. They can support exploration but cannot be relabeled as an independent Test.

## Candidates

| Candidate | Confirmed feasibility fact | Material unknown | Outcome |
|---|---|---|---|
| Perpetual funding/premium crowding | Public REST schemas support timestamp pagination for funding, premium and Spot bars without continuous capture | Earliest/gap coverage, revisions, regional access and exact use terms | Conditional selection |
| Calendar seasonality | Spot 1h history is pageable and small | No justified directional cell; literature reports nonpersistent returns | Rejected |
| On-chain exchange flow | A point-in-time product and exchange-flow metrics exist | Professional quote/key, entitlement, coverage and revision-safe export | Blocked |

## Honest evaluation path

All pre-freeze observations are development or Validation context. A genuine final evaluation must begin only after a later protocol and implementation hash are frozen, then run for a fixed future calendar interval whose data can be downloaded afterward. This removes the need for a continuously connected local computer but requires waiting for independent outcomes. It does not guarantee statistical power.

The proposed experiment fixes a single negative-funding-tail/premium rule, a one-hour information embargo, 24h non-overlapping holdings, BTC/ETH equal sleeves, conservative Spot costs, calendar-week dependence blocks, cash and exposure-matched passive baselines, and explicit negative/uncertain outcomes. See [conditional experiment draft](M21_1_PROPOSED_EXPERIMENT.md).

## Data and operational limits

- Binance REST documentation does not guarantee complete immutable history or delisted-symbol coverage.
- Binance Vision has convenient bulk files and checksums, but its current dataset terms contain a material restriction on live proprietary use; it is not silently substituted.
- Fixed BTC/ETH avoids a reconstructed altcoin universe but retains known selection/survival history.
- The estimated local footprint for the conditional candidate is below 250 MB, but this is an unmeasured planning envelope.
- A later source probe must not calculate returns or inspect outcomes. Failure of access/provenance conditions ends the proposal.

## Boundaries observed

M21.1 read documentation and existing compact evidence only. It did not query a market endpoint, download data, register an account, use an API key, train a model, run a backtest, calculate PnL, open Batch 3/SQLite, start a collector, or change dependencies, security, Oracle/server or trading state. No next experiment is authorized by this report.
