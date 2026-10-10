# M17.2R — Daily Endpoint Coverage Audit

## Conclusion

The frozen M17.2 primary bootstrap covers complete UTC-midnight intervals, while the reported net return covers the entire evaluation path. On the reused Test, the only excluded boundary is the first 12 hours. That interval had zero return and zero cost for Strategy and Cash, but included the initial passive purchases and market movement: `-0.268241%` for fully invested buy-and-hold and `-0.158903%` for calibrated passive under base costs.

Including every consecutive 4h increment as a post-review sensitivity leaves the practical conclusion unchanged. Its confidence interval also crosses zero. The M17.2 status remains **WEAK** and is not raised by this diagnostic.

## Equity and full-day coverage

The daily coverage columns below describe midnight equity marks. The first daily-return endpoint is one day after the coverage-start mark.

| Window | Full equity path | Midnight marks used | Daily return endpoints | Full days | Initial partial | Terminal partial |
|---|---|---|---|---:|---:|---:|
| Validation | 2024-10-21 04:00 → 2025-05-27 12:00 | 2024-10-22 00:00 → 2025-05-27 00:00 | 2024-10-23 → 2025-05-27 | 217 | 20h | 12h |
| Test | 2025-05-27 12:00 → 2026-01-01 00:00 | 2025-05-28 00:00 → 2026-01-01 00:00 | 2025-05-29 → 2026-01-01 | 218 | 12h | 0h |
| Fold 1 | 2024-07-03 16:00 → 2025-01-02 04:00 | 2024-07-04 00:00 → 2025-01-02 00:00 | 2024-07-05 → 2025-01-02 | 182 | 8h | 4h |
| Fold 2 | 2025-01-02 04:00 → 2025-07-03 20:00 | 2025-01-03 00:00 → 2025-07-03 00:00 | 2025-01-04 → 2025-07-03 | 181 | 20h | 20h |
| Fold 3 | 2025-07-03 20:00 → 2025-12-31 20:00 | 2025-07-04 00:00 → 2025-12-31 00:00 | 2025-07-05 → 2025-12-31 | 180 | 4h | 20h |

The complete 40-row audit for every window, policy, and base/doubled-cost scenario is stored in `coverage.csv`. `boundary_fills.csv` identifies the actual fills and costs assigned to excluded intervals.

## Reused Test boundaries

### Base costs

| Policy | Initial 12h return | Initial excluded costs | Terminal return | Terminal excluded costs | Full-window return |
|---|---:|---:|---:|---:|---:|
| Strategy | 0.000000% | 0.000000 | 0.000000% | 0.000000 | +3.484568% |
| Full buy-and-hold | -0.268241% | 0.899390 | 0.000000% | 0.000000 | -14.938399% |
| Calibrated passive | -0.158903% | 0.532790 | 0.000000% | 0.000000 | -8.849363% |
| Cash | 0.000000% | 0.000000 | 0.000000% | 0.000000 | 0.000000% |

### Doubled costs

| Policy | Initial 12h return | Initial excluded costs | Terminal return | Terminal excluded costs | Full-window return |
|---|---:|---:|---:|---:|---:|
| Strategy | 0.000000% | 0.000000 | 0.000000% | 0.000000 | +2.957361% |
| Full buy-and-hold | -0.357898% | 1.797563 | 0.000000% | 0.000000 | -15.091372% |
| Calibrated passive | -0.212015% | 1.064859 | 0.000000% | 0.000000 | -8.939983% |
| Cash | 0.000000% | 0.000000 | 0.000000% | 0.000000 | 0.000000% |

The terminal liquidation occurred exactly at the final midnight mark, so its effect and costs are already included in the last full-day return. They are correctly zero in the excluded terminal column.

## Reconciliation

For every policy and scenario, the audit verifies:

`total factor = initial boundary factor × compounded full-day factor × terminal boundary factor`

This equals `final equity / initial equity`. No boundary mark is counted twice. The maximum absolute reconciliation error across all 40 combinations is `5.78e-15`, consistent with floating-point rounding.

For the base Test:

- Strategy: boundary factor `1.0`; full-day compounded return `+3.484568%`; full-window return `+3.484568%`.
- Full buy-and-hold: initial factor `0.997317594`; full-day compounded return `-14.709607%`; full-window return `-14.938399%`.
- Calibrated passive: initial factor `0.998410968`; full-day compounded return `-8.704296%`; full-window return `-8.849363%`.
- Cash: all factors equal `1.0`.

## Statistical scope

The historical frozen primary result remains unchanged. It uses 218 complete UTC-day returns whose endpoint dates are exactly `2025-05-29 00:00 UTC` through `2026-01-01 00:00 UTC`; their underlying midnight marks run from `2025-05-28 00:00 UTC`. It excludes the initial interval from `2025-05-27 12:00` to `2025-05-28 00:00`. There is no excluded terminal interval.

Frozen primary Strategy-minus-calibrated-passive result:

- Mean daily difference: `+0.0004705545`.
- Paired 28-day bootstrap 95% CI: `[-0.0015752306, +0.0020235369]`.
- Positive bootstrap fraction: `71.58%`.
- Repetitions: 5,000; seed: 42.

The full net returns quoted above refer to the entire equity path, including the initial partial interval.

The predefined post-review sensitivity uses all 1,311 consecutive 4h increments from `2025-05-27 12:00` through `2026-01-01 00:00`, with exact paired timestamps. Blocks are defined by 28 calendar days from the path start. There are eight blocks; the last block retains its actual sum and observation count.

Base-cost 4h sensitivity:

- Mean 4h difference: `+0.0000815203`.
- 95% CI: `[-0.0002617335, +0.0003352389]`.
- Positive bootstrap fraction: `71.90%`.
- Linear daily display (`×6`): point `+0.0004891216`, CI `[-0.0015704012, +0.0020114336]`.

The `×6` values are linear scaling for display. They are not the mean of compounded daily returns and do not replace the frozen endpoint. Under doubled costs, the linear daily point is `+0.0004704588` with CI `[-0.0016049882, +0.0020019370]`.

## Calibration coverage

Lambda values are unchanged. Their sigma estimates used complete UTC days only:

| Calibration | Expected equity path | Midnight coverage | Daily endpoints | Observations | Excluded terminal | Lambda |
|---|---|---|---|---:|---:|---:|
| Main Train | 2023-01-21 00:00 → 2024-10-21 04:00 | 2023-01-21 → 2024-10-21 | 2023-01-22 → 2024-10-21 | 639 | 4h | 0.592390 |
| Fold 1 | 2023-01-21 00:00 → 2024-03-16 04:00 | 2023-01-21 → 2024-03-16 | 2023-01-22 → 2024-03-16 | 420 | 4h | 0.691582 |
| Fold 2 | 2023-01-21 00:00 → 2024-07-03 08:00 | 2023-01-21 → 2024-07-03 | 2023-01-22 → 2024-07-03 | 529 | 8h | 0.633253 |
| Fold 3 | 2023-01-21 00:00 → 2025-01-01 20:00 | 2023-01-21 → 2025-01-01 | 2023-01-22 → 2025-01-01 | 711 | 20h | 0.650081 |

The calibration CSV persisted coverage metadata and sigma results but not calibration equity ledgers. Therefore the boundary returns and boundary costs of calibration paths cannot be reconstructed from saved evidence without rerunning simulations. M17.2R does not rerun them and does not change lambda retrospectively.

## Integrity and verification

- M17.2 source directory contained 15 files. SHA-256 and byte size snapshots before and after the audit are identical.
- The frozen M17.2 protocol SHA-256 remains `7598fa6f8e3633a2650ad17461f8c9a543480f1215b5be98e2ea2d298adccf6c`.
- Lambda, fills, allocations, costs, dates, total PnL, and frozen M17.2 output were not changed.
- Targeted endpoint tests: `8 passed` on Windows and `8 passed` in WSL.
- Full WSL pytest: `316 passed, 3 warnings in 24.57s`; no skips or exclusions were added.
- `pip check`: no broken requirements.
- The warnings are the existing `python-binance`/`websockets` deprecations.
- No dependency versions changed.

The endpoint omission slightly understates the Test advantage of Strategy over calibrated passive because only passive lost value during the excluded initial 12 hours. Both the frozen daily CI and the all-4h sensitivity CI cross zero. The correct scope remains: **WEAK evidence on reused research periods, with no statistically resolved incremental advantage**.
