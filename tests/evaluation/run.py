"""Run the evaluation dataset and print a scorecard; optionally rewrite the golden file.

    python -m tests.evaluation.run                 # scorecard
    python -m tests.evaluation.run --json          # machine-readable
    python -m tests.evaluation.run --update-golden # after a reviewed behaviour change

No API key and no model network call: scripted model, in-memory checkpoints, the real ERP
read path on the InvenLogic TEST replica (tests/erp_db.py: TEST_ERP_ADMIN_URL and
TEST_ERP_READER_URL must be set). Snapshots never contain this run's ERP ids.
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

GOLDEN = Path(__file__).parent / "golden" / "golden_snapshots.json"


def evaluate() -> list[dict[str, Any]]:
    from tests.evaluation.cases import CASES
    from tests.evaluation.harness import check, run_case

    rows = []
    for case in CASES:
        observation = run_case(case)
        rows.append(
            {
                "id": case.id,
                "category": case.category,
                "problems": check(case, observation),
                "snapshot": observation.snapshot(),
            }
        )
    return rows


def write_golden(rows: Sequence[dict[str, Any]]) -> None:
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    data = {row["id"]: row["snapshot"] for row in rows}
    GOLDEN.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--update-golden", action="store_true")
    args = parser.parse_args(argv)
    logging.getLogger("agentic_platform").setLevel(logging.ERROR)
    rows = evaluate()
    if args.update_golden:
        write_golden(rows)
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
    else:
        by_category: Counter[str] = Counter()
        passed: Counter[str] = Counter()
        for row in rows:
            by_category[row["category"]] += 1
            passed[row["category"]] += not row["problems"]
            mark = "PASS" if not row["problems"] else "FAIL"
            print(f"{mark}  {row['category']:<13} {row['id']}")
            for problem in row["problems"]:
                print(f"        - {problem}")
        print()
        for category, total in by_category.items():
            print(f"{category:<13} {passed[category]}/{total}")
        print(f"{'total':<13} {sum(passed.values())}/{len(rows)}")
    return 0 if all(not row["problems"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
