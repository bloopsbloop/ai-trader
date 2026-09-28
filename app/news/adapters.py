"""Small source adapters; no source-specific logic leaks into storage or CLIs."""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Protocol
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

import requests

from app.ai.http import PublicSession
from .models import RawNewsItem, utc_iso
from .normalize import normalize_item


class NewsSourceAdapter(Protocol):
    def fetch(self, start=None, end=None, symbols=(), limit=250): ...
    def normalize(self, item, collected_at=None, historical_import=False): ...
    def source_name(self): ...
    def supports_backfill(self): ...
    def rate_limit_info(self): ...


class RSSAdapter:
    def __init__(self, name, url, session=None):
        if urlsplit(url).scheme != "https":
            raise ValueError("RSS source must use HTTPS")
        self.name, self.url = name, url
        self.session = session or PublicSession()

    def source_name(self):
        return self.name

    def supports_backfill(self):
        return False

    def rate_limit_info(self):
        return {"policy": "incremental feed only", "minimum_interval_seconds": 300}

    @staticmethod
    def _field(item, name):
        element = item.find(name)
        if element is None:
            element = item.find("{*}" + name)
        return "" if element is None else (element.text or element.get("href", ""))

    def fetch(self, start=None, end=None, symbols=(), limit=250):
        with self.session.get(self.url, timeout=(5, 15), stream=True) as response:
            response.raise_for_status()
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > 2_000_000:
                    raise ValueError("Feed exceeds 2 MB")
                chunks.append(chunk)
        payload = b"".join(chunks)
        if b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper():
            raise ValueError("Unsafe XML feed")
        root = ET.fromstring(payload)
        result = []
        for item in list(root.findall(".//item")) + list(root.findall(".//{*}entry")):
            title = re.sub(r"<[^>]*>", "", self._field(item, "title")).strip()
            link = self._field(item, "link")
            raw_date = self._field(item, "pubDate") or self._field(item, "published")
            raw_updated = self._field(item, "updated") or None
            summary = re.sub(r"<[^>]*>", " ", self._field(item, "description") or self._field(item, "summary"))
            source_id = self._field(item, "guid") or self._field(item, "id") or None
            if not title or not link:
                continue
            result.append(RawNewsItem(self.name, "rss", title, link, raw_date or None,
                                      re.sub(r"\s+", " ", summary).strip(), source_id,
                                      raw_updated, None,
                                      {"title": title, "link": link, "published": raw_date,
                                       "updated": raw_updated, "summary": summary,
                                       "source_article_id": source_id}))
            if len(result) >= limit:
                break
        return result

    def normalize(self, item, collected_at=None, historical_import=False):
        return normalize_item(item, collected_at, historical_import,
                              source_guarantees_availability=False)


class GDELTAdapter:
    URL = "https://api.gdeltproject.org/api/v2/doc/doc"

    def __init__(self, session=None):
        self.session = session or PublicSession()

    def source_name(self):
        return "gdelt_doc"

    def supports_backfill(self):
        return True

    def rate_limit_info(self):
        return {"minimum_interval_seconds": 5, "maximum_records_per_request": 250,
                "note": "Article-list windows and availability are controlled by GDELT."}

    @staticmethod
    def _stamp(value):
        if not value:
            return None
        try:
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            return value

    def fetch(self, start=None, end=None, symbols=(), limit=250):
        names = {"BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana"}
        terms = [names[symbol] for symbol in symbols if symbol in names] or list(names.values())
        params = {"query": "(" + " OR ".join(terms) + ")", "mode": "artlist",
                  "format": "json", "maxrecords": min(int(limit), 250), "sort": "datedesc"}
        if start:
            params["startdatetime"] = start.strftime("%Y%m%d%H%M%S")
        if end:
            params["enddatetime"] = end.strftime("%Y%m%d%H%M%S")
        response = self.session.get(self.URL, params=params, timeout=(15, 45))
        response.raise_for_status()
        payload = response.json()
        result = []
        for article in payload.get("articles", []):
            url, title = article.get("url"), article.get("title")
            if not url or not title:
                continue
            domain = article.get("domain") or urlsplit(url).hostname or "unknown"
            seen = self._stamp(article.get("seendate"))
            result.append(RawNewsItem(domain, "gdelt_doc_api", title, url,
                                      raw_published_at=None, summary="",
                                      source_article_id=None, raw_updated_at=None,
                                      language=article.get("language"), raw_payload=article,
                                      archive_observed_at=seen))
        return result

    def normalize(self, item, collected_at=None, historical_import=False):
        # GDELT's seendate is third-party archive observation, not a source-reported
        # publication timestamp and not proof of when our system knew the article.
        return normalize_item(item, collected_at, historical_import=True,
                              source_guarantees_availability=False)


class HackerNewsAdapter:
    """Historical public story index; submission time is not source publication time."""
    URL = "https://hn.algolia.com/api/v1/search_by_date"

    def __init__(self, session=None):
        self.session = session or PublicSession()

    def source_name(self):
        return "hackernews_algolia"

    def supports_backfill(self):
        return True

    def rate_limit_info(self):
        return {"maximum_requests_per_hour": 10_000, "maximum_retrievable_hits": 1_000,
                "note": "HN submission time is archive observation, not publisher timestamp."}

    def fetch(self, start=None, end=None, symbols=(), limit=250):
        names = {"BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana"}
        terms = [names[symbol] for symbol in symbols if symbol in names] or list(names.values())
        filters = []
        if start:
            filters.append(f"created_at_i>={int(start.timestamp())}")
        if end:
            filters.append(f"created_at_i<{int(end.timestamp())}")
        base_params = {"tags": "story", "hitsPerPage": min(int(limit), 1000), "page": 0}
        if filters:
            base_params["numericFilters"] = ",".join(filters)
        hits = {}
        for term in terms:
            response = self.session.get(self.URL, params={**base_params, "query": term}, timeout=(10, 30))
            response.raise_for_status()
            for hit in response.json().get("hits", []):
                if hit.get("objectID"):
                    hits[hit["objectID"]] = hit
        result = []
        for hit in sorted(hits.values(), key=lambda value: value.get("created_at_i", 0), reverse=True)[:limit]:
            title, object_id = hit.get("title"), hit.get("objectID")
            if not title or not object_id:
                continue
            url = hit.get("url") or f"https://news.ycombinator.com/item?id={object_id}"
            result.append(RawNewsItem(
                "hackernews", "hn_algolia_api", title, url, raw_published_at=None,
                summary=(hit.get("story_text") or ""), source_article_id=str(object_id),
                language="en", raw_payload=hit, archive_observed_at=hit.get("created_at")))
        return result

    def normalize(self, item, collected_at=None, historical_import=False):
        return normalize_item(item, collected_at, historical_import=True,
                              source_guarantees_availability=False)


ADAPTERS = {
    "gdelt": lambda: GDELTAdapter(),
    "hackernews": lambda: HackerNewsAdapter(),
    "coindesk": lambda: RSSAdapter("coindesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    "cointelegraph": lambda: RSSAdapter("cointelegraph", "https://cointelegraph.com/rss"),
}
