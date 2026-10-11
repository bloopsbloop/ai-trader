# M20.1 — Data and Execution Feasibility Research

## Executive result

**Status: `CONDITIONAL_ON_RESOURCES`.** Binance Spot order-flow pressure plus depth resilience is the only direction that justifies proposing a bounded capture review, because the required forward trades/BBO/L2 are public and belong to the execution venue. M20.1R divides it into a 30-minute/2-GiB Stage A smoke capture and, only after separate review and authorization, a seven-day/100-GB Stage B data-quality capture. Neither was run.

This status does not identify a successful strategy. It says only that one direction has a practical method to measure whether suitable data can be captured. Cross-venue and listing mechanisms remain possible economic questions, but their current point-in-time provenance, clock/execution path or sample timeline do not pass all mandatory gates.

## Comparison of the three directions

### A. Cross-venue Spot price discovery

- **Mechanism:** fragmented venues can incorporate shocks at different times.
- **New information:** ordered trades and executable BBO across venues, beyond Binance OHLCV.
- **Evidence:** original research documents historical fragmentation and heterogeneous price discovery.
- **Data:** free forward trades/BBO exist on major venues; historical synchronized receive-time data is a commercial/conditional path.
- **Execution:** Binance-only long-only capture is possible in principle, but non-colocated latency, common-shock confounding, quote-currency basis and one-sided exposure are severe.
- **Decision:** not selected. Scientific existence of price discovery is not evidence that a residual Binance move remains after the project receives and trades on it.

### B. Spot order-flow pressure and depth resilience

- **Mechanism:** aggressive trades consume liquidity; additions, cancellations and replenishment determine whether pressure persists.
- **New information:** event-level demand/supply and book recovery on Binance.
- **Evidence:** original U.S.-equity research supports a relation between OFI, price changes and depth; transfer to predictive crypto net returns is unproven.
- **Data:** official forward trades, bookTicker, diff-depth and snapshots are sufficient to test capture/reconstruction. Trade archives alone can test trade pressure but cannot test depth resilience.
- **Execution:** same-venue observation avoids cross-venue mapping, but latency, fees and adverse selection remain. Aggregate L2 cannot identify passive queue position.
- **Decision:** selected only as a conditional data-quality pilot candidate.

### C. Drift after official Spot listing announcements

- **Mechanism:** expanded access/attention may create gradual demand after announcement or open.
- **New information:** a defined official access event rather than broad news sentiment.
- **Evidence:** an original listing study reports heterogeneous event returns and pre-event effects.
- **Data:** official pages expose article and open times, but complete immutable history, first-seen time, revisions/deletions and historical tradability are not documented as guaranteed.
- **Execution:** no Binance fill exists before open; opening spread, thin depth, eligibility and leakage are material. Slower post-open horizons may be representable, but independent events are rare.
- **Decision:** not selected. Low storage does not resolve archive provenance or sample scarcity.

## Confirmed facts versus assumptions

Confirmed from primary sources:

- Binance streams expose trades, maker-side flag, BBO, `U/u` depth identifiers and exchange timestamps; client receive time must be added by a collector.
- Binance requires a snapshot-plus-buffer procedure for local books and disconnects stream connections at 24 hours.
- Official public archives contain Spot trades/aggTrades/klines; equivalent historical Spot L2 was not established there.
- Current exchangeInfo and filters define order eligibility, but are not historical universe records.
- Coinbase and Kraken publish real-time trade/book interfaces; Kraken L2 includes checksum support.
- Tardis/Kaiko document commercial historical tick/L2/reference products; exact entitlement and cost are account-dependent.
- Binance listing articles can distinguish publication and trading-open times.
- Current Binance payloads document `E/T` for trade, `E/U/u` for diff-depth, and only `u` for bookTicker. Diff-depth is published in 100 ms or 1000 ms batches, and REST snapshot coverage is capped at 5,000 levels per side.

Assumptions/unknowns:

- Message rates, payload compression, gap frequency, local clock quality and storage are synthetic scenarios until measured.
- The user’s Binance fees, allowed symbols, order size, latency, slippage and fill behavior are `UNKNOWN`.
- Complete announcement archive/version history and exact independent listing-event rate are `UNKNOWN`.
- Commercial price, license suitability and exact multi-venue overlap are `UNKNOWN`.
- No assumed price discovery, OFI impact or listing response is treated as a tradable return.

## Execution and data-quality barriers

Illustrative round-trip break-even is 19 bps under a low-cost/liquid scenario, 30 bps under a regular/liquid scenario and 70 bps under a stressed/new-listing scenario. Each combines both fees and all-in execution shortfall; spread is not counted twice. These are transparent synthetic cases, not the user’s costs or a return forecast.

Cross-venue needs synchronized receipt and enough residual move after end-to-end latency. Order-flow needs complete L2 reconstruction, gap rejection, clock monitoring and measured cost. Listings need versioned first-seen evidence, event taxonomy, point-in-time filters/universe and realistic opening fills.

## Statistical limits

Messages are not independent observations. Cross-venue inference clusters common shocks/venue-days; order-flow inference clusters non-overlapping episodes and days; listing inference clusters related announcements/assets. Every design needs chronological Development/Validation/sealed final evaluation, horizon purge and a Validation-only selection rule.

Synthetic 80%-power illustrations show the constraint: with assumed independent-unit standard deviations, MDE declines from 4.20 to 0.94 bps for 100–2,000 cross-venue episodes, from 10.22 to 4.17 bps for 30–180 order-flow days, and from 4.48 to 2.24 percentage points for 25–100 listing events. Unknown variance, dependence and multiplicity can make requirements larger; no fixed number of months is promised.

Observable order flow does not establish prediction, and prediction after local receipt does not establish net trading edge. Future reporting must distinguish a negative point estimate, a confidence interval proving a negative effect, exclusion of the preregistered economically sufficient effect, and an inconclusive result. Neither short capture may define final independence or sample size, and pilot PnL cannot change the hypothesis.

## Resource realism

Sizing formulas produce approximately 19–778 GB per 30 days for a one-symbol, three-venue trades/BBO scenario and 32 GB to 13.6 TB per 30 days across low/high order-flow cases. The ranges are intentionally broad because the message rate is unknown. Listing change-only storage is small, but continuous monitoring and long calendar time remain.

A computer that sleeps or is turned off cannot provide complete receive-time history. A dedicated local continuous run can be assessed with disk/clock/process alarms; no Oracle, cloud or server is assumed. Paid history is conditional on a future budget and license decision.

## Proposed next step, not authorization

If the user later approves Stage A, the maximum next step is the 30-minute, one-symbol BTCUSDT technical smoke capture defined in [M20_1_DECISION.md](M20_1_DECISION.md): public trades/bookTicker/depth only, 2 GiB hard cap, no credentials, orders or PnL. Stage B requires a written Stage A review plus another explicit resource authorization; it cannot start automatically. Neither stage can become the sealed final evaluation.

Until that approval, the correct action is to stop. No collector, prospective period or next milestone begins automatically.

## Deliverables

- [Scope protocol](M20_1_SCOPE.md)
- [Primary-source register](M20_1_SOURCE_REGISTER.md)
- [Data feasibility](M20_1_DATA_FEASIBILITY.md)
- [Execution feasibility](M20_1_EXECUTION_FEASIBILITY.md)
- [Statistical feasibility](M20_1_STATISTICAL_FEASIBILITY.md)
- [Decision and conditional pilot design](M20_1_DECISION.md)
- [M20.1R specification review](M20_1R_SPEC_REVIEW.md)

No synthetic helper or `data/research/m20_1_feasibility_v1` namespace was necessary; all calculations are transparent formulas/tables in the documents.

## Verification

The M20.1R publication checks and exact checkpoint SHA are recorded in [M20_1R_SPEC_REVIEW.md](M20_1R_SPEC_REVIEW.md). No test, dependency or protected artifact was modified for this documentation review.

## Boundary confirmation

No Oracle/server, Batch 3, `.env`, SQLite, security, dependency, production logic, model, strategy, parameter or frozen artifact was accessed or changed. No market/event data was downloaded, no live stream or REST market/account endpoint connected, no account/key used, no collector/trading process started, and no training, backtest, PnL replay or parameter search ran. M20.1R authorizes publication of documentation only.
