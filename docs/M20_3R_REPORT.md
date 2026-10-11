# M20.3R — Async Lifecycle and Recovery Correctness Audit

## Status and boundary

**OFFLINE_ADAPTER_VERIFIED**, limited to hermetic lifecycle, recovery, storage and replay behavior.

M20.3R used injected clocks, futures, events, HTTP responses, WebSocket connections and writers. It made no DNS, REST or WebSocket request and did not execute Stage A or Stage B. It did not touch trading, Batch 3, Oracle, server state, `.env`, SQLite, models, dependencies or security settings. The source base is commit `7311a11e843c9ef0f542d66d93f7c7f50167f5dc` plus the uncommitted M20.3/M20.3R patch.

Before changes, `docs/M20_3R_BASELINE_HASHES.json` recorded 25 source, test, document and synthetic-artifact files. The four original M20.3 documents and eight files in `data/research/m20_3_offline_adapter_v1` remain byte-identical. New compact outputs are create-only under `data/research/m20_3r_adapter_audit_v1`.

## Findings and corrections

| Finding | Result before correction | Correction | Regression evidence | Remaining limit |
|---|---|---|---|---|
| Single WebSocket reader | **Confirmed.** When snapshot completed while `recv` remained pending, the next loop could create a second reader and abandon the first task. | The adapter retains one owned `recv_task` until it completes, consumes every task in a simultaneous done set, and cancels plus awaits all owned tasks during shutdown. | Controlled `Future`/queue tests hold `recv` pending across snapshot completion, assert `max_active == 1`, complete snapshot and market data simultaneously, and inspect `asyncio.all_tasks(loop)` before closing an explicit event loop. | Real library/network scheduling remains Stage A work. |
| Recovery without disconnect | **Confirmed.** A sequence gap or rejected required depth could leave the book invalid until an external disconnect. HTTP 200 was treated as sufficient even when snapshot overlap failed. | Gap, malformed required depth, local loss and failed snapshot overlap start bounded same-connection recovery. Recovery succeeds only when `BookState.valid` is true. Repeated invalid snapshots end as `recovery_exhausted`; HTTP retry exhaustion remains explicit. | Tests cover open-connection gap, malformed required depth, too-old snapshot, a gap inside buffered updates, repeated recovery and bounded exhaustion. | Lost history is never reconstructed; recovery starts a new usable state from a valid overlap/snapshot. |
| Requests HTTP outcomes | **Confirmed.** Native `requests` failures and response-size limits were not normalized; body loading was unbounded and `Retry-After` lookup was case-sensitive. | `Timeout`, `ConnectionError` and oversized response have explicit outcomes. 418/429, 5xx, malformed JSON and other statuses are classified. `Content-Length` and streamed bytes are bounded. Header lookup is case-insensitive. Rate-limit waits are capped by the remaining run budget. | Monkeypatched `requests` tests cover every listed response/error and never open a socket. | The raw bounded body is retained in a `SnapshotResponse`; malformed HTTP 200 is persisted as a snapshot envelope and fails book validation rather than becoming a success. |
| Deadline and cancellation | **Confirmed.** Connect, backoff and the requests worker did not consistently share the overall budget, and cancellation semantics of `to_thread` were unstated. | Remaining monotonic budget caps connect, read polling, snapshot timeout and retry/reconnect waits. WebSocket close has a separate two-second default cleanup budget. Owned asyncio tasks are cancelled and awaited. The requests worker is shielded from coroutine cancellation and joined before completion is reported. | Tests cover hung connect, silent receive, long `Retry-After`, pending HTTP cancellation, pending snapshot, reconnect cancellation and a close coroutine that must be cancelled. | Python cannot kill a blocking `requests` thread. The request gets the remaining active budget as connect/read timeout, and cancellation waits for it to return so no hidden work remains. DNS/OS behavior can exceed that library timeout; therefore a strict real-network wall-clock cleanup bound is not claimed until Stage A. |
| Writer failure/finalization | **Confirmed.** Failures could collapse to `writer_failure`, and `run_live_capture` could finalize an already aborted writer. | Primary reasons are preserved as `storage_cap`, `minimum_free_space`, `record_too_large` or `disk_write_failure`. Control-record writes use the same path. Closed/aborted writers return `INCOMPLETE_STORAGE_FAILURE`; no final marker is emitted and finalization is not attempted. | Injected writer tests cover event/control paths and the complete orchestration entry point. | An incomplete raw file is recovery material only and is never labelled verified/finalized. |
| Preflight probe and path handling | **Confirmed.** A fixed probe name could cause a pre-existing file to be removed in `finally`. | Preflight validates configuration and a single-component `run_id`, resolves the prospective path below the root, creates a UUID probe with exclusive mode, and removes it only after this invocation created it. | Tests retain a pre-existing `.m20_3_write_probe`, cover success and rejected escaping run IDs, and confirm no run directory is created by preflight. | Filesystem races outside the exclusive probe and create-only writer contract remain ordinary OS concerns. |
| Fault replay | **Confirmed.** Direct adapter calls to `invalidate()` and `start_epoch()` changed runtime state without an equivalent replay transition. | `book_invalidate` and `continuity_reset` are persisted controls and `CaptureEngine.process_control()` applies the same transition. Control persistence happens before the runtime mutation. Replay only treats an actual `stop` control as the independently reconstructed stop reason. | Finalized replay tests cover disconnect before the next market message, reconnect without a successful snapshot, gap/recovery, local loss and cancellation. Retry exhaustion and writer failure retain terminal/incomplete semantics. | Transport telemetry remains capture-only; it is not presented as independently reconstructed. |

## Deadline and cleanup contract

The active deadline is based on monotonic elapsed time and is independent of market-message arrival. Each connect timeout, snapshot timeout, receive poll and retry wait uses no more than the remaining active budget. A wait that consumes the final budget resolves to `time_limit`.

Cleanup is separate. WebSocket close is limited by `cleanup_timeout_seconds` (default two seconds), after which its task is cancelled and awaited. Snapshot and receive tasks are always cancelled and awaited. For the existing `requests` dependency, coroutine cancellation cannot terminate the worker thread. M20.3R therefore waits for the bounded request call to return instead of claiming completion while background work continues. This is an honest ownership guarantee, not a claim of instantaneous cancellation.

## Recovery and replay result

Same-connection recovery reached a valid book after a sequence gap, malformed required depth, stale snapshot and buffered-overlap gap. Two independent gap episodes recovered without a reconnect. Repeated invalid overlap stopped at the configured recovery limit with no infinite requests.

Runtime continuity changes now have durable controls. A disconnect immediately persists a continuity reset, so replay remains invalid even when no later market event exposes the session change. Known local loss persists `book_invalidate`. Cancellation and stop controls preserve the terminal reason. A writer failure without a final marker remains `RECOVERY_UNFINALIZED`/`INCOMPLETE_STORAGE_FAILURE`; it is not a verified finalized replay.

## Backpressure scope

The real `websockets.connect` call receives `max_size` and `max_queue` from the frozen adapter limits. The application ingress queue is also bounded. The legacy fake-list test checks defensive handling when one callback supplies more items than the application queue can accept; it is not evidence about sustained real traffic. Sustained contention, upstream loss, library-version buffering and OS socket buffering remain unverified until an authorized Stage A run.

## Verification

- Targeted M20.2/M20.2R/M20.3/M20.3R tests: **77 passed, 0 failed, 0 skipped**.
- Full WSL pytest: **407 passed, 0 failed, 0 skipped, 3 existing dependency deprecation warnings in 24.25 seconds**.
- `pip check`: no broken requirements.
- Existing WSL protected fixtures: **55/55 hashes matched**.
- M20.2 protected documents/artifacts: **14/14 matched**.
- M20.2R protected documents/artifacts: **16/16 matched**.
- Original M20.3 documents and synthetic artifacts: **12/12 matched the pre-change baseline**.
- M20.3R artifact manifest: verified after generation.
- `git diff --check`: passed after final generation.

The three warnings are the pre-existing `python-binance`/`websockets` deprecations. Dependency versions were unchanged.

## Unverified before Stage A

No conclusion is made about real Binance schemas, public-host availability, server ping/pong behavior, DNS stalls, TCP/TLS teardown, actual snapshot latency, exchange rate-limit headers, real traffic rate, sustained contention, upstream loss, clock quality, CPU/RAM/disk throughput or OS behavior during power/process failure. Stage A still requires separate authorization. No Stage A/B command was run and no real capture directory was created.
