# M20.5 — Extended Capture Readiness Protocol

## Frozen starting point

- Source commit: `f58ae976e39e314afdf093f475108e8396fc730d` (`main`, equal to `origin/main` after publication of accepted M20.4).
- M20.4 status remains `SMOKE_CAPTURE_PASSED`.
- Existing Stage A raw is read-only at `/home/ostap/ai_trader_capture/stage_a_20261011T211113Z`.
- Baseline hashes of code that may be changed are recorded in `docs/M20_5_BASELINE_HASHES.json` before implementation.
- Previous protocols, reports, manifests, synthetic artifacts and raw captures are frozen and must not be rewritten.

## Questions under review

1. What accounts for the approximately 703 KB Stage A `report.json`, and which report structures are fixed, bounded by configuration, or capable of growing with book levels, sessions, errors or events?
2. Can final metadata be made explicitly bounded without losing the totals needed to diagnose truncated histories, and is the existing one-MiB Stage A finalization reserve then sufficient near the storage cap?
3. Can a separate future extended profile enforce seven-day and 100 GB decimal upper bounds, a distinct confirmation, create-only output and no automatic Stage A transition while preserving every Stage A default?
4. Can future preflight and runtime checks cover both Linux filesystem free space and the physical Windows volume containing the WSL VHDX, failing closed when host measurement is unavailable?
5. Do virtual-time lifecycle tests cover reconnect/recovery, UTC changes, deadline, storage stops, cancellation, finalization and replay without implying live reliability?
6. Can a shell-free measurement wrapper preserve start/end timestamps, monotonic elapsed time and exit status for both successful and failing local child processes?
7. What explains Stage A `bookTicker` unmatched records when evaluated chronologically and, separately, with explicitly retrospective future-message knowledge?

## Frozen restrictions

M20.5 is local and offline. It may read the existing Stage A raw, use bounded synthetic data, mocks, virtual time and official documentation. It must not make a REST, WebSocket, DNS or market-data request; run Stage A or Stage B; touch Oracle/server state, Batch 3, `.env`, SQLite, trading processes, models, labels, PnL, backtests, signals, dependencies, security settings or frozen artifacts. Synthetic outputs must remain below 100 MiB. M20.5 is not committed or pushed.

## Readiness criteria

- Stage A CLI behavior, 1800-second maximum, 2 GiB cap and existing explicit confirmation remain the default.
- A separate explicit extended profile has upper bounds of 604,800 seconds and 100,000,000,000 bytes and requires a different confirmation. No command or state automatically promotes Stage A to it.
- Final reports omit unbounded full-book arrays and retain a deterministic bounded book summary. Diagnostic histories have configured limits plus total and omitted counts so truncation is visible.
- A stress case with realistically large book state and over-limit diagnostic histories produces at most 524,288 bytes of report plus manifest and marker. The standard 1,048,576-byte finalization reserve must cover that bound. Small-cap tests must exercise successful finalization and honest failure without large fixtures.
- Extended preflight requires at least the cap plus a 50,000,000,000-byte physical-host reserve and checks Linux and host space separately. Runtime host checks are mandatory for the extended profile and fail closed if they cannot be refreshed.
- The original 100 GB option and one smaller alternative are documented, but neither is activated. Current host free space below 150 GB cannot be waived retroactively.
- Lifecycle and wrapper tests are hermetic and complete under virtual time or local dummy children.
- Comparability results partition the original Stage A observations into counted, reproducible categories, distinguish chronological from retrospective knowledge, preserve original capture metrics and introduce no percentage gate.
- Targeted tests, full WSL pytest, `pip check`, protected hashes, new artifact hashes and `git diff --check` pass.

## Verdicts

The final verdict is one of `READY_FOR_RESOURCE_DECISION`, `BLOCKED_BY_RESOURCES`, or `BLOCKED_BY_IMPLEMENTATION`. None authorizes Stage B. If implementation criteria pass but the physical host lacks the frozen 150 GB preflight space, the verdict is `BLOCKED_BY_RESOURCES`; a lower cap remains only a future user decision.
