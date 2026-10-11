# M20.2 — Comparability Addendum

## Correction to the proposed M20.1 gate

The M20.1 provisional requirement that at least 95% of eligible `bookTicker` observations have an exact retained depth update ID is not a demonstrated collector-correctness threshold. M20.2 removes it as a pass/fail gate and does not replace it with another arbitrary percentage. A future Stage A must report observed coverage, counts and collection conditions before any Stage B criterion is proposed.

This correction does not alter the published M20.1 documents. It narrows the interpretation of one proposed diagnostic.

## Exchange semantics used

- A diff-depth message publishes one atomic batch covering `U…u`. Reconstruction can retain the state after `u` when the sequence overlaps the next expected ID.
- The prototype does not invent states for IDs strictly inside a batch. Therefore a `bookTicker.u` inside `U…u` is `unmatched`, even when both streams are correct.
- A retained exact-ID checkpoint permits a BBO comparison only when the reconstructed book is valid at that point.
- `bookTicker` supplies update ID `u`, bid/ask prices and quantities, but no event or transaction timestamp. The collector must not synthesize one.
- Neither stream specifies a global cross-stream delivery order. Arrival order is captured locally and is not called exchange causality or network latency.

These semantics were checked against the official Binance Spot WebSocket stream and local order-book reconstruction documentation during protocol preparation. M20.2 made no market-data connection.

## Four independent diagnostics

1. **Depth sequence integrity.** Numerator: classified depth messages that do not reveal a sequence gap. Denominator: all classified depth messages, including buffered, applied, stale, duplicate, invalid-state and gap observations. Gap count is also published. This is an ingestion/reconstruction diagnostic, not completeness proof outside observed sessions.
2. **Internal book invariants.** Numerator: invariant checks that pass. Denominator: all invariant checks performed after snapshots and applied batches. Crossed books or invalid levels are failures. Passing internal checks does not prove agreement with an independent exchange state.
3. **Exact-ID bookTicker agreement.** Numerator: exact-ID comparisons whose BBO values agree. Denominator: exact-ID comparisons (`comparable + inconsistent`). If the denominator is zero, status is `NOT_EVALUABLE`; it is not reported as a 0% mismatch rate.
4. **Exact-ID coverage.** Numerator: observations with a retained exact-ID checkpoint (`comparable + inconsistent`). Denominator: all classified bookTicker observations (`comparable + inconsistent + unmatched + stale`). Counts for every class remain visible.

No category may be discarded to improve a ratio. `unmatched` means no reconstructable exact state was available, while `stale` means the relevant checkpoint is older than bounded retention. Neither is an agreement result.

## Synthetic batching example

Start with a valid depth state at update ID 100. A correct diff-depth message arrives with `U=101, u=103`; only the post-batch state at 103 is reconstructable. Correct bookTicker messages with IDs 101 and 102 cannot be compared exactly, while ID 103 can. Exact-ID coverage is therefore 1/3 even though no sequence gap or book inconsistency occurred. This demonstrates why a fixed 95% floor cannot establish collector correctness.

The final synthetic run deliberately includes this behavior. It reports exact-ID coverage **2/4**, agreement **2/2**, sequence integrity **3/4** with one injected gap, and book invariant passes **4/4**. Those values are test fixtures, not Binance measurements or proposed operating thresholds.
