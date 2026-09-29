"""Source provenance and centralized information-availability policy."""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class SourceProvenance:
    source_name: str
    source_type: str
    supports_backfill: bool
    timestamp_basis: str
    historical_availability_guarantee: bool
    timestamp_confidence: str
    provenance_version: str = "news-provenance-v2"

    def to_dict(self):
        return asdict(self)


def availability_policy(*, published_at, collected_at, historical_import, provenance,
                        has_mapping):
    """Return availability fields and eligibility from explicit source evidence."""
    if historical_import:
        trusted = (provenance.historical_availability_guarantee and published_at and
                   provenance.timestamp_basis in {
                       "authoritative_publication", "public_post_creation",
                       "verified_historical_observation"})
        availability_timestamp = published_at if trusted else None
        availability_basis = provenance.timestamp_basis if trusted else "unknown_historical_availability"
        availability_confidence = provenance.timestamp_confidence if trusted else "unknown"
    else:
        availability_timestamp = collected_at
        availability_basis = "local_observation"
        availability_confidence = "exact"

    reasons = []
    if not published_at:
        reasons.append("unknown_publication_time")
    if provenance.timestamp_confidence not in {"high", "exact", "source_reported"}:
        reasons.append("unreliable_publication_timestamp")
    if not availability_timestamp:
        reasons.append("historical_availability_unknown")
    if published_at and availability_timestamp and published_at > availability_timestamp:
        reasons.append("publication_after_availability")
    if not has_mapping:
        reasons.append("no_symbol_or_market_mapping")
    return (availability_timestamp, availability_basis, availability_confidence,
            not reasons, ";".join(reasons) or None)
