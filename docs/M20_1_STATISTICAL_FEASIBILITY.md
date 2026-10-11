# M20.1 — Statistical Feasibility

## Common design rules

- The economic endpoint is net Binance Spot long-only return after account-specific commissions and measured execution shortfall, with cash as the minimum control. Predictive and ranking metrics are secondary.
- Development, Validation and sealed final evaluation are chronological. A viewed period cannot later become the sealed Test after a hypothesis or threshold was chosen.
- Features stop at decision receipt time. Labels begin only after the full assumed order latency. Purge covers latency plus the complete holding horizon; embargo covers shared-event spillover where needed.
- Hyperparameters, venues, symbols, horizons, event definitions, costs and decision thresholds are selected only on Development/Validation. Final evaluation runs once.
- Walk-forward results are grouped by the natural independent unit, not by individual messages. Multiple horizons/features require a preregistered family and multiplicity control or simultaneous confidence intervals.
- A negative result means the preregistered net effect and economically relevant upper confidence bound fail their threshold. An inconclusive result means uncertainty still includes both economically useful and harmful effects. Missing/bad data is a feasibility failure, not a negative economic result.

## A. Cross-venue design

- **Unit:** a non-overlapping lead episode, clustered by UTC day and common market shock; instrument-level observations are not independent during the same shock.
- **Treatment/question:** after an external venue’s preregistered upward trade/BBO innovation is locally received, does a Binance-only long entered after fixed latency outperform a same-venue Binance continuation rule and cash?
- **Endpoint:** paired net bps per eligible episode and aggregate capital-path return under one-position constraints.
- **Splits:** chronological venue-day blocks; symbols appearing in all splits with point-in-time eligibility; walk-forward retraining/calibration only before each block.
- **Dependence:** shared crypto factor, stablecoin/basis moves, simultaneous venues, overlapping holding horizons and repeated bursts.
- **Falsification:** nonpositive net paired effect, unstable venue leadership, effect disappearing after receive-time ordering, or no advantage over same-venue control.

## B. Order-flow/depth design

- **Unit:** non-overlapping pressure episodes, with inference clustered by UTC day; messages within an episode are measurements, not independent trials.
- **Treatment/question:** does one fixed depth-resilience statistic add net return beyond signed aggressive-trade imbalance after fixed delay/costs?
- **Endpoint:** paired net bps per episode and capital-path return versus trade-only control and cash.
- **Splits:** grouped chronological days; purge by holding horizon around boundaries; entire reconnect/gap intervals excluded by rules frozen before label calculation.
- **Dependence:** clustered volatility, repeated updates from one parent order, overlapping book recovery and market-wide shocks.
- **Falsification:** resilience increment nonpositive, sign instability, missing-data sensitivity, or gross effect below measured execution cost.

## C. Listing-event design

- **Unit:** an independent listing information event, not each pair/hour. Related announcements, postponements, Alpha/pre-market exposure and simultaneous pairs are one event cluster.
- **Treatment/question:** from the first eligible Binance decision after verified publication or open, is predefined market-adjusted net return positive versus cash and a fixed no-text buy-at-eligibility control?
- **Endpoint:** event-level net abnormal return and portfolio return with overlap/capital constraints.
- **Splits:** chronological grouped events; issuers/tokens cannot cross a boundary through related announcements; purge includes the event window and information-leak window.
- **Dependence:** common listing waves, correlated token markets, repeated assets and overlapping holding windows.
- **Falsification:** gains precede verified first-seen time, post-cost drift is nonpositive, or result is driven by a few tokens/events.

## Synthetic sensitivity only

For a two-sided 5% test with approximate 80% power, an illustrative minimum detectable mean is

`MDE ≈ (1.96 + 0.84) × sigma_independent_unit / sqrt(n_effective)`.

The values below are not fitted to M18 or any market data. They show how assumptions, clustering and rare events govern feasibility.

| Direction | Synthetic standard deviation of independent-unit mean | Effective n | Approximate MDE |
|---|---:|---:|---:|
| Cross-venue episode | 15 bps | 100 / 500 / 2,000 | 4.20 / 1.88 / 0.94 bps |
| Order-flow daily paired mean | 20 bps | 30 / 90 / 180 days | 10.22 / 5.90 / 4.17 bps |
| Listing event abnormal return | 8 percentage points | 25 / 50 / 100 events | 4.48 / 3.17 / 2.24 pp |

Millions of messages do not imply millions of independent observations. Autocorrelation and common shocks reduce effective n; multiplicity raises the required threshold. Listing counts may take years and the variance is unknown, so no fixed calendar duration is promised.

## What a bounded data-quality pilot must measure first

- Message and independent-episode rates by time of day; autocorrelation and cluster size without optimizing a trading rule.
- Gap/reconnect frequency, usable-day rate and clock offset/jitter.
- Distribution of spread and executable depth at candidate decision times, without selecting thresholds by PnL.
- For listings: eligible-event frequency, related-event cluster frequency, revision/postponement rate and completeness evidence.
- Variance of the preregistered independent-unit endpoint can be estimated only after a later protocol defines it; pilot estimates are planning inputs and cannot be used as final evidence.

The eventual sample-size decision must be made from pilot quality/frequency and an economically meaningful minimum effect, then frozen before a sealed segment. M20.1 does not make that decision.

## Interpretation constraints after M20.1R

Observing and reconstructing order flow establishes data availability only. It does not establish a predictive signal. A predictive association measured strictly after local receipt still does not establish a net trading edge after latency, fees, spread, slippage, impact and missed fills.

Future conclusions must distinguish:

- **Negative point estimate:** the estimate is below zero, regardless of uncertainty.
- **Statistically supported negative effect:** an appropriately adjusted confidence interval lies below zero under the registered endpoint/design.
- **Economically sufficient effect excluded:** the adjusted upper confidence bound lies below the preregistered minimum useful net effect; this can occur even if zero is not excluded in a differently framed test.
- **Inconclusive:** uncertainty includes economically useful and harmful effects, or data quality/effective sample size prevents the registered inference.

Failure to reject zero is not proof of no effect. The short smoke capture and seven-day data-quality capture cannot be treated as final estimates of dependence, endpoint variance or required sample size. They may provide planning ranges only. The research hypothesis, feature family, horizon and thresholds must not be changed in response to pilot PnL; PnL analysis is outside both stages.
