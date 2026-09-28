from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def utc_iso(value):
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    value = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return value.isoformat()


def canonical_url(value):
    parts = urlsplit(value or "")
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("Article URL must be HTTP(S)")
    query = [(key, item) for key, item in parse_qsl(parts.query)
             if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"),
                       urlencode(sorted(query)), ""))


def digest(value):
    if not isinstance(value, str):
        value = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalized_text(value):
    return re.sub(r"\W+", " ", (value or "").casefold(), flags=re.UNICODE).strip()


@dataclass(frozen=True)
class RawNewsItem:
    source: str
    source_type: str
    title: str
    url: str
    raw_published_at: str | None = None
    summary: str = ""
    source_article_id: str | None = None
    raw_updated_at: str | None = None
    language: str | None = None
    raw_payload: dict = field(default_factory=dict)
    archive_observed_at: str | None = None


@dataclass(frozen=True)
class NormalizedArticle:
    id: str
    source: str
    source_type: str
    source_article_id: str | None
    title: str
    summary: str
    url: str
    published_at: str | None
    first_seen_at: str | None
    archive_observed_at: str | None
    collected_at: str
    updated_at: str | None
    raw_published_at: str | None
    raw_updated_at: str | None
    language: str
    symbols: tuple[str, ...]
    symbol_confidence: dict[str, float]
    market_wide: bool
    event_type: str
    sentiment: float
    importance: float
    confidence: float
    scorer_method: str
    scorer_version: str
    timestamp_confidence: str
    first_seen_confidence: str
    raw_payload_hash: str
    content_hash: str
    title_key: str
    ml_eligible: bool
    ml_ineligible_reason: str | None
    raw_payload: dict

    def to_record(self):
        result = asdict(self)
        result["symbols"] = list(self.symbols)
        return result
