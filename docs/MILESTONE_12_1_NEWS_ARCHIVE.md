# Milestone 12.1 — Historical News Archive Foundation

## Scope and safety boundary

This milestone adds a local, additive news archive. It does not train a model,
alter `ML_V1_MARKET`, change the `ml_v1_market_forward_001` experiment, place
orders, or connect the archive to Batch 3 decisions. Batch 1/2 and the Batch 3
paper database remain separate.

The canonical database is `data/news/archive.sqlite3`. Generated coverage files
are written to `data/news/reports/`. Both locations are local runtime data and
are ignored by Git.

## Storage design

`news_articles` contains the canonical normalized record and enough raw metadata
to audit it:

- source, source type, source article ID, title, summary, URL and canonical URL;
- source publication/update strings alongside parsed timestamps;
- source publication, local first-seen, archive observation, and collection time;
- language, mapped symbols and mapping confidence, and market-wide status;
- event type, sentiment, importance, confidence, scorer method and version;
- raw-payload and normalized-content hashes plus the preserved JSON payload;
- timestamp confidence, ML eligibility, and explicit ineligibility reasons.

`news_duplicates` records every rejected candidate, its canonical article,
deduplication reason, payload hash, and observation time. It provides an audit
trail without overwriting the canonical article's historical timestamps.

`news_backfill_checkpoints` stores completion per source, query contract, and
one-day time window. A resumed run skips only completed windows. Failed windows
remain retryable.

## Timestamp contract

`published_at` is parsed only from a timestamp reported by the publisher/source.
The original string remains in `raw_published_at`.

`first_seen_at` means the earliest time this application observed the item. A
live incremental collection sets it to the collection time and labels it
`observed_by_collector`. A historical import leaves it null unless an adapter can
prove historical information availability. Neither publication time nor today's
import time is silently substituted.

`archive_observed_at` stores a third-party archive/index observation separately.
For GDELT this is `seendate`; for Hacker News it is the submission time. Neither
is treated as publisher publication time or proof that this application knew the
article then.

`collected_at` is the local ingestion time. Records with an unknown/unreliable
publication timestamp, unknown historical availability, a future publication
relative to first seen, or no usable symbol/market mapping are ML-ineligible.
Coverage includes integrity counts for publication after collection, first seen
before publication, and historical records that incorrectly claim first seen.

## Source adapters

Every adapter exposes `fetch`, `normalize`, `source_name`, `supports_backfill`,
and `rate_limit_info`.

| Adapter | Mode | Backfill | Timestamp treatment |
| --- | --- | --- | --- |
| CoinDesk RSS | Incremental | No | Feed publication time; local first seen |
| Cointelegraph RSS | Incremental | No | Feed publication time; local first seen |
| GDELT DOC API | Historical | Yes, bounded | `seendate` is archive observation only |
| Hacker News Algolia | Historical | Yes, bounded | submission is archive observation only |

The historical adapters are discovery/archive inputs. Their imported rows remain
ML-ineligible because the original publisher publication time and this system's
historical availability are not established. This is intentional leakage
protection. RSS ingestion begins reliable local first-seen tracking from the
moment this collector runs; it cannot reconstruct the past.

The RSS parser requires HTTPS, rejects DTD/entity declarations, enforces a 2 MB
response limit, and uses bounded network timeouts. Collection failures are
reported per source without exposing response content or secrets.

## Normalization and deduplication

Initial symbol mapping recognizes explicit tickers, project names, and aliases
for BTC/Bitcoin, ETH/Ethereum/Ether, and SOL/Solana. Each match records a mapping
confidence. General crypto, macro, regulation, Fed, and ETF stories can be marked
market-wide.

The event taxonomy is `regulation`, `listing`, `delisting`, `hack`, `exploit`,
`partnership`, `upgrade`, `ETF`, `macro`, `exchange`, `legal`, `tokenomics`,
`whale`, `adoption`, `outage`, and `other`. A conservative deterministic
headline/summary ruleset creates sentiment, importance, and confidence. Stored
metadata names the method `deterministic_headline_rules` and scorer version
`historical-headline-v1`; it is not presented as LLM output.

Deduplication checks source identity, canonical URL, source article ID, and then
normalized title/content plus a six-hour publication-time proximity window.
Tracking parameters are removed from URLs. Identical titles far apart remain
distinct to avoid collapsing real updates.

## Commands

Incremental collection:

```powershell
python -m app.news.collect --limit 20
python -m app.news.collect --source coindesk --source cointelegraph --limit 20
```

Bounded, resumable historical import:

```powershell
python -m app.news.backfill --source hackernews --start 2026-09-01 --end 2026-09-28 --symbols BTC,ETH,SOL --limit 100 --resume
python -m app.news.backfill --source gdelt --start 2026-09-27T00:00:00Z --end 2026-09-28T00:00:00Z --symbols BTC,ETH,SOL --limit 50 --resume
```

Coverage export:

```powershell
python -m app.news.coverage
```

It emits JSON, Markdown, and CSV breakdowns for date, source, and symbol. The
report includes date ranges, missing days, timestamp confidence, duplicate rate,
sentiment, event types, eligibility reasons, and integrity checks.

## Local validation snapshot

The bounded validation archive contains 82 canonical records: 42 Hacker News
historical discoveries and 40 current RSS articles (20 per RSS source). A second
identical RSS run inserted zero records and logged 40 duplicates. Resuming the
eight-day historical window skipped all eight completed checkpoints.

Observed/archive coverage spans 2026-09-01 through 2026-09-28. Symbol mappings
are BTC 37, ETH 6, and SOL 3; four records are market-wide. Sixteen current RSS
records are ML-eligible. All 42 historical discovery records are deliberately
ineligible because historical publication/availability is not established.

Seven calendar days have no observations in this small sample: September 5, 8,
11, 13, 16, 18, and 24. GDELT validation was attempted but returned a rate limit
response after an earlier TLS timeout, so the sample contains no GDELT records.
Hacker News is a broad public story index rather than a curated financial news
provider. These limitations prevent a valid historical WITH_NEWS comparison at
this stage.
