# Milestone 11.3 — ML shadow mode

## Boundary

`ML_V1_MARKET` is integrated as an observer after the complete Batch 3 heuristic
transaction. It cannot change ranking, BUY/WATCH/PASS, position sizing,
RiskManager, PaperBroker orders, or portfolio state. Shadow latency and failure
therefore occur after paper decisions are committed. The model remains unsuitable
for trade control because its historical test and walk-forward returns were
negative.

The integration is local and uses the separate Batch 3 AI SQLite database. It has
no exchange order client or live trading path.

## Inference contract

Each cycle retains the candle objects already fetched for the heuristic. The public
client requests 250 bars for indicator warm-up; the existing heuristic continues
to use its latest 50 bars. Shadow inference:

1. removes every 15m, 1h, or 4h candle that is not fully closed at the cutoff;
2. calls the same `candle_features` and backward `merge_multitimeframe` functions
   used by the Milestone 11.1 dataset;
3. selects the latest closed 1h row and verifies every joined source timestamp is
   no later than that row;
4. verifies model version, feature version, feature names and order, and all 59
   saved training medians;
5. applies only those saved train medians for missing values and hashes the exact
   ordered numeric vector passed to the model;
6. stores `SHADOW_BUY` at or above the saved 0.50 threshold, otherwise
   `SHADOW_PASS`.

Unknown or missing features fail the shadow cycle. A missing/corrupt model,
registry hash mismatch, feature error, or inference exception is logged and the
normal Batch 3 cycle continues. Model-load alerts are deduplicated. A schema alert
is queued only after three consecutive observed failures. Predictions themselves
never create Telegram messages.

## SQLite schema

The additive `ml_shadow_predictions` table contains:

```text
id, candle_close_timestamp, symbol, base_timeframe, model_version,
feature_version, probability_positive, decision_threshold, shadow_decision,
market_price, scoring_mode, feature_hash, created_at, heuristic_score,
heuristic_decision, target_timestamp, resolved_at, gross_future_return,
estimated_cost, actual_net_return, actual_label, prediction_correct
```

The unique key is `(model_version, symbol, base_timeframe,
candle_close_timestamp)`. `INSERT OR IGNORE` and a conditional resolver update
make both predictions and outcomes restart-safe.

At or after the closed candle exactly four hours after inference, the resolver
calculates:

```text
gross_future_return = close[T+4h] / close[T] - 1
estimated_cost = 2 * (0.0004 + 0.0005)
actual_net_return = gross_future_return - estimated_cost
actual_label = actual_net_return > 0.001
```

The future candle is never present in the inference vector.

## CLI and dashboard

```powershell
python -m app.ml.shadow status
python -m app.ml.shadow report
python -m app.ml.shadow report --db data/ai/custom.sqlite3
```

The commands are read-only. They show model version, total/resolved/unresolved
counts, latest prediction time, and recent rows. Precision, recall, ROC AUC, PR
AUC, Brier score, SHADOW_BUY average net return and hit rate remain labelled
`Not enough data` until at least 20 predictions resolve.

Both dashboards include an **ML Shadow** page with the prominent message:
“ML shadow predictions do not control paper trades.” It displays probability,
threshold, theoretical action, same-timestamp heuristic decision, outcome state,
and actual return.

## Local validation

A dedicated scanner-only database, `data/ai/m11_3_validation.sqlite3`, was used so
the existing Batch 3 database and positions were not touched. One public-data scan
loaded `ML_V1_MARKET` with 59 features and generated three predictions at the
2026-09-28 03:00 UTC candle close:

| Symbol | ML probability | Shadow | Heuristic score | Heuristic |
|---|---:|---|---:|---|
| BTCUSDT | 0.3590 | SHADOW_PASS | 0.4069 | PASS |
| ETHUSDT | 0.3840 | SHADOW_PASS | 0.2692 | PASS |
| SOLUSDT | 0.4846 | SHADOW_PASS | 0.3760 | PASS |

The run was `SCAN_ONLY`, created no account and no paper trade. Reopening the
database and processing the same slot returned `already_processed`; prediction
count stayed at three. All three remain unresolved until their 07:00 UTC future
candle is available, so forward metrics correctly show `Not enough data`.

The validation database is an ignored local artifact. It is separate from
`data/ai/batch3.sqlite3`.

## Limitations

Live indicator values use 250 public candles as warm-up, while the historical
dataset retained a ten-day warm-up. Definitions, units, alignment, order, and
trained imputation are shared, but exponentially weighted indicators can retain a
small initialization difference. Forward observations cover only timestamps at
which the runner and symbol are available. If a symbol leaves the liquid universe,
an unresolved outcome waits until candles for that symbol are observed again.
Twenty resolved samples only unlock metric display; it is not enough evidence for
promotion. A substantially longer forward PAPER period across market regimes is
required.
