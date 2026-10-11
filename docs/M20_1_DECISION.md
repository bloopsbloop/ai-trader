# M20.1 — Decision

## Status

**`CONDITIONAL_ON_RESOURCES`**

Of the three allowed directions, Binance Spot order-flow pressure plus depth resilience is the only candidate suitable for a *proposed* bounded data-quality pilot. This is conditional on the user later approving an uninterrupted local run and a hard disk budget. It is not authorization to start a collector, not an approved trading experiment, and not evidence of trading edge.

Cross-venue price discovery fails the present execution/clock gate: free forward streams exist, but common receive-time ordering, symbol/basis compatibility and non-colocated Binance latency must all be measured, while useful effects may decay before execution. Listing drift fails the present provenance/sample gate: the official source exposes articles and open times, but a complete immutable archive, first-seen/revision history, historical tradability and a practical timeline for enough independent events are not established.

## Mandatory-gate comparison

| Gate | Cross-venue | Order flow + depth | Listing announcements |
|---|---|---|---|
| Explainable observable mechanism | Pass | Pass | Pass |
| Required point-in-time data | Conditional paid history or continuous multi-venue capture | Public forward Binance streams; history conditional on provider | Official forward pages exist; completeness/version history unresolved |
| Testable temporal causality | Conditional on common receive clock | Conditional on audited event sequence and fixed delay | Conditional on first-seen/revision provenance |
| Realistic Binance-only execution | Weak: latency critical and one-sided | Conditional: same venue, but costs/adverse selection material | Conditional: opening liquidity and gaps material |
| Independent future evaluation | Possible after a separate capture | Possible after pilot and protocol | Possible, but event scarcity may require a long wait |
| Local operational burden | High, multiple continuous feeds | High but bounded to one venue/symbol in pilot | Low bandwidth, high calendar/completeness burden |
| M20.1 disposition | Not selected | **Conditional candidate** | Not selected |

## Strongest arguments

**For the selected direction:** Binance publishes raw trades, BBO, sequenced diff-depth updates and a snapshot procedure. The data directly observes supply/demand on the future execution venue and can distinguish a trade-only pressure question from the incremental value of depth resilience. A short pilot can falsify data feasibility without trading or calculating PnL.

**Against the selected direction:** the horizon is latency- and cost-sensitive, aggregate L2 does not reveal queue position, and a personal computer outage permanently breaks a prospective segment. Even perfect capture would establish only measurability; observed impact may be contemporaneous and may not survive roughly 19–30 bps illustrative round-trip costs.

## Proposed two-stage pilot — neither stage is authorized

### Exact question

Can a non-colocated local process capture **BTCUSDT Binance Spot raw trades, bookTicker and diff-depth** with auditable local order, bounded storage and explicitly classified gaps, sufficiently well to design a later prospective order-flow/depth study?

This pilot does not ask whether a signal is profitable.

### Shared minimum data and observability limits

- One symbol only: BTCUSDT.
- Raw `trade`, `bookTicker`, and `depth@100ms` messages plus REST depth snapshots used for initialization/recovery.
- Exchange event/trade time, IDs and `U/u`; local UTC and monotonic receive time recorded before parsing.
- Session/reconnect log, clock-offset observations, current exchangeInfo/filter snapshots, raw partition hashes and normalization version.
- No account, API key, order endpoint, user stream, balance, news, Batch 3, SQLite or trading process.
- Diff-depth represents batched published price-level state updates. It cannot recover individual-order history, queue position, hidden liquidity, intra-message `U…u` trajectory or unchanged levels outside snapshot coverage.
- Cross-stream reconciliation uses exact update-ID states where available; it does not assume a global arrival order for depth and bookTicker.

### Stage A — technical smoke capture

Purpose: verify actual schemas, timestamp-at-callback behavior, snapshot overlap, sequence handling, clean shutdown, write rate, CPU/memory load and pre-write storage enforcement. It is not a long-term reliability test and cannot assess predictive signal or trading edge.

- Proposed maximum: **30 minutes or 2 GiB total stored bytes, whichever occurs first**. Thirty minutes is long enough to exercise initialization and ordinary flow without implying continuity; 2 GiB bounds an unexpectedly high write rate.
- One BTCUSDT session; an intentional reconnect may be exercised only if it fits the bound and is declared in advance.
- Stored-byte accounting includes raw, normalized, indexes, temporary files and reports. The writer must reserve the next batch before writing and stop if it would cross 2 GiB; no automatic deletion.
- Outputs are schema inventory, snapshot-overlap result, raw/normalized count reconciliation, message/byte rates, clock-source observations, resource peaks and shutdown/recovery log.
- Stage A requires a separate future authorization. Completion does **not** authorize Stage B.

### Stage B — bounded data-quality capture

Transition requires a written Stage A review, confirmation that every acceptance/stop control worked, revised storage projection from Stage A, and separate user approval of continuous runtime and resources.

- Maximum elapsed time: **7 consecutive calendar days**, chosen to include repeated 24-hour connection lifecycles and weekday/weekend operation; it is not statistically sufficient for an edge study.
- Hard stored-data cap: **100 GB decimal (93.13 GiB)** including raw, normalized, indexes, temporary files and reports. Warning at 70 GB; pre-write reservation stops capture before 100 GB. No automatic deletion/rotation to continue.
- Requires at least **150 GB decimal (139.70 GiB)** free at start and a separately confirmed minimum-free-space stop. These are project requirements, not Binance guarantees.
- Requires explicit approval for the computer to remain awake and continuously connected. Sleep/shutdown remains downtime and is never imputed.
- No paid source. Failure of free capture leads to a new feasibility/budget decision, never an automatic purchase.

### Data-quality acceptance criteria

These are design thresholds, not documented exchange guarantees:

1. At least five complete UTC-day partitions. Transport availability uses all monotonic seconds from declared Stage B start to final stop as denominator and must be ≥99.5%; planned reconnect, sleep and outage remain measured. Usable reconstructed-book coverage uses the same denominator and is reported separately.
2. Every detected `U/u` discontinuity invalidates the interval and triggers resnapshot; zero unexplained sequence gaps remain in accepted partitions.
3. Every eligible bookTicker observation is classified comparable, unmatched, stale or inconsistent. Comparable share and counts are reported over all eligible observations; provisional comparable share must be ≥95%. Only exact-ID comparable pairs enter `inconsistent/comparable`, whose provisional ceiling is 0.1%. Unmatched/stale data cannot be removed to improve it. This is a reconstruction diagnostic, not an independent exchange-state proof.
4. `trade.E`, `trade.T`, `depth.E`, `U/u` and `bookTicker.u` are stored exactly as supplied; bookTicker is not assigned a synthetic exchange time. Local UTC and monotonic receive timestamps are captured at complete-message callback before parsing/queuing. Clock source, offset estimate and uncertainty are recorded. Provisional UTC-alignment criteria remain p99 absolute estimated offset ≤50 ms and no unexplained excursion above 250 ms; a REST round trip is not called exact offset or one-way latency.
5. Raw partitions are immutable and hash-verified; raw counts reconcile to normalized accepted/rejected counts.
6. Daily raw/stored bytes in GB and GiB, message-rate percentiles, CPU/memory peaks and recovery time are reported. Pre-write accounting proves the hard cap was not exceeded.

### Stop conditions

- Any attempted order, credential request, account/private endpoint or live/paper/shadow process.
- A stage-specific cap would be crossed by the next reserved write, minimum free-space condition, persistent clock failure, unrecoverable sequence state, repeated unexplained gaps, or inability to keep the host continuously running.
- Source/API terms forbid the intended local research retention.
- Pilot code cannot preserve raw data before parsing or cannot deterministically reconstruct accepted partitions.

### Permitted pilot analysis and sealed future segment

Stage A/B analysis may examine only capture quality, schema stability, gaps, clock behavior, storage, event rates, book reconstruction and coarse episode-frequency planning. Neither stage is a final estimate of independence or sample size. Neither may calculate/select by PnL or choose OFI/depth features, horizon, entry rule, universe or threshold. Any later economic protocol must freeze the feature, delay, horizon, costs, controls, multiplicity rule and sample-size rule after pilot review. Data collected after that freeze—not Stage A/B—must contain a separately designated sealed final evaluation segment.

## What could change this decision

- User approval of continuous local runtime and disk budget would permit a separate decision on the scoped pilot.
- A verified paid dataset with suitable local/provider receive timestamps, completeness incidents and acceptable license/cost could change historical feasibility, but price and entitlement remain unknown.
- A documented immutable listing feed with first-seen/revision history and credible event frequency could improve the listing direction.
- Measured end-to-end cross-venue/Binance latency and common instrument coverage could improve or reject cross-venue feasibility.

## Remaining limitations

No real message-rate, clock, gap, spread, slippage, fee-account, event-frequency or endpoint-variance measurement was made. Commercial prices/licenses were not established. Scientific results come from different markets/periods and support mechanisms, not current net profitability. The chosen status therefore remains conditional and cannot supersede M19.1 with a trading claim.

## Stop rule

Stop after publishing this feasibility review locally. Do not start the pilot, collector, prospective window, M20.2 or any trading/research computation without a new explicit instruction.
