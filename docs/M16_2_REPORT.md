# Milestone 16.2 — Native Learning-to-Rank Controlled Comparison

## Scope

M16.1/M16.1R closed an order-dependent HGB pairwise-difference implementation as a limited negative experiment. M16.2 is the final bounded test of the current ranking hypothesis using native XGBoost `rank:ndcg` on the original 86 item features against XGBoost squared-error regression. It does not change trading code and is research-only.

## Runtime and protocol

- Python 3.14.6, XGBoost 3.4.1, CPU, 4 threads.
- Installed locally with `.\.venv\Scripts\python.exe -m pip install xgboost==3.4.1 --no-deps`.
- Protocol SHA-256: `a5f66c52a0eb6661b0c41467be20af93ac224b7e741bc5f17281d9b66ebd8e7f`.
- Exactly 16 market-data fits: four on the main split and four inside each of three walk-forward folds.
- No early stopping, seed search, feature search, gates, or Test-based tuning.
- Test is a reused research holdout; eligibility remains conditioned on complete 8h outcomes.

## Verdict

Status: **WEAK**. Native ranker Test Top-1 is 21.18% versus 19.37% for matched regression, a +1.81% effect. The preregistered practical threshold was +2 percentage points. Corrected 24h block CI is [-0.15%, 3.77%] and crosses zero. Validation favored regression (19.51% vs 20.83%), and only one of three walk-forward effects was positive.

The ranker beats random, momentum, and relative-strength Top-1 baselines, but selects BTC in 74.05% of Test groups versus a 22.21% actual winner frequency. Selection evidence is therefore strongly concentrated. Frozen inference is permutation invariant after alignment: zero score or Top-1 changes for reverse order and seeds 7/42.

## Ranking metrics

| Model | Split | Top-1 | Top-2 | Top-3 | Spearman | NDCG@3 | Raw-return winner |
|---|---|---:|---:|---:|---:|---:|---:|
| ranking | train | 21.02% | 37.29% | 51.36% | 0.1556 | 0.6500 | 17.62% |
| ranking | validation | 19.51% | 34.82% | 48.16% | 0.0438 | 0.5984 | 15.69% |
| ranking | test | 21.18% | 36.58% | 50.68% | 0.0540 | 0.5925 | 16.28% |
| regression | train | 21.29% | 38.63% | 53.72% | 0.0972 | 0.6186 | 18.76% |
| regression | validation | 20.83% | 38.07% | 51.18% | 0.0277 | 0.5901 | 15.84% |
| regression | test | 19.37% | 35.45% | 49.46% | 0.0099 | 0.5786 | 14.77% |
| frozen_m15_3 | test | 19.08% | 33.39% | 47.13% | -0.0020 | 0.5736 | 14.96% |

## Walk-forward

| Fold | Objective | Config | Top-1 | Effect ranker-regressor | Net return | Closed-trade DD |
|---|---|---|---:|---:|---:|---:|
| 1 | ranking | depth5 | 16.14% | -1.07% | 0.16% | 4.64% |
| 1 | regression | depth5 | 17.22% | -1.07% | -0.47% | 7.00% |
| 2 | ranking | depth5 | 18.20% | -0.66% | -6.94% | 8.32% |
| 2 | regression | depth5 | 18.87% | -0.66% | -8.44% | 9.80% |
| 3 | ranking | depth3 | 21.69% | 2.62% | -11.09% | 11.74% |
| 3 | regression | depth3 | 19.07% | 2.62% | -8.83% | 11.10% |

## Economic diagnostic

Fixed first-phase 8h schedule, 10% equity exposure, 656 trades on Test, and 0.18% round-trip costs already included in returns. Drawdown is closed-trade equity drawdown, not mark-to-market.

| Method | Net return | PnL | Mean selected return | PF | Closed-trade DD | Included costs |
|---|---:|---:|---:|---:|---:|---:|
| ranking | -9.39% | -93.90 | -0.149% | 0.746 | 9.55% | 112.80 |
| regression | -11.71% | -117.07 | -0.188% | 0.712 | 11.98% | 112.24 |
| frozen_m15_3 | -9.33% | -93.28 | -0.148% | 0.771 | 9.34% | 113.03 |
| momentum | -10.83% | -108.34 | -0.172% | 0.805 | 11.65% | 114.04 |
| relative_strength | -10.45% | -104.53 | -0.166% | 0.811 | 10.83% | 114.32 |
| cash | 0.00% | 0.00 | nan% | nan | 0.00% | nan |

## Research conclusion

- Predictive signal: weak and temporally unstable.
- Ranking signal: above simple baselines on reused Test, but no sustained, practically significant superiority over matched regression.
- Trading edge: not demonstrated; absolute returns remain negative and cash is better.

The current 8h ranking-objective line should be frozen. This result does not justify another ranking library, hyperparameter expansion, gate, shadow mode, Batch 3 integration, or live trading.
