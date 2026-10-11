# M20.5 — Extended Capture Readiness Report

**Verdict: `BLOCKED_BY_RESOURCES`.** The identified implementation blockers were addressed and verified offline, but the physical Windows volume containing the WSL VHDX has 131,268,493,312 bytes free, below the frozen 150,000,000,000-byte preflight requirement for the 100 GB decimal profile. This verdict does not authorize Stage B or another Stage A.

## Published starting point and scope

Accepted M20.4 was published first as commit `f58ae976e39e314afdf093f475108e8396fc730d`; local `main` and `origin/main` matched after the push. M20.5 starts from that commit and remains uncommitted. No Binance market-data REST/WebSocket connection, capture, Stage A/B, trading process, Oracle/server change, Batch 3 integration, `.env`, SQLite, dependency or security change occurred in M20.5. Only public documentation pages were consulted online; the existing Stage A raw was read only.

## Stage A metadata diagnosis

The 702,934-byte Stage A `report.json` is large because it embeds the complete terminal book. The `book` component alone is 612,809 bytes before the outer document's additional indentation and contains 4,332 bid plus 6,606 ask levels. Other measured components are small: counters 551 bytes, telemetry 364 bytes, state transitions 202 bytes, effective config 235 bytes and session history 17 bytes. The final book therefore dominates the report; event counters and short histories do not explain its size.

The old book arrays could also grow as new price levels arrived. M20.5 changes future live reports to `bounded-book-v2`:

- five top levels per side, total current level counts, update ID, validity and buffer count replace full arrays;
- original REST snapshot bid/ask counts are stored separately from current book counts;
- live in-memory books are limited to 20,000 levels per side; crossing the limit invalidates the book and requests normal bounded recovery rather than silently pruning levels;
- state-transition and session histories retain at most 256 entries and expose total and omitted counts;
- counters and fixed telemetry remain complete.

The historical M20.4 schema remains replay-compatible. Read-only replay still returns `VERIFIED_FINALIZED` with all stored derived fields matching.

Applying the bounded schema diagnostically to Stage A produced a 3,501-byte report. A synthetic stress case with 20,000 levels per side and 1,001 transitions retained 256 transitions, reported 745 omitted entries and serialized to 29,083 bytes. The enforced ceilings are 524,288 bytes for the report, 65,536 bytes for the manifest and 65 bytes for the marker: 589,889 bytes total. The unchanged 1,048,576-byte reserve therefore has 458,687 bytes of margin over the explicit worst-case final metadata allowance. Oversized final metadata fails with `final_metadata_limit` and cannot create a false final marker.

Small-cap tests verified successful finalization with a large in-memory book and saturated diagnostic history, rejection of oversized metadata, and absence of a marker after failure. The solution bounds structures and serialized output rather than merely increasing the reserve.

## Extended profile and lifecycle

The existing CLI continues to default to Stage A: BTCUSDT, 1,800-second maximum, 2 GiB cap and `PUBLIC_MARKET_CAPTURE_AUTHORIZED`. A separately selected `--profile extended` has these upper bounds:

- 604,800 seconds;
- 100,000,000,000 bytes;
- 10,000,000,000 Linux-filesystem minimum remaining space;
- 50,000,000,000 physical-host minimum remaining space;
- separate `EXTENDED_PUBLIC_MARKET_CAPTURE_AUTHORIZED` confirmation;
- mandatory explicit Windows VHDX path.

There is no automatic transition from Stage A. Duration and size are stop ceilings rather than promises of seven complete days. Snapshot attempts, recovery limit and reconnect limit remain three.

Reconnect and recovery telemetry now distinguishes total counts from the current and maximum consecutive streaks. Limits apply to consecutive unsuccessful operation, so isolated recoveries during a long run do not consume one lifetime counter. Virtual-time tests cover repeated stable reconnects, repeated recovered gaps, UTC steps, deadline, cap/free-space stop, cancellation, finalization and replay. These tests are hermetic and do not establish live seven-day reliability.

The Stage A shell quoting failure was replaced by a shell-free Python child-process wrapper. Local dummy runs recorded independent UTC start/end, monotonic elapsed time and exit codes 0, 7 and 9; PowerShell observed the propagated failure code 9. The Requests timeout limitation is unchanged: [Requests documents that timeout is not a deadline for the complete response body](https://requests.readthedocs.io/en/latest/user/quickstart/#timeouts).

## Linux and physical-host storage

The two free-space domains are now checked independently. Microsoft documents that WSL's ext4 filesystem is held in a dynamically growing VHDX and that Linux-visible VHD capacity can exceed space available on the physical Windows device: [How to manage WSL disk space](https://learn.microsoft.com/en-us/windows/wsl/disk-space).

The offline check found:

| Domain | Available | Required for 100 GB profile | Result |
|---|---:|---:|---|
| Linux filesystem | 1,023,069,380,608 bytes | 110,000,000,000 bytes | pass |
| Windows host volume containing `ext4.vhdx` | 131,268,493,312 bytes | 150,000,000,000 bytes | fail |

The host monitor validates the explicit `.vhdx` path, derives its Windows drive, queries that drive without a shell, and fails closed on missing VHDX, command failure or invalid output. Extended preflight requires both domains. During a future run the writer refreshes the physical-host check at the first write, at most every 64 MiB of additional data, and during finalization; failure or crossing the 50 GB remaining-space stop terminates storage writes explicitly.

The original proposal remains a 100 GB cap plus 50 GB physical reserve. One possible smaller alternative is a 75 GB cap with the same 50 GB reserve, requiring 125 GB initially. The current host measurement exceeds that by only about 6.27 GB, and the short Stage A projection does not establish its adequacy. The alternative is documented only; it is not implemented, selected or authorized.

## `bookTicker` comparability audit

Binance defines `bookTicker.u` as an order-book update ID and diff-depth messages as ranges from first ID `U` through final ID `u`: [Spot WebSocket Market Streams](https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams). The Stage A raw was scanned without changing its original metrics.

The chronological reconstruction reproduced 115 comparable and 145,777 unmatched observations exactly. The unmatched denominator partitions as follows:

| Cause | Count | Share of 145,777 |
|---|---:|---:|
| Ticker arrived before a future depth packet whose range contained the ID | 142,739 | 97.9160% |
| Ticker arrived before a future depth packet ending at the exact ID | 1,145 | 0.7854% |
| ID was inside a previously received multi-ID depth packet, but was not its endpoint | 1,833 | 1.2574% |
| Book unavailable during initial snapshot construction | 60 | 0.0412% |
| Exact-checkpoint retention | 0 | 0% |
| Other or unknown | 0 | 0% |

Of the 60 initial unavailable observations, retrospective data places 2 at future exact endpoints and 58 inside future depth packets. Any category involving a future record is a retrospective data-quality explanation unavailable at online decision time. The result adds no pass percentage and does not alter the original 115/145,892 exact-ID comparable share or 0/115 mismatch count.

The raw REST snapshot contained exactly 5,000 bids and 5,000 asks. The legacy Stage A `known_snapshot_levels=5001` was computed after buffered depth changes, so it is not the REST snapshot size. Future reports use separate `snapshot_level_counts` and current `level_counts`.

## Verification

- Targeted market-capture tests: **88 passed in 1.23 s**.
- Full WSL suite: **418 passed, 3 warnings in 35.03 s**.
- `pip check`: no broken requirements.
- M20.4 legacy replay: `VERIFIED_FINALIZED`, derived fields match.
- Existing WSL frozen fixtures: 55/55 matched.
- Frozen M20.4 raw and documents: unchanged.
- New outputs total far below 100 MiB; no raw copy was created.
- `git diff --check` and no-index checks for new files: passed.

Compact derived outputs are create-only under `data/research/m20_5_extended_readiness_v1`. M20.5 remains uncommitted and unpushed.
