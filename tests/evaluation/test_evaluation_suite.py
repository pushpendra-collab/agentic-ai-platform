"""Phase 13: the evaluation dataset and the golden regression suite (MOCK VERIFIED).

* `test_case_meets_its_expectations`: every case's structured expectations (route,
  tools, ERP access, outcome, error category) and safety invariants hold.
* `test_case_matches_its_golden_snapshot`: the wording-free record of what happened
  (stages, routing decision, tool and business statuses, model calls, retries,
  timeouts, verification, error code/category, writes) equals the reviewed golden file.
  Replies are never compared verbatim. After a reviewed behaviour change, regenerate it
  with `python -m tests.evaluation.run --update-golden` and review the diff.

No API key and no model network call: scripted model, in-memory checkpoints, the real ERP
read path on the InvenLogic TEST replica (skipped without it; tests/erp_db.py).
"""

from __future__ import annotations

import json
from collections import Counter

import pytest

from tests.evaluation.cases import CASES, CASES_BY_ID
from tests.evaluation.harness import INVARIANTS, EvalCase, Observation, check, run_case
from tests.evaluation.run import GOLDEN

REQUIRED = {
    "general": 3,
    "business": 7,
    "follow_up": 4,
    "multi_domain": 4,
    "safety": 7,
    "reliability": 6,
}

_observations: dict[str, Observation] = {}


def observe(case: EvalCase) -> Observation:
    if case.id not in _observations:
        _observations[case.id] = run_case(case)
    return _observations[case.id]


def test_the_dataset_covers_every_required_area() -> None:
    counts = Counter(case.category for case in CASES)
    for category, minimum in REQUIRED.items():
        assert counts[category] >= minimum, (category, counts[category])
    for case in CASES:
        assert case.expect.status, case.id
        assert set(case.invariants) <= set(INVARIANTS), case.id


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_case_meets_its_expectations(case: EvalCase) -> None:
    problems = check(case, observe(case))
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_case_matches_its_golden_snapshot(case: EvalCase) -> None:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert case.id in golden, f"no golden snapshot for {case.id}: run --update-golden"
    assert observe(case).snapshot() == golden[case.id]


def test_golden_file_has_no_stale_cases() -> None:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert set(golden) == set(CASES_BY_ID)


def test_no_case_ever_writes() -> None:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert not any(snapshot["write_occurred"] for snapshot in golden.values())


def test_the_suite_is_repeatable() -> None:
    """Same case, fresh agent, same snapshot: the evaluation is deterministic."""
    for case_id in ("business_pending_bill", "reliability_transient_dependency",
                    "multi_low_stock_and_po"):  # fmt: skip
        case = CASES_BY_ID[case_id]
        assert run_case(case).snapshot() == run_case(case).snapshot()
