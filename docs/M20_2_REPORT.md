# M20.2 — Offline-Verified Market Data Capture Prototype

## Status

**OFFLINE_VERIFIED**

The isolated prototype processes synthetic application messages through a mock transport, writes a bounded create-only raw log, reconstructs a limited order book, publishes separate quality diagnostics, and deterministically replays the saved log. This status means only that the specified offline behaviors were exercised. It does not mean `LIVE_CAPTURE_VERIFIED`, authorize Stage A or Stage B, or establish predictive signal or trading edge.

Source checkpoint: `b28ed0a4ba64ea46083e4644714fea16963f49d1`.

## Implemented components

- `transport.py`: finite mock transport; every non-mock transport request fails explicitly. No live adapter exists.
- `envelope.py`: application-ingress envelope with session, local sequence, UTC and monotonic receive values, raw payload text, schema, clock kind and configured timestamp unit.
- `storage.py`: create-only JSONL run namespace, conservative pre-write byte/free-space checks, final reserve, hashes, close/finalize behavior and partial-tail detection.
- `book.py`: bounded pre-snapshot buffer and checkpoint retention, Decimal price/quantity handling, `U/u` overlap/gap rules, zero-quantity removal, invalidation and resnapshot recovery.
- `engine.py`: validation, rejection reasons, session/clock tracking, bounded queue, explicit stop reasons, valid/invalid durations and separate quality ratios.
- `replay.py` and CLI: offline reconstruction from a saved `raw.jsonl`; imports, help and replay contain no network path.

No strategy, signal, model, order, account, key, paper/shadow runner, SQLite integration, daemon or scheduler was added.

## Synthetic run

The final create-only run is `data/research/m20_2_offline_capture_v1/synthetic_final`. It contains ten synthetic records over two labeled sessions, including a buffered batched update, snapshot overlap, intra-batch and exact-ID bookTicker messages, an injected sequence gap, resnapshot, malformed payload and UTC step with continuous monotonic time.

| Metric | Result |
|---|---:|
| received / persisted / rejected | 10 / 10 / 1 |
| applied / buffered depth | 2 / 1 |
| sequence gaps | 1 |
| sequence integrity | 3 / 4 |
| book invariant passes | 4 / 4 |
| comparable / unmatched / stale / inconsistent | 2 / 2 / 0 / 0 |
| exact-ID coverage | 2 / 4 |
| exact-ID agreement | 2 / 2 |
| valid / invalid synthetic duration | 5 s / 3 s |
| UTC steps | 1 |
| stop reason | `input_exhausted` |
| final run files | 5,761 bytes |

Final reconstructed state is valid at update ID 201, with bid `99 × 4` and ask `100 × 6`. The replay matched the mock-online result for the final book, valid/invalid transitions, counters, durations, comparability diagnostics, sessions and stop reason. The rates, durations and ordering are deliberately synthetic and say nothing about Binance traffic, latency or future host performance.

## Fault-injection coverage

Hermetic tests cover normal and overlapping sequences, snapshot race, stale/duplicate updates, sequence gap and resnapshot, exact Decimal keys and zero removal, bounded history, intra-batch/unmatched/stale/exact/mismatched/no-comparable bookTicker cases, malformed and incomplete payloads, wrong symbol, timestamp units, UTC step, queue saturation, pre-write cap, minimum free space, injected disk failure, partial final record, user stop, virtual time stop, session change, snapshot retry exhaustion and explicit network-transport rejection.

The queue test proves bounded nonblocking behavior directly. The current single-threaded mock engine drains each queued item immediately, so it does not measure sustained producer/consumer contention. The interrupted-write test proves that a non-newline tail is detected and excluded from replay; it does not emulate every filesystem or power-loss mode.

## Storage, time and reconstruction limits

- The run budget counts all files found below the run namespace plus the pending write and reserved final bytes. It therefore includes raw, derived, report, manifest and any temporary files placed there. No current writer temp file exists. An external process can still consume free space between check and write, so the mechanism cannot promise an absolute disk-wide guarantee.
- Stage A's proposed bounds remain 30 minutes or 2 GiB total files, but M20.2 tests only small injected limits. The elapsed-time stop is evaluated at application-message ingress using monotonic time; a future live adapter needs an independent idle timer for a silent connection.
- The raw record is the application payload as delivered to the transport boundary, not a network packet capture. UTC and monotonic receive values do not measure exact one-way network latency.
- Snapshot depth limits the known book range. Resnapshot restores future validity only. Missing history is not reconstructed.
- Checkpoints and the pre-snapshot buffer are bounded. Old IDs become stale and IDs within a published batch remain unmatched.
- Storage is JSONL for this prototype. Durability semantics, throughput, resource peaks, shutdown under OS failure and recovery on the target host remain unmeasured.

## Verification

Verification used Ubuntu WSL2 and the existing isolated Linux environment. The tested source was baseline commit `b28ed0a4ba64ea46083e4644714fea16963f49d1` plus the uncommitted M20.2 files listed in the artifact manifest.

- Targeted M20.2 tests: **20 passed in 0.09 s**.
- Protected fixture/manifest selection: **15 passed in 0.88 s**. The existing 55-file WSL frozen-fixture manifest remained valid; tracked files from the baseline have no content diff.
- Full project suite: **350 passed, 0 failed, 0 skipped, 3 existing dependency deprecation warnings in 25.13 s**.
- `pip check`: **No broken requirements found.**
- CLI help and replay: successful; replay returned `OFFLINE_VERIFIED`, `input_exhausted`, 10 received records.
- `git diff --check`: **passed** after final file generation.

The three warnings come from the installed `python-binance`/`websockets` packages (`WebSocketClientProtocol` and `websockets.legacy` deprecations). Dependency versions were not changed.

## Offline operation

From the isolated WSL checkout:

```bash
cd /home/ostap/ai_trader_wsl_c497d40e
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m app.research.market_capture --help
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m app.research.market_capture replay \
  /mnt/c/Users/ostap/PycharmProjects/Al_Trader/data/research/m20_2_offline_capture_v1/synthetic_final
```

These commands only read local files. The CLI exposes replay only.

## Conditions before any separate Stage A decision

A future operator must separately review and approve runtime, uninterrupted host availability, the 2 GiB budget, minimum free-space threshold and output location. Before a real run, an independently reviewed live public-market adapter would need callback-time stamping, REST snapshot acquisition/retry behavior, reconnect/backoff, an idle timer, actual resource telemetry, terms/retention review, clock monitoring, clean shutdown and proof that the network path cannot access account or order endpoints. Stage A itself would then measure schemas, write rate, resource use, gaps and recovery; none of those live properties has been verified here.

M20.2 does not start a collector and does not authorize an automatic transition to Stage B.
