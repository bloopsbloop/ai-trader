# M20.2 — Offline Capture Prototype Protocol

## Baseline and purpose

- Source checkpoint: `b28ed0a4ba64ea46083e4644714fea16963f49d1`.
- Build a minimal isolated prototype for a possible future Stage A collector and verify it only with synthetic messages, virtual clocks, mock transport and fault injection.
- Engineering endpoint: deterministic persistence, reconstruction, quality accounting, bounded shutdown and replay equivalence. No predictive or economic endpoint exists in M20.2.

## Permitted scope

- New isolated module under `app/research/market_capture/`.
- Raw application-event envelope, bounded JSONL writer, offline/mock transport, depth reconstruction state machine, quality counters, replay and report generation.
- Hermetic tests and a compact synthetic artifact namespace under `data/research/m20_2_offline_capture_v1`.
- Standard library and already installed dependencies only.

## Architecture

1. `transport`: protocol plus finite `MockTransport`; the prototype has no live adapter implementation.
2. `envelope`: timestamp and preserve complete input bytes/text before semantic parsing.
3. `writer`: create-only namespace, bounded queue, pre-write budget/reserve/free-space checks, checksummed JSONL and final manifest.
4. `book`: buffer depth before snapshot, apply official `U/u` semantics with exact decimals, invalidate on gaps and retain a bounded set of exact-ID checkpoints.
5. `quality`: independent sequence, internal-book, bookTicker-comparability and coverage counters.
6. `engine`: process mock envelopes, reject malformed records with reasons, manage sessions/clocks/stops and write derived telemetry.
7. `replay`: read persisted raw JSONL, detect truncated tail, reproduce processing and compare deterministic derived state/counters.

## Frozen semantics for this milestone

- Diff-depth messages are atomic published batches. No state inside `U…u` is synthesized.
- bookTicker comparison requires an exact retained checkpoint at its `u`. Zero comparable observations is `NOT_EVALUABLE`.
- Local UTC and monotonic values come from an injected clock at application ingress before parsing. Synthetic clocks are labeled synthetic.
- Missing exchange timestamps remain absent. Timestamp unit (`MILLISECOND` or `MICROSECOND`) is explicit and validated.
- Resnapshot restores future validity only and never repairs missing history.
- Storage budget covers every file in the run namespace, including raw, derived, indexes, temporary files and reserved final report/manifest bytes.

## Test scenarios

- Normal sequence and predetermined book states.
- Snapshot race/overlap, stale/duplicate and overlapping update ranges.
- Gap, invalid state and recovery only after a new snapshot; retry exhaustion.
- Depth/bookTicker delivery permutations, exact match, exact mismatch, intra-batch ID, stale ID and no comparable states.
- Malformed JSON, unknown type/source, wrong symbol, missing fields and timestamp-unit validation.
- UTC step with continuous monotonic time and reconnect/session boundary.
- Queue saturation, cap before next write, low free space, disk failure, interrupted final record, user/time stop and clean close.
- Online-style mock processing versus offline replay equivalence.
- Any attempted non-mock/network transport fails explicitly.

## Completion criteria

- All listed scenarios have meaningful assertions against predefined small books and expected counters.
- Offline replay reproduces final valid/invalid transitions, retained final book, counters and session boundaries from persisted synthetic raw records.
- Output namespace is create-only; writer stops before caps, preserves partial-write evidence and never deletes/overwrites to continue.
- Synthetic milestone artifacts remain below 100 MiB.
- Targeted tests, full WSL pytest, `pip check`, protected hashes and `git diff --check` pass.
- Documentation states what remains unverified until separately authorized Stage A.

## Prohibited actions

No Binance or other network connection, WebSocket, REST snapshot, download, account, key, balance or order. No Stage A/B, daemon, service, scheduler, autostart, Oracle/server, Batch 3, `.env`, SQLite, live/paper/shadow trading, strategy, feature/threshold selection, PnL, backtest, training, dependency/security/production-logic/frozen-artifact change, git commit or push. Import, CLI help, tests and replay must remain offline.
