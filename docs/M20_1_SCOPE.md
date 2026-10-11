# M20.1 — Data and Execution Feasibility Research Scope

## Baseline

- Working tree baseline: commit `f3a6f99e676bb8c67c9e3f01800bb1909e4f6293` (`Milestone 19.1 - Evidence Review and Research Stop Decision`).
- M19.1 status: `NO_JUSTIFIED_NEXT_EXPERIMENT` — among the three reviewed hypotheses, current data and constraints did not support an experiment ready for justified execution. This was not a universal no-alpha conclusion.
- No repository `AGENTS.md`, separately named project brief, or Master Handoff was found in the project checkout. The corrected M19.1 evidence ledger and hypothesis review are the local research baseline for this review.

## Questions

This feasibility review considers exactly three directions:

1. Cross-venue spot price discovery used only to inform Binance Spot long-only decisions.
2. Binance Spot aggressive trade pressure and order-book depth resilience.
3. Drift after official Binance Spot listing announcements.

For each direction the review asks whether the economic mechanism is observable with point-in-time data, whether the data can be obtained with adequate provenance, whether an honest causal ordering and independent future evaluation are possible, and whether a non-colocated personal-computer setup could represent executable Binance Spot returns after all material costs.

## Permitted work

- Read existing non-Batch-3 research documentation and compact summaries only where needed to resolve evidence scope.
- Read public primary sources: official exchange/provider documentation, original research, and authors' public repositories.
- Create the seven new M20.1 analytical documents.
- Perform small synthetic scenario calculations for data volume, execution break-even, and statistical sensitivity. Every input must be labeled documented, assumed, or unknown.
- Run existing verification commands once at the end.

## Prohibited work

- No Oracle, server, live, paper, shadow, or Batch 3 access or integration.
- No `.env`, SQLite, production logic, security policy, dependency, model, parameter, prior protocol, prior artifact, or prior research-output changes.
- No market/event dataset download, live-stream connection, account registration, credential use, subscription purchase, or provider contact.
- No model training, backtest, PnL replay, parameter search, real-market economic calculation, collector, background process, or automation.
- No fourth strategy family, new milestone, git commit, or push.

## Mandatory feasibility gates

A direction can be selected only if all gates pass, or if a clearly identified resource decision is the sole unresolved dependency:

1. Observable and economically explainable hypothesis.
2. Availability of the required point-in-time data and provenance.
3. Testable temporal causality at the intended horizon.
4. Realistic Binance Spot long-only execution path including latency, fees, spread, slippage, adverse selection, and exchange filters.
5. A chronological, independent future evaluation can be reserved.
6. Operational load is acceptable on the stated local setups, or the unresolved resource dependency is explicit.

Failure of a mandatory gate cannot be offset by an attractive mechanism, large message count, or arbitrary aggregate score.

## Decision labels

- `FEASIBLE_FOR_SCOPED_DATA_PILOT`: a bounded data-quality pilot can be proposed, but is not authorized or evidence of trading edge.
- `CONDITIONAL_ON_RESOURCES`: feasibility depends on unapproved budget, runtime mode, or access.
- `NO_FEASIBLE_DIRECTION`: none passes the mandatory gates.

At most one direction may be selected. Unknown parameters remain `UNKNOWN`; scenario assumptions do not become facts.

## Stop criteria

Stop and report rather than launch work if required point-in-time fields are unavailable, event ordering cannot be audited, realistic execution cannot be represented, a fresh evaluation cannot be reserved, or the only viable data path needs an unapproved account, subscription, always-on host, or budget. Stop source collection at 12–18 substantive primary sources or earlier once the material questions are resolved. Do not select by projected pilot PnL.

## Authority boundary

M20.1 is a feasibility review. It cannot authorize a collector, prospective period, trading experiment, strategy implementation, or M20.2. Any later data collection requires a separate user decision after reviewing source terms, monetary and operational cost, completeness controls, and execution realism.

M20.1R further separates any future pilot into Stage A smoke capture and Stage B data-quality capture. Neither is authorized by these documents, and Stage A cannot roll into Stage B automatically.
