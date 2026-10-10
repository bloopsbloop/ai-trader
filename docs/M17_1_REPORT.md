# M17.1 — Absolute Trend Strategy Economic Baseline

## Conclusion

**Status: WEAK.** The fixed 4h Donchian 120/60 hypothesis made money over the reused 2023–2025 history, including under doubled costs, but it does not meet the frozen promotion criteria. The reused Test period returned **-6.01%**, the primary 14-day paired bootstrap interval versus cash includes zero, and the strategy underperformed equal-sleeve buy-and-hold.

This is a research result only. It does not change Batch 1/2/3, paper trading, live trading, SQLite, Telegram, dashboard, or deployment behavior.

## Duplicate audit

Decision: **NOT_DUPLICATE**. Existing Batch 2 Donchian experiments used different symbol-specific lookbacks (10/5 through 55/20), 15m/1h/4h data, 15% exposure, and same-close backtest fills. M17.1 uses one frozen 120/60 rule, seven independent equal sleeves, and next-consecutive-open execution.

## Frozen protocol

- Universe: BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT, LINKUSDT, DOGEUSDT.
- Common data: 2023-01-01 00:00 UTC through 2025-12-31 20:00 UTC; 6,576 synchronized 4h candles per symbol.
- Economic path: 2023-01-21 00:00 UTC through 2025-12-31 20:00 UTC after 120 warmup bars.
- Entry: close above the maximum high of the previous 120 completed candles.
- Exit: close below the minimum low of the previous 60 completed candles.
- Execution: next open only when the next candle is exactly four hours later. A pending order expires across a gap.
- Portfolio: $1,000 split once into seven independent $142.8571 sleeves. No cash transfers, rebalancing, leverage, shorts, or pyramiding.
- Base costs: 0.04% commission and 0.05% slippage per side. Stress costs are doubled.
- Open terminal positions are liquidated at the final close with the applicable costs.
- Protocol SHA-256: `19b840a5ad7c618f745321f903d3b8bfab912c9d3eb2da2eb00dfd90b1f1095b`.

The complete immutable definition, hashes, split boundaries, and limitations are in `docs/M17_1_PROTOCOL.json`.

## Aggregate economics

| Scenario | Net return | Ending equity | Annualized | Max drawdown | Trades | Costs |
|---|---:|---:|---:|---:|---:|---:|
| Zero-cost decomposition | +131.70% | $2,317.04 | +32.99% | -27.87% | 124 | $0.00 |
| Base costs | +124.78% | $2,247.75 | +31.62% | -28.45% | 124 | $49.66 |
| Doubled costs | +118.06% | $2,180.55 | +30.28% | -29.04% | 124 | $97.52 |
| Buy-and-hold, base costs | +198.92% | $2,989.18 | — | — | 7 | same cost model |

Base-cost turnover was $55,173.17 (55.17x initial capital), mean holding time was 584.0 hours, median holding time was 470.0 hours, average exposure was 40.92%, and the mark-to-market maximum drawdown lasted 1,605 four-hour observations.

## Chronological results

Continuous positions are carried across every boundary. Returns are computed from boundary mark-to-market equity.

| Window | Net return |
|---|---:|
| Train | +50.76% |
| Validation | +58.04% |
| Reused Test | **-6.01%** |

The three frozen continuous evaluation windows returned +45.39%, -10.70%, and +4.89%. Separate restarted-from-cash diagnostics returned +62.0%, -10.0%, and +6.0%, respectively. These restarted runs are secondary diagnostics and do not replace the continuous primary path.

## Paired bootstrap

The primary synchronized daily block bootstrap uses 14-day blocks, 5,000 repetitions, and seed 42.

| Comparison | Mean daily difference | 95% interval | P(difference > 0) |
|---|---:|---:|---:|
| Strategy minus cash | +0.0920% | [-0.0447%, +0.2432%] | 90.0% |
| Strategy minus buy-and-hold | -0.0634% | [-0.1670%, +0.0429%] | 13.6% |

Seven-day and 28-day block sensitivities are preserved in `bootstrap.csv`. Neither the reused Test result nor the primary interval supports promotion.

## Symbol contribution

Six of seven sleeves were profitable over the full path. SOL, XRP, and DOGE contributed most; BNB lost $10.22. Removing the strongest sleeve still leaves positive aggregate contribution, so the total is not solely one-symbol driven. This does not cure the negative Test period or weak bootstrap evidence.

## Leakage and accounting controls

Tests cover exclusion of the current candle from both channels, invariance of past signals to future values, the 120-bar warmup, signal-to-next-open ordering, pending-order cancellation across gaps, nonnegative cash and no leverage, two-sided cost accounting, buy-and-hold accounting, mark-to-market drawdown, terminal liquidation, carried state, and deterministic paired bootstrap sampling.

## Limitations

- This is a fixed-universe survivorship study on history already used by prior project research, not untouched confirmation.
- Historical spread, order-book depth, exchange filters, minimum notional, and quantity rounding are unavailable.
- Slippage is a fixed estimate, and 4h close marks cannot represent intrabar or tick-level risk.
- Buy-and-hold benefited from the strong crypto regime and also carried materially larger exposure and drawdown; the comparison is economic, not risk matched.
- No parameter search was performed. The WEAK decision applies to this one frozen 120/60 specification.

## Reproduction

Run locally from the project root:

```text
.venv\Scripts\python.exe -m app.research.absolute_trend
.venv\Scripts\python.exe -m pytest -q
```

No data download is performed by the research command.
