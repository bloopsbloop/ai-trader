"""Deterministic news normalization; all scoring is explicitly heuristic."""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import re

from .models import (NormalizedArticle, RawNewsItem, canonical_url, digest,
                     normalized_text, utc_iso)

SUPPORTED_SYMBOLS = ("BTC", "ETH", "SOL")
ALIASES = {"BTC": ("bitcoin",), "ETH": ("ethereum", "ether"), "SOL": ("solana",)}
EVENT_TYPES = ("regulation", "listing", "delisting", "hack", "exploit", "partnership",
               "upgrade", "ETF", "macro", "exchange", "legal", "tokenomics", "whale",
               "adoption", "outage", "other")

EVENT_RULES = (
    ("delisting", r"\bdelist(?:s|ed|ing)?\b", -.9, .95),
    ("listing", r"\b(?:listing|lists|listed)\b", .6, .7),
    ("hack", r"\b(?:hack|hacked|stolen|breach)\b", -.9, .95),
    ("exploit", r"\b(?:exploit|vulnerability)\b", -.85, .9),
    ("outage", r"\b(?:outage|downtime|halted)\b", -.6, .75),
    ("ETF", r"\bETF\b", .25, .8),
    ("regulation", r"\b(?:regulator|regulation|SEC|CFTC|MiCA)\b", -.05, .8),
    ("legal", r"\b(?:lawsuit|court|legal|settlement|charged)\b", -.25, .7),
    ("partnership", r"\b(?:partnership|partners? with|collaboration)\b", .5, .6),
    ("upgrade", r"\b(?:upgrade|mainnet|fork)\b", .3, .55),
    ("tokenomics", r"\b(?:unlock|burn|issuance|tokenomics)\b", -.1, .6),
    ("whale", r"\b(?:whale|large holder)\b", 0, .45),
    ("adoption", r"\b(?:adoption|integrates?|payment support)\b", .45, .6),
    ("exchange", r"\b(?:exchange|Binance|Coinbase|Kraken)\b", 0, .5),
    ("macro", r"\b(?:Federal Reserve|\bFed\b|interest rates?|inflation|CPI|jobs report)\b", 0, .7),
)

MARKET_WIDE = re.compile(r"\b(?:crypto market|cryptocurrency market|digital assets?|Federal Reserve|\bFed\b|interest rates?|inflation|CPI|regulation|SEC|ETF)\b", re.I)
UNCERTAIN = re.compile(r"\b(?:rumou?r|may|could|unconfirmed|alleged|reportedly|denies|fake)\b", re.I)


def map_symbols(text, allowed=SUPPORTED_SYMBOLS):
    result = {}
    for symbol in allowed:
        explicit = re.search(rf"(?<!\w)(?:\${symbol}|{symbol}USDT)(?!\w)", text, re.I)
        bare = re.search(rf"(?<!\w){symbol}(?!\w)", text)
        alias = any(re.search(rf"\b{re.escape(name)}\b", text, re.I) for name in ALIASES[symbol])
        if explicit:
            result[symbol] = .99
        elif alias:
            result[symbol] = .95
        elif bare:
            result[symbol] = .85
    return result


def classify(text):
    event, sentiment, importance = "other", 0.0, .2
    for candidate, pattern, direction, weight in EVENT_RULES:
        if re.search(pattern, text, re.I):
            event, sentiment, importance = candidate, direction, weight
            break
    confidence = .25 if UNCERTAIN.search(text) else .65
    if confidence < .5:
        sentiment = 0.0
    return event, sentiment, importance, confidence


def normalize_item(item: RawNewsItem, collected_at=None, historical_import=False,
                   source_guarantees_availability=False):
    collected = utc_iso(collected_at or datetime.now(timezone.utc))
    url = canonical_url(item.url)
    text = f"{item.title} {item.summary}".strip()
    mapping = map_symbols(text)
    market_wide = bool(MARKET_WIDE.search(text))
    event, sentiment, importance, confidence = classify(text)
    def source_time(value):
        try:
            return utc_iso(value)
        except (ValueError, TypeError):
            return utc_iso(parsedate_to_datetime(value))
    try:
        published = source_time(item.raw_published_at) if item.raw_published_at else None
        timestamp_confidence = "source_reported" if published else "unknown"
    except (ValueError, TypeError):
        published, timestamp_confidence = None, "unparseable"
    try:
        updated = source_time(item.raw_updated_at) if item.raw_updated_at else None
    except (ValueError, TypeError):
        updated = None
    archive_observed = utc_iso(item.archive_observed_at) if item.archive_observed_at else None
    if historical_import:
        first_seen = archive_observed if source_guarantees_availability else None
        first_seen_confidence = "archive_guaranteed" if first_seen else "unknown_historical"
    else:
        first_seen, first_seen_confidence = collected, "observed_by_collector"
    reasons = []
    if published is None:
        reasons.append("unknown_publication_time")
    if timestamp_confidence != "source_reported":
        reasons.append("unreliable_publication_timestamp")
    if first_seen is None:
        reasons.append("historical_availability_unknown")
    if published and first_seen and published > first_seen:
        reasons.append("publication_after_availability")
    if not mapping and not market_wide:
        reasons.append("no_symbol_or_market_mapping")
    title_key = digest(normalized_text(item.title))
    content_hash = digest(normalized_text(item.title) + "\n" + normalized_text(item.summary))
    raw_hash = digest(item.raw_payload)
    identity = f"source:{item.source}:{item.source_article_id}" if item.source_article_id else f"url:{url}"
    return NormalizedArticle(
        id=digest(identity), source=item.source, source_type=item.source_type,
        source_article_id=item.source_article_id, title=item.title.strip()[:1000],
        summary=item.summary.strip()[:10000], url=url, published_at=published,
        first_seen_at=first_seen, archive_observed_at=archive_observed,
        collected_at=collected, updated_at=updated,
        raw_published_at=item.raw_published_at, raw_updated_at=item.raw_updated_at,
        language=(item.language or "und").lower(), symbols=tuple(sorted(mapping)),
        symbol_confidence=mapping, market_wide=market_wide, event_type=event,
        sentiment=sentiment, importance=importance, confidence=confidence,
        scorer_method="deterministic_headline_rules", scorer_version="historical-headline-v1",
        timestamp_confidence=timestamp_confidence, first_seen_confidence=first_seen_confidence,
        raw_payload_hash=raw_hash, content_hash=content_hash, title_key=title_key,
        ml_eligible=not reasons, ml_ineligible_reason=";".join(reasons) or None,
        raw_payload=item.raw_payload)
