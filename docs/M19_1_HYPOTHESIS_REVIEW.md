# M19.1 — Hypothesis Review

## Decision

**`NO_JUSTIFIED_NEXT_EXPERIMENT`.** Among the three reviewed hypotheses, current data and constraints do not support an experiment ready for justified execution. This is not evidence that every possible trading advantage is absent, and it does not refute the three economic mechanisms. Reusing OHLCV proxies would blur those mechanisms and invite another search over already viewed 2023–2025 and January–September 2026 data. Those periods may remain exploratory data, but after hypothesis selection they cannot be relabeled as a new independent Test. No backtest, optimizer, training, market-data download, data collection, or strategy implementation was run.

Predictive signal, ranking signal, and trading edge are distinct conclusions. A feature may improve forecast or ranking metrics without surviving execution costs, and a negative trading simulation does not by itself prove that every forecast formulation lacks information.

## Candidate 1 — Cross-venue spot price discovery

1. **Mechanism.** Fragmented liquidity and transfer/capital frictions can make order-flow shocks reach one spot venue before another. A long-only Binance signal would act only when a predefined external venue leads upward and the Binance price has not yet incorporated the move.
2. **Information available at decision time.** Timestamped trades or executable best bid/ask from Binance and at least two independent spot venues, venue status, fees, and contemporaneous symbol eligibility.
3. **Minimum data and provenance.** Synchronized raw trade/BBO streams with clock-quality metadata, outages, fees, and point-in-time listings/filters. The project has Binance candles, not a verified historical multi-venue feed.
4. **Frequency and horizon.** Seconds to several minutes; likely frequent observations but far fewer independent dislocations.
5. **Execution/cost risks.** Latency, spread, taker fees, adverse selection, stale quotes, venue outages, and disappearance of the lead before a Binance fill. Long-only execution captures only one side of a cross-venue relation.
6. **Strong baseline.** Same-venue Binance short-horizon return/flow continuation with identical latency and costs, plus cash.
7. **Falsification.** The external-venue lead coefficient or net long-only return is nonpositive after realistic latency and taker costs, changes sign by period, or vanishes against the same-venue baseline.
8. **Difference from completed lines.** The information is where price discovery occurs across venues, rather than another transformation of Binance OHLCV or another cross-asset ranker.
9. **Minimum experiment.** Freeze venues, common spot universe, event clock, latency, one lead statistic, one entry/exit rule, and costs; use chronological venue-day groups and no parameter search.
10. **Honest later check.** Possible only after a forward synchronized capture period is sealed. Existing viewed periods cannot be reconstructed reliably from the present files.

**Decision: not ready for experiment.** The required multi-venue point-in-time data and execution timestamps are absent. A candle proxy would not test price leadership. Cross-venue price discovery remains one possible future question; this review does not establish that it is the best direction.

## Candidate 2 — Spot order-flow pressure with depth resilience

1. **Mechanism.** Persistent aggressive buying can move the best quotes; if displayed depth replenishes while buy imbalance remains positive, execution demand may continue before inventory and liquidity adjust. The proposed effect is short-horizon continuation, not a generic momentum indicator.
2. **Information available at decision time.** Sequenced trades, best bid/ask updates, queue sizes, spread, local depth, and stream-gap status up to the decision timestamp.
3. **Minimum data and provenance.** Reconstructable Binance Spot L1/L2 events with sequence numbers plus trades and exact arrival times. Binance public archives provide Spot trades, aggregate trades, and klines, but the reviewed official archive does not supply equivalent historical Spot order-book events. The project has none of these depth histories.
4. **Frequency and horizon.** Sub-minute to several minutes, with many correlated observations but a much smaller number of independent pressure episodes.
5. **Execution/cost risks.** Spread crossing, queue loss, adverse selection, stream gaps, spoofing/cancel activity, market impact, and signal decay during network latency.
6. **Strong baseline.** Signed aggressive-trade imbalance without depth, evaluated with the same decision delay, spread, fees, and holding rule.
7. **Falsification.** Depth resilience adds no out-of-sample net return over trade imbalance, the sign is unstable, or gross predictability is smaller than spread and fees.
8. **Difference from completed lines.** It uses event-level supply and demand at executable quotes, which the prior candle-based classifiers, gates, and Donchian strategy never observed.
9. **Minimum experiment.** One fixed OFI/resilience definition, one long-only trigger, one short holding horizon, strict event sequencing, grouped chronological days, and purge covering the full holding horizon.
10. **Honest later check.** Feasible through forward capture with completeness monitoring and a sealed future evaluation segment; not feasible from current OHLCV.

**Decision: not ready for experiment.** The cited order-flow result is based on U.S. equities and mainly contemporaneous price impact; extending it to predictive Binance Spot returns is our hypothesis, not a sourced fact. Without historical depth, the proposed mechanism cannot be tested faithfully. This data failure does not refute the mechanism.

## Candidate 3 — Post-announcement drift after new Spot listings

1. **Mechanism.** A new listing expands market access and attention. If demand arrives gradually after an official announcement or trading open, a long-only post-event drift may remain after the first jump.
2. **Information available at decision time.** Official announcement publication/first-seen time, announced trading-open time, point-in-time symbol status and filters, and prices available before the decision.
3. **Minimum data and provenance.** A complete historical announcement archive with verifiable availability timestamps, revisions, listing/open times, delistings, contemporaneous symbol universe, and pre-listing prices from venues where the asset already traded. The current news archive is partial and does not meet this event-specific standard.
4. **Frequency and horizon.** Rare events, with plausible holding horizons from hours to a few days; effective sample size is the number of independent listings.
5. **Execution/cost risks.** Large opening spreads, gaps, delayed deposits, thin depth, filter changes, price limits, adverse selection, and substantial pre-announcement information leakage.
6. **Strong baseline.** Cash and a fixed buy-at-first-eligible-time rule without text sentiment, evaluated against market-adjusted returns and full opening costs.
7. **Falsification.** No positive post-publication abnormal return after executable costs, gains occur only before the verified timestamp, or results depend on a few venues/tokens.
8. **Difference from completed lines.** It isolates one defined access event rather than adding broad sparse news features to every market row.
9. **Minimum experiment.** Freeze an event taxonomy and archive first, then use event-level chronological splits with one holding horizon and no token-specific tuning.
10. **Honest later check.** Possible only after enough newly observed announcements are captured prospectively; event scarcity means this may require a long calendar wait.

**Decision: not ready for experiment.** Published event studies report listing effects but also pre-event returns consistent with anticipation or leakage. The project lacks complete point-in-time announcements and tradability history, so current reconstruction could introduce hindsight and survivorship bias. The absence of suitable records blocks this design; it is not a negative event-study result.

## Comparison

| Candidate | Mechanism clarity | Distinct from completed work | Current data fit | Binance Spot execution | Cost sensitivity | Minimal test complexity | Falsifiable / fresh check |
|---|---|---|---|---|---|---|---|
| Cross-venue price discovery | Strong | Strong | Poor | Technically possible, latency-critical | Very high | High | Yes, after forward multi-venue capture |
| Order-flow plus depth resilience | Strong but predictive extension unproven | Strong | Unavailable | Possible with reliable streams | Very high | High | Yes, after forward depth capture |
| Listing-event drift | Strong and narrow | Strong | Incomplete provenance | Possible but opening execution is difficult | High | Moderate | Yes, after enough prospectively logged events |

None is ready for an honest experiment with current data. The correct next action is to stop research execution. A later decision may revisit one candidate only after its raw point-in-time dataset, completeness rules, universe history, and independent future evaluation window exist. Any new collection requires a separate decision covering feasibility, monetary and operational cost, source terms, completeness monitoring, storage, and whether realistic long-only Binance Spot execution can be represented. This review neither authorizes nor starts collection.

## Primary sources and what they support

1. [Makarov and Schoar, “Trading and Arbitrage in Cryptocurrency Markets”](https://dspace.mit.edu/entities/publication/7f91bfb5-ba77-4d0e-9c79-ec75e104e6cc) documents recurrent cross-exchange crypto price deviations and a role for exchange-specific signed volume. This supports fragmentation as a mechanism, not profitable long-only Binance execution today.
2. [Makarov and Schoar, “Price Discovery in Cryptocurrency Markets”](https://www.aeaweb.org/articles?id=10.1257/pandp.20191020) reports that exchange contributions to price discovery vary with market integration and segmentation. Our proposed lead signal and horizon remain untested in this project.
3. [Cont, Kukanov, and Stoikov, “The Price Impact of Order Book Events”](https://arxiv.org/abs/1011.6402) finds a strong relation between order-flow imbalance, short-interval price changes, and depth in U.S. equities. Applicability to predictive Binance Spot returns, crypto fees, and latency is uncertain.
4. [Binance public-data documentation](https://github.com/binance/binance-public-data) specifies downloadable Spot klines, trades, and aggregate trades and their schemas. It supports historical trade feasibility; it does not establish that a reconstructable historical Spot L2 book is present in that archive.
5. [Ante, “Market Reaction to Exchange Listings of Cryptocurrencies”](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3450301) reports average listing-window abnormal returns across historical exchanges. It is motivating evidence only; heterogeneous venues and pre-event effects prevent direct transfer to executable Binance Spot returns.
6. [Binance Spot API exchange information](https://github.com/binance/binance-spot-api-docs/blob/master/rest-api.md#exchange-information) documents current symbol status, permissions, and trading filters. It shows which execution constraints matter, but a current endpoint is not a point-in-time historical universe.

## Stop rule

M19.1 stops here with `NO_JUSTIFIED_NEXT_EXPERIMENT`: among these three hypotheses, under current data and constraints, no next experiment is ready for justified execution. It is not a universal no-alpha conclusion. No `M19_1_NEXT_EXPERIMENT_DESIGN.md` is created because no candidate passed data-feasibility review. Existing periods may be used only as explicitly exploratory data; none may be relabeled as a fresh independent Test after hypothesis selection. No new research milestone or data collection is authorized by this document.
