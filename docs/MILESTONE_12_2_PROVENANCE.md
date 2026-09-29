# Milestone 12.2 — News Archive Expansion & Provenance

## Safety boundary

This milestone only changes the local news archive. It does not retrain or load
an ML model into trading, modify `ml_v1_market_forward_001`, or connect news to
PaperBroker, RiskManager, or Batch 3 decisions. The pre-migration archive is
preserved as `data/news/archive.pre_m12_2.sqlite3`.

## Provenance and availability

Every normalized article now stores:

- source type and whether the adapter supports historical backfill;
- timestamp basis and confidence;
- whether the source guarantees historical public availability;
- provenance version;
- `availability_timestamp`, `availability_basis`, and
  `availability_confidence`;
- mapping method/version and `canonical_story_id`.

Eligibility is determined in one policy function. Historical data is eligible
only when its source explicitly guarantees a timestamp with one of these bases:
authoritative publication, public post creation, or verified historical
observation. Current import time, third-party index time, scraped archives, and
unknown availability do not qualify.

`first_seen_at` retains its narrower meaning: when this application first
observed an item live. An authoritative historical `published_at` can be the ML
availability timestamp without pretending that this application observed the
article at that time.

## Sources

| Source | Backfill | Availability basis | Historical ML eligibility |
| --- | --- | --- | --- |
| GitHub Releases: bitcoin/bitcoin | Yes | authoritative public release | Yes |
| GitHub Releases: ethereum/go-ethereum | Yes | authoritative public release | Yes |
| GitHub Releases: solana-labs/solana | Yes | authoritative public release | Yes |
| GDELT DOC | Yes | third-party index observation | No |
| Hacker News Algolia | Yes | public submission/index observation | No |
| CoinDesk RSS | No | local observation | Eligible after local observation |
| Cointelegraph RSS | No | local observation | Eligible after local observation |

GitHub backfill uses public release posts and requires no token. It requests at
most 100 releases per page, applies bounded time windows, and stops paging after
passing the requested start. The unauthenticated API allowance is documented as
60 requests per hour, so long runs are paced and resumable.

GDELT now uses bounded retries with exponential delays for transient connection,
timeout, and server failures. Rate limits are not retried indefinitely: the
window is marked `RATE_LIMITED` and the run stops. Checkpoints use `PENDING`,
`COMPLETE`, `FAILED`, and `RATE_LIMITED`; resume skips only complete windows.

## Mapping and story provenance

Entity rules v2 recognize canonical names, `$TICKER`, `TICKERUSDT`, and
uppercase standalone tickers. A lowercase common word such as `sol` does not map
to Solana. Market-wide detection remains independent from asset mapping.

Cross-source title/content matches within six hours point to one
`canonical_story_id`. The duplicate candidate remains in
`news_source_evidence`, including source, source article ID, URL, observation
time, payload hash, and deduplication reason. Similar stories outside the time
window remain separate.

## Commands

```powershell
python -m app.news.backfill --source github --start 2023-01-01 --end 2026-01-01 --symbols BTC,ETH,SOL --limit 500 --resume
python -m app.news.coverage
python -m app.news.readiness --start 2023-01 --end 2025-12
python -m app.news.validate
python -m app.news.export --ml-eligible-only
```

Coverage adds monthly counts, missing months, availability basis/confidence,
source provenance, syndication evidence, eligible percentage, and a
month × BTC/ETH/SOL/market-wide CSV matrix.

Readiness evaluates every month/symbol cell using configurable minimum article,
eligible article, coverage-day, and source-diversity thresholds. The default
`USABLE_FOR_EXPERIMENT` threshold requires 80% of all target cells to pass;
otherwise a dataset with some eligible coverage is `PARTIAL`, and one with none
is `NOT_READY`.

Validation checks future and inconsistent timestamps, eligibility without an
availability timestamp, missing provenance, invalid symbols, duplicate IDs,
orphaned source evidence, and checkpoint statuses. Export writes compressed
JSONL and includes timestamp provenance while excluding raw payloads.

## Local result

The bounded 2023–2025 GitHub backfill added 191 authoritative records: 20 BTC,
59 ETH, and 112 SOL. Together with Milestone 12.1 the archive contains 273
canonical articles, of which 207 are ML-eligible (75.82%). Coverage now spans
2023-01-10 through 2026-09-28.

Counts by year are 87 for 2023, 76 for 2024, 28 for 2025, and 82 for 2026.
The 2023–2025 readiness result is `PARTIAL`: 42 of 144 monthly asset/market cells
pass the default criteria. This reflects sparse release-only coverage and should
not be interpreted as a complete news dataset. Validation reports zero integrity
issues. The ML-safe gzip JSONL export contains 207 rows.

Two earlier GDELT checkpoints remain `FAILED` from the Milestone 12.1 TLS/rate
limit attempts. The new GitHub backfill completed 9 windows without failures or
rate limits, and a resume check skipped all three completed BTC windows.
