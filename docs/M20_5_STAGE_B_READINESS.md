# M20.5 — Stage B Readiness

## Verdict

**`BLOCKED_BY_RESOURCES`**

The implementation is ready for a resource decision, but the approved 100 GB decimal profile is not runnable under its frozen physical-disk rule today. The Windows volume holding the Ubuntu `ext4.vhdx` has 131,268,493,312 bytes free; the profile requires at least 150,000,000,000 bytes before launch so that a full 100 GB cap cannot consume the 50 GB physical reserve.

This verdict does not authorize Stage B, another Stage A, a smaller cap or any market connection.

## Resolved implementation conditions

- Future reports and diagnostic histories are bounded, with visible totals and omitted counts.
- The terminal in-memory book is capped; overflow invalidates and recovers rather than silently pruning.
- The one-MiB finalization reserve exceeds the explicit 589,889-byte metadata maximum.
- Stage A remains the CLI default and preserves its 1,800-second, 2 GiB and confirmation boundaries.
- Extended capture is separately selected, capped at seven days and 100 GB decimal, and requires a different confirmation plus an explicit VHDX path.
- Linux and physical Windows free space are checked separately before launch and during writes. Host-monitor failure stops writes rather than assuming capacity.
- Reconnect/recovery lifetime totals are distinct from consecutive streaks used by retry limits.
- The process wrapper records UTC start/end, monotonic elapsed time and exit code without shell interpolation.
- Offline lifecycle, finalization, replay and historical M20.4 compatibility tests pass.

## Conditions requiring a user decision

1. Provide at least 150 GB free on the existing physical Windows volume while preserving the 50 GB stop reserve, then repeat the offline preflight; or make a separate decision about a lower cap.
2. If considering the single documented alternative, review a 75 GB cap with the same 50 GB reserve. It would require 125 GB initially, but the current margin is only about 6.27 GB and no evidence yet proves that this cap covers a seven-day traffic regime.
3. Explicitly authorize the selected runtime, disk cap, host availability and public market connection in a future task. No selection is implied by this document.

The Stage A linear estimate of about 43.16 GB for seven days is based on one 30-minute interval. It does not cover high-traffic regimes, reconnects, recovery snapshots or rare stalls, and cannot replace the resource decision.
