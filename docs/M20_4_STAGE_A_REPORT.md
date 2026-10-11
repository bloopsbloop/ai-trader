# M20.4 — Stage A Public Market Data Smoke Capture Report

**Status: `SMOKE_CAPTURE_PASSED`.** This status means only that one bounded BTCUSDT public-market capture completed, finalized and replayed correctly under the frozen Stage A controls. It is not evidence of predictive signal, trading edge, upstream completeness, seven-day reliability or readiness to trade.

## Identity and scope

- Source and remote checkpoint: `4e4308ad5bea00a55b593581b546ffb872ed07d1` (`main`).
- Executed checkout: `/home/ostap/ai_trader_m20_4_4e4308a_exact`, detached at that checkpoint with all ten `app/research/market_capture/*.py` blob IDs matching the commit.
- Python: `/home/ostap/ai_trader_wsl_c497d40e/.venv/bin/python`, Python 3.14.4.
- Protocol SHA-256 fixed before the connection: `7908fd0f1d7175322d578ff8638e10b91cd791067592a593273782445141e00e`.
- Run ID: `stage_a_20261011T211113Z`.
- Raw namespace: `/home/ostap/ai_trader_capture/stage_a_20261011T211113Z`.
- One public WebSocket session (`public-1`) and one public REST snapshot attempt were used. No API key, account endpoint, order, private stream, trading process or second Stage A run was used.

The streams were BTCUSDT `trade`, `bookTicker` and `depth@100ms`, plus a 5000-level public REST depth snapshot. Analysis below is limited to capture and data quality. No PnL, return, label, predictive relation, signal, horizon, feature or threshold was calculated.

## Timing and shutdown

| Observation | UTC / duration |
|---|---:|
| Process invocation recorded by the launch wrapper | `2026-10-11T21:13:50.372477878Z` |
| Persisted `session_start` control | `2026-10-11T21:13:51.969539Z` |
| Persisted `stop` control | `2026-10-11T21:43:52.615248Z` |
| Monotonic measured capture duration | `1800.645715331 s` |
| Finalization marker mtime | `2026-10-11T21:43:52.800758651Z` |
| Observed stop-control-to-marker finalization interval | `0.185511 s` |

The stop reason was `time_limit`; the run outcome was `stopped`. The process and its workers were absent after completion. The launch wrapper's post-process timestamp and exit-code variables were lost because of a wrapper quoting defect, so an exact process-return timestamp is unavailable. The persisted monotonic interval, stop control, finalized marker and later absence of a collector are independently available. The `0.185511 s` cleanup figure measures stop-control to final-marker creation, not a universal HTTP shutdown bound.

Requests connect/read timeouts remain activity timeouts rather than a total HTTP wall-clock deadline. A slowly arriving response body can prolong a future request, and the adapter waits for its blocking HTTP worker rather than leaving hidden work. This successful run does not prove the absence of rare DNS, network, OS or response-body stalls.

## Files, limits and storage

| File | Bytes | SHA-256 |
|---|---:|---|
| `raw.jsonl` | 127,794,885 | `51049909f9fd589cb43d0471be459bb42b9bfe102ef973bae40080c89602e131` |
| `report.json` | 702,934 | `8b8f6f749d06e76596684eca63e1b3959e9b6497cd73cb559ba0e0a9d7673635` |
| `manifest.json` | 813 | `ac9712ba2c048be3737a1951464e31b3d1616ada7163f30ce42534fa474022d0` |
| `manifest.sha256` | 65 | `9fd749ebe59b203fb0684b133ee6cb44fbc93c37d57c8c5ac984f581ee95e432` |
| **Total run namespace** | **128,498,697** | — |

The final marker contains the correct SHA-256 of `manifest.json`; the manifest in turn verifies the exact raw and capture report sizes and hashes. Total files used about 5.98% of the 2,147,483,648-byte cap. WSL free space after finalization was 1,023,069,716,480 bytes, above the 4,294,967,296-byte minimum. The Windows volume containing the WSL virtual disk had 132,435,476,480 bytes free after the run. No raw copy was made in the Windows checkout.

At the observed short-run rate, raw data grew at about 70,972 bytes/s and the complete namespace at about 71,363 bytes/s. Linear projection of the complete-namespace rate is approximately 6.166 GB decimal/day and 43.160 GB decimal/seven days. This is arithmetic from one 30-minute market interval; it does not cover other traffic regimes, metadata growth, failures or recovery, and does not prove that a 100 GB Stage B budget would suffice.

## Schemas and event rates

The raw log contains 208,691 JSONL records: 208,688 events and three controls (`session_start`, `snapshot_attempt`, `stop`). Every line parsed. All events contained the complete `m20.3-envelope-v3` fields, all controls used `m20.2r-control-v1`, and all decoded payloads contained the stream-specific fields required by the capture engine.

| Event | Count | Mean rate over 1800.645715331 s |
|---|---:|---:|
| `trade` | 44,901 | 24.9361/s |
| `bookTicker` | 145,892 | 81.0220/s |
| `depth@100ms` | 17,894 | 9.9375/s |
| REST snapshot | 1 | initialization only |
| **All events** | **208,688** | **115.8962/s** |

Transport telemetry counted 208,687 WebSocket messages received, queued and processed; the additional event is the REST snapshot. Queue high-water was 1 of 4096. Known local drops, oversized messages, queue saturation, receive-sequence errors, rejected records, monotonic discontinuities and detected UTC steps were all zero.

## Book reconstruction and comparability

- The book became valid at record 81 after the one snapshot and ended valid at update ID `101289192419`, with 5001 known snapshot levels and no buffered depth remaining.
- Valid duration was 1,798.646121403 s of 1,800.645715331 s (`99.888951%`); initial invalid/awaiting-snapshot duration was 1.999593928 s (`0.111049%`); unknown-duration intervals were 0.
- Observed depth sequence checks passed 17,894/17,894 with 0 detected gaps. This states continuity only for received observations and is not proof that Binance or the network supplied every upstream event.
- Depth processing recorded 17,880 applied, 20 initially buffered, 14 stale, 0 duplicate and 0 invalid-buffered observations. Book invariants passed 17,881/17,881 with 0 failures.
- Exact-ID `bookTicker` comparability was only 115/145,892 (`0.078825%`): 145,777 were unmatched, 0 stale and 0 inconsistent. Among comparable states, mismatches were 0/115. The small comparable subset is reported as observed and is not evaluated against an invented 95% gate.
- WebSocket reconnects and recovery cycles were 0; snapshot attempts were 1; snapshot retry exhaustion was 0. Thus reconnect/recovery behavior was not exercised by this live interval.

## Replay and integrity

Read-only offline replay returned `VERIFIED_FINALIZED`. It independently verified the manifest marker, raw/report hashes, the persisted stop reason and every stored derived field. Replay reported `data_quality=VALID`, no partial tail, the same duration/counters/transitions, and the same final book and invariants. Capture-side `report.json` correctly remains labelled `CAPTURE_NOT_REPLAY_VERIFIED`; replay supplies the separate independent verification rather than rewriting the frozen capture artifact.

Protected integrity checks after the run matched 55/55 WSL frozen fixtures and 22/22 files in the M20.3R artifact manifest. The raw namespace was not edited after finalization.

## Resource observations

Twenty-nine OS samples were taken roughly every 58–67 seconds. The highest sampled process value was 10.4% in the `ps` CPU field, and the highest sampled RSS was 54,972 KiB (about 53.7 MiB). These sparse observations are not exact CPU or memory peaks. Adapter-internal exact peak CPU/RAM telemetry was unavailable. The last pre-deadline sample showed 127,391,794 raw bytes and 1,023,070,830,592 bytes free in WSL.

## Verification and decision boundary

- Full WSL test suite: `407 passed, 3 warnings in 29.62s` using `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q`.
- Dependency check: `No broken requirements found.`
- `git diff --check`: passed.
- Windows Smart App Control, dependencies, `.env`, SQLite, Batch 3, Oracle/server state and trading processes were not changed.

The run supports discussing a separately specified Stage B because the bounded live adapter finalized and replayed one real interval within its storage and local-loss controls. It does not authorize Stage B. Any decision must account for the short single-regime sample, low exact-ID comparable share, unexercised live reconnect/recovery paths, unknown rare-stall behavior and the uncertainty in the linear storage projection. A new explicit authorization and resource decision remain required.
