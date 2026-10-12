# M21.1 — Historical-Data Research Feasibility Scope

## Frozen starting point

- Source checkpoint: `0b9daa24f9b4e9dc8a2146512a2b6dfda0d574d5` on `main`.
- M20.5 remains accepted as `BLOCKED_BY_RESOURCES`; Stage B and another Stage A are outside this milestone.
- Prior protocols, reports, manifests, datasets and research artifacts are read-only evidence.

## Question

Determine whether at most one methodologically justified next experiment exists that can use historical data, run locally without a continuous connection, test a genuinely different economic mechanism, and retain an honest path to evaluation outside data already used to choose the hypothesis.

## Method fixed before review

1. Summarize the existing evidence without reinterpreting missing data as a negative result.
2. Review no more than three economically distinct candidate mechanisms.
3. Use at most twelve substantive primary sources to establish data availability, timestamps, coverage, terms and known biases. Separate documented facts from inferences and unknowns.
4. For each candidate require a concrete data source, feasible Binance Spot long-only execution, falsifiable economic claim, chronological evaluation design, realistic costs and an evaluation period with stated provenance.
5. Select at most one candidate without an additive score. The allowed decisions are `JUSTIFIED_FOR_PREREGISTRATION`, `CONDITIONAL_ON_DATA_ACCESS`, and `NO_JUSTIFIED_EXPERIMENT`.

If a candidate is selected, the output is only a proposed experiment for later review. M21.1 does not preregister, acquire data, train, backtest, calculate PnL, choose parameters or execute the experiment.

## Boundaries

Local documentation and code inspection plus public documentation and scientific-literature reading are allowed. No collector, Stage A/B, market stream, dataset download, account registration, paid access, API key, Batch 3, SQLite, `.env`, Oracle/server action, trading process, model fitting, backtest, return/PnL calculation, dependency/security change, commit or push is allowed.

## Completion checks

The required documents must distinguish completed negative, completed uncertain and unperformed work; identify already viewed periods; state material unknowns; contain valid local links; preserve protected hashes; pass `git diff --check`; and be followed by one full pytest run in the existing WSL environment after the final document changes.
