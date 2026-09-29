"""Small source adapters; no source-specific logic leaks into storage or CLIs."""
from __future__ import annotations

from datetime import datetime, timezone
import re
import time
from typing import Protocol
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

import requests

from app.ai.http import PublicSession, RateLimited
from .models import RawNewsItem, utc_iso
from .normalize import normalize_item
from .provenance import SourceProvenance


class NewsSourceAdapter(Protocol):
    def fetch(self, start=None, end=None, symbols=(), limit=250): ...
    def normalize(self, item, collected_at=None, historical_import=False): ...
    def source_name(self): ...
    def supports_backfill(self): ...
    def rate_limit_info(self): ...
    def provenance(self): ...
    def backfill_window_days(self): ...


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

    def backfill_window_days(self): return 1
    def provenance(self):
        return SourceProvenance(self.name, "rss", False, "source_reported_publication",
                                False, "high")

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
        return normalize_item(item, collected_at, historical_import, provenance=self.provenance())


class GDELTAdapter:
    URL = "https://api.gdeltproject.org/api/v2/doc/doc"

    def __init__(self, session=None, sleep=time.sleep, attempts=3):
        self.session = session or PublicSession()
        self.sleep, self.attempts = sleep, attempts

    def source_name(self):
        return "gdelt_doc"

    def supports_backfill(self):
        return True

    def rate_limit_info(self):
        return {"minimum_interval_seconds": 5, "maximum_records_per_request": 250,
                "note": "Article-list windows and availability are controlled by GDELT."}

    def backfill_window_days(self): return 1
    def provenance(self):
        return SourceProvenance("gdelt_doc", "gdelt_doc_api", True,
                                "third_party_index_timestamp", False, "low")

    def _request(self, params):
        last = None
        for attempt in range(self.attempts):
            try:
                response = self.session.get(self.URL, params=params, timeout=(10, 30))
                response.raise_for_status()
                return response
            except RateLimited:
                raise
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
                last = exc
                if isinstance(exc, requests.HTTPError) and exc.response is not None and exc.response.status_code < 500:
                    raise
                if attempt + 1 < self.attempts:
                    self.sleep(min(8, 2 ** attempt))
        raise last

    @staticmethod
    def _stamp(value):
        if not value:
            return None
        try:
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            return value

    def fetch(self, start=None, end=None, symbols=(), limit=250):
        names = {"BTC": '("Bitcoin" OR "BTC")', "ETH": '("Ethereum" OR "Ether" OR "ETH")',
                 "SOL": '("Solana" OR "$SOL")'}
        terms = [names[symbol] for symbol in symbols if symbol in names] or list(names.values())
        params = {"query": "(" + " OR ".join(terms) + ")", "mode": "artlist",
                  "format": "json", "maxrecords": min(int(limit), 250), "sort": "datedesc"}
        if start:
            params["startdatetime"] = start.strftime("%Y%m%d%H%M%S")
        if end:
            params["enddatetime"] = end.strftime("%Y%m%d%H%M%S")
        response = self._request(params)
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
                              provenance=self.provenance())


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

    def backfill_window_days(self): return 7
    def provenance(self):
        return SourceProvenance("hackernews", "hn_algolia_api", True,
                                "public_post_creation", False, "medium")

    def fetch(self, start=None, end=None, symbols=(), limit=250):
        names = {"BTC": ("Bitcoin", "BTC"), "ETH": ("Ethereum", "Ether", "ETH"),
                 "SOL": ("Solana",)}
        terms = [term for symbol in symbols for term in names.get(symbol, ())] or ["Bitcoin", "Ethereum", "Solana"]
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
                              provenance=self.provenance())


class GitHubReleasesAdapter:
    """Authoritative public release posts for core BTC/ETH/SOL repositories."""
    URL = "https://api.github.com/repos/{repo}/releases"
    REPOSITORIES = {"BTC": "bitcoin/bitcoin", "ETH": "ethereum/go-ethereum",
                    "SOL": "solana-labs/solana"}

    def __init__(self, session=None): self.session = session or PublicSession()
    def source_name(self): return "github_releases"
    def supports_backfill(self): return True
    def backfill_window_days(self): return 366
    def rate_limit_info(self):
        return {"maximum_unauthenticated_requests_per_hour": 60,
                "minimum_interval_seconds": 1,
                "note": "Public releases only; up to 100 results per page."}
    def provenance(self):
        return SourceProvenance("github_releases", "official_project_release", True,
                                "authoritative_publication", True, "high")

    def fetch(self, start=None, end=None, symbols=(), limit=250):
        selected = [symbol for symbol in symbols if symbol in self.REPOSITORIES] or list(self.REPOSITORIES)
        result = []
        for symbol in selected:
            repo = self.REPOSITORIES[symbol]
            page = 1
            while len(result) < limit:
                response = self.session.get(self.URL.format(repo=repo),
                                            params={"per_page": 100, "page": page},
                                            headers={"Accept": "application/vnd.github+json",
                                                     "X-GitHub-Api-Version": "2022-11-28"},
                                            timeout=(10, 30))
                response.raise_for_status()
                releases = response.json()
                if not releases:
                    break
                oldest = None
                for release in releases:
                    published = release.get("published_at")
                    if release.get("draft") or not published:
                        continue
                    stamp = datetime.fromisoformat(published.replace("Z", "+00:00"))
                    oldest = stamp if oldest is None or stamp < oldest else oldest
                    if (start and stamp < start) or (end and stamp >= end):
                        continue
                    title = f"{symbol} {release.get('name') or release.get('tag_name') or 'release'}"
                    result.append(RawNewsItem(
                        f"github:{repo}", "official_project_release", title,
                        release.get("html_url"), published, (release.get("body") or "")[:10000],
                        str(release.get("id")), release.get("updated_at"), "en", release))
                    if len(result) >= limit:
                        break
                if len(releases) < 100 or (start and oldest and oldest < start):
                    break
                page += 1
        return result[:limit]

    def normalize(self, item, collected_at=None, historical_import=False):
        return normalize_item(item, collected_at, historical_import=True,
                              provenance=self.provenance())


ADAPTERS = {
    "gdelt": lambda: GDELTAdapter(),
    "hackernews": lambda: HackerNewsAdapter(),
    "github": lambda: GitHubReleasesAdapter(),
    "coindesk": lambda: RSSAdapter("coindesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    "cointelegraph": lambda: RSSAdapter("cointelegraph", "https://cointelegraph.com/rss"),
}
