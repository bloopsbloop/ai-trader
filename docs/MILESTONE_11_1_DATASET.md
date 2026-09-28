# Milestone 11.1 — ML dataset and feature pipeline

Implemented locally in `C:\Users\ostap\PycharmProjects\Al_Trader`. This milestone
does not train or activate a model. Batch 3 continues using its existing scoring.

## Run

```powershell
# Build comparable WITH_NEWS and WITHOUT_NEWS datasets (default).
.\.venv\Scripts\python.exe -B -m app.ml.dataset

# One variant or a custom local cache/archive.
.\.venv\Scripts\python.exe -B -m app.ml.dataset --with-news
.\.venv\Scripts\python.exe -B -m app.ml.dataset --without-news --symbol BTCUSDT --timeframe 1h
.\.venv\Scripts\python.exe -B -m app.ml.dataset --history-dir data/history --news-db data/ai/batch3.sqlite3
```

Outputs are gzip CSV plus JSON metadata in `data/ml/datasets/`. Data is ignored by
git. The metadata stores configuration, exact feature schema, costs, target,
periods, row/class counts, three walk-forward folds, news coverage limitations,
and train-only preprocessing state.

## Dataset contract

One row represents a closed base-timeframe candle. The default base timeframe is
1h. 15m and 4h features are backward as-of joined by their close timestamps; every
saved `source_timestamp_*` is less than or equal to the row timestamp. Indicators
restart after real candle gaps, so rolling windows never bridge missing market data.
Duplicate timestamps fail the build. Warm-up and incomplete rows are excluded.

The target is binary, not an exact price prediction:

```text
future_net_return = close[T+4] / close[T] - 1
                    - 2 * (0.0004 commission + 0.0005 slippage)
label = 1 when future_net_return > 0.001 minimum edge
```

For the default 1h dataset, the horizon is four candles / four hours. A positive
label therefore requires gross movement above 0.28% (0.18% estimated round-trip
cost plus 0.10% meaningful edge).

Splits use distinct timestamps globally across all symbols: 60% train, 20%
validation and 20% test. Rows whose future target crosses a split boundary are
purged. The preprocessing helper can only fit rows marked `train`; it drops
NaN-heavy, constant and exact duplicate features, records train medians, applies
them to later periods, and does no scaling.

## Local build result

- Symbols: BTCUSDT, ETHUSDT, SOLUSDT.
- Feature timeframes: 15m, 1h, 4h; base/target timeframe: 1h.
- Candle period requested: 2023-01-01 through 2025-12-31.
- Rows: 78,617 in each variant.
- Labels overall: 49,898 negative (63.47%), 28,719 positive (36.53%).
- WITHOUT_NEWS: 61 exported market features; 60 usable after train-only duplicate
  detection (`momentum_4_15m` duplicates the one-hour return at 1h sample times).
- WITH_NEWS: 78 exported features (61 market + 17 news); 60 currently usable.
- Train: 2023-01-01 09:00 UTC – 2024-10-19 08:00 UTC, 47,069 rows;
  30,356 negative / 16,713 positive.
- Validation: 2024-10-19 13:00 UTC – 2025-05-26 12:00 UTC, 15,768 rows;
  9,458 negative / 6,310 positive.
- Test: 2025-05-26 17:00 UTC – 2025-12-31 20:00 UTC, 15,780 rows;
  10,084 negative / 5,696 positive.

The local Batch 3 news database contains current forward observations, but none
overlap 2023–2025. WITH_NEWS columns therefore record zero observed historical
events and are marked as unavailable coverage; no news was fabricated. They are
constant and the train-only filter removes them from a model-ready matrix. A fair
WITH_NEWS versus WITHOUT_NEWS training comparison requires a real timestamped
historical news archive.

The 2023–2025 market period was previously viewed by project research. The test
partition is untouched by this pipeline's train/preprocessing logic, but it is not
a pristine project-wide holdout. A later truly unseen period is required before
making out-of-sample performance claims.

## Validation

`python -m pytest -q -p no:cacheprovider`: **112 passed**, with three existing
Binance/websockets deprecation warnings. Eleven dataset tests cover feature/label
timing, backward multi-timeframe joins, gap handling, duplicate rejection,
chronological purging, walk-forward splits, publication and first-seen news time,
variant parity, metadata and train-only preprocessing.

No dependency was added: the pipeline uses the project's existing pandas/numpy.
No Oracle access, server file, systemd/nginx configuration, `.env`, live Binance
endpoint, Batch 1/2 code, Batch 3 trading state or git history was changed.
