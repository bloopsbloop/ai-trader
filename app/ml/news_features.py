"""Historical news aggregation. Empty coverage stays explicitly empty/zero."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd

NEWS_FEATURES = ["news_count_recent", "positive_news_count", "negative_news_count",
                 "mean_sentiment", "max_importance", "freshness_weighted_score",
                 "max_confidence", "symbol_specific_news_count", "market_wide_news_count",
                 "event_listing", "event_delisting", "event_security", "event_regulation",
                 "event_adoption", "event_upgrade", "event_unlock", "event_other"]


def load_news_sqlite(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "ai_news" not in tables:
            return pd.DataFrame()
        articles = [json.loads(row[0]) for row in connection.execute("SELECT payload FROM ai_news")]
        scans = [json.loads(row[0]) for row in connection.execute("SELECT payload FROM ai_scans")]
    finally:
        connection.close()
    scored = {}
    for scan in scans:
        for score in scan.get("news_scores", []):
            scored[score["article_id"]] = score
    rows = []
    for article in articles:
        from app.ai.news import Article
        article_id = Article(**article).id
        score = scored.get(article_id)
        if score:
            rows.append({**article, **score})
    return pd.DataFrame(rows)


def aggregate_news(samples: pd.DataFrame, news: pd.DataFrame, window_hours=24) -> pd.DataFrame:
    """Aggregate only news published and first observed no later than sample T."""
    output = pd.DataFrame(0.0, index=samples.index, columns=NEWS_FEATURES)
    if news.empty:
        return output
    items = news.copy()
    items["published_at"] = pd.to_datetime(items["published_at"], utc=True)
    items["first_seen_at"] = pd.to_datetime(items["first_seen_at"], utc=True)
    window = pd.Timedelta(hours=window_hours)
    earliest, latest = pd.to_datetime(samples["timestamp"], utc=True).agg(["min", "max"])
    items = items[(items["published_at"] <= latest) & (items["published_at"] > earliest - window)
                  & (items["first_seen_at"] <= latest)]
    if items.empty:
        return output
    for index, sample in samples[["timestamp", "symbol"]].iterrows():
        timestamp = pd.Timestamp(sample["timestamp"])
        eligible = items[(items["published_at"] <= timestamp) & (items["first_seen_at"] <= timestamp)
                         & (items["published_at"] > timestamp - window)]
        if eligible.empty:
            continue
        symbols = eligible["symbols"].apply(lambda value: value if isinstance(value, list) else [])
        specific = eligible[symbols.apply(lambda values: sample["symbol"] in values)]
        market = eligible[symbols.apply(len) == 0]
        relevant = pd.concat([specific, market]).drop_duplicates(subset="article_id")
        if relevant.empty:
            continue
        sentiment = pd.to_numeric(relevant["sentiment"], errors="coerce").fillna(0)
        ages = (timestamp - relevant["published_at"]).dt.total_seconds().clip(lower=0)
        freshness = np.exp(-ages / 21600)
        output.loc[index, "news_count_recent"] = len(relevant)
        output.loc[index, "positive_news_count"] = int((sentiment > 0).sum())
        output.loc[index, "negative_news_count"] = int((sentiment < 0).sum())
        output.loc[index, "mean_sentiment"] = sentiment.mean()
        output.loc[index, "max_importance"] = pd.to_numeric(relevant["importance"], errors="coerce").max()
        output.loc[index, "freshness_weighted_score"] = (sentiment * freshness).sum() / max(1, freshness.sum())
        output.loc[index, "max_confidence"] = pd.to_numeric(relevant["confidence"], errors="coerce").max()
        output.loc[index, "symbol_specific_news_count"] = len(specific)
        output.loc[index, "market_wide_news_count"] = len(market)
        for event in ("listing", "delisting", "security", "regulation", "adoption", "upgrade", "unlock", "other"):
            output.loc[index, f"event_{event}"] = int((relevant["event_type"] == event).any())
    return output.fillna(0.0)
