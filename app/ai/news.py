"""Public RSS/Atom ingestion, conservative symbol resolution and scorer contract."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import math
import re
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit
from xml.etree import ElementTree as ET

import requests
from app.ai.http import PublicSession


def utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def fingerprint(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_url(url):
    parts = urlsplit(url)
    if parts.scheme not in {"https", "http"} or not parts.hostname:
        raise ValueError("Invalid article URL")
    # Preserve identity parameters; remove known tracking parameters only.
    from urllib.parse import parse_qsl, urlencode
    query = [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_") and k not in {"fbclid", "gclid"}]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), urlencode(sorted(query)), ""))


@dataclass(frozen=True)
class Article:
    source: str
    title: str
    url: str
    published_at: str
    first_seen_at: str

    @property
    def title_key(self):
        return fingerprint(re.sub(r"\W+", " ", self.title.casefold()).strip())

    @property
    def id(self):
        return fingerprint(canonical_url(self.url))


EVENTS = {"listing", "delisting", "security", "regulation", "adoption", "upgrade", "unlock", "other"}


@dataclass(frozen=True)
class Assessment:
    sentiment: float
    importance: float
    freshness: float
    confidence: float
    event_type: str
    scorer_version: str

    def __post_init__(self):
        for field, low in (("sentiment", -1), ("importance", 0), ("freshness", 0), ("confidence", 0)):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= 1:
                raise ValueError(f"Invalid {field}")
        if self.event_type not in EVENTS or not self.scorer_version:
            raise ValueError("Invalid scorer metadata")


class NewsScorer(Protocol):
    def score(self, article: Article, now: datetime) -> Assessment: ...


def freshness(article, now):
    published, seen = utc(article.published_at), utc(article.first_seen_at)
    now = utc(now)
    if published > now or seen > now:
        return 0.0
    age = (now - published).total_seconds()
    return math.exp(-age / 21600) if age <= 86400 else 0.0


class BaselineScorer:
    """Transparent, untrained English headline classifier, replaceable via Protocol."""
    RULES = (("delisting", r"\bdelist(?:s|ed|ing)?\b", -0.9, 0.95),
             ("security", r"\b(hack|hacked|exploit|stolen)\b", -0.9, 0.95),
             ("unlock", r"\bunlock\b", -0.3, 0.6),
             ("listing", r"\b(listing|lists|listed)\b", 0.6, 0.7),
             ("adoption", r"\b(adoption|partnership|integrates)\b", 0.5, 0.6),
             ("upgrade", r"\b(upgrade|mainnet)\b", 0.3, 0.5),
             ("regulation", r"\b(sec|etf|regulator|regulation)\b", 0.0, 0.8))

    def score(self, article, now):
        event, sentiment, importance = "other", 0.0, 0.2
        for kind, pattern, direction, weight in self.RULES:
            if re.search(pattern, article.title, re.I):
                event, sentiment, importance = kind, direction, weight
                break
        uncertain = re.search(r"\b(rumou?r|may|could|unconfirmed|not|denies|fake)\b", article.title, re.I)
        return Assessment(sentiment if not uncertain else 0, importance, freshness(article, now),
                          0.2 if uncertain else 0.6, event, "headline-baseline-v1")


ALIASES = {"BTC": ("bitcoin",), "ETH": ("ethereum",), "SOL": ("solana",),
           "BNB": ("binance coin",), "XRP": ("ripple",), "LINK": ("chainlink",), "DOGE": ("dogecoin",)}
AMBIGUOUS = {"LINK", "ONE", "NEAR", "GAS", "ON", "W", "S", "IO", "TRUMP"}


def resolve_symbols(title, symbols):
    result = []
    for symbol in symbols:
        base = symbol.removesuffix("USDT")
        explicit = re.search(r"(?<!\w)(?:\$" + re.escape(base) + r"|" + re.escape(symbol) + r")(?!\w)", title)
        bare = base not in AMBIGUOUS and len(base) > 2 and re.search(r"(?<!\w)" + re.escape(base) + r"(?!\w)", title)
        alias = any(re.search(r"\b" + re.escape(name) + r"\b", title, re.I) for name in ALIASES.get(base, ()))
        if explicit or bare or alias:
            result.append(symbol)
    return sorted(result)


def parse_feed(payload, source, seen):
    if len(payload) > 2_000_000 or b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper():
        raise ValueError("Unsafe or oversized feed")
    root = ET.fromstring(payload)
    result = []
    for item in list(root.findall(".//item")) + list(root.findall(".//{*}entry")):
        def field(name):
            element = item.find(name)
            if element is None:
                element = item.find("{*}" + name)
            return "" if element is None else (element.text or element.get("href", ""))
        title = re.sub(r"<[^>]*>", "", field("title")).strip()[:1000]
        raw_date = field("pubDate") or field("published") or field("updated")
        try:
            try:
                published = utc(raw_date)
            except ValueError:
                published = utc(parsedate_to_datetime(raw_date))
            url = canonical_url(field("link"))
            if title and published <= utc(seen):
                result.append(Article(source, title, url, published.isoformat(), utc(seen).isoformat()))
        except (ValueError, TypeError, AttributeError, OverflowError):
            continue
    return result


class NewsCollector:
    DEFAULT_FEEDS = ("https://www.coindesk.com/arc/outboundfeeds/rss/", "https://cointelegraph.com/rss")

    def __init__(self, feeds=None, session=None):
        self.feeds = self.DEFAULT_FEEDS if feeds is None else tuple(feeds)
        if any(urlsplit(url).scheme != "https" for url in self.feeds):
            raise ValueError("Feeds require HTTPS")
        # Independent cooldowns: one failed feed must not suppress other sources.
        self.sessions = {url: session or PublicSession() for url in self.feeds}

    def collect(self):
        articles, health = [], {}
        for url in self.feeds:
            try:
                with self.sessions[url].get(url, timeout=(5, 15), stream=True) as response:
                    response.raise_for_status()
                    chunks, size = [], 0
                    for chunk in response.iter_content(65536):
                        size += len(chunk)
                        if size > 2_000_000:
                            raise ValueError("Feed exceeds size limit")
                        chunks.append(chunk)
                items = parse_feed(b"".join(chunks), url, datetime.now(timezone.utc))
                articles.extend(items)
                health[url] = {"ok": bool(items), "items": len(items),
                               "observed_at": datetime.now(timezone.utc).isoformat()}
            except (requests.RequestException, ValueError, ET.ParseError) as exc:
                health[url] = {"ok": False, "error": type(exc).__name__}
        return articles, health
