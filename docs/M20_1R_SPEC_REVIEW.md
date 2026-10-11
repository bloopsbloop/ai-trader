# M20.1R — Pilot Specification Review

## Result

M20.1 remains **`CONDITIONAL_ON_RESOURCES`**. Order-flow/depth remains only a candidate for a future data-quality exercise. This review corrects the capture specification; it does not authorize Stage A, Stage B, a collector, market-data acquisition, trading, or M20.2.

Official Binance Spot WebSocket, REST and filter documentation was rechecked on **2026-10-11**. The source URLs and fact/inference classification are retained in [M20_1_SOURCE_REGISTER.md](M20_1_SOURCE_REGISTER.md).

## Ambiguities corrected

| Prior ambiguity | Correction |
|---|---|
| “Reconstructable L2” could imply every order event was visible. | Diff-depth is batched published price-level state over `U…u`. It does not identify individual orders, intra-batch trajectory, queue position, hidden liquidity or unchanged levels outside the 5,000-level snapshot. |
| bookTicker could be compared with whichever depth message was latest. | No global cross-stream order is assumed. Exact-ID checkpoints are `comparable`; ahead/intra-batch states are `unmatched`; older unavailable states are `stale`; exact-ID disagreement is `inconsistent`. |
| A 0.1% mismatch ceiling lacked a denominator and could be improved by exclusions. | `inconsistent/comparable` is reported with counts and applies only to exact-ID pairs. `comparable/all eligible bookTicker` is reported separately with a provisional 95% floor. Every eligible observation is classified. |
| Every stream appeared to have an exchange timestamp. | Trade has `E/T`; diff-depth has `E/U/u`; bookTicker has `u` and no documented `E/T`; REST snapshot has `lastUpdateId`, not an event timestamp. Missing fields remain missing. |
| Local receive timestamp could be read as wire/NIC arrival. | It is taken at the application callback on receipt of complete message bytes, before parsing/queuing. It cannot establish kernel/NIC arrival or exact one-way latency. |
| REST time could be treated as exact clock offset. | Round-trip observations provide only an offset estimate with network/asymmetry uncertainty. Clock source/status and uncertainty must be stored. No system/NTP setting may be changed. |
| “Availability ≥99.5%” lacked a denominator. | Transport and usable reconstructed-book coverage each use all scheduled Stage B monotonic seconds. Five full UTC days are a separate requirement. Planned reconnect and outage remain measured. |
| Resnapshot could appear to repair history. | It restores subsequent state only. Lost messages and receive-time history stay missing; no interpolation or hidden fill is permitted. |
| 100 GB cap could be checked after writes or by free-space polling. | The cap includes raw, normalized, indexes, temporary files and reports. Each batch is reserved before writing; capture stops before crossing. No automatic deletion is allowed. GB/GiB are explicit. |
| A seven-day pilot was the first operational step. | Stage A is a separate 30-minute/2-GiB technical smoke capture. Stage B is at most seven days/100 GB and needs a written Stage A review plus new user authorization. |
| Pilot statistics could drift into signal selection. | Neither stage evaluates PnL, proves prediction/edge, fixes final independence/sample size, or selects a feature, horizon, threshold or strategy. |

## Observable versus unidentifiable depth properties

After a valid snapshot overlap and without a gap, a future system could measure displayed BBO/spread, quantities at observed price levels, top-N displayed depth inside known coverage, changes in published depth and recovery of a defined displayed-depth band. It cannot identify individual-order causality, priority/queue position, hidden liquidity, the sequence within one `U…u` batch or a complete deep book beyond snapshot coverage.

bookTicker reconciliation is useful for detecting implementation/state inconsistencies, but it is not fully independent validation because both streams originate from Binance and share update IDs. States without an exact reconstructable ID remain unmatched/stale rather than being forced into a comparison.

## Clock validity

- Exchange fields are retained without synthesis.
- `receive_monotonic` is authoritative for order/duration inside one uninterrupted process boot.
- `receive_utc` supports UTC alignment only while the UTC-to-monotonic mapping and its uncertainty meet project thresholds.
- A UTC step/excess uncertainty invalidates UTC alignment for the affected interval but need not invalidate monotonic local ordering.
- Process/boot/monotonic discontinuity starts a new session and breaks local order across that boundary.

The p99 50 ms and 250 ms excursion limits remain provisional project criteria, not Binance guarantees and not measurements already achieved.

## Future stages — not authorized

### Stage A: technical smoke

- BTCUSDT only; trade, bookTicker and depth@100ms plus initialization/recovery snapshot.
- Maximum **30 minutes or 2 GiB total storage**, whichever first.
- Tests schema handling, snapshot overlap, counts/hashes, callback timestamping, byte/message rates, clock observations, CPU/memory, pre-write cap enforcement and clean shutdown.
- Does not test long-term reliability, predictive signal or trading edge.

### Stage B: data quality

- Starts only after separate Stage A review and explicit approval of runtime/disk resources.
- Maximum seven consecutive calendar days; at least five complete UTC days.
- 100 GB decimal (93.13 GiB) hard cap, at least 150 GB decimal (139.70 GiB) initial free space, plus a reviewed minimum-free-space stop.
- Project transport availability ≥99.5%; usable reconstructed-book coverage reported separately.
- Same prohibitions: no keys, accounts, orders, PnL, training or strategy selection.

Stage A never extends into Stage B automatically.

## Resource decisions still required

Before Stage A: user approval to run a public-market-data smoke capture for up to 30 minutes, allocate 2 GiB, keep the computer awake, and accept temporary network/disk/CPU use. Before Stage B: review actual Stage A rates and failures, select the minimum-free-space stop, confirm at least 150 GB decimal free, approve seven-day continuous operation and the 100 GB cap. Paid data remains a separate budget/license decision.

## Unknowns retained

Actual message/byte rates, compression, callback delay, clock offset/uncertainty, gap rate, snapshot retry behavior, comparable share, mismatch rate, CPU/memory use and storage growth remain `UNKNOWN`. Queue position, hidden liquidity and intra-batch order trajectory are structurally unobservable from these streams. User fees, order latency, slippage and net edge were not measured. Long-run independent-unit count and sample size cannot be inferred from Stage A or assumed from seven days.

## Statistical interpretation

Observable order flow is not a predictive result. A predictive effect after receive time is not net trading edge. Future reporting must separate a negative point estimate, statistically supported negative effect, exclusion of an economically sufficient effect, and inconclusive evidence. Absence of significance is not proof of absence. Pilot PnL is prohibited and cannot revise the hypothesis.

## Publication scope

Only the seven M20.1 documents and this addendum belong to the checkpoint. No code, collector, dataset, namespace or earlier artifact is part of this change.

Verification basis: commit `f3a6f99e676bb8c67c9e3f01800bb1909e4f6293` plus these eight documentation files.

- Existing WSL environment, full command: `.venv/bin/python -m pytest -q -p no:cacheprovider`.
- Result: **330 passed, 0 failed, 0 skipped, 3 existing dependency deprecation warnings in 25.41 seconds**.
- Test log SHA-256: `8f3afc35ef22dfcbab4f993a29755a379f473d527c6302cde0a55a7c33702f09`.
- `pip check`: **No broken requirements found.**
- Protected fixtures: **55/55 matched**; M18 cross-tree manifest: **159/159 matched**; M18 result manifest: **12/12 matched**.
- Frozen M18.1 protocol, M17.2 protocol and M17.2 summary hashes matched.
- Local Markdown links and final `git diff --check` passed before publication.
