# M20.1 — Data Feasibility

## Common point-in-time requirements

Every raw record would need: venue, instrument as published, normalized base/quote mapping, exchange event time, local monotonic and UTC receive times, connection/session identifier, raw payload hash, sequence/update identifiers, capture status and the point-in-time instrument/filter record. Cross-venue order requires both exchange and receive clocks; exchange timestamps alone cannot prove what the Binance decision process knew first.

Historical provider data and a project-side forward capture answer different questions. A provider’s local timestamp is evidence about its recorder, while a future project collector’s timestamp is evidence about the project setup. Neither may be silently substituted for the other.

## A. Cross-venue Spot price discovery

### Mechanism and new observation

Fragmented liquidity and capital frictions can cause a trade/quote shock to reach one venue before another. The new observable is the ordered cross-venue sequence of trades and executable BBO changes. A Binance-only long-only rule could act after an upward external lead and before Binance incorporates it. This is not two-sided arbitrage: it assumes no capital or short position at the leading venue.

Scientific evidence supports heterogeneous price discovery and historical fragmentation, but it does not prove a current, post-cost, one-sided lead. Correlation may reflect a common shock; a lead at exchange timestamp may disappear when ordered by local receipt; an apparent spread is not profit unless the Binance buy and later sell are executable.

### Venue/data matrix

| Source | Trades | BBO/L2 | Historical | Time/order limits | Access |
|---|---|---|---|---|---|
| Binance official | Real-time raw/agg trades; public archive | Real-time bookTicker and diff-depth; current REST snapshot | Trades/aggTrades/klines free; historical Spot L2 not established | Exchange event/trade time and update IDs; project receive time absent | Public; no account for market streams/archive |
| Coinbase official | Real-time ticker/trades documented | Real-time level2 documented | Suitable long-history replay not established by reviewed official page | Project receive time absent; product mapping differs | Public real-time; authenticated trading is irrelevant to this review |
| Kraken official | Real-time and REST post-trade | Real-time L2 snapshot/update with checksum | Endpoint availability exists, full desired retention not established | Match/publication timestamps on post-trade; project receive time absent | Public market data |
| Tardis | Native historical trades/BBO/L2 for supported venues | Captured updates and generated snapshots; provider-local receive time | Binance Spot since stated provider coverage dates; exact multi-venue overlap conditional | Recorder location and incidents matter | Paid plan/API key for meaningful history; one-day samples only are free |
| Kaiko | Commercial tick trades/BBO | Commercial snapshots/L2 | Varies by exchange/tier | Versioned/provider-derived data; project receive time differs | Subscription; exact price/entitlement `UNKNOWN` |

### Missing attributes and verdict

- Common Spot instruments cannot be matched by ticker alone; BTC/USD and BTC/USDT have stablecoin/basis risk. Exact contemporaneous common USDT coverage across independent venues is `UNKNOWN`.
- Historical listing, delisting, tick/lot and venue-status records must be point-in-time. Current metadata cannot repair survivorship.
- Forward free capture can establish project receive order, but a personal computer shutdown creates unrecoverable cross-venue gaps. Provider history can improve continuity but changes the receiver/latency context.
- **Historical availability:** conditional on a paid provider; free synchronized history not established.
- **Forward availability:** public streams, technically available.
- **Primary blocker:** trustworthy common event ordering plus non-colocated latency and continuous multi-venue operation.

## B. Spot order-flow pressure and depth resilience

### Mechanism and new observation

Aggressive buys consume asks; additions/cancellations and replenishment determine whether depth absorbs pressure. The new observable is the sequenced interaction of trades, BBO/L2 updates and recovery of displayed liquidity. A price response contemporaneous with imbalance is market impact, not automatically a forecast. A profitable signal would require subsequent return after the decision delay to exceed execution costs.

Binance raw trades include individual trade IDs and maker-side flags. `isBuyerMaker=false` permits an inference that the buyer was taker/aggressor; it is an exchange flag, not a full initiating-order audit. `aggTrade` combines executions belonging to one taker order, reducing message count but losing individual execution detail. Trades alone can test signed aggressive-trade pressure. They cannot test cancellations, queue changes, replenishment or depth resilience.

### Reconstruction requirements

1. Start buffering diff-depth messages.
2. Fetch the REST snapshot and verify overlap with the buffered `U/u` range.
3. Apply updates in sequence; zero quantity removes a level.
4. Detect every sequence discontinuity, invalidate the affected interval, reconnect and resnapshot.
5. Record the local receive clock before parsing and retain the raw message.
6. Reconcile reconstructed top-of-book against bookTicker as a diagnostic, allowing for asynchronous channel ordering.
7. Record planned 24-hour reconnects and all unscheduled disconnects.

### Order-book observability boundary

The official diff-depth stream publishes price-level quantity updates in messages whose update-ID interval is `U…u`, at a documented 100 ms or 1000 ms update speed. It is a stream of published state updates, not a market-by-order feed and not a complete sequence of individual submissions, cancellations and executions. Applying one message produces the state at its final `u`; the path through individual update IDs inside a batched `U…u` message is not recoverable.

The initial REST snapshot is limited to 5,000 levels per side. Levels outside it remain unknown until they change, so the reconstructed book is not a guaranteed full-depth book. L2 also does not expose order identity, priority or queue position.

Potentially measurable after a valid snapshot/stream overlap: displayed quantity by observed price level, BBO and spread, top-N cumulative displayed depth within known levels, consumption/replenishment of published levels between messages, recovery time at a defined observed depth band, and published L2 imbalance. Not identified: individual-order arrival/cancel trajectory inside a batch, queue position/fill priority, hidden/iceberg liquidity, attribution of a depth change to one order, and unchanged unknown levels outside snapshot coverage. These boundaries define measurement capability; no feature, horizon or threshold is selected here.

### Depth/bookTicker reconciliation

There is no documented global delivery order across `depth` and `bookTicker`. Reconciliation is a diagnostic keyed by update ID, not by “latest message” arrival order:

- **Comparable:** a retained reconstructed depth checkpoint ends at the same update ID `u` as bookTicker, the session is accepted, and no gap occurred. Compare bid/ask price and quantity at that exact state.
- **Unmatched:** the bookTicker `u` is ahead of available depth, or lies inside a batched `U…u` range whose intra-message state cannot be reconstructed. It may later become comparable only if an exact checkpoint exists; otherwise it remains unmatched.
- **Stale:** bookTicker `u` is older than the retained reconstructed state and no exact checkpoint remains. It is not compared with the newer book.
- **Inconsistent:** an exact-ID comparable pair exists but BBO price or quantity differs.

Every bookTicker observation received during an accepted, transport-active session after warm-up belongs to exactly one category. `comparable_share = comparable / all eligible bookTicker observations`. `mismatch_rate = inconsistent / comparable`; its numerator and denominator are reported as counts. The provisional 0.1% ceiling applies only to exact-ID comparable pairs and is meaningful only if comparable share also meets a separately reported provisional floor of 95%. Neither threshold is a Binance guarantee. Unmatched/stale observations may not be discarded to improve mismatch rate. Because both streams originate from the same exchange state and share update IDs, this is an ingestion/reconstruction cross-check, not a fully independent market-state oracle.

### Time fields and capture clocks

| Record | Exchange fields documented by Binance | Project fields required later |
|---|---|---|
| raw `trade` | `E` event time, `T` trade time, `t` trade ID | `receive_utc`, `receive_monotonic`, session and raw sequence |
| `depth@100ms` | `E` event time, `U` first and `u` final update ID; no trade time | same local receive fields |
| `bookTicker` | `u` update ID; **no `E` or `T` in the documented payload** | same local receive fields |
| REST snapshot | `lastUpdateId`; no event/trade timestamp in the documented snapshot | request-start/end UTC and monotonic times, without treating either as snapshot event time |

The future application would timestamp receipt immediately when its WebSocket callback obtains the complete message bytes and before JSON parsing, queuing or disk I/O. This is an application-boundary timestamp, not NIC/kernel arrival time. `receive_monotonic` determines local ordering and durations within a boot; `receive_utc` supports UTC alignment. The mapping between them requires periodic observation of system UTC against a documented external time source, with offset, round-trip uncertainty and source status recorded. A REST `/time` round trip can only bound offset under network/asymmetry assumptions; it is not exact clock offset or one-way latency.

UTC-clock steps or uncertainty beyond the project threshold invalidate cross-clock UTC alignment for the affected interval, but do not erase local ordering if the monotonic clock remained continuous. A monotonic discontinuity/reset, duplicate/non-increasing local sequence, or capture restart breaks local ordering and starts a new session. The pilot may observe clocks but must not change system time, NTP configuration or security settings.

### Continuity measures

- **Scheduled window:** Stage B, if separately authorized, is at most seven consecutive calendar days from recorded start to stop.
- **Transport availability denominator:** all monotonic wall-clock seconds in that scheduled window, excluding only time before the declared start and after a final stop. The numerator is seconds with a live subscribed connection receiving expected heartbeat/market activity or completing a declared reconnect grace interval. Sleep and network outage remain in the denominator.
- **Usable reconstructed-book coverage denominator:** the same scheduled seconds. The numerator includes only intervals after successful snapshot overlap and before the next detected gap; warm-up, resnapshot, unknown-gap and shutdown intervals are unusable.
- **Complete UTC day:** 00:00:00–24:00:00 UTC with accepted raw partitions and no unclassified gap. Stage B asks for at least five such days and project transport availability ≥99.5%; usable-book coverage is reported separately and cannot be hidden by transport availability.

A planned 24-hour reconnect is a documented session transition and still creates a measured coverage interval. A recoverable sequence gap invalidates the lost interval, resnapshots, and may resume later coverage. A prolonged outage remains missing. A terminal condition ends capture. Resnapshot restores only subsequent state; it does not recreate lost messages or receive-time history. No forward fill, interpolation or silent gap repair is permitted. The five-day and 99.5% values are project criteria, not Binance service guarantees.

### Availability and verdict

- **Trade-only history:** free official trades/aggTrades archive, but this changes the question to trade-pressure predictability.
- **Historical depth:** commercially available from reviewed providers; no official free historical Binance Spot L2 archive established.
- **Forward depth:** public Binance streams plus REST snapshot, no account/key required for market data.
- **Key absent attribute in official history:** local receive time and reconstructable L2.
- **Primary blocker:** an uninterrupted, audited local capture and measured storage/message rate. This is measurable with a bounded data-quality pilot; no trading or return evaluation is needed in that pilot.

## C. Drift after official Spot listing announcements

### Mechanism and new observation

A Spot listing can expand access and attention; demand may arrive after the announcement or trading open. The new observable is a narrowly defined official access event, distinct from generic news sentiment. The decision must know the original article, `first_seen_at`, the announced open time, revisions, eligible pair, current filters and whether a pre-event price exists elsewhere.

Article publication, first observation by the project and actual trading open are different clocks. A new asset listing differs from a new quote pair for an already listed asset, a futures listing, Alpha/pre-market exposure, Earn/Margin availability, relisting or a regional product. Price before Binance open may exist only on other venues and may already embed leaked/anticipated information.

### Archive requirements

- Preserve every fetched official response with URL, content hash, HTTP metadata and project receive time; never overwrite an earlier version.
- Parse publication time and scheduled open time while retaining raw text; revisions/postponements become linked events.
- Freeze an event taxonomy before return analysis and maintain negative evidence/monitoring logs to assess archive completeness.
- Capture point-in-time symbol status, quote pairs, filters, regional eligibility and delistings.
- Establish pre-event price source without survivorship and normalize quote currency.

### Availability and verdict

- **Historical:** official pages exist, but completeness, original first-seen time, edits/deletions and historical universe cannot be guaranteed from the reviewed interface. A retrospective archive would be vulnerable to hindsight.
- **Forward:** low-bandwidth monitoring is possible, but only while the machine is running; no official immutable feed/SLA was established.
- **Cost:** public pages are free to read; durable commercial event data and its price are `UNKNOWN`.
- **Primary blockers:** rare independent events, unverified archive completeness, revisions, pre-event leakage and potentially long time to a sealed evaluation.

## Operational volume scenarios

These are sizing formulas, not observations. Table values use decimal **GB** (`10^9` bytes); resource controls must display both GB and binary **GiB** (`2^30` bytes). Let `V = venues × symbols × messages/s × bytes/message × 86,400`. A planning multiplier of 2.5 represents raw plus normalized copies and indexes; compression is deliberately not assumed. A future reservation must also budget temporary atomic-write files, logs/reports and filesystem headroom.

| Direction / synthetic scenario | Assumptions | Raw/day | Stored/day (×2.5) | 30 days |
|---|---|---:|---:|---:|
| Cross-venue low | 3 venues, 1 symbol, 5 msg/s, 200 B | 0.26 GB | 0.65 GB | 19 GB |
| Cross-venue high | 3 venues, 1 symbol, 100 msg/s, 400 B | 10.37 GB | 25.92 GB | 778 GB |
| Order flow low | 1 venue, 1 symbol, 20 msg/s, 250 B | 0.43 GB | 1.08 GB | 32 GB |
| Order flow high | 1 venue, 7 symbols, 500 msg/s/symbol, 600 B | 181.44 GB | 453.60 GB | 13.6 TB |
| Listings, change-only storage | 1 poll/min, 2–20 KB normalized response | 0.003–0.029 GB | plus raw changed versions | under 1 GB/month if changes are sparse |

Actual message rates, payloads, compression, symbols and retention are `UNKNOWN` and must be measured before allocating long-term storage. The high cases show why “public and free” does not mean operationally cheap.

A future writer must reserve/check the encoded record size plus atomic-write/index overhead **before** accepting each write or batch, and stop cleanly before the hard cap would be crossed. Periodic free-space polling alone is insufficient. Data must never be automatically deleted, rotated away or compressed destructively merely to continue capture; a cap condition finalizes current files and stops.

## Personal-computer suitability

| Requirement | Computer may be switched off | Dedicated local continuous run |
|---|---|---|
| Cross-venue ordering | Not suitable: gaps cannot be reconstructed with project receive times | Conditionally suitable with clock monitor, reconnect logic, disk alarms and redundant process supervision |
| L2 resilience | Not suitable for a complete prospective segment | Conditionally suitable for a bounded pilot; full study needs demonstrated continuity |
| Listing first-seen archive | Missed first-seen timestamps and revisions are likely | Low storage burden, but rare-event wait and source completeness remain |

No Oracle, cloud or server is assumed. A paid historical provider could reduce continuity risk but requires a separate budget/license decision and still would not reproduce this project’s receive latency.
