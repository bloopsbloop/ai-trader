from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
MANIFEST = json.loads((ROOT / "docs/M18_2_EXECUTION_MANIFEST.json").read_text(encoding="utf-8"))


def decide(*, blocker: str | None, a_positive_evidence: bool, b_positive_evidence: bool,
           a_point_positive: bool, b_point_positive: bool,
           stress_strategy_positive: bool, stress_relative_positive: bool) -> str:
    statuses = MANIFEST["overall_statuses"]
    if blocker is not None:
        return blocker
    if (a_positive_evidence and b_positive_evidence and stress_strategy_positive and
            stress_relative_positive):
        return statuses["promising"]
    if not a_point_positive or not b_point_positive:
        return statuses["no_combined"]
    return statuses["inconclusive"]


def test_blocker_has_first_precedence() -> None:
    assert decide(blocker="DATA_QUALITY_BLOCKED", a_positive_evidence=True,
                  b_positive_evidence=True, a_point_positive=True, b_point_positive=True,
                  stress_strategy_positive=True, stress_relative_positive=True) == "DATA_QUALITY_BLOCKED"


def test_both_claims_and_cost_stress_are_required_for_promising() -> None:
    expected = "PROMISING_CANDIDATE_UNCERTAIN_PROVENANCE"
    assert decide(blocker=None, a_positive_evidence=True, b_positive_evidence=True,
                  a_point_positive=True, b_point_positive=True,
                  stress_strategy_positive=True, stress_relative_positive=True) == expected
    for field in ("a_positive_evidence", "b_positive_evidence",
                  "stress_strategy_positive", "stress_relative_positive"):
        values = dict(a_positive_evidence=True, b_positive_evidence=True,
                      a_point_positive=True, b_point_positive=True,
                      stress_strategy_positive=True, stress_relative_positive=True)
        values[field] = False
        assert decide(blocker=None, **values) == "PARTIAL_OR_INCONCLUSIVE"


def test_nonpositive_point_precedes_inconclusive() -> None:
    for a_point, b_point in ((False, True), (True, False), (False, False)):
        assert decide(blocker=None, a_positive_evidence=False, b_positive_evidence=False,
                      a_point_positive=a_point, b_point_positive=b_point,
                      stress_strategy_positive=False,
                      stress_relative_positive=False) == "NO_EVIDENCE_FOR_COMBINED_HYPOTHESIS"


def test_positive_points_with_incomplete_evidence_are_inconclusive() -> None:
    assert decide(blocker=None, a_positive_evidence=True, b_positive_evidence=False,
                  a_point_positive=True, b_point_positive=True,
                  stress_strategy_positive=True,
                  stress_relative_positive=True) == "PARTIAL_OR_INCONCLUSIVE"


def test_frozen_bootstrap_and_provenance_values() -> None:
    bootstrap = MANIFEST["bootstrap"]
    assert bootstrap["ordinary_two_sided_quantiles"] == [0.025, 0.975]
    assert bootstrap["bonferroni_two_sided_quantiles"] == [0.0125, 0.9875]
    assert (bootstrap["repetitions"], bootstrap["seed"], bootstrap["primary_block_days"]) == (5000, 42, 28)
    assert MANIFEST["provenance"] == "HOLDOUT_PROVENANCE_UNCERTAIN"
    assert MANIFEST["frozen_protocol"]["execution_authorized_field_remains"] is False
