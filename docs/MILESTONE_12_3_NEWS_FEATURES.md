# Milestone 12.3 — Leakage-Safe Historical News Features

## Scope

This milestone builds feature datasets only. It does not train `ML_WITH_NEWS`,
retrain or modify `ML_V1_MARKET`, change `ml_v1_market_forward_001`, or connect
features to Batch 3 trading.

## Time contract

Every feature uses only `availability_timestamp`. At sample time `T`, the
rolling interval is `(T-window, T]`: an article exactly at `T` is included, and
an article exactly at the left boundary is excluded. `published_at`,
`collected_at`, `archive_observed_at`, and future `updated_at` values never
control feature inclusion.

The contract is versioned as `news-feature-v1` and records:

- windows: 1h, 4h, 12h, 24h, 72h;
- freshness parameter: 6 hours;
- event taxonomy: `event-taxonomy-v1`;
- mapping: `entity-rules-v2`;
- provenance: `news-provenance-v2`;
- coverage lookback and gating thresholds.

## Features

There are 100 news features. Each rolling window contains counts split by
symbol/market scope and sentiment, sentiment/importance/confidence aggregates,
and freshness-weighted sentiment and importance. Event-type counts use the 24h
window. Compact 24h provenance features count authoritative events, public posts,
high-confidence records, and source diversity.

Freshness is deterministic:

```text
weight = exp(-age_hours / 6)
```

No decay parameter was selected using validation or test results.

## Coverage and gating

Completed authoritative backfill windows are persisted in
`news_coverage_intervals`. This makes these cases distinct:

```text
news_count = 0, news_coverage_available = 1  # source checked; no event
news_count = 0, news_coverage_available = 0  # archive coverage unknown
```

Coverage features are:

- `news_coverage_available`;
- `eligible_news_count_recent`;
- `source_count_recent`;
- `coverage_days_recent`;
- `news_data_quality_score`.

The default row gate requires an authoritative interval covering `T`, at least
one recent source, and at least two covered days in the 72h lookback. It does not
require an article, so genuine zero-news rows remain usable. The decision is
stored in `row_news_eligible_for_training` with an explicit exclusion reason.

## Dataset views

The existing market target, market features, symbols, horizon, and chronological
train/validation/test split are unchanged.

- `FULL_WITH_NEWS_FEATURES`: all 78,617 market rows plus coverage indicators.
- `COVERAGE_FILTERED_WITH_NEWS`: 78,401 rows passing the coverage gate.
- `WITHOUT_NEWS`: existing market-only contract and output name remain unchanged.

The full dataset contains 160 model features: 60 market features and 100 news
features. It has 47,069 train, 15,768 validation, and 15,780 test rows. The
filtered view has 46,922 train, 15,699 validation, and 15,780 test rows.

Rows by symbol in the full view are BTC 26,248, ETH 26,248, and SOL 26,121. The
filtered view contains BTC 26,163, ETH 26,163, and SOL 26,075 rows.

Across the full view, 11,046 rows have one or more eligible articles in the 72h
window and 216 fail coverage gating. Average contributing articles per row are
0.069 for BTC, 0.176 for ETH, and 0.306 for SOL. This sparse and source-limited
coverage remains a material research limitation.

## Commands and outputs

```powershell
python -m app.ml.news_features
python -m app.ml.dataset --with-news --timeframe 1h --start 2023-01-01 --end 2025-12-31T23:59:59 --news-db data/news/archive.sqlite3
```

Feature reports are written to `data/ml/news_features/`:

- `feature_schema.json`;
- `coverage_summary.json`;
- `coverage_summary.md`;
- `sample_features.csv`;
- `sample_event_validation.json`.

Datasets are written to `data/ml/datasets/` as compressed CSV plus metadata:

- `ml_v1_1h_with_news_v2_full`;
- `ml_v1_1h_with_news_v2_coverage_filtered`.

## Manual event check

The known `BTC Bitcoin Core 23.2` release has availability time
2023-05-18 15:11:14 UTC. At the 14:00 sample the 72h count is zero. At the first
following sample, 16:00, the feature is present. At 2023-05-21 17:00 it has
expired from the 72h window. The exact-at-T and left-boundary behavior is also
covered by deterministic tests.

The optimized full local build took approximately 98 seconds. Articles are
sorted once per symbol and joined with timestamp binary searches; source
coverage is calculated vectorially. The implementation does not scan every
archived article for every sample.
