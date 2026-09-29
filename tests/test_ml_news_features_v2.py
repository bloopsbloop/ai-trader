import math

import pandas as pd
import pytest

from app.ml.news_features import (NEWS_FEATURES_V2, NEWS_FEATURE_VERSION, NewsFeatureConfig,
                                  aggregate_archive_news, feature_metadata)

T = pd.Timestamp("2024-06-10T12:00:00Z")


def article(article_id, available, symbols=("BTC",), market=False, sentiment=.5,
            event="upgrade", published=None, updated=None, basis="authoritative_publication"):
    return {"article_id": article_id, "source": "official", "title": article_id,
            "published_at": published or available, "updated_at": updated,
            "availability_timestamp": available, "availability_basis": basis,
            "availability_confidence": "high", "symbols": list(symbols),
            "market_wide": market, "event_type": event, "sentiment": sentiment,
            "importance": .8, "confidence": .9, "mapping_version": "entity-rules-v2",
            "provenance_version": "news-provenance-v2", "ml_eligible": True}


def articles(*rows):
    frame = pd.DataFrame(rows)
    frame["availability_timestamp"] = pd.to_datetime(frame["availability_timestamp"], utc=True)
    return frame


def intervals(symbol="BTC", start=T - pd.Timedelta(days=10), end=T + pd.Timedelta(days=10)):
    return pd.DataFrame([{"source": "official", "query_hash": "q", "window_start": start,
                          "window_end": end, "symbols": [symbol],
                          "availability_guaranteed": True, "provenance_version": "news-provenance-v2"}])


def samples(symbol="BTCUSDT", times=(T,)):
    return pd.DataFrame({"timestamp": list(times), "symbol": [symbol] * len(times)})


def test_only_availability_timestamp_controls_inclusion_and_t_is_inclusive():
    frame = articles(
        article("future", T + pd.Timedelta(seconds=1), published=T - pd.Timedelta(days=2)),
        article("exact", T, published=T - pd.Timedelta(days=1)))
    row = aggregate_archive_news(samples(), frame, intervals()).iloc[0]
    assert row.news_count_1h == 1
    assert row.authoritative_event_count_24h == 1


def test_future_update_is_not_a_feature_time_and_published_does_not_bypass_availability():
    baseline = articles(article("a", T - pd.Timedelta(minutes=30), updated=T))
    future_edit = baseline.copy(); future_edit["updated_at"] = T + pd.Timedelta(days=100)
    first = aggregate_archive_news(samples(), baseline, intervals())
    second = aggregate_archive_news(samples(), future_edit, intervals())
    pd.testing.assert_series_equal(first[NEWS_FEATURES_V2].iloc[0], second[NEWS_FEATURES_V2].iloc[0])


def test_window_left_boundary_is_exclusive():
    frame = articles(article("boundary", T - pd.Timedelta(hours=1)))
    row = aggregate_archive_news(samples(), frame, intervals()).iloc[0]
    assert row.news_count_1h == 0 and row.news_count_4h == 1


def test_freshness_decay_matches_documented_formula():
    frame = articles(article("new", T, sentiment=1),
                     article("old", T - pd.Timedelta(hours=6), sentiment=-1))
    row = aggregate_archive_news(samples(), frame, intervals(),
                                 NewsFeatureConfig(decay_half_life_hours=6)).iloc[0]
    expected = (1 - math.exp(-1)) / (1 + math.exp(-1))
    assert row.freshness_weighted_sentiment_12h == pytest.approx(expected)


def test_market_wide_reaches_all_symbols_but_specific_does_not_leak():
    frame = articles(article("btc", T, symbols=("BTC",)),
                     article("macro", T, symbols=(), market=True, event="macro"))
    eth = aggregate_archive_news(samples("ETHUSDT"), frame, intervals("ETH")).iloc[0]
    btc = aggregate_archive_news(samples(), frame, intervals()).iloc[0]
    assert eth.news_count_1h == 1 and eth.market_wide_news_count_1h == 1
    assert btc.news_count_1h == 2 and btc.symbol_news_count_1h == 1
    assert eth.event_macro_count_24h == 1


def test_zero_news_with_coverage_differs_from_missing_coverage():
    covered = aggregate_archive_news(samples(), pd.DataFrame(), intervals()).iloc[0]
    missing = aggregate_archive_news(samples(), pd.DataFrame(), pd.DataFrame()).iloc[0]
    assert covered.news_count_24h == missing.news_count_24h == 0
    assert covered.news_coverage_available == 1
    assert missing.news_coverage_available == 0
    assert covered.row_news_eligible_for_training
    assert not missing.row_news_eligible_for_training


def test_coverage_gating_reasons_are_explicit():
    short = intervals(start=T - pd.Timedelta(hours=1))
    row = aggregate_archive_news(samples(), pd.DataFrame(), short).iloc[0]
    assert not row.row_news_eligible_for_training
    assert "insufficient_coverage_days" in row.row_news_exclusion_reason


def test_feature_contract_is_versioned_and_compact():
    metadata = feature_metadata()
    assert metadata["feature_version"] == NEWS_FEATURE_VERSION == "news-feature-v1"
    assert metadata["availability_column"] == "availability_timestamp"
    assert metadata["window_definitions"] == "(T-window,T]"
    assert metadata["feature_count"] == len(NEWS_FEATURES_V2) == 100
