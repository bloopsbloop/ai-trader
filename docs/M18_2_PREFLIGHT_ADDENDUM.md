# M18.2 — Preflight Execution Clarifications

This addendum authorizes execution of the already frozen M18.1 plan. It does not modify `M18_1_EVALUATION_PROTOCOL.json`; the historical `execution_authorized: false` field correctly records that M18.1 itself did not authorize acquisition or evaluation.

## Bonferroni interpretation

For the two formal co-primary claims, marginal 97.5% two-sided bootstrap intervals provide a nominal 95% simultaneous level through Bonferroni adjustment, assuming the marginal bootstrap intervals have their nominal coverage. With only ten 28-day calendar blocks, actual finite-sample coverage is not guaranteed. This limitation must accompany any result.

The frozen quantiles remain 1.25% and 98.75%. The ordinary intervals remain 2.5% and 97.5%. Repetitions remain 5,000, seed remains 42, and block sizes remain 28 days primary with 14/56-day sensitivity.

## Provenance label

`PROMISING_INDEPENDENT_CANDIDATE` in the historical M18.1 decision document is retained only as a reference to that frozen document. M18.2 must use:

```text
PROMISING_CANDIDATE_UNCERTAIN_PROVENANCE
```

This corrects the label, not the success criteria. Every result permanently retains `HOLDOUT_PROVENANCE_UNCERTAIN`. The period must not be described as untouched, fully independent, or prospective.

## Overall decision precedence

M18.2 applies the following order exactly:

1. An acquisition, checksum, schema, timestamp, grid, hash, alignment, reconciliation, or other integrity blocker stops the evaluation and returns the corresponding blocker status.
2. If both separate positive-evidence classifications pass and the registered doubled-cost Strategy net return and Strategy-minus-calibrated-passive point effect are positive, return `PROMISING_CANDIDATE_UNCERTAIN_PROVENANCE`.
3. Otherwise, if either co-primary point estimate is nonpositive, return `NO_EVIDENCE_FOR_COMBINED_HYPOTHESIS`.
4. Otherwise return `PARTIAL_OR_INCONCLUSIVE`.

Separate A and B classifications are always reported when economic evaluation completes. `NO_EVIDENCE_FOR_COMBINED_HYPOTHESIS` does not state that both individual hypotheses are disproven.

## Execution boundary

The user request titled “AI_TRADER — M18.2: Acquisition and One-Time Frozen Evaluation” is the explicit authorization to acquire the registered official public archives and execute the registered batch once. Its captured request SHA-256 is recorded in `M18_2_EXECUTION_MANIFEST.json`.

Before economic access, the implementation and synthetic validation must be complete and the exact evaluation-code hash must be recorded. Results cannot be used to alter that code. A technical interruption may resume only from preserved acquisition/validation artifacts and must be reported. A material defect discovered after opening economic output stops the work for review; it cannot be silently corrected and replaced.
