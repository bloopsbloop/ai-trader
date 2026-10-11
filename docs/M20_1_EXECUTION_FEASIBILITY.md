# M20.1 — Execution Feasibility

## Execution boundary

The only permitted future trading model is Binance Spot, long-only, no leverage and no shorting. No colocated infrastructure is assumed. Cross-venue data may inform a Binance order, but the design cannot claim two-sided arbitrage or assume inventory/capital on another venue.

Binance documents MARKET, LIMIT and conditional order families and symbol-specific filters. MARKET gives higher fill certainty but crosses available liquidity and may fill at multiple prices. LIMIT can miss, partially fill or become adversely selected. LIMIT_MAKER prevents immediate taker execution but does not make a fill free or certain. Tick size, step size, minimum/maximum quantity, minimum notional, percent-price and account/order constraints must be captured and applied at order time.

The user’s actual maker/taker rate, discounts, tax/special commission, account eligibility and allowed symbols are **UNKNOWN**. The public schedule is reference information, not the user’s fee. A future study must query or manually record account-specific rates without embedding credentials in research data.

## Cost accounting

For a completed long round trip:

`required gross edge (bps) = entry fee + exit fee + entry execution shortfall + exit execution shortfall`

Execution shortfall is measured against the decision-time mid or another preregistered benchmark and already includes half-spread, slippage, market impact and the adverse price move during signal-to-order latency. Spread must not be added again. Missed and partial fills are reported separately through fill rate, executed quantity and opportunity cost; they are not converted into imaginary full fills.

### Illustrative break-even scenarios

These are synthetic feasibility assumptions, not estimates from project market data and not expected returns.

| Scenario | Fee each side | Execution shortfall each side | Round-trip break-even | Interpretation |
|---|---:|---:|---:|---|
| Low-cost liquid illustration | 7.5 bps | 2 bps | **19 bps** | Uses a possible discounted fee only as a scenario; user eligibility is unknown. |
| Regular/liquid illustration | 10 bps | 5 bps | **30 bps** | Public regular-rate reference plus modest all-in shortfall. |
| Stressed/new-listing illustration | 10 bps | 25 bps | **70 bps** | Opening gaps/thin depth represented through larger all-in shortfall. |

Any future edge below the applicable break-even is economically negative before infrastructure/time costs. An observed quote response, forecast coefficient or paper mid-price return cannot satisfy this test by itself.

## Direction matrix

| Cost/risk | Cross-venue price discovery | Trade pressure + depth resilience | Listing drift |
|---|---|---|---|
| Entry/exit commission | Two Binance fills; exact account rate unknown | Two Binance fills; high turnover may compound fees | Two Binance fills; pair promotions cannot be assumed |
| Spread/slippage/impact | Signal often decays while crossing; BBO size matters | Same order flow being measured can worsen the fill | Opening spreads/depth can be extreme; first prints may not support intended size |
| Signal-to-order latency | Core risk: venue receipt, computation, network and Binance acknowledgement can consume a seconds-scale lead | Core risk at sub-minute horizon; 100 ms depth batching is not zero latency | Less sensitive if horizon is hours/days, but the first repricing can be instantaneous |
| Adverse selection | An external lead may arrive because faster traders already act on Binance | A buy after aggressive buying may be the liquidity exit for informed flow | Announcement buyers face leakage, crowded entry and reversal |
| Missed/partial fills | Passive order may miss the lead; market order changes price | Passive queue position is unavailable from L2 aggregate depth | Price/percent filters, volatility and thin book can reject or fragment fills |
| Size/rounding | Must satisfy current Binance filters | Must satisfy filters at every high-frequency decision | New pairs may have changing or unusual increments/minimum notional |
| Long-only limitation | Downward leads become cash/exit observations, not short profits | Negative pressure mainly prevents/ends exposure | Only positive drift is tradeable; negative post-listing effects are avoidable, not shortable |

## Direction-specific feasibility

### A. Cross-venue

The required effect is likely shortest where price discovery is strongest. A non-colocated client must demonstrate, rather than assume, enough residual Binance move after local receipt plus order acknowledgement to exceed roughly 19–30 bps in the illustrations. Common shocks, stablecoin basis and stale quotes can create false leads. Historical provider-local receipt times can answer a provider-clock research question but cannot validate the user’s future end-to-end latency.

**Execution conclusion:** technically orderable on Binance, but no realistic path is established until end-to-end latency and executable BBO depth are measured. The research mechanism is stronger than the current retail execution case.

### B. Order flow and depth

Trade/L2 messages are directly from the execution venue, avoiding cross-venue symbol/basis mapping. Diff-depth is batched published price-level state, not market-by-order history: it cannot recover the intra-batch order trajectory, hidden liquidity or queue position. Therefore it may support later study of displayed-depth consumption/replenishment at exact reconstructed states, but cannot validate passive fill priority. The signal responds to activity that also creates adverse selection. A MARKET order pays the prevailing book; a passive order changes the question to queue/fill modeling, which aggregate L2 cannot identify. The intended horizon must be long enough that network/order latency and at least 19–30 bps illustrative round-trip cost do not mechanically dominate.

**Execution conclusion:** the cleanest path of the three, but only conditional. A data-quality pilot must first measure message rate, gaps, clock quality and reconstructability; a later paper/shadow execution study would separately measure acknowledgements/fills and is not authorized here.

### C. Listing announcements

Announcement time and actual open time are distinct. Before open there is no Binance Spot fill; at open, filters, region eligibility, book depth and price discovery can change rapidly. A market order may suffer the 70 bps stressed illustration or much more; a limit order may miss. If the asset traded elsewhere beforehand, much information may already be incorporated. A new pair for an existing Binance asset is not economically equivalent to a new asset.

**Execution conclusion:** slower post-open horizons may be representable without colocation, but opening costs and event scarcity must be measured prospectively. Current records cannot support a reliable execution replay.

## Parameters requiring future measurement

- Account-specific entry/exit commission and asset in which commission is charged.
- Decision-to-submit, submit-to-acknowledgement and acknowledgement-to-fill latency distributions.
- Decision-time BBO, depth consumed, fill VWAP, partial-fill rate, cancellation rate and missed-fill opportunity cost.
- Order-size distribution and impact by symbol/regime.
- Filter/status snapshots and rejection frequency.
- Whether execution shortfall already embeds spread in every reported endpoint.

No scenario here is a profitability forecast.
