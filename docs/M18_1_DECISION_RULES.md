# M18.1 — Frozen Evaluation Decision Rules

## Scope and lock

These rules apply to the single continuous Donchian 120/60 path registered in `M18_1_EVALUATION_PROTOCOL.json`. M18.1 only registers the plan. It does not authorize data acquisition, backtesting, model training, forward observation, or trading.

Frozen evaluation protocol SHA-256: `0997c5ba7f63351bbf10ab6271aa6d996ce9232dfefd8a792e735d57e1449f5e`.

After candidate data or results are opened, the universe, period, strategy, warm-up, initial state, lambda, benchmarks, costs, endpoints, bootstrap, and rules below cannot be changed. A material implementation defect requires a versioned correction that preserves the first result and documents the resulting loss of holdout cleanliness.

## Two separate questions

### A. Absolute economic result

The estimate is the unchanged Strategy's mean daily net return relative to zero-yield Cash under base costs. Report its full-window net and annualized returns, mark-to-market drawdown, realized volatility, exposure, turnover, costs, trades, and holding duration.

Classification:

- `POSITIVE_ABSOLUTE_EVIDENCE`: point estimate is positive, full-window Strategy net return is positive, and the multiplicity-adjusted lower confidence bound is above zero.
- `INCONCLUSIVE_ABSOLUTE`: point estimate is positive but its adjusted interval includes zero, or full-window net return is not positive.
- `NO_ABSOLUTE_EVIDENCE`: point estimate is zero or negative. Do not optimize on the opened period.

### B. Incremental value over calibrated passive

The estimate is the exact-date paired mean daily net-return difference, Strategy minus the passive portfolio that invests the frozen fraction `0.5923903262274034` at inception and holds the remainder as cash. The passive portfolio is not recalibrated on 2026 data.

Classification:

- `POSITIVE_INCREMENTAL_EVIDENCE`: paired point estimate is positive, Strategy full-window net return is positive, and the multiplicity-adjusted paired lower confidence bound is above zero.
- `INCONCLUSIVE_INCREMENTAL`: paired point estimate is positive but its adjusted interval includes zero, or Strategy full-window net return is not positive.
- `NO_INCREMENTAL_EVIDENCE`: paired point estimate is zero or negative. Do not optimize on the opened period.

A positive relative effect with a negative absolute Strategy result is not a trading edge and cannot receive either positive combined conclusion.

## Uncertainty and multiplicity

For both questions, use exact aligned complete UTC-day simple returns from `2026-01-02 00:00` through `2026-10-01 00:00 UTC`. The equity path begins and ends at midnight, so the expected 273 daily observations cover the entire path without partial endpoints. Verify that compounded daily factors reconcile to terminal equity within `1e-12`.

The primary uncertainty method is a paired non-overlapping calendar-time block bootstrap:

- 28 calendar days per primary block;
- 5,000 repetitions;
- seed 42;
- block sums and counts retained;
- the expected 10th block is incomplete and retains its actual 21-day sum/count;
- no `dropna`, date intersection, or window mixing is allowed.

Report ordinary two-sided 95% intervals for each estimate. Because A and B are two formal co-primary claims, positive evidence uses Bonferroni simultaneous two-sided 97.5% intervals, calculated with 1.25% and 98.75% bootstrap quantiles. This fixes family-wise error at no more than 5% for the two claims. No other comparison is formal.

The 14-day and 56-day block calculations are sensitivity analyses. Fully invested buy-and-hold, doubled costs, individual symbols, and calendar subwindows are descriptive controls. They cannot rescue a failed co-primary claim or increase status.

## Robustness reporting

Run the identical frozen policy under doubled commission and slippage using the same data, dates, signals, and passive lambda. Report absolute and relative point estimates and uncertainty, but treat them as secondary. A promising base result accompanied by negative Strategy net return or negative relative point effect under doubled costs must be flagged as execution-cost fragile and sent to review; it is never promoted automatically.

Report the following three slices from the continuous portfolio ledger:

1. `2026-01-01 00:00` through `2026-04-01 00:00 UTC`;
2. `2026-04-01 00:00` through `2026-07-01 00:00 UTC`;
3. `2026-07-01 00:00` through `2026-10-01 00:00 UTC`.

There is no reset, refit, recalibration, or new initial capital at slice boundaries. These slices describe temporal concentration and are not independent replications.

No user-defined maximum acceptable drawdown exists. Drawdown must be reported, but no threshold may be invented after observing it.

## Overall interpretation

- `PROMISING_INDEPENDENT_CANDIDATE`: both A and B satisfy their positive-evidence rules, the Strategy remains positive and the relative point effect remains positive under doubled costs, and all integrity checks pass. Because provenance is uncertain, this label still requires human review and does not authorize shadow, paper, or live use.
- `PARTIAL_OR_INCONCLUSIVE`: at least one positive point estimate has an interval crossing zero, only one formal question passes, or stress results are fragile.
- `NO_EVIDENCE`: the absolute Strategy estimate is nonpositive, the incremental estimate is nonpositive, or both formal questions fail without a positive uncertain signal.
- `DATA_QUALITY_BLOCKED`: acquisition, checksum, schema, timestamp, common-grid, boundary, hash, alignment, or reconciliation checks fail. Do not calculate or inspect economic conclusions from a partial dataset.

Nine months and a small number of Donchian trades may not provide enough statistical power. Wide intervals are an expected possible outcome and must be reported as inconclusive rather than used to justify another historical search.

No outcome produces automatic trading or monitoring. An encouraging result permits only a separate review. A negative result closes this frozen-policy check on the opened candidate and does not authorize tuning against it.
