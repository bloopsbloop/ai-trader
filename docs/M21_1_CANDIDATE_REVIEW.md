# M21.1 — Candidate Review

## Evidence boundary

The project has already inspected 2023–2025 Spot history and the January–September 2026 M18.2 period. Those observations remain exploratory and cannot become a new independent Test. Completed lines include candle-based classification, 4h and 8h ranking, candidate filters, a clean gate, rolling-window diagnostics, partial news features, indicator rules and Donchian 120/60. Their outcomes range from negative to unresolved; none demonstrated a trading edge. Cross-venue discovery, depth resilience and listing drift were not executed because their required point-in-time inputs were absent.

M20 added capture engineering, not economic evidence. M20.4 collected one 30-minute BTCUSDT interval; M20.5 left a seven-day depth capture `BLOCKED_BY_RESOURCES`. The user's inability to maintain a continuous connection now removes that path from consideration without refuting its mechanism.

## Candidate 1 — Negative perpetual crowding and Spot rebound

**Mechanism.** A deeply negative realized USD-M funding rate together with a negative premium-index state indicates that leveraged short demand is paying to remain short and that the perpetual trades below its reference basket. Constrained arbitrage, margin pressure and later short covering can produce a rebound. The counter-hypothesis is equally plausible: negative funding contains bearish information and price continues down.

**Who may pay and persistence.** Urgent leveraged traders accept funding and adverse execution to keep or close positions; capital-constrained arbitrageurs may not instantly eliminate the gap. The effect should disappear if arbitrage capacity increases, if funding merely reflects public information, or after fees and delayed Spot entry.

**New information.** Realized funding and the perpetual premium are derivative-market state, absent from the completed Spot OHLCV/news/ranking lines. The proposal uses no new classifier, rolling window, indicator family or cross-sectional ranker.

**Spot implementation.** BTCUSDT and ETHUSDT Spot only, long or cash, no futures position and no leverage. A qualifying event can open a delayed 24h Spot holding; signals while already holding are ignored.

**Falsification.** The hypothesis fails economically if the fixed rule's net event-return point estimate is nonpositive or the final portfolio fails cash after frozen costs. It remains uncertain if estimates are positive but the predeclared confidence and baseline hurdles are not met, or if results depend on one asset or a few episodes.

**Data feasibility.** Public funding, premium-index and Spot-kline REST endpoints accept bounded start/end queries and can be checkpointed by timestamp. No account or API key is shown for these market-data calls. Exact earliest coverage, gaps, revisions, regional accessibility and applicable downstream-use terms have not been verified by an actual request. This candidate is therefore conditional rather than ready to run.

**Biases.** BTC and ETH are chosen because they are the two persistent liquid assets already central to the project, which creates selection-history bias but avoids pretending to reconstruct a survivorship-free altcoin universe. Pre-freeze history is development evidence only. Funding interval changes must be read from actual `fundingTime` values; an 8h cadence must not be assumed. Features become available only after settlement/bar close, followed by a one-hour embargo.

**Scale.** For two symbols at 1h resolution, funding plus Spot and premium histories are expected to be hundreds of thousands rather than millions of rows. A conservative local envelope of 250 MB for raw responses, normalized tables, provenance and hashes is an inference, not a measured download size.

## Candidate 2 — Calendar liquidity/return seasonality

**Mechanism.** Global market openings, fiat banking hours, weekends and recurring settlement schedules create periodic liquidity demand. Time-zone-constrained traders could pay for immediacy, allowing a fixed long-only hour/day window to earn a return.

**Difference and implementation.** Clock time is an exogenous observable and would use BTC/ETH Spot long-or-cash without leverage. It differs from technical indicators, but it uses the same already viewed OHLCV outcomes.

**Evidence against immediate selection.** Primary research reports persistent volume patterns but no persistent return pattern across time. Choosing among hours, weekdays, month turns and interactions creates a large hidden family of rules. Selecting a directional cell from 2023–2026 would be another search over viewed data; fixing a cell without evidence would be arbitrary.

**Data feasibility.** Spot 1h klines are pageable and small. Timestamps are clear, but historical archive revisions and exact pair coverage remain source limitations. No delisted universe is needed for fixed BTC/ETH.

**Falsification.** A fixed cell would be rejected by nonpositive net return, temporal sign changes or disappearance against adjacent-hour controls. No single cell is justified for preregistration now, so the experiment is not proposed.

## Candidate 3 — On-chain exchange-flow pressure

**Mechanism.** Exchange inflows can represent inventory becoming available for sale; outflows can reduce immediately tradable supply. Slow blockchain settlement and heterogeneous monitoring may delay Spot adjustment. The party paying is the trader demanding immediate exchange liquidity before the broader market has processed the flow.

**New information and implementation.** Entity-adjusted inflow/outflow data is distinct from price, news and derivatives features. A long-only strategy could hold Spot only after a frozen outflow/low-inflow state and otherwise hold cash.

**Data problem.** Glassnode documents historical and point-in-time exchange metrics, but API access requires a Professional subscription/add-on and an API key. Ordinary exchange metrics are mutable because address labels and statistical methods change. Exact point-in-time entitlement, historical coverage, cost and redistribution/live-use terms are unknown. Account registration or purchase is outside the milestone.

**Scale and bias.** Daily or hourly BTC/ETH values would be locally small, likely below 100 MB, but that estimate is not an access guarantee. Entity labeling creates revision bias; restricting to BTC/ETH retains selection-history bias. A non-point-in-time export cannot be treated as known at the original timestamp.

**Falsification.** No incremental net return over Spot momentum/volume controls, instability after immutable-vintage data, or dependence on revised recent labels would reject the hypothesis. The current access/provenance gap blocks even a clean preregistration path.

## Comparison

| Candidate | Distinct mechanism | Historical source | Spot long-only execution | Honest future check | Decision |
|---|---|---|---|---|---|
| Perpetual crowding/rebound | Yes: derivative funding and premium | Public pageable REST, coverage not yet verified | Direct, delayed Spot buy/cash | Future batch retrieval after a frozen protocol | **Conditional candidate** |
| Calendar seasonality | Marginally distinct, same OHLCV | Public Spot klines | Direct | Possible, but no justified cell | Reject |
| On-chain exchange flow | Yes: blockchain inventory movement | Professional keyed service; mutable ordinary history | Direct | Possible only with immutable entitlement | Blocked by data access |

No additive score is used. Candidate 1 alone has a distinct observable, a concrete long-only implementation and a future batch-data path, but it does not pass the data-access condition in M21.1.
