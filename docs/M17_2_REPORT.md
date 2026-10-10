# M17.2 — Risk-Calibrated Passive Benchmark Comparison

## Result

**Status: WEAK.** The unchanged Donchian 120/60 strategy produced a positive result on the reused Test and outperformed the train-risk-calibrated passive portfolio there, including under doubled costs. The primary paired 28-day bootstrap confidence interval still crosses zero, so the frozen `PROMISING_INCREMENTAL_EVIDENCE` rule is not satisfied.

All Validation, Test, and walk-forward periods are reused research periods. None is an untouched holdout.

## Frozen method

- Source revision: `5f5983a251752bd6a620d139b450360df70b2d19`
- Research lineage revision: `c497d40e4cbf4c370e0746e540082ded8f093757`
- Exact tested patch bundle SHA-256 (module + protocol + tests, path-delimited): `3854ae2e13fd3e67208c3d3d43df9b2fb10292601f59debaf4e0864f03e197af`
- Frozen protocol SHA-256: `7598fa6f8e3633a2650ad17461f8c9a543480f1215b5be98e2ea2d298adccf6c`
- Universe: BTC, ETH, SOL, BNB, XRP, LINK, DOGE against USDT; synchronized 4h history.
- Strategy: unchanged M17.1R Donchian 120/60, seven isolated equal sleeves, next-open execution, terminal liquidation.
- Passive benchmark: equal sleeves bought once at the evaluation start and sold once at the terminal close; no rebalance or transfers; cash yield zero.
- Calibration: `lambda = min(1, sigma_strategy_train / sigma_buyhold_train)` using synchronized daily net returns and sample standard deviation (`ddof=1`) from prior Train only.
- Base costs: 4 bps commission plus 5 bps slippage per side. Stress costs are doubled and reuse the base-cost lambda without recalibration.
- Primary endpoint: exact-date mean daily net-return difference, Strategy minus calibrated passive.
- Bootstrap: paired non-overlapping block sums/counts, 5,000 repetitions, seed 42; 28 days primary, 14/56 days sensitivity.

The main Train calibration used 639 daily observations and produced:

| Calibration | Strategy daily sigma | Buy-and-hold daily sigma | Lambda |
|---|---:|---:|---:|
| Main Train | 0.017556 | 0.029636 | 0.592390 |
| Fold 1 Train | 0.019012 | 0.027490 | 0.691582 |
| Fold 2 Train | 0.018415 | 0.029080 | 0.633253 |
| Fold 3 Train | 0.019696 | 0.030298 | 0.650081 |

The one main-Train lambda was reused unchanged for Validation and Test. Each walk-forward evaluation used only its own earlier expanding Train interval. Mutating candles after a calibration boundary is covered by a regression test and does not change the resulting lambda.

## Primary reused Test

Period: 2025-05-27 12:00 UTC through 2025-12-31 20:00 UTC. All values include base execution costs.

| Policy | Net return | Annualized return | Daily vol | Max drawdown | Avg exposure | Turnover | Costs | Trades |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Strategy | +3.4846% | +5.8886% | 1.3398% | -19.5066% | 27.6560% | 6,281.18 | 5.6531 | 22 |
| Full buy-and-hold | -14.9384% | -23.6830% | 3.1829% | -42.7018% | 99.8476% | 1,850.56 | 1.6655 | 7 |
| Calibrated passive | -8.8494% | -14.3397% | 1.9670% | -28.8103% | 60.6196% | 1,096.25 | 0.9866 | 7 |
| Cash | 0.0000% | 0.0000% | 0.0000% | 0.0000% | 0.0000% | 0.00 | 0.0000 | 0 |

The primary mean daily difference was `+0.0004706`. Its 28-day paired bootstrap 95% interval was `[-0.0015752, +0.0020235]`, with 71.58% positive bootstrap samples. The interval crosses zero.

Under doubled costs, Test strategy return was `+2.9574%`, calibrated passive return was `-8.9400%`, and the paired mean daily difference remained positive at `+0.0004495`. The 28-day interval still crossed zero: `[-0.0016082, +0.0020127]`.

## Temporal stability

The base-cost 28-day point effects were:

| Window | Mean daily difference | 95% interval | Positive bootstrap fraction |
|---|---:|---:|---:|
| Validation | +0.0005605 | [-0.0011709, +0.0018908] | 76.42% |
| Fold 1 | -0.0005215 | [-0.0018640, +0.0008829] | 22.50% |
| Fold 2 | +0.0000493 | [-0.0017428, +0.0023157] | 48.52% |
| Fold 3 | +0.0004248 | [-0.0022013, +0.0029566] | 62.66% |

Two of three walk-forward point effects were positive, but Fold 1 was negative and every interval crossed zero. The evidence is therefore temporally mixed and statistically uncertain.

## Attribution and risk

On the reused Test, strategy sleeve contributions in USDT were: BTC `-13.14`, ETH `-49.13`, SOL `+15.29`, BNB `+37.49`, XRP `+14.77`, LINK `+31.71`, DOGE `-2.14`. The largest absolute contribution represented 30.02% of total absolute sleeve contributions. Results were not driven by a single profitable sleeve, but ETH was a material detractor.

The strategy had lower realized daily volatility and a smaller absolute maximum drawdown than the calibrated passive benchmark on the reused Test. It also used much higher turnover, so its result remains more sensitive to execution assumptions even though doubled-cost point estimates stayed positive.

The full-history continuous path is retained separately as descriptive output. The main-Train lambda shown on that full period is explicitly non-causal before the calibration end and is excluded from the decision.

## Decision rule

Six of seven frozen criteria passed: positive Test strategy return, two positive fold point effects, positive doubled-cost Test effect, positive doubled-cost strategy return, no higher Test volatility, and no worse Test drawdown. The required primary 28-day CI lower bound was not positive. The final status is therefore **WEAK**.

## Verification

- Environment: WSL2, Ubuntu 26.04.1 LTS, Linux Python 3.14.4.
- Scientific packages: NumPy 2.5.3, pandas 3.0.6, SciPy 1.18.1, scikit-learn 1.9.1, XGBoost 3.4.1.
- Targeted M17.2 tests: `9 passed` in Windows and `9 passed` in WSL.
- Full WSL command: `.venv/bin/python -m pytest -q`
- Full WSL result: `308 passed, 3 warnings in 24.44s`.
- Warnings: existing deprecations from `python-binance`/`websockets`; no test skips or weakened assertions were added.
- `pip check`: no broken requirements.
- Frozen fixture verification: all 55 expected file sizes and SHA-256 hashes matched.
- Final research artifact verification: all 14 entries in the output artifact manifest matched after transfer to the Windows checkout.
- Windows Smart App Control was not changed. Oracle, servers, trading processes, `.env`, SQLite, production logic, frozen artifacts, and dependency versions were not changed.

## Artifacts and limitations

The immutable output is under `data/research/m17_2_risk_calibrated_benchmark_v1`. It contains the frozen protocol copy, calibration table, window metrics, exact daily returns, paired effects and block summaries, signals/fills/trades, portfolio ledger, symbol attribution, source hashes, summary, and artifact hashes.

This is a small set of reused historical windows, and block-bootstrap intervals are wide. Calibration targets daily volatility only; it does not equalize tail risk, intraday risk, liquidity capacity, or market impact. Passive allocation is fixed at entry, so subsequent asset weights drift. Historical OHLC execution with fixed slippage cannot represent order-book impact. These findings support further observation, not a production or trading decision.
