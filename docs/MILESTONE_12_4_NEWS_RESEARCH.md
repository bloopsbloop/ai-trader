# Milestone 12.4 — ML WITH_NEWS Research Comparison

## Research boundary

This experiment is local and research-only. It does not overwrite
`ML_V1_MARKET`, modify `ml_v1_market_forward_001`, or connect a news model to
Batch 3. The news archive readiness remains `PARTIAL`.

## Fair comparison

MARKET_ONLY and WITH_NEWS_FULL use the same 78,617 sample rows, symbols, target,
four-hour horizon, chronological splits, costs, seed 42, model family, and fixed
HistGradientBoosting parameters. The market feature prefix and targets were
verified row-for-row before training.

The fixed parameters are learning rate 0.05, 140 iterations, 15 leaf nodes,
minimum 50 samples per leaf, L2 regularization 1.0, balanced sample weights, and
disabled early stopping. Preprocessing is fitted on train only. Threshold and
optional sigmoid calibration are selected using validation only. Test is not
used for feature selection, preprocessing, calibration, threshold selection, or
status criteria design.

After train-only filtering, MARKET_ONLY has 59 usable features. WITH_NEWS_FULL
has the same 59 market features plus 82 usable news features, for 141 total.
Eighteen of the 100 candidate news features are removed as constant, duplicate,
or otherwise unusable on train.

## Test results

| Variant | ROC AUC | PR AUC | Precision | Recall | Brier | Net return | Trades | Profit factor | Drawdown |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| MARKET_ONLY | 0.5750 | 0.4208 | 0.5281 | 0.0330 | 0.2272 | -0.88% | 136 | 0.911 | 1.94% |
| WITH_NEWS_FULL | 0.5735 | 0.4203 | 0.5371 | 0.0216 | 0.2273 | +0.65% | 91 | 1.094 | 0.97% |
| WITH_NEWS_FILTERED | 0.5741 | 0.4189 | 0.5394 | 0.0241 | 0.2272 | -0.95% | 108 | 0.891 | 1.80% |

The coverage-filtered model is also compared with a market-only model trained on
the same 78,401-row subset. That market reference has Test ROC AUC 0.5764, PR
AUC 0.4210, and net return -0.69%; filtering therefore does not demonstrate a
news advantage.

All primary variants selected threshold 0.50 on validation. MARKET_ONLY produced
647 validation signals and 215 simulated trades; WITH_NEWS_FULL produced 480
signals and 162 trades; WITH_NEWS_FILTERED produced 512 signals and 170 trades.
The filtered variant had positive validation return, while full news and market
were negative, which did not persist consistently into Test.

## Walk-forward

News improved both ROC AUC and PR AUC in only fold 2. In fold 1 ROC improved but
PR declined; in fold 3 ROC declined while PR improved. News trading return was
better in all three folds, but the third-fold result used only 35 trades.

| Fold | Market ROC / PR | News ROC / PR | Market return | News return | Market/news trades |
| --- | --- | --- | ---: | ---: | ---: |
| 1 | 0.5736 / 0.4420 | 0.5747 / 0.4416 | -0.52% | +0.53% | 184 / 178 |
| 2 | 0.5632 / 0.4529 | 0.5649 / 0.4542 | -5.92% | -0.91% | 358 / 378 |
| 3 | 0.5732 / 0.4138 | 0.5724 / 0.4142 | -2.94% | +0.05% | 157 / 35 |

## Paired and segmented evidence

On 14,172 of 15,780 Test rows, the absolute probability change is below 0.02.
There are 1,490 rows in the 0.02–0.05 bucket, 113 in 0.05–0.10, and only 5 at
or above 0.10. Of the 1,041 rows where news raises probability by at least 0.02,
only 292 contain an active eligible article. Of the 567 rows where it lowers
probability, only 7 contain active news. This indicates that coverage/activity
structure drives much of the model difference.

On the 842 news-active Test rows, WITH_NEWS_FULL has lower ROC AUC (0.6144 versus
0.6335) and PR AUC (0.4578 versus 0.4765). Both strategies have too few active
segment trades for a reliable trading conclusion. On the 14,938 inactive rows,
classification is effectively unchanged, while news emits fewer trades.

BTC and ETH both have worse news-model ROC/PR metrics. SOL has no active eligible
news in Test, so its result cannot measure event value. Event samples are also
sparse: upgrade and hack/exploit each have 48 rows and are reported as
`NOT_ENOUGH_DATA`; regulation, ETF, exchange, and macro have none. The `other`
category has 192 rows but neither model emits a trade in that group.

## Importance and ablation

The highest news permutation importance is `coverage_days_recent` at 0.00053,
followed by `mean_importance_72h` at 0.00032 and `neutral_count_72h` at 0.00008.
Seventy-four of 82 usable news features have effectively zero validation
importance. Importance is predictive association, not causality.

Counts/coverage-only ablation has Test ROC AUC 0.5727, PR AUC 0.4179, and net
return -0.86%. Full news improves trading outcome relative to this ablation but
does not improve classification over market-only.

## Conclusion

Status: `MIXED`.

The full news model improves simulated Test return and drawdown, and its trading
result is better in each walk-forward fold. It slightly worsens Test ROC AUC and
PR AUC, performs worse on news-active rows, and most probability changes occur
without active news. Current evidence does not demonstrate reliable incremental
predictive value from actual news events. It is not a production-readiness claim.

Reports are under `data/ml/reports/news_research/`. The separate artifact is
under `data/models/ml_v1_news_research/` and carries `research_only=true`,
`news_archive_readiness=PARTIAL`, and `not_for_batch3_control=true`.
