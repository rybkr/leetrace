from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from server.sandbox import RUNNER_SCRIPT  # noqa: E402

PROBLEMS_DIR = PROJECT_ROOT / "problems"
INDEX_PATH = PROBLEMS_DIR / "index.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "_problems"
DEFAULT_WORKERS = 25
DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_MEMORY_MB = 256
DEFAULT_FAIL_RATE_THRESHOLD = 0.15
DEFAULT_AGGREGATE_PATHS = [
    PROJECT_ROOT
    / "codex_problem_repair_runs"
    / "smoke_single_easy_v2"
    / "aggregate.json",
    PROJECT_ROOT / "codex_problem_repair_runs" / "pilot_20_each_v1" / "aggregate.json",
    PROJECT_ROOT
    / "codex_problem_repair_campaigns"
    / "full_run_gpt54_campaign"
    / "round_01"
    / "aggregate.json",
    PROJECT_ROOT
    / "codex_problem_repair_runs"
    / "remaining_mediums_and_holdouts_gpt54_high_fast_v2"
    / "aggregate.json",
    PROJECT_ROOT
    / "codex_problem_repair_runs"
    / "holdout_followup_v1"
    / "medium_holdouts"
    / "aggregate.json",
    PROJECT_ROOT
    / "codex_problem_repair_runs"
    / "holdout_followup_v1"
    / "easy_holdouts"
    / "aggregate.json",
    PROJECT_ROOT
    / "codex_problem_repair_runs"
    / "easy_untargeted_v2"
    / "easy_untargeted"
    / "aggregate.json",
]
MANUAL_ACCEPTED_REPAIRS = [
    {
        "id": "roman-to-integer",
        "difficulty": "Easy",
        "problem_path": str((PROBLEMS_DIR / "roman-to-integer.json").resolve()),
        "solution_path": str(
            (
                PROJECT_ROOT
                / "codex_problem_repair_runs"
                / "smoke_remaining_v1"
                / "batch_001_easy"
                / "expected_solutions"
                / "roman-to-integer.py"
            ).resolve()
        ),
    }
]
UNRESOLVED_IDS = {
    "fair-candy-swap",
    "find-anagram-mappings",
    "find-indices-with-index-and-value-difference-i",
    "similar-rgb-color",
    "sort-array-by-parity",
    "numbers-with-same-consecutive-differences",
    "pancake-sorting",
    "powerful-integers",
}
TARGET_DIFFICULTIES = {"Easy", "Medium"}
_FILE_SIZE_LIMIT_BYTES = 1024 * 1024
_MAX_CHILD_PROCESSES = 0


@dataclass(frozen=True)
class ProblemSource:
    id: str
    difficulty: str
    problem_path: Path
    solution_path: Path


@dataclass
class ProblemOutcome:
    id: str
    difficulty: str
    status: str
    total_tests: int
    kept_tests: int
    failed_tests: int
    fail_rate: float
    reason: str | None
    first_suite_error: str | None
    output_path: str | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a cleaned Easy/Medium export from the repaired problem set by "
            "replaying the saved GPT solutions against the current test suites."
        )
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory to create. Default: {DEFAULT_OUTPUT_DIR}.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Number of parallel problem workers. Default: {DEFAULT_WORKERS}.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Per-subprocess wall timeout. Default: {DEFAULT_TIMEOUT_SECONDS}.",
    )
    parser.add_argument(
        "--memory-mb",
        type=int,
        default=DEFAULT_MEMORY_MB,
        help=f"Per-subprocess memory limit in MB. Default: {DEFAULT_MEMORY_MB}.",
    )
    parser.add_argument(
        "--max-fail-rate",
        type=float,
        default=DEFAULT_FAIL_RATE_THRESHOLD,
        help=(
            "Drop the whole problem when more than this fraction of tests fail. "
            f"Default: {DEFAULT_FAIL_RATE_THRESHOLD}."
        ),
    )
    parser.add_argument(
        "--include-id",
        action="append",
        default=[],
        help="Restrict the export to specific IDs. Repeatable.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional cap after filtering, useful for smoke tests.",
    )
    parser.add_argument(
        "--keep-output-dir",
        action="store_true",
        help="Do not delete the output directory first. Default behavior replaces it.",
    )
    return parser.parse_args()


def ensure_valid_args(args: argparse.Namespace) -> None:
    if args.workers < 1:
        raise ValueError("--workers must be at least 1.")
    if args.timeout_seconds < 1:
        raise ValueError("--timeout-seconds must be at least 1.")
    if args.memory_mb < 32:
        raise ValueError("--memory-mb must be at least 32.")
    if not 0 <= args.max_fail_rate <= 1:
        raise ValueError("--max-fail-rate must be between 0 and 1.")
    if args.limit is not None and args.limit < 1:
        raise ValueError("--limit must be positive when provided.")


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def build_check_function(test_cases: list[str]) -> str:
    lines = ["def check(candidate):"]
    for test_case in test_cases:
        for line in str(test_case).splitlines():
            lines.append(f"    {line}")
    return "\n".join(lines)


def load_problem_sources(include_ids: set[str] | None) -> list[ProblemSource]:
    sources: dict[str, ProblemSource] = {}

    for aggregate_path in DEFAULT_AGGREGATE_PATHS:
        if not aggregate_path.exists():
            continue
        data = json.loads(aggregate_path.read_text(encoding="utf-8"))
        for item in data.get("successful_repairs", []):
            difficulty = item.get("difficulty")
            problem_id = item.get("id")
            if difficulty not in TARGET_DIFFICULTIES or not problem_id:
                continue
            if problem_id in UNRESOLVED_IDS:
                continue
            sources[problem_id] = ProblemSource(
                id=problem_id,
                difficulty=difficulty,
                problem_path=Path(item["problem_path"]),
                solution_path=Path(item["solution_path"]),
            )

    for item in MANUAL_ACCEPTED_REPAIRS:
        problem_id = item["id"]
        if problem_id in UNRESOLVED_IDS:
            continue
        sources[problem_id] = ProblemSource(
            id=problem_id,
            difficulty=item["difficulty"],
            problem_path=Path(item["problem_path"]),
            solution_path=Path(item["solution_path"]),
        )

    if include_ids:
        sources = {
            problem_id: source
            for problem_id, source in sources.items()
            if problem_id in include_ids
        }

    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    ordered_ids = [
        entry["id"]
        for entry in index
        if entry["difficulty"] in TARGET_DIFFICULTIES and entry["id"] in sources
    ]
    return [sources[problem_id] for problem_id in ordered_ids]


def _set_limits(cpu_seconds: int, memory_bytes: int) -> None:
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        resource.setrlimit(
            resource.RLIMIT_FSIZE, (_FILE_SIZE_LIMIT_BYTES, _FILE_SIZE_LIMIT_BYTES)
        )
        resource.setrlimit(
            resource.RLIMIT_NPROC, (_MAX_CHILD_PROCESSES, _MAX_CHILD_PROCESSES)
        )
    except (ImportError, OSError, ValueError):
        pass


def run_solution(
    *,
    solution_code: str,
    entry_point: str,
    test_cases: list[str],
    preamble: str,
    any_order: bool,
    timeout_seconds: int,
    memory_mb: int,
) -> dict[str, Any]:
    payload = json.dumps(
        {
            "code": solution_code,
            "entry_point": entry_point,
            "test_cases": test_cases,
            "preamble": preamble,
            "any_order": any_order,
        }
    )
    memory_bytes = memory_mb * 1024 * 1024
    start = time.monotonic()
    try:
        process = subprocess.run(
            [sys.executable, "-c", RUNNER_SCRIPT],
            input=payload,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            preexec_fn=lambda: _set_limits(timeout_seconds, memory_bytes),
        )
    except subprocess.TimeoutExpired:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return {
            "passed": 0,
            "total": len(test_cases),
            "error": f"Time limit exceeded ({timeout_seconds}s)",
            "first_failure": None,
            "time_ms": elapsed_ms,
        }
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return {
            "passed": 0,
            "total": len(test_cases),
            "error": str(exc)[:200],
            "first_failure": None,
            "time_ms": elapsed_ms,
        }

    elapsed_ms = int((time.monotonic() - start) * 1000)
    if process.returncode != 0 and not process.stdout.strip():
        return {
            "passed": 0,
            "total": len(test_cases),
            "error": process.stderr.strip()[:200] or "Process crashed",
            "first_failure": None,
            "time_ms": elapsed_ms,
        }

    try:
        result = json.loads(process.stdout.strip())
    except json.JSONDecodeError as exc:
        return {
            "passed": 0,
            "total": len(test_cases),
            "error": f"Runner produced invalid output: {exc}",
            "first_failure": None,
            "time_ms": elapsed_ms,
        }

    result["time_ms"] = elapsed_ms
    return result


def evaluate_problem(
    source: ProblemSource,
    *,
    output_dir: Path,
    timeout_seconds: int,
    memory_mb: int,
    max_fail_rate: float,
) -> tuple[ProblemOutcome, dict[str, Any] | None]:
    if not source.problem_path.exists():
        return (
            ProblemOutcome(
                id=source.id,
                difficulty=source.difficulty,
                status="dropped",
                total_tests=0,
                kept_tests=0,
                failed_tests=0,
                fail_rate=1.0,
                reason="Problem file missing.",
                first_suite_error=None,
                output_path=None,
            ),
            None,
        )
    if not source.solution_path.exists():
        return (
            ProblemOutcome(
                id=source.id,
                difficulty=source.difficulty,
                status="dropped",
                total_tests=0,
                kept_tests=0,
                failed_tests=0,
                fail_rate=1.0,
                reason="Expected solution file missing.",
                first_suite_error=None,
                output_path=None,
            ),
            None,
        )

    problem = json.loads(source.problem_path.read_text(encoding="utf-8"))
    test_cases = list(problem.get("test_cases", []))
    total_tests = len(test_cases)
    if total_tests == 0:
        return (
            ProblemOutcome(
                id=source.id,
                difficulty=source.difficulty,
                status="dropped",
                total_tests=0,
                kept_tests=0,
                failed_tests=0,
                fail_rate=1.0,
                reason="Problem has no test cases.",
                first_suite_error=None,
                output_path=None,
            ),
            None,
        )

    solution_code = source.solution_path.read_text(encoding="utf-8")
    any_order = "any order" in str(problem.get("description", "")).lower()
    suite_result = run_solution(
        solution_code=solution_code,
        entry_point=problem.get("entry_point", ""),
        test_cases=test_cases,
        preamble=problem.get("preamble", ""),
        any_order=any_order,
        timeout_seconds=timeout_seconds,
        memory_mb=memory_mb,
    )
    passed = int(suite_result.get("passed", 0))
    total = int(suite_result.get("total", total_tests))

    if passed == total and total > 0:
        kept_test_cases = test_cases
        failed_test_cases: list[str] = []
    else:
        kept_test_cases = []
        failed_test_cases = []
        for test_case in test_cases:
            case_result = run_solution(
                solution_code=solution_code,
                entry_point=problem.get("entry_point", ""),
                test_cases=[test_case],
                preamble=problem.get("preamble", ""),
                any_order=any_order,
                timeout_seconds=timeout_seconds,
                memory_mb=memory_mb,
            )
            case_passed = int(case_result.get("passed", 0))
            case_total = int(case_result.get("total", 1))
            if case_passed == case_total == 1:
                kept_test_cases.append(test_case)
            else:
                failed_test_cases.append(test_case)

    failed_tests = len(failed_test_cases)
    kept_tests = len(kept_test_cases)
    fail_rate = failed_tests / total_tests if total_tests else 1.0
    if kept_tests == 0 or fail_rate > max_fail_rate:
        return (
            ProblemOutcome(
                id=source.id,
                difficulty=source.difficulty,
                status="dropped",
                total_tests=total_tests,
                kept_tests=kept_tests,
                failed_tests=failed_tests,
                fail_rate=round(fail_rate, 4),
                reason=(
                    f"Dropped after filtering: {failed_tests}/{total_tests} tests failed "
                    f"({fail_rate:.1%})."
                ),
                first_suite_error=suite_result.get("error"),
                output_path=None,
            ),
            None,
        )

    exported = dict(problem)
    exported["test_cases"] = kept_test_cases
    exported["check_function"] = build_check_function(kept_test_cases)
    output_path = output_dir / f"{source.id}.json"
    write_json(output_path, exported)
    status = "kept" if failed_tests == 0 else "trimmed"
    reason = None
    if failed_tests:
        reason = (
            f"Removed {failed_tests}/{total_tests} failing tests ({fail_rate:.1%})."
        )
    return (
        ProblemOutcome(
            id=source.id,
            difficulty=source.difficulty,
            status=status,
            total_tests=total_tests,
            kept_tests=kept_tests,
            failed_tests=failed_tests,
            fail_rate=round(fail_rate, 4),
            reason=reason,
            first_suite_error=suite_result.get("error"),
            output_path=str(output_path),
        ),
        exported,
    )


def build_validation_report(
    outcomes: list[ProblemOutcome], kept_ids: list[str]
) -> dict[str, Any]:
    kept = [outcome for outcome in outcomes if outcome.status in {"kept", "trimmed"}]
    dropped = [outcome for outcome in outcomes if outcome.status == "dropped"]
    trimmed = [outcome for outcome in outcomes if outcome.status == "trimmed"]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "selected_problems": len(outcomes),
            "kept_problems": len(kept),
            "trimmed_problems": len(trimmed),
            "dropped_problems": len(dropped),
            "kept_test_cases": sum(outcome.kept_tests for outcome in kept),
            "dropped_test_cases": sum(outcome.failed_tests for outcome in outcomes),
        },
        "kept_problem_ids": kept_ids,
        "trimmed_problems": [
            {
                "id": outcome.id,
                "difficulty": outcome.difficulty,
                "total_tests": outcome.total_tests,
                "kept_tests": outcome.kept_tests,
                "failed_tests": outcome.failed_tests,
                "fail_rate": outcome.fail_rate,
            }
            for outcome in trimmed
        ],
        "dropped_problems": [
            {
                "id": outcome.id,
                "difficulty": outcome.difficulty,
                "total_tests": outcome.total_tests,
                "failed_tests": outcome.failed_tests,
                "fail_rate": outcome.fail_rate,
                "reason": outcome.reason,
                "first_suite_error": outcome.first_suite_error,
            }
            for outcome in dropped
        ],
    }


def main() -> None:
    args = parse_args()
    ensure_valid_args(args)

    include_ids = set(args.include_id) if args.include_id else None
    sources = load_problem_sources(include_ids=include_ids)
    if args.limit is not None:
        sources = sources[: args.limit]
    if not sources:
        raise ValueError("No Easy/Medium repaired problems matched the export filters.")

    output_dir = args.output_dir.resolve()
    if output_dir.exists() and not args.keep_output_dir:
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    outcomes: list[ProblemOutcome] = []
    with ThreadPoolExecutor(max_workers=min(args.workers, len(sources))) as executor:
        future_map = {
            executor.submit(
                evaluate_problem,
                source,
                output_dir=output_dir,
                timeout_seconds=args.timeout_seconds,
                memory_mb=args.memory_mb,
                max_fail_rate=args.max_fail_rate,
            ): source
            for source in sources
        }
        for index, future in enumerate(as_completed(future_map), start=1):
            outcome, _ = future.result()
            outcomes.append(outcome)
            print(
                f"[{index}/{len(sources)}] {outcome.id}: {outcome.status} "
                f"kept={outcome.kept_tests}/{outcome.total_tests}"
            )

    outcomes.sort(key=lambda item: item.id)
    kept_outcomes = {
        outcome.id: outcome
        for outcome in outcomes
        if outcome.status in {"kept", "trimmed"}
    }

    index_entries = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    exported_index = []
    kept_ids: list[str] = []
    for entry in index_entries:
        outcome = kept_outcomes.get(entry["id"])
        if outcome is None:
            continue
        kept_ids.append(entry["id"])
        exported_index.append(
            {
                "id": entry["id"],
                "title": entry["title"],
                "difficulty": entry["difficulty"],
                "tags": entry["tags"],
                "test_count": outcome.kept_tests,
                "verified": True,
            }
        )

    write_json(output_dir / "index.json", exported_index)
    write_json(
        output_dir / "validation_report.json",
        build_validation_report(outcomes, kept_ids),
    )
    write_json(
        output_dir / "export_report.json",
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "output_dir": str(output_dir),
            "config": {
                "workers": args.workers,
                "timeout_seconds": args.timeout_seconds,
                "memory_mb": args.memory_mb,
                "max_fail_rate": args.max_fail_rate,
                "selected_problems": len(sources),
            },
            "outcomes": [outcome.__dict__ for outcome in outcomes],
        },
    )

    kept = sum(1 for outcome in outcomes if outcome.status in {"kept", "trimmed"})
    trimmed = sum(1 for outcome in outcomes if outcome.status == "trimmed")
    dropped = sum(1 for outcome in outcomes if outcome.status == "dropped")
    print(f"\nExport directory: {output_dir}")
    print(f"Kept problems: {kept}")
    print(f"Trimmed problems: {trimmed}")
    print(f"Dropped problems: {dropped}")


if __name__ == "__main__":
    main()
