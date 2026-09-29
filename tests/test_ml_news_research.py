import numpy as np
import pandas as pd
import pytest

from app.ml.news_features import NEWS_FEATURES_V2
from app.ml.news_research import (align_market_subset, compare_walk_forward, feature_sets,
                                  paired_predictions, research_artifact_flags,
                                  research_status, segmented_analysis, validate_same_period)


def frame(rows=8):
    timestamps = pd.date_range("2025-01-01", periods=rows, freq="h", tz="UTC")
    return pd.DataFrame({"timestamp": timestamps, "target_timestamp": timestamps + pd.Timedelta(hours=4),
                         "symbol": ["BTCUSDT"] * rows, "label": [0, 1] * (rows // 2),
                         "future_net_return": np.linspace(-.01, .02, rows),
                         "split": ["test"] * rows, "eligible_news_count_recent": [0, 1] * (rows // 2),
                         "news_coverage_available": 1, "row_news_eligible_for_training": True})


def test_same_period_comparison_rejects_mismatch():
    left, right = frame(), frame()
    assert validate_same_period(left, right)
    right.loc[0, "label"] = 1
    with pytest.raises(ValueError, match="label"):
        validate_same_period(left, right)


def test_coverage_filtered_alignment_is_shared_subset():
    market = frame()
    subset = market.iloc[[1, 3, 5]].copy()
    aligned = align_market_subset(market, subset)
    assert aligned["timestamp"].tolist() == subset["timestamp"].tolist()


def test_feature_sets_preserve_market_and_separate_news():
    market = ["market_a", "market_b"]
    sets = feature_sets({"features": market}, {"features": market + NEWS_FEATURES_V2})
    assert sets["market_only"] == market
    assert set(sets["market_plus_full_news"]) - set(market) == set(NEWS_FEATURES_V2)
    assert len(sets["market_plus_counts_coverage"]) < len(sets["market_plus_full_news"])


def test_shared_row_pairing_and_delta_buckets():
    data = frame()
    paired = paired_predictions(data, np.full(len(data), .5), np.array([.5, .51, .53, .56, .61, .4, .48, .52]))
    assert len(paired) == len(data)
    assert set(paired["absolute_delta_bucket"]) <= {"<0.02", "0.02-0.05", "0.05-0.10", ">=0.10"}
    assert {"increased", "decreased", "almost_nothing"} <= set(paired["direction"])


def test_news_active_segmentation_keeps_active_and_inactive_separate():
    data = frame(40)
    market = np.linspace(.3, .7, len(data)); news = market + .01
    rows = segmented_analysis(data, market, news, .5, .5)
    assert {(row["segment"], row["variant"]) for row in rows} == {
        (segment, variant) for segment in ("news_active", "news_inactive_valid_coverage")
        for variant in ("MARKET_ONLY", "WITH_NEWS_FULL")}
    assert all(row["rows"] == 20 for row in rows)


def test_walk_forward_comparison_preserves_worse_folds():
    def fold(number, roc, net):
        return {"fold": number, "threshold": .6,
                "classification": {"roc_auc": roc, "pr_auc": roc - .1},
                "trading": {"net_return": net, "trade_count": 10, "profit_factor": 1,
                            "max_drawdown": .02}}
    compared = compare_walk_forward([fold(1, .6, .1), fold(2, .55, 0)],
                                    [fold(1, .5, -.1), fold(2, .56, .01)])
    assert compared[0]["news_roc_auc"] < compared[0]["market_roc_auc"]
    assert len(compared) == 2


def test_status_uses_test_walk_forward_and_coverage_criteria():
    market = {"test": {"classification": {"roc_auc": .5, "pr_auc": .4},
                        "trading": {"net_return": 0}}}
    news = {"test": {"classification": {"roc_auc": .6, "pr_auc": .5},
                      "trading": {"net_return": .1}}}
    walk = [{"news_roc_auc": .6, "market_roc_auc": .5, "news_pr_auc": .5, "market_pr_auc": .4}] * 2
    status, criteria = research_status(market, news, walk, 600)
    assert status == "PROMISING_RESEARCH_SIGNAL" and criteria["minimum_active_test_rows_met"]


def test_research_artifact_cannot_claim_batch3_control():
    flags = research_artifact_flags()
    assert flags == {"research_only": True, "news_archive_readiness": "PARTIAL",
                     "not_for_batch3_control": True}
