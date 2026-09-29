"""Leakage-safe historical news features based only on availability_timestamp."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd

from app.news.normalize import EVENT_TYPES, MAPPING_VERSION

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARCHIVE = PROJECT_ROOT / "data" / "news" / "archive.sqlite3"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "ml" / "news_features"
NEWS_FEATURE_VERSION = "news-feature-v1"
WINDOW_HOURS = (1, 4, 12, 24, 72)
WINDOW_METRICS = ("news_count", "symbol_news_count", "market_wide_news_count",
                  "positive_count", "negative_count", "neutral_count", "mean_sentiment",
                  "max_positive_sentiment", "min_negative_sentiment", "mean_importance",
                  "max_importance", "mean_confidence", "max_confidence",
                  "freshness_weighted_sentiment", "freshness_weighted_importance")
EVENT_FEATURES = tuple(f"event_{event}_count_24h" for event in EVENT_TYPES)
PROVENANCE_FEATURES = ("authoritative_event_count_24h", "public_post_count_24h",
                       "high_confidence_news_count_24h", "source_diversity_count_24h")
COVERAGE_FEATURES = ("news_coverage_available", "eligible_news_count_recent",
                     "source_count_recent", "coverage_days_recent", "news_data_quality_score")
NEWS_FEATURES_V2 = [f"{metric}_{hours}h" for hours in WINDOW_HOURS for metric in WINDOW_METRICS]
NEWS_FEATURES_V2 += list(EVENT_FEATURES + PROVENANCE_FEATURES + COVERAGE_FEATURES)

# M11.1 compatibility API. WITH_NEWS_V2 uses NEWS_FEATURES_V2.
NEWS_FEATURES = ["news_count_recent", "positive_news_count", "negative_news_count",
                 "mean_sentiment", "max_importance", "freshness_weighted_score",
                 "max_confidence", "symbol_specific_news_count", "market_wide_news_count",
                 "event_listing", "event_delisting", "event_security", "event_regulation",
                 "event_adoption", "event_upgrade", "event_unlock", "event_other"]


@dataclass(frozen=True)
class NewsFeatureConfig:
    windows_hours: tuple[int, ...] = WINDOW_HOURS
    decay_half_life_hours: float = 6.0
    coverage_lookback_hours: int = 72
    minimum_coverage_sources: int = 1
    minimum_coverage_days: float = 2.0
    feature_version: str = NEWS_FEATURE_VERSION
    taxonomy_version: str = "event-taxonomy-v1"
    mapping_version: str = MAPPING_VERSION
    provenance_version: str = "news-provenance-v2"


def load_archive(path: Path):
    """Load normalized articles and explicit source coverage intervals read-only."""
    if not Path(path).exists():
        return pd.DataFrame(), pd.DataFrame()
    connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "news_articles" not in tables:
            return pd.DataFrame(), pd.DataFrame()
        articles = pd.read_sql_query("""
            SELECT id AS article_id,source,title,published_at,updated_at,availability_timestamp,
                   availability_basis,availability_confidence,symbols_json,market_wide,event_type,
                   sentiment,importance,confidence,mapping_version,provenance_version,ml_eligible
            FROM news_articles
        """, connection)
        intervals = (pd.read_sql_query("SELECT * FROM news_coverage_intervals", connection)
                     if "news_coverage_intervals" in tables else pd.DataFrame())
    finally:
        connection.close()
    if not articles.empty:
        articles["symbols"] = articles.pop("symbols_json").apply(json.loads)
        articles["availability_timestamp"] = pd.to_datetime(
            articles["availability_timestamp"], utc=True, format="mixed")
        articles["ml_eligible"] = articles["ml_eligible"].astype(bool)
        articles["market_wide"] = articles["market_wide"].astype(bool)
    if not intervals.empty:
        intervals["symbols"] = intervals.pop("symbols_json").apply(json.loads)
        intervals["window_start"] = pd.to_datetime(intervals["window_start"], utc=True, format="mixed")
        intervals["window_end"] = pd.to_datetime(intervals["window_end"], utc=True, format="mixed")
        intervals["availability_guaranteed"] = intervals["availability_guaranteed"].astype(bool)
    return articles, intervals


def _symbol(value):
    value = str(value).upper()
    return value[:-4] if value.endswith("USDT") else value


def _empty(samples):
    result = pd.DataFrame(0.0, index=samples.index, columns=NEWS_FEATURES_V2)
    result["row_news_eligible_for_training"] = False
    result["row_news_exclusion_reason"] = "no_trusted_archive_coverage"
    return result


def aggregate_archive_news(samples: pd.DataFrame, articles: pd.DataFrame,
                           coverage_intervals: pd.DataFrame, config=None):
    """Use the half-open rolling interval (T-window, T], never another timestamp."""
    config = config or NewsFeatureConfig()
    result = _empty(samples)
    if samples.empty:
        return result
    sample_times = pd.to_datetime(samples["timestamp"], utc=True)
    eligible = articles.copy()
    if not eligible.empty:
        eligible = eligible[eligible["ml_eligible"] & eligible["availability_timestamp"].notna()].copy()
        eligible = eligible.sort_values("availability_timestamp")
    symbols = {_symbol(value) for value in samples["symbol"].unique()}
    relevant_by_symbol = {}
    for symbol in symbols:
        if eligible.empty:
            relevant = eligible
        else:
            relevant = eligible[eligible.apply(
                lambda row: bool(row["market_wide"] or symbol in row["symbols"]), axis=1)].copy()
        relevant_by_symbol[symbol] = relevant.reset_index(drop=True)
    guaranteed = coverage_intervals.copy()
    if not guaranteed.empty:
        guaranteed = guaranteed[guaranteed["availability_guaranteed"]].copy()
    interval_by_symbol = {symbol: ([] if guaranteed.empty else [
        row for row in guaranteed.itertuples(index=False) if symbol in row.symbols]) for symbol in symbols}

    # Coverage is interval data, so calculate it vectorially rather than once per
    # article/sample pair. Completed empty windows still represent valid coverage.
    for symbol in symbols:
        group_index = samples.index[samples["symbol"].map(_symbol) == symbol]
        times = sample_times.loc[group_index]
        available = np.zeros(len(group_index), dtype=bool)
        coverage_days = np.zeros(len(group_index), dtype=float)
        source_masks = {}
        lookback = pd.Timedelta(hours=config.coverage_lookback_hours)
        for interval in interval_by_symbol[symbol]:
            covering = (times >= interval.window_start) & (times < interval.window_end)
            overlap = (times > interval.window_start) & (times - lookback < interval.window_end)
            available |= covering.to_numpy()
            source_masks.setdefault(interval.source, np.zeros(len(group_index), dtype=bool))
            source_masks[interval.source] |= overlap.to_numpy()
            overlap_start = pd.Series(
                np.maximum((times - lookback).to_numpy(dtype="datetime64[ns]"),
                           np.datetime64(interval.window_start.to_datetime64())), index=group_index)
            overlap_end = pd.Series(
                np.minimum(times.to_numpy(dtype="datetime64[ns]"),
                           np.datetime64(interval.window_end.to_datetime64())), index=group_index)
            duration = ((overlap_end - overlap_start).dt.total_seconds() / 86400).clip(lower=0)
            coverage_days = np.maximum(coverage_days, duration.to_numpy())
        source_count = (sum(mask.astype(int) for mask in source_masks.values())
                        if source_masks else np.zeros(len(group_index), dtype=int))
        quality = np.mean(np.vstack([
            available.astype(float),
            np.minimum(1, source_count / max(1, config.minimum_coverage_sources)),
            np.minimum(1, coverage_days / max(.001, config.minimum_coverage_days))]), axis=0)
        accepted = (available & (source_count >= config.minimum_coverage_sources) &
                    (coverage_days >= config.minimum_coverage_days))
        reasons = []
        for is_available, sources_count, days in zip(available, source_count, coverage_days):
            row_reasons = []
            if not is_available: row_reasons.append("no_trusted_archive_coverage")
            if sources_count < config.minimum_coverage_sources: row_reasons.append("insufficient_source_diversity")
            if days < config.minimum_coverage_days: row_reasons.append("insufficient_coverage_days")
            reasons.append(";".join(row_reasons))
        result.loc[group_index, "news_coverage_available"] = available.astype(int)
        result.loc[group_index, "source_count_recent"] = source_count
        result.loc[group_index, "coverage_days_recent"] = coverage_days
        result.loc[group_index, "news_data_quality_score"] = quality
        result.loc[group_index, "row_news_eligible_for_training"] = accepted
        result.loc[group_index, "row_news_exclusion_reason"] = reasons

    for index, timestamp, raw_symbol in zip(samples.index, sample_times, samples["symbol"]):
        symbol = _symbol(raw_symbol)
        relevant = relevant_by_symbol[symbol]
        times_ns = (np.array([], dtype="datetime64[ns]") if relevant.empty else
                    relevant["availability_timestamp"].to_numpy(dtype="datetime64[ns]"))
        right = np.searchsorted(times_ns, timestamp.to_datetime64(), side="right")
        earliest = np.searchsorted(
            times_ns, (timestamp - pd.Timedelta(hours=max(config.windows_hours))).to_datetime64(), side="right")
        if earliest == right:
            continue
        slices = {}
        for hours in config.windows_hours:
            left = np.searchsorted(times_ns, (timestamp - pd.Timedelta(hours=hours)).to_datetime64(), side="right")
            window = relevant.iloc[left:right]
            slices[hours] = window
            if window.empty:
                continue
            sentiment = pd.to_numeric(window["sentiment"], errors="coerce").fillna(0.0)
            importance = pd.to_numeric(window["importance"], errors="coerce").fillna(0.0)
            confidence = pd.to_numeric(window["confidence"], errors="coerce").fillna(0.0)
            ages = (timestamp - window["availability_timestamp"]).dt.total_seconds() / 3600
            weights = np.exp(-ages.to_numpy() / config.decay_half_life_hours)
            name = lambda metric: f"{metric}_{hours}h"
            result.at[index, name("news_count")] = len(window)
            result.at[index, name("symbol_news_count")] = sum(symbol in values for values in window["symbols"])
            result.at[index, name("market_wide_news_count")] = int(window["market_wide"].sum())
            result.at[index, name("positive_count")] = int((sentiment > 0).sum())
            result.at[index, name("negative_count")] = int((sentiment < 0).sum())
            result.at[index, name("neutral_count")] = int((sentiment == 0).sum())
            result.at[index, name("mean_sentiment")] = sentiment.mean()
            result.at[index, name("max_positive_sentiment")] = sentiment[sentiment > 0].max() if (sentiment > 0).any() else 0
            result.at[index, name("min_negative_sentiment")] = sentiment[sentiment < 0].min() if (sentiment < 0).any() else 0
            result.at[index, name("mean_importance")] = importance.mean()
            result.at[index, name("max_importance")] = importance.max()
            result.at[index, name("mean_confidence")] = confidence.mean()
            result.at[index, name("max_confidence")] = confidence.max()
            result.at[index, name("freshness_weighted_sentiment")] = float(np.average(sentiment, weights=weights))
            result.at[index, name("freshness_weighted_importance")] = float(np.average(importance, weights=weights))
        day = slices.get(24, relevant.iloc[0:0])
        if not day.empty:
            for event in EVENT_TYPES:
                result.at[index, f"event_{event}_count_24h"] = int((day["event_type"] == event).sum())
            result.at[index, "authoritative_event_count_24h"] = int((day["availability_basis"] == "authoritative_publication").sum())
            result.at[index, "public_post_count_24h"] = int((day["availability_basis"] == "public_post_creation").sum())
            result.at[index, "high_confidence_news_count_24h"] = int(day["availability_confidence"].isin(("high", "exact")).sum())
            result.at[index, "source_diversity_count_24h"] = day["source"].nunique()

        recent = slices.get(config.coverage_lookback_hours, relevant.iloc[0:0])
        result.at[index, "eligible_news_count_recent"] = len(recent)
    return result


def coverage_statistics(frame):
    work = frame.copy()
    work["year"] = pd.to_datetime(work["timestamp"], utc=True).dt.year.astype(str)
    grouped = {}
    for keys in (("split",), ("symbol",), ("year",), ("split", "symbol")):
        if any(key not in work for key in keys): continue
        values = {}
        grouper = keys[0] if len(keys) == 1 else list(keys)
        for raw_name, group in work.groupby(grouper, observed=True):
            parts = raw_name if isinstance(raw_name, tuple) else (raw_name,)
            label = "|".join(map(str, parts))
            any_news = group["eligible_news_count_recent"] > 0
            accepted = group["row_news_eligible_for_training"].astype(bool)
            values[label] = {"rows": len(group), "rows_with_any_eligible_news": int(any_news.sum()),
                             "rows_no_news_valid_coverage": int((~any_news & accepted).sum()),
                             "rows_insufficient_coverage": int((~accepted).sum()),
                             "rows_excluded_by_gating": int((~accepted).sum()),
                             "average_eligible_articles": float(group["eligible_news_count_recent"].mean())}
        grouped["_by_".join(keys)] = values
    return grouped


def feature_metadata(config=None):
    config = config or NewsFeatureConfig()
    return {"feature_version": config.feature_version, "feature_count": len(NEWS_FEATURES_V2),
            "features": NEWS_FEATURES_V2, "window_definitions": "(T-window,T]",
            "parameters": asdict(config), "availability_column": "availability_timestamp",
            "forbidden_time_columns": ["published_at", "collected_at", "archive_observed_at"],
            "event_taxonomy": list(EVENT_TYPES)}


def export_feature_reports(frame, output=DEFAULT_OUTPUT, config=None):
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    metadata, stats = feature_metadata(config), coverage_statistics(frame)
    (output / "feature_schema.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    (output / "coverage_summary.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    total = len(frame); usable = int(frame["row_news_eligible_for_training"].sum())
    any_news = int((frame["eligible_news_count_recent"] > 0).sum())
    lines = ["# News feature coverage", "", f"- Version: {NEWS_FEATURE_VERSION}",
             f"- Features: {len(NEWS_FEATURES_V2)}", f"- Samples: {total}",
             f"- Coverage-gated rows: {usable}", f"- Rows with eligible news: {any_news}"]
    (output / "coverage_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    frame[frame["eligible_news_count_recent"] > 0].head(100).to_csv(
        output / "sample_features.csv", index=False)
    return {"feature_schema": str(output / "feature_schema.json"),
            "coverage_summary": str(output / "coverage_summary.json"),
            "sample_features": str(output / "sample_features.csv")}


def aggregate_news(samples: pd.DataFrame, news: pd.DataFrame, window_hours=24) -> pd.DataFrame:
    """Compatibility aggregator for the earlier Batch 3 scored-news format."""
    output = pd.DataFrame(0.0, index=samples.index, columns=NEWS_FEATURES)
    if news.empty: return output
    items = news.copy(); items["published_at"] = pd.to_datetime(items["published_at"], utc=True)
    items["first_seen_at"] = pd.to_datetime(items["first_seen_at"], utc=True)
    window = pd.Timedelta(hours=window_hours)
    for index, sample in samples[["timestamp", "symbol"]].iterrows():
        timestamp = pd.Timestamp(sample["timestamp"])
        eligible = items[(items["published_at"] <= timestamp) & (items["first_seen_at"] <= timestamp)
                         & (items["published_at"] > timestamp - window)]
        if eligible.empty: continue
        symbols = eligible["symbols"].apply(lambda value: value if isinstance(value, list) else [])
        specific = eligible[symbols.apply(lambda values: sample["symbol"] in values)]
        market = eligible[symbols.apply(len) == 0]
        relevant = pd.concat([specific, market]).drop_duplicates(subset="article_id")
        if relevant.empty: continue
        sentiment = pd.to_numeric(relevant["sentiment"], errors="coerce").fillna(0)
        ages = (timestamp - relevant["published_at"]).dt.total_seconds().clip(lower=0)
        freshness = np.exp(-ages / 21600)
        output.loc[index, ["news_count_recent", "positive_news_count", "negative_news_count"]] = [len(relevant), int((sentiment > 0).sum()), int((sentiment < 0).sum())]
        output.loc[index, "mean_sentiment"] = sentiment.mean(); output.loc[index, "max_importance"] = pd.to_numeric(relevant["importance"], errors="coerce").max()
        output.loc[index, "freshness_weighted_score"] = (sentiment * freshness).sum() / max(1, freshness.sum())
        output.loc[index, "max_confidence"] = pd.to_numeric(relevant["confidence"], errors="coerce").max()
        output.loc[index, "symbol_specific_news_count"] = len(specific); output.loc[index, "market_wide_news_count"] = len(market)
        for event in ("listing", "delisting", "security", "regulation", "adoption", "upgrade", "unlock", "other"):
            output.loc[index, f"event_{event}"] = int((relevant["event_type"] == event).any())
    return output.fillna(0.0)


def load_news_sqlite(path: Path) -> pd.DataFrame:
    if not path.exists(): return pd.DataFrame()
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "ai_news" not in tables: return pd.DataFrame()
        articles = [json.loads(row[0]) for row in connection.execute("SELECT payload FROM ai_news")]
        scans = [json.loads(row[0]) for row in connection.execute("SELECT payload FROM ai_scans")]
    finally: connection.close()
    scored = {score["article_id"]: score for scan in scans for score in scan.get("news_scores", [])}
    rows = []
    from app.ai.news import Article
    for article in articles:
        score = scored.get(Article(**article).id)
        if score: rows.append({**article, **score})
    return pd.DataFrame(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build leakage-safe historical news feature samples")
    parser.add_argument("--db", default=str(DEFAULT_ARCHIVE)); parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--start", default="2023-01-01"); parser.add_argument("--end", default="2025-12-31T23:00:00Z")
    parser.add_argument("--symbol", action="append", dest="symbols"); parser.add_argument("--half-life", type=float, default=6)
    args = parser.parse_args(argv)
    config = NewsFeatureConfig(decay_half_life_hours=args.half_life)
    articles, intervals = load_archive(Path(args.db))
    times = pd.date_range(args.start, args.end, freq="h", tz="UTC")
    samples = pd.DataFrame([(stamp, symbol) for stamp in times for symbol in (args.symbols or ["BTCUSDT", "ETHUSDT", "SOLUSDT"])], columns=["timestamp", "symbol"])
    features = aggregate_archive_news(samples, articles, intervals, config)
    frame = pd.concat([samples, features], axis=1); frame["split"] = "unsplit"
    files = export_feature_reports(frame, args.output, config)
    print(json.dumps({"samples": len(frame), "feature_count": len(NEWS_FEATURES_V2),
                      "output": str(args.output), "files": files}, indent=2))


if __name__ == "__main__": main()
