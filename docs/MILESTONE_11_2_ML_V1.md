# Milestone 11.2 — ML_V1_MARKET

## Scope and safety boundary

This milestone trains one offline market-only decision model from the Milestone
11.1 `WITHOUT_NEWS` dataset. It does not load API keys, place exchange orders, or
connect the model to the Batch 3 continuous runner. Batch 1/2, the Batch 3 SQLite
state, Telegram, dashboard, deployment files, `.env`, and server infrastructure are
unchanged.

Run locally:

```powershell
.\.venv\Scripts\python.exe -B -m app.ml.train --force
.\.venv\Scripts\python.exe -B -m app.ml.evaluate
```

The registry refuses to overwrite an existing model unless `--force` is explicit.
Training never evaluates test data. The separate evaluation command loads the saved
model and opens the test partition.

## Model and target

`ML_V1_MARKET` uses scikit-learn `HistGradientBoostingClassifier`. XGBoost,
LightGBM, and CatBoost were not installed; this fallback keeps the dependency and
serialization surface small. The selected bounded-search configuration is:

```text
learning_rate=0.05
max_iter=140
max_leaf_nodes=15
min_samples_leaf=50
l2_regularization=1.0
early_stopping=false
class_weight=balanced sample weights
random_seed=42
```

The target is unchanged from Milestone 11.1:

```text
future_net_return = close[T+4] / close[T] - 1
                    - 2 * (0.0004 commission + 0.0005 slippage)
label = 1 when future_net_return > 0.001
```

The base timeframe is 1h and the prediction horizon is four hours. The model uses
59 market features after train-only removal of one exact duplicate. Input features
cover 15m, 1h, and 4h. The full dataset has 78,617 rows: 49,898 negative (63.47%)
and 28,719 positive (36.53%).

## Chronology and selection

- Train: 2023-01-01 09:00 UTC through 2024-10-19 08:00 UTC; 47,069 rows,
  30,356 negative and 16,713 positive.
- Validation: 2024-10-19 13:00 UTC through 2025-05-26 12:00 UTC; 15,768 rows,
  9,458 negative and 6,310 positive.
- Test: 2025-05-26 17:00 UTC through 2025-12-31 20:00 UTC; 15,780 rows,
  10,084 negative and 5,696 positive.

Two small parameter configurations, each weighted and unweighted, are compared on
validation only. The threshold grid is 0.50 through 0.75 in 0.05 steps. A minimum
trade guard rejects thresholds supported by too few trades. Test rows cannot affect
the preprocessing medians, feature selection, hyperparameters, class weighting,
calibration, or threshold.

Sigmoid calibration was fitted on the first half of validation and accepted only
after the second half improved Brier score from 0.253172 to 0.236595, beyond the
predefined 0.001 margin. The final threshold of 0.50 was then selected on that
second validation half. Test was not used. Calibration parameters are stored in the
serialized model and registry metadata.

## Classification results

| Period | ROC AUC | PR AUC | Precision | Recall | F1 | Brier |
|---|---:|---:|---:|---:|---:|---:|
| Train | 0.6999 | 0.5568 | 0.7752 | 0.0557 | 0.1039 | 0.2125 |
| Validation | 0.5654 | 0.4540 | 0.5348 | 0.0548 | 0.0995 | 0.2367 |
| Test | 0.5750 | 0.4208 | 0.5281 | 0.0330 | 0.0621 | 0.2272 |

Test confusion matrix is `[[9916, 168], [5508, 188]]`. The low recall is a direct
consequence of conservative probabilities/thresholding and must not be hidden by
the modest AUC values.

## Trading simulation and baselines

The comparable simulator allows one position at a time, invests 10% of current
virtual equity, and holds it for the fixed four-hour label horizon. Entries use only
the current row prediction. `future_net_return` already includes the configured
round-trip commission and slippage; reported fee estimates are attribution and are
not subtracted a second time.

| Test strategy | Net return | Trades | Win rate | Profit factor | Expectancy | Max drawdown |
|---|---:|---:|---:|---:|---:|---:|
| ML_V1_MARKET | -0.8795% | 136 | 47.79% | 0.911 | -0.0647 USDT | 1.9396% |
| Batch 3 market heuristic reconstruction | -8.3807% | 424 | 38.68% | 0.645 | -0.1977 USDT | 8.6773% |
| Simple trend | -17.6310% | 911 | 37.10% | 0.581 | -0.1935 USDT | 17.8426% |
| Class-prior / cash | 0.0000% | 0 | n/a | n/a | 0 | 0.0000% |
| Buy & Hold, comparable 10% total exposure | -1.0949% | 3 | 33.33% | 0.337 | -3.6497 USDT | 5.8549% |

ML realized -8.7953 USDT, estimated fees of 10.8125 USDT, turnover of 27,031.36
USDT, and ending equity of 991.2047 USDT. It beat the reconstructed heuristic,
simple trend, and comparable-exposure Buy & Hold on net return. It did not beat the
no-skill cash baseline and is not a profitable deployment candidate. The heuristic
baseline is reconstructed from historical market features; it is not a replay of
the full live Batch 3 state machine.

## Walk-forward result

Each fold selects its threshold on an inner chronological validation slice and then
refits on the fold's past before evaluating the next unseen interval.

| Fold | Threshold | ROC AUC | PR AUC | Net return | Trades | Drawdown | Profit factor | Expectancy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 0.70 | 0.5736 | 0.4420 | -0.5218% | 184 | 4.2393% | 0.967 | -0.0284 |
| 2 | 0.65 | 0.5632 | 0.4529 | -5.9241% | 358 | 9.0101% | 0.814 | -0.1655 |
| 3 | 0.65 | 0.5732 | 0.4138 | -2.9399% | 157 | 4.0677% | 0.788 | -0.1873 |

Mean ROC AUC is 0.5700 and mean PR AUC is 0.4362. Mean fold return is -3.1286%,
mean max drawdown 5.7724%, mean profit factor 0.856, and mean expectancy -0.1270
USDT. Across folds the simulator produced 699 trades and -93.8586 USDT total PnL.

## Feature importance

Validation permutation importance uses PR AUC and is predictive, not causal. The
top 20 are:

| Rank | Feature | Mean importance |
|---:|---|---:|
| 1 | atr_fraction_14_15m | 0.020887 |
| 2 | adx_14_4h | 0.005289 |
| 3 | rsi_14_1h | 0.003132 |
| 4 | sma_distance_20_1h | 0.003100 |
| 5 | distance_recent_high_20_15m | 0.002942 |
| 6 | ema_distance_20_1h | 0.002921 |
| 7 | high_low_range_15m | 0.002411 |
| 8 | return_1_15m | 0.002165 |
| 9 | macd_fraction_1h | 0.002054 |
| 10 | distance_recent_high_20_4h | 0.002021 |
| 11 | volatility_20_1h | 0.001742 |
| 12 | high_low_range_1h | 0.001715 |
| 13 | adx_14_15m | 0.001656 |
| 14 | bollinger_position_20_1h | 0.001432 |
| 15 | atr_fraction_14_4h | 0.001341 |
| 16 | adx_14_1h | 0.001302 |
| 17 | macd_hist_fraction_4h | 0.001194 |
| 18 | trend_slope_20_4h | 0.001039 |
| 19 | macd_fraction_4h | 0.000980 |
| 20 | distance_recent_low_20_4h | 0.000713 |

## Artifacts and news compatibility

The model registry is `data/models/ml_v1_market/` and contains the joblib model,
metadata, feature list, and training configuration. Reports are under
`data/ml/reports/`: JSON, CSV, Markdown, walk-forward CSV, and feature importance
CSV. These generated data directories are ignored by git.

No `ML_WITH_NEWS` model was trained and no historical WITH_NEWS performance claim
is made. A valid version requires a real historical article archive containing
`published_at`, `first_seen_at`, source, reproducible symbol mapping, and frozen
sentiment/importance/event reconstruction rules. Inferred or fabricated article
times are not acceptable.

## Limitations

The 2023–2025 period has already been viewed elsewhere in this project, so the test
is isolated from this training implementation but is not a pristine project-wide
holdout. The model has weak discrimination, very low recall, negative test and
walk-forward expectancy, and no demonstrated regime robustness. The simulation
uses fixed horizon exits and simplified position overlap/capital rules rather than
the complete forward RiskManager/PaperBroker lifecycle. Reported baseline results
share this simulator, while Buy & Hold uses comparable 10% total exposure. The
model should remain offline until a later truly unseen period and forward PAPER
results demonstrate positive performance after costs.
