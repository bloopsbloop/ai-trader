# M17.1R — Temporal Accounting and Reproducibility Corrections

## Verdict

All four review defects were confirmed and corrected. Trading rules and trading events did not change: 248 signals, 248 fills, 124 completed trades, total costs, turnover, and final PnL are identical to M17.1. Reporting boundaries, timestamps, daily returns, and bootstrap inputs changed as intended.

The corrected research status remains **WEAK** under the original frozen criteria. Full-history economics are positive and survive doubled costs, but the corrected reused Test remains negative at **-5.44%**, and every full-history confidence interval against cash still crosses zero.

## Confirmed defects and corrections

1. Window returns used the first post-candle mark as the denominator and omitted the first increment. Returns now use exact pre-window and post-final-candle capital boundaries.
2. Close equity and signals were labeled with candle open time. `bar_open_time`, `bar_close_time`, `signal_available_time`, `fill_time`, and `equity_mark_time` are now explicit.
3. Chunk state did not carry Donchian history. It now carries the rolling 120 highs/lows and exact pending fill state.
4. The runner could overwrite frozen results. It now rejects the original namespace and every existing destination before writing.

An initial equity mark was also added before any fill. This captures first-day buy-and-hold costs and includes initial-capital drawdown.

## Trading invariance

| Item | Original | Corrected | Reason |
|---|---:|---:|---|
| Signals | 248 | 248 | Same Donchian rules; only timestamps corrected |
| Fills | 248 | 248 | Same next-open prices and quantities |
| Completed trades | 124 | 124 | Identical trade ledger |
| Base ending equity | $2,247.75 | $2,247.75 | Trading path unchanged |
| Base net return | +124.78% | +124.78% | Trading path unchanged |
| Base costs | $49.66 | $49.66 | No fee duplicated or removed |
| Base turnover | $55,173.17 | $55,173.17 | Fill notionals unchanged |
| Max drawdown | -28.45% | -28.45% | Existing trough was already below initial capital |

The corrected annualized return is 31.619% versus 31.625% because the economic path now ends at the actual final close, four hours later. Mean daily return is 0.09181% versus 0.09198% because daily marks now use exact close-time midnight boundaries and include the first day.

## Continuous windows

The source-candle periods are unchanged. Positions carry across boundaries.

| Window | Original | Corrected | Change |
|---|---:|---:|---:|
| Train | +50.756% | +50.756% | 0.000 pp |
| Validation | +58.038% | +57.684% | -0.354 pp |
| Reused Test | -6.008% | **-5.445%** | +0.564 pp |

The product of the three corrected return factors equals the full-path factor to floating-point precision (`2.2477508012641207` versus `2.2477508012641210`). No interval is lost or counted twice.

Corrected continuous fixed windows are +45.390%, -11.145%, and +4.856%. Original values were +45.390%, -10.696%, and +4.885%. The first window did not change because its omitted first increment was flat.

Restarted-from-cash diagnostics remain separate and unchanged: +62.247%, -9.895%, and +6.425%. Their unchanged PnL confirms that the correction concerns continuous boundary accounting rather than strategy tuning.

## Bootstrap

All intervals below cover the **full reused history**, not the Test window. Settings remain 5,000 repetitions and seed 42.

| Comparison | Block | Mean daily difference | Corrected 95% CI | Positive fraction |
|---|---:|---:|---:|---:|
| Strategy − cash | 14d | +0.09181% | [-0.04047%, +0.23789%] | 90.82% |
| Strategy − cash | 7d | +0.09181% | [-0.02135%, +0.21030%] | 94.40% |
| Strategy − cash | 28d | +0.09181% | [-0.03495%, +0.24504%] | 90.68% |
| Strategy − buy-and-hold | 14d | -0.06169% | [-0.16257%, +0.04790%] | 13.42% |

The original 14d cash interval was [-0.04473%, +0.24316%] with a 90.04% positive fraction. The small change comes from corrected daily boundaries and initial capital. `bootstrap_positive_fraction` is descriptive resampling frequency, not the probability of a true edge. Mean holding is 584 hours (24.3 days), longer than the primary 14-day block, so serial-dependence uncertainty remains material.

## Risk and exposure

| Metric | Strategy | Buy-and-hold |
|---|---:|---:|
| Net return | +124.78% | +198.92% |
| Annualized return | +31.62% | +44.98% |
| Average exposure | 40.91% | 99.97% |
| Maximum exposure | 100.00% | 100.00% |
| Mark-to-market drawdown | -28.45% | -52.97% |
| Drawdown duration | 1,605 marks (~267.5 days) | 2,077 marks (~346.2 days) |
| Commission + slippage | $49.66 | $3.59 |
| Turnover | $55,173.17 | $3,989.97 |

Lower raw return than fully invested buy-and-hold is not by itself a failure because exposure and drawdown differ substantially. This correction does not establish risk-adjusted superiority and intentionally adds no risk-matched benchmark.

SOL, XRP, and DOGE contributed $1,080.68, or 86.61% of total net profit. Six of seven sleeves remain profitable; BNB contributed -$10.22. No symbol was removed.

## Data and reproducibility evidence

All seven common histories contain 6,576 unique UTC candles from 2023-01-01 00:00 through 2025-12-31 20:00, with finite positive OHLC, valid bounds, identical grids, and zero 4h gaps. No OHLCV file was changed or repaired.

Synthetic regression tests cover close timestamps, initial equity and first-day costs, exact window boundaries, geometric reconciliation, position carry without fees, entry/exit immediately after chunk boundaries, pending orders across a boundary, a chunk shorter than lookback, a boundary gap, terminal accounting, overwrite protection, strict daily alignment, partial bootstrap block counts, data validation, and frozen artifact hashes. Continuous and chunked signals, fills, trades, equity marks, cash, and quantity match exactly.

A deliberate second run failed with `FileExistsError`; hashes of both old and corrected namespaces were unchanged afterward.

## Test status

- M17.1R regression suite: **17 passed**.
- Full `pytest -q`: attempted twice, but collection is blocked by Windows Application Control error **4551** on scikit-learn `_criterion.cp314-win_amd64.pyd`; 11 ML test modules cannot import.
- A broad run excluding those 11 modules produced **192 passed and 4 failed**; all four failures reach the same blocked scikit-learn DLL through AI dashboard/shadow imports.

The failure is an execution-policy block in the local `.venv`, confirmed by `ctypes.WinDLL` and `net helpmsg 4551` (“An Application Control policy has blocked this file”). It is not a failing M17.1R assertion. No dependency was reinstalled or security policy changed as part of this milestone.

## Scope confirmation

The original M17.1 namespace, frozen protocol, OHLCV, and all recorded M16.2 artifacts retain their baseline hashes. Batch 1/2/3, SQLite, paper portfolios, runner, RiskManager, Telegram, dashboard, `.env`, production trading logic, Oracle, and server files were not changed. No data was downloaded, no live order was sent, and no commit or push was created.
