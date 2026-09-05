"""Run the evaluation and report what happened.

    python eval/run_eval.py                  # every case, three repeats
    python eval/run_eval.py --split holdout  # only the held-out cases
    python eval/run_eval.py --repeats 1      # fast pass

The report separates scenario-level results (did the case pass every time?) from
run-level results (how many individual runs passed), because a case that passes two
times in three is not a case that passes.
"""

from __future__ import annotations

import argparse
import copy
import json
import statistics
import sys
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from cases import CASES, by_split  # noqa: E402
from harness import CaseResult, run_case  # noqa: E402

OUT_DIR = HERE / "results"


def run(split: str, repeats: int, provider: str) -> list[CaseResult]:
    results: list[CaseResult] = []
    selected = by_split(split)
    for index, case in enumerate(selected, start=1):
        for attempt in range(repeats):
            # Cases carry mutable fields the runner consumes; give each run its own copy.
            result = run_case(copy.deepcopy(case), model_provider=provider)
            results.append(result)
            mark = "pass" if result.passed else "FAIL"
            suffix = f" run {attempt + 1}/{repeats}" if repeats > 1 else ""
            print(f"[{index:>2}/{len(selected)}] {case.id:<8} {case.split:<8} {mark}{suffix}")
            if not result.passed:
                for problem in result.failures + result.invariant_failures:
                    print(f"          - {problem}")
    return results


def summarise(results: list[CaseResult]) -> dict:
    by_case: dict[str, list[CaseResult]] = defaultdict(list)
    for result in results:
        by_case[result.case_id].append(result)

    scenarios_passed = sum(1 for runs in by_case.values() if all(r.passed for r in runs))
    runs_passed = sum(1 for r in results if r.passed)

    resolvable = [r for r in results if _expects_assignment(r.case_id)]
    resolvable_ok = [r for r in resolvable if r.assigned is not None]

    escalation_cases = [r for r in results if r.category in {"constraint", "adversarial"} and _expects_escalation(r.case_id)]
    escalation_ok = [r for r in escalation_cases if r.human_requests >= 1 and r.assigned is None]

    ordinary = [r for r in results if r.category == "ordinary"]
    interruptions = [r.human_requests for r in ordinary]

    violations = [problem for r in results for problem in r.invariant_failures]

    return {
        "runs": len(results),
        "scenarios": len(by_case),
        "scenarios_passed": scenarios_passed,
        "runs_passed": runs_passed,
        "resolvable_completion": {
            "numerator": len(resolvable_ok),
            "denominator": len(resolvable),
            "pct": _pct(len(resolvable_ok), len(resolvable)),
        },
        "correct_escalation": {
            "numerator": len(escalation_ok),
            "denominator": len(escalation_cases),
            "pct": _pct(len(escalation_ok), len(escalation_cases)),
        },
        "policy_violations": len(violations),
        "policy_violation_detail": sorted(set(violations)),
        "interruption_burden_ordinary": {
            "total_human_requests": sum(interruptions),
            "runs": len(interruptions),
        },
        "trace_completeness": {
            "numerator": sum(1 for r in results if r.trace_complete),
            "denominator": len(results),
            "pct": _pct(sum(1 for r in results if r.trace_complete), len(results)),
        },
        "messages_per_run": {
            "median": statistics.median([r.messages for r in results]) if results else 0,
            "max": max((r.messages for r in results), default=0),
        },
        "model_calls_per_run": {
            "median": statistics.median([r.model_calls for r in results]) if results else 0,
            "max": max((r.model_calls for r in results), default=0),
        },
        "wall_seconds_per_run": {
            "median": round(statistics.median([r.wall_seconds for r in results]), 3) if results else 0,
            "max": round(max((r.wall_seconds for r in results), default=0), 3),
        },
        "by_category": _by_category(results),
    }


def _by_category(results: list[CaseResult]) -> dict:
    grouped: dict[str, dict[str, int]] = defaultdict(lambda: {"runs": 0, "passed": 0})
    for result in results:
        grouped[result.category]["runs"] += 1
        grouped[result.category]["passed"] += int(result.passed)
    return dict(grouped)


def _pct(numerator: int, denominator: int) -> float:
    return round(100.0 * numerator / denominator, 1) if denominator else 0.0


def _case(case_id: str):
    return next(case for case in CASES if case.id == case_id)


def _expects_assignment(case_id: str) -> bool:
    return _case(case_id).expect.state == "confirmed" or _case(case_id).expect.assigned_count == 1


def _expects_escalation(case_id: str) -> bool:
    return _case(case_id).expect.escalation_blocker is not None


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Relay evaluation suite.")
    parser.add_argument("--split", choices=["dev", "holdout", "all"], default="all")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--provider", default="offline", help="offline or auto/bedrock")
    parser.add_argument("--json", default=str(OUT_DIR / "latest.json"))
    args = parser.parse_args()

    results = run(args.split, args.repeats, args.provider)
    summary = summarise(results)

    print()
    print("=" * 78)
    print(f"split={args.split}  repeats={args.repeats}  provider={args.provider}")
    print("=" * 78)
    print(f"scenarios passing every repeat : {summary['scenarios_passed']}/{summary['scenarios']}")
    print(f"individual runs passing        : {summary['runs_passed']}/{summary['runs']}")
    rc = summary["resolvable_completion"]
    print(f"resolvable completion          : {rc['numerator']}/{rc['denominator']} ({rc['pct']}%)")
    ce = summary["correct_escalation"]
    print(f"correct escalation             : {ce['numerator']}/{ce['denominator']} ({ce['pct']}%)")
    print(f"policy violations              : {summary['policy_violations']}")
    tc = summary["trace_completeness"]
    print(f"trace completeness             : {tc['numerator']}/{tc['denominator']} ({tc['pct']}%)")
    ib = summary["interruption_burden_ordinary"]
    print(f"human requests on ordinary runs: {ib['total_human_requests']} across {ib['runs']} runs")
    print(f"messages per run (median/max)  : {summary['messages_per_run']['median']}/{summary['messages_per_run']['max']}")
    print(f"wall seconds per run (med/max) : {summary['wall_seconds_per_run']['median']}/{summary['wall_seconds_per_run']['max']}")
    print(f"by category                    : {summary['by_category']}")
    if summary["policy_violation_detail"]:
        print()
        print("POLICY VIOLATIONS -- this blocks release:")
        for problem in summary["policy_violation_detail"]:
            print(f"  - {problem}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"summary": summary, "runs": [asdict(result) for result in results]}
    Path(args.json).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print()
    print(f"Wrote {args.json}")

    failed = summary["scenarios"] - summary["scenarios_passed"]
    return 1 if failed or summary["policy_violations"] else 0


if __name__ == "__main__":
    sys.exit(main())
