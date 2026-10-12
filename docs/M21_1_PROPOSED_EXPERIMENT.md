# M21.1 — Proposed Experiment (Conditional Draft)

This is a design for review, not a preregistration and not authorization to acquire data or run the experiment.

## Hypothesis and mechanism

After an unusually negative settled Binance USD-M perpetual funding rate and a negative completed premium-index bar, forced or urgent short positioning is sufficiently crowded that BTCUSDT/ETHUSDT Spot has a positive 24h expected return after conservative execution costs. The null includes continuation of bearish information and a gross effect smaller than costs.

## Inputs and universe

- Fixed universe: BTCUSDT and ETHUSDT, evaluated as two equal isolated sleeves.
- `fundingRate`: realized regular funding only, timestamped by `fundingTime`; special rates are excluded and counted.
- `premiumIndexKlines`: completed 1h bar close.
- Spot 1h klines: next eligible bar for delayed entry and the bar ending 24h later for exit accounting.
- No order book, news, market classifier, ranker, on-chain input, futures position or leverage.

All raw responses, request parameters, retrieval timestamps and hashes must be preserved. Funding cadence is inferred from successive timestamps, never assumed to be eight hours.

## Frozen signal candidate

For each symbol, after at least 365 calendar days of past funding observations:

1. the just-settled regular funding rate is at or below the expanding 5th percentile computed strictly from earlier observations; and
2. the most recently completed 1h premium-index close is below zero.

Wait one complete hour after `fundingTime`, then enter Spot at the next 1h open plus frozen slippage. Hold 24 hours. Ignore further signals for that symbol until exit. The 5% tail, one-hour embargo and 24h horizon are single fixed choices; they are not reselected by asset, regime or split.

## Costs and execution

- Base: 10 bp Spot commission plus 5 bp slippage per side, 30 bp round trip.
- Sensitivity: doubled to 60 bp round trip.
- No BNB discount, maker rebate, leverage, stop order or intrabar fill assumption.
- A missing entry/exit bar, trading halt or non-finite observation makes that event ineligible and is reported, not filled forward.

The cost values are conservative design assumptions, not a claim about the user's future fee tier. They must be reconsidered before preregistration if the venue schedule changes, without looking at strategy outcomes.

## Development, Validation and final evaluation

- **Development:** earliest verified common history through 2023-12-31. Used only for data QA and descriptive mechanism checks; no threshold alternatives.
- **Validation:** 2024-01-01 through 2025-12-31. Used once to determine whether the fixed design is operationally coherent, not to tune it.
- **Exploratory-only 2026 history:** all observations through the eventual protocol-freeze timestamp, including the already viewed M18.2 period. Reported only as known selection context; never called independent Test.
- **Final evaluation:** the first twelve complete UTC months beginning after a later preregistration and code/hash freeze. It is future data now but can be downloaded in batches after the period; no continuous local connection is required.

The final interval must be fixed before its first outcome exists. It is not extended after results are viewed. Twelve months is a calendar commitment, not a power claim.

## Chronology, purge and dependence

- Features use only funding settlements and premium bars available before entry.
- One-hour availability embargo protects against ambiguous publication timing.
- Each symbol's events are non-overlapping; the purge is 24h on each side of split boundaries.
- Walk-forward diagnostics use calendar-year folds and never change the rule.
- Primary dependence units are calendar weeks containing one or more closed positions, not hourly bars or funding rows.
- Simultaneous BTC/ETH events remain within the same week/block; they are not treated as independent samples.

## Baselines and controls

1. Cash at 0% return.
2. An equal BTC/ETH passive portfolio scaled to the strategy's realized time-in-market, rebalanced monthly, with identical entry/exit cost assumptions.
3. Unconditional 24h Spot returns after all eligible regular funding settlements, using the same one-hour embargo, as a descriptive mechanism control.
4. Funding-tail-only and premium-only conditions are ablations reported with multiplicity adjustment; they cannot replace the primary rule.

## Endpoint and decision rule

Primary statistical endpoint: mean base-cost net return per non-overlapping event for the equal-sleeve portfolio, with a paired calendar-week block bootstrap confidence interval fixed before execution.

Economic hurdles: final compounded portfolio return must exceed cash and the exposure-matched passive baseline at base costs. Doubled costs are a predeclared sensitivity and cannot upgrade a failed base result.

- **Negative:** primary point estimate is nonpositive, compounded return does not beat cash, or the effect has the wrong sign in either BTC or ETH with no defensible aggregate stability.
- **Uncertain:** positive point estimate but interval includes zero, too few independent weeks, baseline hurdle fails, substantial missingness exists, or the result is dominated by a few episodes.
- **Positive research result:** lower confidence bound exceeds zero and both economic hurdles pass under the frozen base-cost protocol. This still does not authorize paper or live trading.

## Multiplicity and no-tuning rule

There is one primary rule, one portfolio and one primary endpoint. Per-asset results, two ablations and doubled costs are secondary; family-wise inference must be adjusted and cannot promote the status. No alternative quantile, horizon, embargo, universe, model, regime, stop, or cost may be chosen after Validation or final results. A negative or uncertain final result closes this formulation; a new formulation requires a new economic mechanism or genuinely new information, not another parameter grid.
