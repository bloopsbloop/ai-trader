# M20.3 — Public Market Transport Adapter, Offline Verification Report

## Status

**OFFLINE_ADAPTER_VERIFIED**

M20.3 adds a BTCUSDT-only public-market adapter boundary and verifies its lifecycle with fake HTTP/WebSocket clients, virtual clocks and bounded synthetic files. No real Binance connection, DNS lookup, REST request, WebSocket session, Stage A or Stage B was executed. This status permits only a separate decision about Stage A; it is not `LIVE_CAPTURE_VERIFIED` and is not evidence of a trading edge.

Published M20.2/M20.2R source baseline: `7311a11e843c9ef0f542d66d93f7c7f50167f5dc`.

## Implemented

- Fixed market-data-only endpoints for BTCUSDT combined `trade`, `bookTicker`, `depth@100ms` and REST `/api/v3/depth?symbol=BTCUSDT&limit=5000`.
- Dependency-injected WebSocket connector, snapshot client, clock and sleep function. Real clients import `websockets`/`requests` only when the explicit capture path is invoked; tests use fakes.
- No `.env`, key, account, signed request, order endpoint, user stream or shared trading client.
- Application-ingress UTC/monotonic timestamps before parsing/queueing; original combined WebSocket body and original REST response body remain unchanged.
- Snapshot request/response metadata is stored separately in the envelope. Missing `symbol` is supplied to validation from request metadata, never inserted into raw REST JSON.
- Bounded message size, queue, recovery buffer, snapshot attempts and reconnect attempts.
- Bounded timeout/429/418/5xx retry behavior with `Retry-After`, bounded backoff and terminal exhaustion.
- New continuity epoch and snapshot after reconnect; no claim that missing history is repaired.
- Independent elapsed deadline, read polling, cancellation and stream-idle diagnostic. Stream inactivity alone does not invalidate a valid book.
- Separate transport telemetry: received, queued, processed, byte counts, local drops, queue high-water mark, reconnects, snapshot attempts and idle diagnostics. Unknown upstream loss is not estimated. CPU/RAM remain `UNAVAILABLE` because no new dependency or sufficiently portable sampler was introduced.
- Offline preflight validates limits, unique output path, writability and free space without opening a network connection.
- CLI keeps offline replay and adds `preflight`, `capture --dry-run`, and an explicit confirmed future `capture` path.

## M20.2R core integration changes

The core envelope schema advances to `m20.3-envelope-v3` with separate metadata. The engine can parse Binance combined-stream wrappers after raw persistence and can validate a raw REST snapshot with symbol carried only in metadata. Lifecycle control records now include session start, disconnect, snapshot attempt/failure, reconnect wait, idle and cancellation. Capture-side transport telemetry is stored in the report but excluded from independently reconstructed replay fields.

M20.2/M20.2R documents, manifests and local synthetic namespaces remain byte-for-byte unchanged. Existing mock transport and offline replay tests remain green.

## Offline integration result

The create-only namespace `data/research/m20_3_offline_adapter_v1` contains one finalized fake run and compact capture/replay/equivalence summaries.

Expected final state was asserted directly:

- exact raw snapshot body without injected symbol;
- final book valid at update ID 101;
- best bid `100 × 3`, best ask `101 × 4`;
- 3 fake WebSocket messages received, queued and processed;
- 361 application bytes received/queued;
- queue high-water mark 1;
- one snapshot attempt, zero reconnects and local drops;
- stop reason `input_exhausted`;
- finalized bytes: 6,147;
- replay integrity `VERIFIED_FINALIZED`;
- independently derived fields all match;
- network performed: false.

The complete M20.3 synthetic namespace has 8 files and 14,771 bytes, below 100 MiB.

## Fault and lifecycle evidence

Hermetic tests block AF_INET/AF_INET6 socket construction, DNS resolution and `create_connection`; asyncio's local AF_UNIX socketpair remains allowed for its event loop.

Verified scenarios:

- normal snapshot initialization and adapter → writer → engine → report → replay;
- snapshot race, delayed/cross-stream ordering and repeated snapshot after disconnect;
- disconnect while snapshot task is pending, task cancellation and new session;
- HTTP timeout, 429 with `Retry-After`, 5xx and retry exhaustion;
- silent application stream with independent deadline and idle diagnostic without false book invalidation;
- depth gap, malformed payload, out-of-order bookTicker and UTC step;
- queue saturation and oversized message as explicit known local losses followed by invalidation/recovery;
- cancellation with pending snapshot and socket close;
- cancellation during reconnect wait;
- cap, free-space preflight and injected writer failure;
- non-default M20.2R config, hashes, finalization and replay regressions.

The first adapter test pass exposed and fixed three lifecycle issues: reconnect was prematurely stopped by an initial `input_exhausted` value, idle timing had no session-start anchor before the first market message, and writer failure on a session control escaped without an incomplete verdict. The final targeted suite includes regression coverage for these paths.

## Limits and resource decisions for Stage A

A separate Stage A authorization must confirm:

- one local Linux output root and unique run ID;
- maximum 1,800 seconds;
- maximum 2,147,483,648 stored bytes;
- at least 4,294,967,296 bytes remaining after writes, hence at least 6,442,450,944 bytes free at start;
- permission for the host to remain awake and network-connected for up to 30 minutes;
- review of the exact two public market-data-only endpoints;
- acceptance that CPU/RAM telemetry is unavailable in this prototype and that live rates, latency, reconnect behavior and resource use are unknown.

The offline CLI preflight returned `network_performed=false`, `READY_FOR_SEPARATE_LIVE_AUTHORIZATION`, a 60-second test deadline and the 6 GiB start requirement. It created no capture namespace.

## Verification

- Targeted M20.2/M20.2R/M20.3: **55 passed in 0.15 s**.
- Full WSL pytest: **385 passed, 0 failed, 0 skipped, 3 existing dependency warnings in 25.47 s**.
- Protected fixture/manifest selection: **15 passed in 0.90 s**.
- `pip check`: **No broken requirements found**.
- CLI import/help and offline preflight: passed without network.
- Existing 55 WSL protected fixture hashes: unchanged.
- Prior M20.2/M20.2R artifact hashes and the new M20.3 manifest: passed.
- `git diff --check`: passed after final document generation.

The warnings are the existing `python-binance`/`websockets` deprecations. Dependency versions were not changed.

## Unverified until Stage A

The real clients have not connected to Binance. Actual schemas, combined-stream behavior, server ping/pong interaction, REST headers, 24-hour disconnect behavior, traffic rate, sustained contention, snapshot latency, clock quality, CPU/RAM/disk throughput, public-host availability and shutdown under real OS/network faults remain unverified. The library is configured to respond to server ping frames without adding its own periodic ping, but that behavior has not been observed against Binance.

Power loss or forced process termination can leave a recovery-only namespace without a final marker. Local hashes detect accidental or isolated modification, not coordinated malicious rewriting by someone with filesystem access.

There is no automatic Stage B transition. No features, signals, labels, PnL, model, strategy or backtest were added.
