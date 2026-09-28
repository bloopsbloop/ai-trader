from dataclasses import asdict
from app.ai.news import Assessment, freshness, resolve_symbols


def assess_news(articles, symbols, cutoff, scorer):
    result, errors = [], []
    for article in articles:
        if freshness(article, cutoff) == 0:
            continue
        linked = resolve_symbols(article.title, symbols)
        if not linked:
            continue
        try:
            scored = scorer.score(article, cutoff)
            # Validate again at the boundary, even for third-party adapters.
            payload = asdict(scored) if isinstance(scored, Assessment) else dict(scored)
            payload["freshness"] = freshness(article, cutoff)
            assessment = Assessment(**payload)
            result.append({"article_id": article.id, "symbols": linked, **asdict(assessment)})
        except (ValueError, TypeError, KeyError) as exc:
            errors.append({"article_id": article.id, "error": type(exc).__name__})
    return result, errors


def rank(feature_rows, news, eligible, use_news=True):
    result = []
    for symbol, frames in feature_rows.items():
        trend = sum(1 if frames[tf]["trend"] > 0 else 0 for tf in ("15m", "1h", "4h")) / 3
        momentum = max(0, min(1, 0.5 + frames["1h"]["momentum"] * 10))
        volume = min(1, frames["15m"]["volume_ratio"] / 2)
        market = 0.5 * trend + 0.3 * momentum + 0.2 * volume
        related = [item for item in news if symbol in item["symbols"]]
        # Bounded weighted mean prevents repeated stories from adding unlimited strength.
        weights = [item["importance"] * item["confidence"] * item["freshness"] for item in related]
        impact = sum(item["sentiment"] * weight for item, weight in zip(related, weights)) / max(1, sum(weights))
        score = max(0, min(1, market + (0.2 * impact if use_news else 0)))
        confirmed = trend == 1 and frames["15m"]["volume_ratio"] >= 1.1
        unsafe = frames["15m"]["atr_fraction"] > 0.05 or abs(frames["15m"]["return"]) > 0.08
        negative = use_news and impact < -0.25
        decision = "BUY" if score >= 0.75 and confirmed and not unsafe and not negative and symbol in eligible else ("WATCH" if score >= 0.5 and not unsafe and symbol in eligible else "PASS")
        result.append({"symbol": symbol, "score": score, "market_score": market, "news_impact": impact,
                       "decision": decision, "features": frames, "scorer_version": "opportunity-v1",
                       "reason": "market confirmation" if decision == "BUY" else "threshold or safety filter",
                       "news_ids": [item["article_id"] for item in related]})
    return sorted(result, key=lambda row: (-row["score"], row["symbol"]))
