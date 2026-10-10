# M17.1R protocol addendum

This addendum corrects temporal accounting and reproducibility defects in M17.1. It does not change the frozen universe, 4h timeframe, Donchian 120/60 rules, equal sleeves, costs, execution assumptions, splits, bootstrap settings, or acceptance criteria. `M17_1_PROTOCOL.json` remains byte-for-byte frozen.

## Event-time convention

For a candle opened at `T`, `bar_close_time = T + 4h`. A close-based signal becomes available at that close time. At a shared boundary the deterministic event order is:

1. previous candle close and signal;
2. next candle open fill at the same timestamp;
3. next candle close equity mark four hours later.

This is an idealized bar-based ordering and does not assert zero live latency. A missing exact next open expires the pending order. No candle is synthesized; an existing position remains held through a gap.

Close-valued equity is timestamped at `equity_mark_time = bar_close_time`. Terminal liquidation occurs once at the final close and replaces that timestamp's pre-liquidation close mark, so the final fee is included exactly once.

## Initial capital and windows

Each sleeve emits an initial equity mark before its first permitted fill. Buy-and-hold therefore measures its initial fill costs and first-day movement from the original capital.

Window source candles use inclusive open-time bounds `[start, end]`. The return denominator is equity at `start`, immediately before the first included candle's increment. The numerator is equity at `end + 4h`, after the last included candle. Adjacent windows share one capital boundary and neither lose nor duplicate an increment. Open positions carry across continuous boundaries without a synthetic fill or fee.

## Chunk continuation

`SleeveState` carries the last 120 highs and lows, pending side and exact fill time, position, cash, entry accounting, and cumulative costs. A continuation chunk contains only unprocessed candles. It neither repeats the prior boundary candle nor rebuilds rolling history.

## Artifact immutability

The original namespace `data/research/m17_1_absolute_trend_v1` is immutable. Corrected results were written once to `data/research/m17_1_review_corrections_v1`. The runner refuses the original path and refuses every existing output directory, including an empty one.

The default command now fails safely because the corrected namespace is populated. To reproduce into another explicitly versioned, previously nonexistent directory:

```text
.venv\Scripts\python.exe -m app.research.absolute_trend --output-dir data\research\m17_1_review_reproduction_<unique-label>
```

The command performs no network access or market-data download. Never reuse an existing output path.

## Bootstrap interpretation

Daily marks are exact UTC midnight boundaries on a continuous grid. Pairing requires identical unique dates and finite values; no `dropna` alignment is allowed. Sequential block sums and counts, including partial final blocks, are exported. `bootstrap_positive_fraction` is the fraction of resampled estimates above zero, not a posterior probability that an edge exists. All reported confidence intervals cover the full reused history. The roughly 24-day mean holding period limits confidence in the 14-day primary block choice; 7- and 28-day values remain sensitivity checks, not significance-selected alternatives.
