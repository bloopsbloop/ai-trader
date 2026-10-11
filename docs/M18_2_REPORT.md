# M18.2 — Acquisition and One-Time Frozen Evaluation

## Result

**Overall status: `PARTIAL_OR_INCONCLUSIVE`.** Claim A is `INCONCLUSIVE_ABSOLUTE`; Claim B is `INCONCLUSIVE_INCREMENTAL`. Both base-cost point estimates are positive, but both ordinary 95% and Bonferroni-adjusted marginal 97.5% intervals cross zero. Doubled costs make the Strategy full-window net return slightly negative, so the frozen promotion condition also fails.

**Permanent provenance: `HOLDOUT_PROVENANCE_UNCERTAIN`.** This period is not described as untouched, fully independent, or prospective. The result does not authorize paper, shadow, or live trading.

## Frozen execution

- Evaluation period: 2026-01-01 00:00 UTC inclusive through 2026-10-01 00:00 UTC exclusive.
- Universe: BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT, LINKUSDT, DOGEUSDT; synchronized Binance Spot 4h candles.
- Strategy: unchanged long-only Donchian 120/60 with next-open execution, seven isolated equal sleeves, terminal liquidation, and no inherited signal or position.
- Warm-up: exactly 120 saved 4h bars per symbol from 2025-12-12 00:00 through 2025-12-31 20:00 UTC, used only as past channel context.
- Benchmarks: zero-yield Cash, full equal-sleeve buy-and-hold, and calibrated passive with frozen `lambda = 0.5923903262274034`.
- Base costs: 4 bps commission plus 5 bps slippage per side. Stress costs: 8 bps plus 10 bps. Lambda was not recalibrated.
- Frozen M18.1 protocol SHA-256: `0997c5ba7f63351bbf10ab6271aa6d996ce9232dfefd8a792e735d57e1449f5e`.
- Evaluation source commit: `86e3379926fa7b1ab70c241c2e7e02273954704b` plus the frozen, uncommitted evaluation patch.
- Final evaluation-code SHA-256: `2adfb2193e8d5dc515fc597cf6204fffcd7760e262ffeb42494c7879531cd202`.
- Synthetic-test SHA-256: `49d23954cc031ec31d10096e8ce1e54c7f6a8490c9d886e3577f8f3dfdbf4eee`.
- Code-freeze-v2 record SHA-256: `ac9d8ca091bd095a1e74aef345876945c997ef1403a2d16a147938338cd1f1c3`.
- Preflight commit and verified remote SHA: `86e3379926fa7b1ab70c241c2e7e02273954704b` (`Milestone 18.2 - Freeze Evaluation Execution Clarifications`).
- Execution-manifest SHA-256: `8173a78bcf9b2d998ef8bec18afbfa5066bd6d182316969cfa0b602a810b6b75`.

## Acquisition and validation

All 63 registered monthly archives were acquired from the official Binance public Spot archive endpoint: seven symbols times January–September 2026. All 63 matching `.CHECKSUM` files were saved and verified; no API, fallback source, replacement period, or partial period was used. Archive payloads total 642,884 bytes. The acquisition-manifest SHA-256 is `bb2d95ea3a5b14e33c29dfbed6a534f81d147b980cfe0ea8959c6b2cf5c2a90c`.

Every archive passed the registered 12-field schema, microsecond UTC timestamp, candle-bound, nonnegative volume/trade-count, duplicate, ordering, close-time, and exact-grid checks. Each symbol has exactly 1,638 evaluation candles, from 2026-01-01 00:00 through 2026-09-30 20:00 UTC, on one common gap-free 4h grid. No bar was synthesized, forward-filled, or deleted. The normalization-manifest SHA-256 is `c7f6a18c5e2a97f93c75afc3f0f852d54fb65877a618ef4c8225168b87b2dbfb`; individual normalized-file hashes are recorded there.

The first normalization attempt stopped before any strategy simulation or economic output because converted timestamps were assigned beside same-named raw columns. The partial BTC file and attempt manifest were preserved. A narrowly scoped construction fix and explicit resume path were frozen as code-freeze v2 and tested before opening results. Strategy, protocol, costs, endpoints, and statistical methods did not change. The economic evaluation then ran once; it was not rerun.

## Full-window economics

All values include actual entry and exit costs. Initial capital is 1,000 USDT.

| Scenario | Policy | Ending equity | Net return | Mean daily return | Daily vol | Max drawdown | DD duration | Avg / max exposure | Turnover | Costs | Trades | Mean / median hold |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Base | Strategy | 1,009.3951 | +0.9395% | +0.01243% | 1.3546% | -19.6233% | 219.83 d | 39.23% / 100.00% | 10,585.83 | 9.5273 | 40 | 449.3 h / 386 h |
| Base | Full buy-and-hold | 925.0377 | -7.4962% | +0.01192% | 2.8505% | -48.8457% | 259.83 d | 99.88% / 100.00% | 1,925.01 | 1.7325 | 7 | 6,552 h / 6,552 h |
| Base | Calibrated passive | 955.5930 | -4.4407% | -0.00528% | 1.5092% | -30.6354% | 259.83 d | 52.56% / 62.72% | 1,140.36 | 1.0263 | 7 | 6,552 h / 6,552 h |
| Base | Cash | 1,000.0000 | 0.0000% | 0.00000% | 0.0000% | 0.0000% | 0 d | 0% / 0% | 0 | 0 | 0 | 0 h / 0 h |
| Doubled | Strategy | 999.1471 | -0.0853% | +0.00868% | 1.3542% | -20.1258% | 219.83 d | 39.23% / 100.00% | 10,531.14 | 18.9561 | 40 | 449.3 h / 386 h |
| Doubled | Full buy-and-hold | 923.3741 | -7.6626% | +0.01125% | 2.8503% | -48.8457% | 259.83 d | 99.88% / 100.00% | 1,923.31 | 3.4619 | 7 | 6,552 h / 6,552 h |
| Doubled | Calibrated passive | 954.6075 | -4.5392% | -0.00567% | 1.5084% | -30.6251% | 259.83 d | 52.54% / 62.70% | 1,139.35 | 2.0508 | 7 | 6,552 h / 6,552 h |
| Doubled | Cash | 1,000.0000 | 0.0000% | 0.00000% | 0.0000% | 0.0000% | 0 d | 0% / 0% | 0 | 0 | 0 | 0 h / 0 h |

The positive arithmetic mean for full buy-and-hold can coexist with a negative compounded full-window return; the daily endpoint is the registered mean of simple daily returns.

## Co-primary endpoints

There are exactly 273 aligned full UTC-day intervals. Daily factors reconcile each ending/starting equity ratio; the maximum absolute reconciliation error is `1.9984e-15`, below the registered `1e-12` tolerance.

| Claim | Point estimate | Ordinary 95% CI | Adjusted marginal 97.5% CI | Verdict |
|---|---:|---:|---:|---|
| A: Strategy minus Cash | +0.00012428 | [-0.00085126, +0.00173367] | [-0.00090354, +0.00222513] | `INCONCLUSIVE_ABSOLUTE` |
| B: Strategy minus calibrated passive | +0.00017712 | [-0.00112595, +0.00169746] | [-0.00128909, +0.00201133] | `INCONCLUSIVE_INCREMENTAL` |

The primary bootstrap used 5,000 repetitions, seed 42, and ten non-overlapping 28-day calendar blocks; the final block contains 21 observations. The 14-day sensitivity also crosses zero for A (`[-0.00132055, +0.00199782]`) and B (`[-0.00125318, +0.00215920]`); the 56-day sensitivity crosses zero for A (`[-0.00082658, +0.00182444]`) and B (`[-0.00073948, +0.00116200]`). Adjusted 97.5% sensitivity intervals also all cross zero. Bootstrap-positive fractions are retained in the CSV as diagnostics and are not probabilities that an edge exists.

Under doubled costs, Strategy net return is `-0.08529%`. The 28-day Strategy-minus-Cash point is `+0.00008685`, 95% CI `[-0.00089416, +0.00169189]`; Strategy-minus-calibrated-passive is `+0.00014359`, 95% CI `[-0.00116661, +0.00167386]`. Both adjusted intervals also cross zero.

## Stability and attribution

The quarter rows are descriptive slices of one continuous path, with no reset.

| Base-cost policy | Q1 return | Q2 return | Q3 return |
|---|---:|---:|---:|
| Strategy | -9.1766% | -4.4872% | +16.3596% |
| Full buy-and-hold | -27.1663% | -17.8978% | +54.6936% |
| Calibrated passive | -16.0931% | -9.2033% | +25.4310% |
| Cash | 0.0000% | 0.0000% | 0.0000% |

Base-cost Strategy sleeve contributions were BTC `+24.9984`, ETH `-5.8206`, SOL `-16.2102`, BNB `+3.6030`, XRP `-11.1368`, LINK `+10.8108`, and DOGE `+3.1507` USDT, totaling `+9.3951`. Under doubled costs they were BTC `+23.4944`, ETH `-7.2927`, SOL `-17.7960`, BNB `+2.0297`, XRP `-12.5517`, LINK `+9.1601`, and DOGE `+2.1032`, totaling `-0.8529`.

The period is economically unstable across quarters: losses in Q1 and Q2 were recovered only by a strong Q3. The Strategy used much less average exposure and had a substantially smaller drawdown than both passive policies, but its turnover was much higher and its modest base-cost gain disappeared under doubled costs.

## Verification and preservation

- Targeted frozen-evaluation tests: `14 passed in 0.72s` in WSL2.
- Full command: `.venv/bin/python -m pytest -q -p no:cacheprovider`.
- Full result: `330 passed, 0 failed, 0 skipped, 3 warnings in 25.01s`.
- Warnings: existing `python-binance` / `websockets` deprecations.
- Environment: WSL2 Ubuntu 26.04.1 LTS; Python 3.14.4; NumPy 2.5.3; pandas 3.0.6; SciPy 1.18.1; scikit-learn 1.9.1; XGBoost 3.4.1; pytest 9.1.1.
- `pip check`: no broken requirements.
- All 55 protected fixture files matched their registered sizes and SHA-256 hashes after execution.
- All 12 files covered by the result-directory internal artifact manifest matched. The WSL-to-Windows transfer independently matched all 137 holdout files and all 14 result-directory files byte for byte.
- `git diff --check` passed. No new skips, assertion weakening, dependency changes, tuning, optimizer, alternative strategy, replacement window, or additional economic batch was used.
- Windows Smart App Control, Oracle, servers, `.env`, SQLite, production trading logic, Batch 3, and live/paper/shadow processes were not touched.

The acquisition data are in `data/holdouts/m18_1_2026_jan_sep_v1`; the immutable evaluation result is in `data/research/m18_1_frozen_strategy_evaluation_v1`. The latter contains signals, fills, trades, continuous equity, daily returns, block summaries, metrics, quarter slices, symbol attribution, source hashes, status, summary, and its internal manifest. The cross-tree manifest is `docs/M18_2_ARTIFACT_MANIFEST.json`.

## Limits

The main statistical limitation is 273 daily observations but only ten primary calendar blocks, with a 21-day final block. Bonferroni-adjusted marginal intervals give nominal simultaneous 95% coverage only if each bootstrap interval has its nominal coverage; finite-sample coverage is not guaranteed. Fixed OHLC-based slippage cannot model order-book depth, latency, rejected orders, or capacity. Provenance remains uncertain permanently. The observed result is compatible with both no durable edge and a modest positive effect, so the correct frozen scope is `PARTIAL_OR_INCONCLUSIVE`.

The accepted evaluation code, tests, freeze records, report, and compact manifest are published without the ignored market datasets or large result artifacts. No next research experiment was started as part of M18.2.
