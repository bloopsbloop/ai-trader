# M18.1 — Data Provenance and Independence Audit

## Verdict

**HOLDOUT_PROVENANCE_UNCERTAIN.** The proposed period is `2026-01-01 00:00 UTC` through `2026-10-01 00:00 UTC` (end exclusive), covering completed calendar months January–September 2026. It was selected by calendar before acquiring the proposed seven-symbol dataset or calculating strategy returns. It cannot be described as untouched or fully independent.

Frozen evaluation protocol SHA-256: `0997c5ba7f63351bbf10ab6271aa6d996ce9232dfefd8a792e735d57e1449f5e`.

This audit used existing documentation, manifests, metadata, file inventory, and Git history. It did not read candidate-period OHLCV values, query a market API, download archives, or calculate 2026 strategy performance. File creation dates were not treated as proof of independence.

## Previously used periods

The project has repeatedly viewed and used market history through the end of 2025:

- Early backtests and optimizer work used historical Binance data, including examples and evaluations beginning in 2021 and evaluation data from 2023 onward.
- Batch 2 used `2023-01-01` through `2025-12-31` with chronological train/validation research.
- M11–M16 dataset, ML, cross-asset, candidate-filter, target, and ranking work used 2023–2025 history. Their Test partitions were already documented as project-wide reused research periods.
- M17.1/M17.2 used synchronized seven-symbol 4h history from `2023-01-01 00:00` through `2025-12-31 20:00 UTC`, with the economic path ending at `2026-01-01 00:00 UTC` after terminal marking.
- The fixed Donchian 120/60 direction was selected and assessed using that reused 2023–2025 history. Historical tuning on that period is now stopped.

The local `data/history` inventory contains 15m, 1h, and 4h CSVs for the seven frozen symbols. Existing coverage metadata and frozen hashes place those historical datasets through 2025. Their OHLCV contents were not reopened for this provenance audit.

## What is known about 2026

Existing project records establish exposure to some 2026 information:

- The news archive reports coverage through `2026-09-28`, including 82 records dated in 2026. These records were used for news provenance/readiness work, not for M17 Donchian evaluation.
- M11.3 documents a public-data scanner validation at the `2026-09-28 03:00 UTC` candle close for BTCUSDT, ETHUSDT, and SOLUSDT. It displayed ML shadow probabilities and heuristic scores. It was scan-only and created no paper trade, but market-derived outputs from the proposed period were viewed.
- Local Batch 3 and validation SQLite files exist. They were not opened because M18.1 forbids SQLite and process-state access. Their presence means this audit cannot rule out additional locally stored 2026 observations.
- The research was conducted in October 2026. General market/news awareness about January–September 2026 cannot be excluded.

No existing metadata, report, manifest, or Git history inspected in this audit shows:

- a Donchian 120/60 PnL calculation for January–September 2026;
- a seven-asset M17-style passive comparison on that period;
- a ranking-model historical evaluation whose labeled market dataset extends beyond 2025;
- use of January–September 2026 Donchian results to choose the frozen 120/60 policy.

This is absence of located evidence, not proof that the period was unseen. The documented September scan, 2026 news archive, uninspected runtime databases, and general awareness prevent an `UNTOUCHED` verdict.

## Proposed candidate

The registered candidate is:

```text
Start inclusive: 2026-01-01 00:00:00 UTC
End exclusive:   2026-10-01 00:00:00 UTC
```

It contains 273 complete UTC days and an expected 1,638 consecutive 4h candles per symbol. Midnight boundaries were chosen before result access so the complete path and daily endpoint share the same coverage. Selection was based on nine completed calendar months, not on observed returns.

The candidate is best described as a **calendar-selected, not-yet-evaluated period with uncertain project-wide provenance**. A future acquisition milestone may perform one frozen-policy evaluation after review; it must retain this qualification.

## Prospective alternative

If stronger independence is required, a separately reviewed prospective period is proposed:

```text
Start inclusive: 2026-11-01 00:00:00 UTC
End exclusive:   2027-08-01 00:00:00 UTC
```

At preregistration on 2026-10-10, that start is in the future. This is only a proposal. No observer, monitoring, download, or trading process has been started. It cannot be substituted after viewing the January–September candidate result, and it requires its own protocol review before collection.

## Future source and timestamp provenance

If a later milestone authorizes acquisition, the primary source is the official Binance public monthly Spot kline archive with official checksum files. Binance documents the 12 kline fields and states that Spot archive timestamps from 2025 onward are in microseconds: [Binance public data repository](https://github.com/binance/binance-public-data). Binance also documents that REST market-data timestamps default to milliseconds while microseconds can be requested explicitly, which is why archive and API formats must not be conflated: [Binance Spot REST API documentation](https://developers.binance.com/en/docs/products/spot/rest-api).

The acquisition implementation must inspect and validate each archive's schema and units before conversion. For 2026 monthly Spot archives it must require unambiguous microsecond integers, convert explicitly as UTC microseconds, verify exact 4h alignment and expected range, and reject mixed/ambiguous units or schema changes. It must preserve official checksums, raw archive hashes, URLs, and retrieval timestamps in a new non-overwriting namespace.

## Scope

This preregistration reduces future analytical flexibility. It does not make already elapsed data independent, erase general market knowledge, or guarantee adequate power. Nine months and potentially few Donchian trades may yield wide confidence intervals. Positive point estimates with wide intervals will remain inconclusive; a negative result will not trigger optimization on the opened period.
