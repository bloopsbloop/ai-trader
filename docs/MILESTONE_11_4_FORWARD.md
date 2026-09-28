# Milestone 11.4 — forward ML experiment

## Experiment contract

The forward experiment ID is `ml_v1_market_forward_001`. Its immutable metadata
stores the model SHA-256, model/feature versions, 0.50 threshold, start time,
BTCUSDT/ETHUSDT/SOLUSDT scope, 15m/1h/4h features, four-hour target, 0.04%
commission, 0.05% slippage, 0.18% estimated round-trip cost, and theoretical
capital assumptions.

`ML_V1_MARKET` was not retrained. It remains an observer that runs after the
Batch 3 heuristic transaction commits. No shadow path calls PaperBroker or
RiskManager.

## Persistence and resolution

`ml_shadow_predictions` now includes `experiment_id` and `future_close`. Existing
Milestone 11.3 databases receive an additive migration. Two new tables are used:

- `ml_shadow_experiments` stores immutable experiment metadata.
- `ml_shadow_cycles` records successful and failed inference cycles for data-quality
  monitoring.

Every future Batch 3 cycle revisits unresolved predictions for currently observed
symbols. Resolution requires the exact, fully closed 1h candle at T+4h and stores:

```text
future_close
gross_future_return = future_close / inference_close - 1
estimated_cost = 2 * (0.0004 + 0.0005)
actual_net_return = gross_future_return - estimated_cost
actual_label = actual_net_return > 0.001
prediction_correct
resolved_at
```

The update contains `WHERE resolved_at IS NULL`, so restarts cannot overwrite an
outcome. Tests also verify that resolution never changes probability, feature
hash, heuristic score, or heuristic decision.

## Reports

Resolved-sample status labels are:

| Resolved | Label |
|---:|---|
| 0–19 | Not enough data |
| 20–49 | Preliminary |
| 50–199 | Early forward sample |
| 200+ | Larger forward sample |

Precision, recall, F1, ROC AUC, PR AUC, Brier score, SHADOW_BUY mean/median return,
hit rate, theoretical PnL, profit factor, and drawdown are suppressed below 20
resolved observations. AUC is also omitted when only one actual class is present.

The report compares the same timestamp/symbol observations in four groups:

- ML BUY / heuristic BUY
- ML BUY / heuristic PASS (including heuristic WATCH as no-buy)
- ML PASS / heuristic BUY
- ML PASS / heuristic PASS

Disagreement A is ML BUY with heuristic no-buy. Disagreement B is ML PASS with
heuristic BUY. Each category reports count, positive-label rate, mean return, and
median return.

Probability buckets are `0.00–0.40`, `0.40–0.50`, `0.50–0.60`, `0.60–0.70`, and
`0.70–1.00`. They expose count, mean predicted probability, actual positive rate,
and average net return. This is a forward calibration measurement only; the saved
model and calibrator are never changed.

BTC, ETH, and SOL metrics use the same minimum-sample rules. Data-quality warnings
cover scans without a shadow cycle, schema/model/inference failures, stale or
missing candles, outcomes overdue by more than one hour, and per-symbol hourly
timestamp gaps.

## Theoretical comparison

ML shadow and heuristic shadow curves use the exact same resolved prediction rows.
Both start with 1000 USDT and apply a fixed 100 USDT virtual notional to every BUY
signal. PnL is:

```text
100 USDT * actual_net_return
```

`actual_net_return` already subtracts commission and slippage, so costs are not
deducted twice. Curves report equity, PnL, trade count, profit factor and maximum
drawdown. Cash remains fixed at 1000 USDT. These are labelled **THEORETICAL SHADOW
RESULT** and never create orders.

## CLI and exports

```powershell
python -m app.ml.shadow status
python -m app.ml.shadow report
python -m app.ml.shadow resolve
python -m app.ml.shadow export
```

`resolve` requests public 1h candles only for unresolved symbols. `export` writes:

```text
data/ml/forward/summary.json
data/ml/forward/summary.md
data/ml/forward/predictions.csv
data/ml/forward/disagreements.csv
data/ml/forward/probability_buckets.csv
data/ml/forward/equity_curve.csv
```

The dashboard ML Shadow page contains Overview, Performance, Disagreement Analysis,
Probability Buckets / Forward Calibration, Per Symbol, data-quality warnings, and
Recent Predictions. It retains the visible “ML SHADOW ONLY” notice.

## Manual validation

The separate `data/ai/m11_4_validation.sqlite3` database was used with the real
saved model and deterministic candles. One prediction was generated, its exact
four-hour future close was supplied, and the resolver stored a cost-adjusted
outcome. A second resolver run changed zero rows. Probability, feature hash,
heuristic score, and heuristic decision remained byte-for-byte equal.

The validation sample produced one `ML PASS / heuristic BUY` observation. Gross
return was zero, estimated cost 0.18%, and net return -0.18%. Metrics correctly
remain `Not enough data`. The heuristic theoretical curve lost 0.18 USDT on fixed
100 USDT exposure; ML and cash remained at 1000 USDT. All six export files were
created.

## Limitations

The validation row proves mechanics, not predictive value. Forward metrics need at
least 20 resolved rows to display and materially more observations across regimes
before any model decision is justified. Fixed-notional curves permit overlapping
signals and are comparison tools rather than a broker simulation. Outcomes for a
symbol that leaves the observed liquid universe may remain unresolved until that
symbol is fetched again. Timestamp-gap warnings may include intentional runner
downtime.
