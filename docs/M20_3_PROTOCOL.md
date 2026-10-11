# M20.3 — Public Market Transport Adapter, Offline Verification Protocol

## Baseline and scope

- Source commit: `7311a11e843c9ef0f542d66d93f7c7f50167f5dc`.
- Purpose: add a minimal BTCUSDT public-market transport boundary around the M20.2R capture core and verify its lifecycle only with injected fake HTTP/WebSocket clients and virtual time.
- Result may be `OFFLINE_ADAPTER_VERIFIED`, `PARTIAL`, or `BLOCKED`. Even the first status means only eligibility for a separate Stage A decision. It is not `LIVE_CAPTURE_VERIFIED` and says nothing about predictive signal or trading edge.

## Official interface basis

Official Binance Spot documentation was reviewed on **2026-10-11**. No Binance endpoint was called.

- JSON WebSocket streams and local-book procedure: https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md
- Spot REST order-book and rate-limit behavior: https://github.com/binance/binance-spot-api-docs/blob/master/rest-api.md
- Market-data-only hosts: https://github.com/binance/binance-spot-api-docs/blob/master/faqs/market_data_only.md

The documentation states that JSON stream connections are limited to 24 hours, server ping frames arrive every 20 seconds and require matching pong promptly, timestamps default to milliseconds, diff depth is available at 100 ms, `bookTicker` has update ID but no exchange timestamp, REST depth is limited to 5000 levels, and HTTP 429/418 responses carry `Retry-After` behavior. The selected `websockets` library handles server ping frames and automatic pong; the adapter disables optional client-generated periodic pings to avoid a duplicate heartbeat.

## Allowed public endpoints

Only these market-data-only interfaces are allowed, and only a separately authorized future capture command may contact them:

- `wss://data-stream.binance.vision/stream?streams=btcusdt@trade/btcusdt@bookTicker/btcusdt@depth@100ms`
- `GET https://data-api.binance.vision/api/v3/depth?symbol=BTCUSDT&limit=5000`

No `/api/v3/time`, `exchangeInfo`, account, key, signed, balance, order or user-data endpoint is needed. The adapter never reads `.env` and does not instantiate the project's trading client.

## Lifecycle

1. Validate the frozen BTCUSDT-only configuration and perform a filesystem/resource preflight without network access.
2. Create a unique run namespace and bounded writer.
3. Open one combined public WebSocket session with a new session/continuity epoch.
4. Record application callback UTC and monotonic time on the complete raw message before parsing or queueing.
5. Bound message size and queue capacity. Queue saturation is an explicit known local loss: invalidate the book, record the reason and reconnect/resnapshot rather than continue with `valid=True`.
6. Start a bounded REST snapshot attempt while diff-depth messages are buffered. Preserve the exact response body and separate request/response metadata; do not insert symbol or timestamps into the raw body.
7. Apply the M20.2R snapshot-overlap rules. Retry timeout, HTTP 5xx and 429/418 only within configured limits and `Retry-After`/bounded backoff. Exhaustion stops recovery explicitly.
8. On disconnect, record the reason, close the socket, start a new continuity epoch and require a new snapshot. No seamless handover or missing-history repair is claimed.
9. On deadline, cancellation, cap, free-space stop or writer failure, cancel bounded work, close transport, persist a stop/control record when storage remains available, finalize if possible, otherwise leave an explicit incomplete namespace.

## Frozen limits for a future Stage A

- Symbol: BTCUSDT only.
- Streams: `trade`, `bookTicker`, `depth@100ms` only.
- Elapsed deadline: maximum 1800 seconds (30 minutes), enforced independently of message arrival.
- Total run files: maximum 2 GiB (`2,147,483,648` bytes), including raw, reports, manifests, marker and any temporary/derived file below the namespace.
- Minimum free space after any accepted write: 4 GiB (`4,294,967,296` bytes). Preflight therefore requires at least 6 GiB available before a maximum-size run. This is a conservative local safety margin, not a Binance requirement or absolute protection against external disk writes.
- Queue capacity: 4096 messages; maximum raw application record: 2 MiB.
- Snapshot: 5000 levels, 10-second request timeout, at most 3 attempts, bounded 1/2-second retry waits unless a valid `Retry-After` requires a longer wait within the total deadline.
- Reconnect: at most 3 reconnects, bounded 1/2/4-second waits.
- Read polling timeout: 5 seconds so deadline/cancellation remains independent. Stream inactivity diagnostic: 30 seconds. Inactivity alone does not prove transport failure or invalidate the book.

No automatic deletion, overwrite, Stage B transition, service, daemon, scheduler, power-setting, system-clock, NTP or security change is allowed.

## Telemetry definitions

Publish application-observed received, queued, persisted and processed event counts separately; raw/persisted bytes; queue high-water mark; explicit local drops; reconnect and snapshot attempts; wall/monotonic measured duration; valid/invalid/unknown book durations; stop reason and session boundaries. Unknown upstream loss is never inferred. CPU/RAM are `UNAVAILABLE` unless reliably measured with existing standard-library facilities on the executing platform; missing values are not zero.

Application callback time is not NIC time or one-way latency. Stream inactivity, WebSocket connection state and reconstructed-book validity remain separate states.

## Offline acceptance criteria

- Import, help, preflight and dry-run make zero socket/DNS/HTTP/WebSocket calls.
- Fake transport integration covers normal initialization, snapshot races/repeat, cross-stream ordering, gap, disconnect during snapshot, HTTP timeout/429/exhaustion, silent connection with independent deadline, queue saturation, oversized/malformed message, UTC step, cancellation during reconnect, cap/free-space and writer failures, and cancellation with pending work.
- Expected books, state transitions, counters and stop reasons are asserted directly.
- A finalized fake run passes M20.2R hash/config/schema verification and independent replay; an incomplete run is never reported finalized.
- Existing mock transport and offline replay remain usable.
- Targeted/full WSL tests, protected hashes, manifests and `git diff --check` pass without dependency changes.

## Prohibited actions

No real WebSocket or REST request, DNS probe, market download, Stage A/B, account/key/user stream/order, `.env`, Oracle/server, Batch 3, SQLite, live/paper/shadow trading, model, feature, label, PnL, backtest, dashboard, database, scheduler, service, daemon, cloud, multi-symbol or multi-venue expansion. M20.3 code and documentation remain uncommitted.
