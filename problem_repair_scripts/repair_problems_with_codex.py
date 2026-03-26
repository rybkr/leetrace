from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import textwrap
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.generate import extract_test_cases  # noqa: E402
from server.sandbox import _run_sync  # noqa: E402

PROBLEMS_DIR = PROJECT_ROOT / "problems"
INDEX_PATH = PROBLEMS_DIR / "index.json"

DEFAULT_EASY_MODEL = "gpt-5.4-mini"
DEFAULT_MEDIUM_MODEL = "gpt-5.4"
DEFAULT_HARD_MODEL = "gpt-5.4"
DEFAULT_BATCH_SIZE = 20
DEFAULT_PARALLELISM = 3
DEFAULT_TIMEOUT_SECONDS = 1200
DIFFICULTIES = ("Easy", "Medium", "Hard")
MODEL_BY_DIFFICULTY = {
    "Easy": DEFAULT_EASY_MODEL,
    "Medium": DEFAULT_MEDIUM_MODEL,
    "Hard": DEFAULT_HARD_MODEL,
}
REASONING_BY_DIFFICULTY = {
    "Easy": "low",
    "Medium": "medium",
    "Hard": "high",
}
REASONING_EFFORTS = ("low", "medium", "high", "xhigh")
PROMPT_VARIANTS = ("default", "holdout_followup")
SANDBOX_HELPER_REFERENCE = textwrap.dedent(
    """\
    Exact node/helper contract from the repo runtime:

    class TreeNode:
        def __init__(self, val=0, left=None, right=None): ...

    class ListNode:
        def __init__(self, val=0, next=None): ...

    def tree_node(vals): ...
    def list_node(vals): ...
    def is_same_tree(a, b): ...
    def is_same_list(a, b): ...

    Testing rules:
    - For normal scalar/list outputs, use `assert candidate(...) == expected`.
    - For linked-list outputs, use `assert is_same_list(candidate(...), list_node([...]))`.
    - For tree outputs, use `assert is_same_tree(candidate(...), tree_node([...]))`.
    - For linked-list inputs, build them with `list_node([...])`.
    - For tree inputs, build them with `tree_node([...])`.
    - Do not invent extra helpers like `make_tree`, `to_list`, `normalize`, `dedupe`, or `check_merge`.
    - Keep every assertion self-contained; do not define helper functions inside `check_function`.
    """
).strip()


@dataclass(frozen=True)
class ProblemRecord:
    id: str
    title: str
    difficulty: str
    tags: list[str]
    problem_path: Path


@dataclass(frozen=True)
class ProblemAssignment:
    record: ProblemRecord
    solution_path: Path
    backup_path: Path


@dataclass(frozen=True)
class BatchPlan:
    batch_index: int
    difficulty: str
    model: str
    reasoning_effort: str
    service_tier: str | None
    prompt_variant: str
    assignments: list[ProblemAssignment]
    batch_dir: Path
    prompt_path: Path
    stdout_path: Path
    stderr_path: Path
    output_path: Path
    unsolved_path: Path


@dataclass
class WorkerOutcome:
    plan: BatchPlan
    success: bool
    returncode: int | None
    result: dict[str, Any] | None
    error: str | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run Codex in parallel over LeetRace problem batches, repairing "
            "test suites and verifying the resulting updates locally."
        )
    )
    parser.add_argument(
        "--model",
        default=None,
        help=(
            "Optional override to force the same model for all difficulties. "
            "If omitted, difficulty-specific defaults are used."
        ),
    )
    parser.add_argument(
        "--easy-model",
        default=DEFAULT_EASY_MODEL,
        help=f"Model to use for Easy batches. Default: {DEFAULT_EASY_MODEL}.",
    )
    parser.add_argument(
        "--medium-model",
        default=DEFAULT_MEDIUM_MODEL,
        help=f"Model to use for Medium batches. Default: {DEFAULT_MEDIUM_MODEL}.",
    )
    parser.add_argument(
        "--hard-model",
        default=DEFAULT_HARD_MODEL,
        help=f"Model to use for Hard batches. Default: {DEFAULT_HARD_MODEL}.",
    )
    parser.add_argument(
        "--reasoning-effort",
        choices=REASONING_EFFORTS,
        default=None,
        help=(
            "Optional override to force the same reasoning effort for all "
            "selected difficulties. If omitted, difficulty-specific defaults "
            "are used."
        ),
    )
    parser.add_argument(
        "--service-tier",
        default=None,
        help=(
            "Optional Codex service tier override, for example "
            '`flex` to avoid the local `service_tier = "fast"` config.'
        ),
    )
    parser.add_argument(
        "--prompt-variant",
        choices=PROMPT_VARIANTS,
        default="default",
        help="Prompt variant to use for worker batches. Default: default.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Problems per Codex worker. Default: {DEFAULT_BATCH_SIZE}.",
    )
    parser.add_argument(
        "--parallelism",
        type=int,
        default=DEFAULT_PARALLELISM,
        help=f"Maximum Codex workers to run in parallel. Default: {DEFAULT_PARALLELISM}.",
    )
    parser.add_argument(
        "--worker-timeout-seconds",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=(
            "Per-worker timeout in seconds. Default: "
            f"{DEFAULT_TIMEOUT_SECONDS}."
        ),
    )
    parser.add_argument(
        "--per-difficulty-limit",
        type=int,
        default=None,
        help=(
            "Optional cap for how many problems to select from each difficulty "
            "before batching. Useful for pilot runs."
        ),
    )
    parser.add_argument(
        "--difficulty",
        action="append",
        choices=DIFFICULTIES,
        dest="difficulties",
        help=(
            "Difficulty to include. Repeat to include multiple values. "
            "Defaults to Easy, Medium, Hard."
        ),
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=PROJECT_ROOT / "codex_problem_repair_runs",
        help="Root directory for run artifacts.",
    )
    parser.add_argument(
        "--run-name",
        default=None,
        help="Optional run directory name. Defaults to a timestamp-based name.",
    )
    parser.add_argument(
        "--exclude-run-dir",
        type=Path,
        action="append",
        default=[],
        help=(
            "Exclude successfully repaired problem IDs from a prior run "
            "directory. Repeatable."
        ),
    )
    parser.add_argument(
        "--exclude-id",
        action="append",
        default=[],
        help=(
            "Exclude a specific problem ID in addition to any excluded run dirs. "
            "Repeatable."
        ),
    )
    parser.add_argument(
        "--include-id",
        action="append",
        default=[],
        help=(
            "Restrict the run to specific problem IDs. Repeatable. If omitted, "
            "all non-excluded problems are eligible."
        ),
    )
    parser.add_argument(
        "--include-id-file",
        type=Path,
        default=None,
        help=(
            "Optional file containing problem IDs to include, one per line or as "
            "a JSON array."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build the run plan and write artifacts without invoking Codex.",
    )
    return parser.parse_args()


def ensure_valid_args(args: argparse.Namespace) -> None:
    if args.batch_size < 1:
        raise ValueError("--batch-size must be at least 1.")
    if args.parallelism < 1:
        raise ValueError("--parallelism must be at least 1.")
    if args.worker_timeout_seconds < 60:
        raise ValueError("--worker-timeout-seconds must be at least 60.")
    if args.per_difficulty_limit is not None and args.per_difficulty_limit < 1:
        raise ValueError("--per-difficulty-limit must be positive.")


def resolve_model_by_difficulty(args: argparse.Namespace) -> dict[str, str]:
    if args.model:
        return {difficulty: args.model for difficulty in DIFFICULTIES}
    return {
        "Easy": args.easy_model,
        "Medium": args.medium_model,
        "Hard": args.hard_model,
    }


def resolve_reasoning_by_difficulty(args: argparse.Namespace) -> dict[str, str]:
    if args.reasoning_effort:
        return {difficulty: args.reasoning_effort for difficulty in DIFFICULTIES}
    return dict(REASONING_BY_DIFFICULTY)


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def make_run_dir(args: argparse.Namespace) -> Path:
    run_name = args.run_name or datetime.now().strftime("run_%Y%m%d_%H%M%S")
    run_dir = args.output_root / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def load_problem_records() -> list[ProblemRecord]:
    if not INDEX_PATH.exists():
        raise FileNotFoundError(f"Missing index file: {INDEX_PATH}")

    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    records: list[ProblemRecord] = []
    for entry in index:
        difficulty = entry.get("difficulty")
        problem_id = entry.get("id")
        title = entry.get("title")
        if difficulty not in DIFFICULTIES or not problem_id or not title:
            continue
        problem_path = PROBLEMS_DIR / f"{problem_id}.json"
        if not problem_path.exists():
            continue
        records.append(
            ProblemRecord(
                id=problem_id,
                title=title,
                difficulty=difficulty,
                tags=list(entry.get("tags", [])),
                problem_path=problem_path,
            )
        )
    return records


def load_excluded_ids(run_dirs: list[Path]) -> set[str]:
    excluded: set[str] = set()
    for run_dir in run_dirs:
        aggregate_path = run_dir / "aggregate.json"
        if not aggregate_path.exists():
            raise FileNotFoundError(
                f"Expected aggregate.json in exclude run dir: {aggregate_path}"
            )
        payload = json.loads(aggregate_path.read_text(encoding="utf-8"))
        for entry in payload.get("successful_repairs", []):
            problem_id = entry.get("id")
            if problem_id:
                excluded.add(problem_id)
    return excluded


def load_optional_ids(path: Path | None) -> set[str]:
    if path is None:
        return set()
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return set()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {line.strip() for line in raw.splitlines() if line.strip()}
    if isinstance(payload, list):
        return {str(item).strip() for item in payload if str(item).strip()}
    raise ValueError(f"Unsupported include-id file format: {path}")


def build_batches(
    records: list[ProblemRecord],
    difficulties: list[str],
    per_difficulty_limit: int | None,
    batch_size: int,
    run_dir: Path,
    excluded_ids: set[str],
    model_by_difficulty: dict[str, str],
    reasoning_by_difficulty: dict[str, str],
    prompt_variant: str,
) -> list[BatchPlan]:
    grouped: dict[str, list[ProblemRecord]] = defaultdict(list)
    for record in records:
        if record.id not in excluded_ids:
            grouped[record.difficulty].append(record)

    plans: list[BatchPlan] = []
    batch_counter = 0
    for difficulty in difficulties:
        selected = grouped.get(difficulty, [])
        if per_difficulty_limit is not None:
            selected = selected[:per_difficulty_limit]
        for start in range(0, len(selected), batch_size):
            chunk = selected[start : start + batch_size]
            batch_counter += 1
            batch_dir = run_dir / f"batch_{batch_counter:03d}_{difficulty.lower()}"
            batch_dir.mkdir(parents=True, exist_ok=True)
            solution_dir = batch_dir / "expected_solutions"
            backup_dir = batch_dir / "backups"
            solution_dir.mkdir(parents=True, exist_ok=True)
            backup_dir.mkdir(parents=True, exist_ok=True)
            assignments = [
                ProblemAssignment(
                    record=record,
                    solution_path=solution_dir / f"{record.id}.py",
                    backup_path=backup_dir / f"{record.id}.json",
                )
                for record in chunk
            ]
            plans.append(
                BatchPlan(
                    batch_index=batch_counter,
                    difficulty=difficulty,
                    model=model_by_difficulty[difficulty],
                    reasoning_effort=reasoning_by_difficulty[difficulty],
                    service_tier=None,
                    prompt_variant=prompt_variant,
                    assignments=assignments,
                    batch_dir=batch_dir,
                    prompt_path=batch_dir / "worker.prompt.txt",
                    stdout_path=batch_dir / "worker.stdout.log",
                    stderr_path=batch_dir / "worker.stderr.log",
                    output_path=batch_dir / "worker.result.json",
                    unsolved_path=batch_dir / f"unsolved_{difficulty.lower()}_batch_{batch_counter:03d}.json",
                )
            )
    return plans


def build_worker_schema() -> dict[str, Any]:
    updated_item = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "test_count": {"type": "integer", "minimum": 1},
            "notes": {"type": "string"},
        },
        "required": ["id", "test_count", "notes"],
        "additionalProperties": False,
    }
    unsolved_item = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["id", "reason"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "batch_summary": {"type": "string"},
            "updated": {"type": "array", "items": updated_item},
            "unsolved": {"type": "array", "items": unsolved_item},
        },
        "required": ["batch_summary", "updated", "unsolved"],
        "additionalProperties": False,
    }


def build_worker_prompt(plan: BatchPlan) -> str:
    assignment_lines = []
    for assignment in plan.assignments:
        record = assignment.record
        assignment_lines.append(
            (
                f"- id={record.id}; title={json.dumps(record.title)}; "
                f"problem_file={json.dumps(str(record.problem_path))}; "
                f"solution_file={json.dumps(str(assignment.solution_path))}"
            )
        )

    variant_guidance = ""
    if plan.prompt_variant == "holdout_followup":
        variant_guidance = textwrap.dedent(
            """\

            Holdout follow-up guidance:
            - These are remaining holdouts after earlier repair passes, so do not default to marking them unsolved just because the answer set is non-unique.
            - When a problem allows multiple valid outputs, prefer self-contained property-based asserts over a single exact expected output whenever possible.
            - `test_cases` are extracted from assert lines only, so every property check must be encoded directly inside each assert expression.
            - Do not rely on helper definitions, shared mutable state, or setup outside the assert itself.
            - Inline lambdas, comprehensions, sorting, counters, and structural predicates inside a single assert are allowed.
            - For multi-answer outputs, assert invariants such as value multiset preservation, ordering or adjacency constraints, balance constraints, reachability constraints, exact counts, or validity of the returned structure relative to the input.
            - Only mark a problem unsolved if you cannot express a correct self-contained property check within this repo's assert-only autograder format.
            - Keep holdout test suites tight: prefer roughly 6-12 high-signal asserts unless the contract clearly needs more.
            """
        ).rstrip()

    return textwrap.dedent(
        f"""
        You are repairing LeetRace problem files for a single difficulty batch.

        Batch info:
        - difficulty: {plan.difficulty}
        - assigned problem count: {len(plan.assignments)}
        - repo root: {PROJECT_ROOT}
        - unsolved report path: {plan.unsolved_path}

        Allowed writes:
        - The listed problem JSON files only
        - The listed expected solution files only
        - The unsolved report path only

        Do not edit:
        - problems/index.json
        - any problem JSON file not listed below
        - any unrelated project files

        Repo-specific autograder contract:
        - Each problem JSON contains `entry_point`, `preamble`, `check_function`, and `test_cases`.
        - `test_cases` must be a list of Python assert strings executable by the repo sandbox.
        - `check_function` must be `def check(candidate):` followed by the same asserts as `test_cases`.
        - The runtime strips keyword argument names and compares nested lists flexibly only when the description says "any order".
        - Avoid invalid inputs beyond the stated constraints.
        - If the statement implies something like an even-length array, do not generate odd-length test inputs.

        {SANDBOX_HELPER_REFERENCE}
        {variant_guidance}

        Required flow for every assigned problem:
        1. Read the current problem JSON and infer an expected solution that matches the existing wording and constraints.
        2. Use that expected solution to design a corrected, high-signal test suite for this repo's autograder format.
        3. Update the problem JSON so `test_cases` and `check_function` match exactly.
        4. Write the expected solution code you used to the assigned solution file path.
        5. Validate locally against the updated tests before considering the repair complete.
        6. If you cannot confidently solve and validate the problem, leave its JSON unchanged and add it to the unsolved report.

        Editing rules:
        - Preserve `id`, `title`, `difficulty`, `tags`, `entry_point`, `starter_code`, and `preamble`.
        - Do not broaden or change the problem contract.
        - Only edit `check_function` and `test_cases` unless a tiny wording correction is strictly necessary to resolve a direct contradiction. Prefer leaving the description untouched.
        - Keep the tests concise but meaningful. Aim for roughly 8-20 tests per solved problem unless the problem structure clearly warrants fewer.
        - Do not rely on the existing tests being correct.
        - For contracts that are incompatible with this repo's autograder format, mark the problem unsolved.

        Unsolved report requirements:
        - Write a JSON array to {plan.unsolved_path}
        - Each element must be an object with keys `id` and `reason`
        - If every assigned problem is solved, write []

        Final response requirements:
        - Return only JSON that matches the provided schema.
        - Every assigned problem must appear exactly once in either `updated` or `unsolved`.

        Assigned problems:
        {chr(10).join(assignment_lines)}
        """
    ).strip()


def backup_batch(plan: BatchPlan) -> None:
    for assignment in plan.assignments:
        shutil.copy2(assignment.record.problem_path, assignment.backup_path)


def restore_assignment(assignment: ProblemAssignment) -> None:
    shutil.copy2(assignment.backup_path, assignment.record.problem_path)


def coerce_subprocess_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def make_worker_command(
    schema_path: Path,
    output_path: Path,
    model: str,
    reasoning_effort: str,
    service_tier: str | None,
) -> list[str]:
    command = [
        "codex",
        "-a",
        "never",
        "exec",
        "-m",
        model,
        "-c",
        f'model_reasoning_effort="{reasoning_effort}"',
        "--sandbox",
        "workspace-write",
        "--color",
        "never",
        "--output-schema",
        str(schema_path.resolve()),
        "-o",
        str(output_path.resolve()),
    ]
    if service_tier:
        command.extend(["-c", f'service_tier="{service_tier}"'])
    command.append("-")
    return command


def run_worker(
    plan: BatchPlan,
    schema_path: Path,
    timeout_seconds: int,
) -> WorkerOutcome:
    prompt = build_worker_prompt(plan)
    plan.prompt_path.write_text(prompt + "\n", encoding="utf-8")
    backup_batch(plan)

    try:
        process = subprocess.run(
            make_worker_command(
                schema_path=schema_path,
                output_path=plan.output_path,
                model=plan.model,
                reasoning_effort=plan.reasoning_effort,
                service_tier=getattr(plan, "service_tier", None),
            ),
            cwd=PROJECT_ROOT,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        plan.stdout_path.write_text(coerce_subprocess_output(exc.stdout), encoding="utf-8")
        plan.stderr_path.write_text(coerce_subprocess_output(exc.stderr), encoding="utf-8")
        return WorkerOutcome(
            plan=plan,
            success=False,
            returncode=None,
            result=None,
            error=f"Timed out after {timeout_seconds} seconds.",
        )
    except OSError as exc:
        plan.stdout_path.write_text("", encoding="utf-8")
        plan.stderr_path.write_text(str(exc) + "\n", encoding="utf-8")
        return WorkerOutcome(
            plan=plan,
            success=False,
            returncode=None,
            result=None,
            error=f"Failed to launch codex: {exc}",
        )

    plan.stdout_path.write_text(process.stdout, encoding="utf-8")
    plan.stderr_path.write_text(process.stderr, encoding="utf-8")

    if process.returncode != 0:
        return WorkerOutcome(
            plan=plan,
            success=False,
            returncode=process.returncode,
            result=None,
            error="Codex worker exited with a non-zero status.",
        )

    if not plan.output_path.exists():
        return WorkerOutcome(
            plan=plan,
            success=False,
            returncode=process.returncode,
            result=None,
            error="Expected worker output file was not created.",
        )

    try:
        payload = json.loads(plan.output_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return WorkerOutcome(
            plan=plan,
            success=False,
            returncode=process.returncode,
            result=None,
            error=f"Worker output was not valid JSON: {exc}",
        )

    return WorkerOutcome(
        plan=plan,
        success=True,
        returncode=process.returncode,
        result=payload,
        error=None,
    )


def problem_has_usable_tests(problem: dict[str, Any]) -> bool:
    test_cases = problem.get("test_cases", [])
    if not isinstance(test_cases, list) or not test_cases:
        return False
    for test_case in test_cases:
        if not isinstance(test_case, str):
            return False
        if "== None" not in test_case and "==None" not in test_case:
            return True
    return False


def write_unsolved_report(path: Path, unsolved_entries: list[dict[str, str]]) -> None:
    write_json(path, unsolved_entries)


def verify_assignment(
    assignment: ProblemAssignment,
    reported_test_count: int,
) -> tuple[bool, str, int]:
    if not assignment.record.problem_path.exists():
        return False, "Problem file does not exist after worker run.", 0
    if not assignment.solution_path.exists():
        return False, "Expected solution file was not created.", 0

    try:
        problem = json.loads(assignment.record.problem_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, f"Updated problem JSON is invalid: {exc}", 0

    if problem.get("id") != assignment.record.id:
        return False, "Updated problem ID does not match the assigned problem.", 0

    check_function = problem.get("check_function")
    if not isinstance(check_function, str) or not check_function.strip():
        return False, "Updated problem is missing check_function.", 0

    extracted = extract_test_cases(check_function)
    if not extracted:
        return False, "Updated problem check_function does not contain asserts.", 0

    if problem.get("test_cases") != extracted:
        problem["test_cases"] = extracted
        assignment.record.problem_path.write_text(
            json.dumps(problem, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    if not problem_has_usable_tests(problem):
        return False, "Updated problem has no usable test cases.", 0

    test_cases = problem["test_cases"]
    solution_code = assignment.solution_path.read_text(encoding="utf-8")
    any_order = "any order" in str(problem.get("description", "")).lower()
    result = _run_sync(
        code=solution_code,
        entry_point=problem.get("entry_point", ""),
        test_cases=test_cases,
        preamble=problem.get("preamble", ""),
        any_order=any_order,
    )

    passed = result.get("passed", 0)
    total = result.get("total", len(test_cases))
    if passed != total or total == 0:
        error = result.get("error") or result.get("first_failure") or "Verification failed."
        return False, f"Expected solution failed local verification: {error}", 0

    return True, "", total


def reconcile_batch(
    outcome: WorkerOutcome,
    index_entries: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    plan = outcome.plan
    assignment_by_id = {assignment.record.id: assignment for assignment in plan.assignments}
    successful_repairs: list[dict[str, Any]] = []
    failed_repairs: list[dict[str, Any]] = []

    if not outcome.success or outcome.result is None:
        for assignment in plan.assignments:
            restore_assignment(assignment)
            failed_repairs.append(
                {
                    "id": assignment.record.id,
                    "difficulty": plan.difficulty,
                    "batch_index": plan.batch_index,
                    "reason": outcome.error or "Worker failed before producing output.",
                }
            )
        write_unsolved_report(
            plan.unsolved_path,
            [{"id": item["id"], "reason": item["reason"]} for item in failed_repairs],
        )
        return successful_repairs, failed_repairs

    updated = outcome.result.get("updated", [])
    unsolved = outcome.result.get("unsolved", [])

    updated_by_id: dict[str, dict[str, Any]] = {}
    unsolved_by_id: dict[str, dict[str, Any]] = {}

    duplicate_ids: set[str] = set()
    for item in updated:
        problem_id = item.get("id")
        if not problem_id or problem_id in updated_by_id or problem_id in unsolved_by_id:
            if problem_id:
                duplicate_ids.add(problem_id)
            continue
        updated_by_id[problem_id] = item
    for item in unsolved:
        problem_id = item.get("id")
        if not problem_id or problem_id in unsolved_by_id or problem_id in updated_by_id:
            if problem_id:
                duplicate_ids.add(problem_id)
            continue
        unsolved_by_id[problem_id] = item

    for problem_id in sorted(duplicate_ids):
        assignment = assignment_by_id.get(problem_id)
        if assignment is not None:
            restore_assignment(assignment)
        failed_repairs.append(
            {
                "id": problem_id,
                "difficulty": plan.difficulty,
                "batch_index": plan.batch_index,
                "reason": "Worker reported the same problem multiple times.",
            }
        )

    seen_ids = set(updated_by_id) | set(unsolved_by_id)
    assigned_ids = set(assignment_by_id)

    unexpected_ids = seen_ids - assigned_ids
    missing_ids = assigned_ids - seen_ids

    for problem_id in sorted(unexpected_ids):
        failed_repairs.append(
            {
                "id": problem_id,
                "difficulty": plan.difficulty,
                "batch_index": plan.batch_index,
                "reason": "Worker reported an ID that was not assigned to this batch.",
            }
        )

    for problem_id, item in sorted(unsolved_by_id.items()):
        assignment = assignment_by_id[problem_id]
        restore_assignment(assignment)
        failed_repairs.append(
            {
                "id": problem_id,
                "difficulty": plan.difficulty,
                "batch_index": plan.batch_index,
                "reason": item.get("reason", "Marked unsolved by worker."),
            }
        )

    for problem_id in sorted(missing_ids):
        assignment = assignment_by_id[problem_id]
        restore_assignment(assignment)
        failed_repairs.append(
            {
                "id": problem_id,
                "difficulty": plan.difficulty,
                "batch_index": plan.batch_index,
                "reason": "Worker did not report this assigned problem.",
            }
        )

    index_by_id = {entry["id"]: entry for entry in index_entries}

    for problem_id, item in sorted(updated_by_id.items()):
        if problem_id not in assignment_by_id:
            continue
        assignment = assignment_by_id[problem_id]
        ok, error, verified_test_count = verify_assignment(
            assignment=assignment,
            reported_test_count=int(item.get("test_count", 0)),
        )
        if not ok:
            restore_assignment(assignment)
            failed_repairs.append(
                {
                    "id": problem_id,
                    "difficulty": plan.difficulty,
                    "batch_index": plan.batch_index,
                    "reason": error,
                }
            )
            continue

        index_entry = index_by_id.get(problem_id)
        if index_entry is not None:
            index_entry["test_count"] = verified_test_count
            index_entry["verified"] = True

        successful_repairs.append(
            {
                "id": problem_id,
                "difficulty": plan.difficulty,
                "batch_index": plan.batch_index,
                "test_count": verified_test_count,
                "notes": item.get("notes", ""),
                "problem_path": str(assignment.record.problem_path),
                "solution_path": str(assignment.solution_path),
            }
        )

    write_unsolved_report(
        plan.unsolved_path,
        [
            {"id": item["id"], "reason": item["reason"]}
            for item in failed_repairs
            if item.get("difficulty") == plan.difficulty and item.get("batch_index") == plan.batch_index
        ],
    )

    return successful_repairs, failed_repairs


def build_run_metadata(
    args: argparse.Namespace,
    run_dir: Path,
    plans: list[BatchPlan],
    excluded_ids: set[str],
    model_by_difficulty: dict[str, str],
    reasoning_by_difficulty: dict[str, str],
) -> dict[str, Any]:
    return {
        "run_dir": str(run_dir.resolve()),
        "project_root": str(PROJECT_ROOT.resolve()),
        "model_override": args.model,
        "model_by_difficulty": model_by_difficulty,
        "reasoning_effort_override": args.reasoning_effort,
        "reasoning_by_difficulty": reasoning_by_difficulty,
        "service_tier": args.service_tier,
        "prompt_variant": args.prompt_variant,
        "batch_size": args.batch_size,
        "parallelism": min(args.parallelism, max(len(plans), 1)),
        "worker_timeout_seconds": args.worker_timeout_seconds,
        "per_difficulty_limit": args.per_difficulty_limit,
        "difficulties": [plan.difficulty for plan in plans],
        "excluded_ids": sorted(excluded_ids),
        "dry_run": args.dry_run,
    }


def build_batch_manifest(plans: list[BatchPlan]) -> list[dict[str, Any]]:
    manifest = []
    for plan in plans:
        manifest.append(
            {
                "batch_index": plan.batch_index,
                "difficulty": plan.difficulty,
                "model": plan.model,
                "reasoning_effort": plan.reasoning_effort,
                "prompt_variant": plan.prompt_variant,
                "unsolved_path": str(plan.unsolved_path),
                "problem_ids": [assignment.record.id for assignment in plan.assignments],
                "problem_paths": [
                    str(assignment.record.problem_path) for assignment in plan.assignments
                ],
                "solution_paths": [
                    str(assignment.solution_path) for assignment in plan.assignments
                ],
            }
        )
    return manifest


def main() -> None:
    args = parse_args()
    ensure_valid_args(args)

    difficulties = args.difficulties or list(DIFFICULTIES)
    model_by_difficulty = resolve_model_by_difficulty(args)
    reasoning_by_difficulty = resolve_reasoning_by_difficulty(args)
    excluded_ids = load_excluded_ids(args.exclude_run_dir)
    excluded_ids.update(args.exclude_id)
    include_ids = set(args.include_id)
    include_ids.update(load_optional_ids(args.include_id_file))
    records = load_problem_records()
    if include_ids:
        records = [record for record in records if record.id in include_ids]
    run_dir = make_run_dir(args)
    schema_path = run_dir / "worker.schema.json"
    write_json(schema_path, build_worker_schema())

    plans = build_batches(
        records=records,
        difficulties=difficulties,
        per_difficulty_limit=args.per_difficulty_limit,
        batch_size=args.batch_size,
        run_dir=run_dir,
        excluded_ids=excluded_ids,
        model_by_difficulty=model_by_difficulty,
        reasoning_by_difficulty=reasoning_by_difficulty,
        prompt_variant=args.prompt_variant,
    )
    if not plans:
        raise ValueError("No problems matched the requested filters.")

    if args.service_tier:
        plans = [
            BatchPlan(
                batch_index=plan.batch_index,
                difficulty=plan.difficulty,
                model=plan.model,
                reasoning_effort=plan.reasoning_effort,
                service_tier=args.service_tier,
                prompt_variant=plan.prompt_variant,
                assignments=plan.assignments,
                batch_dir=plan.batch_dir,
                prompt_path=plan.prompt_path,
                stdout_path=plan.stdout_path,
                stderr_path=plan.stderr_path,
                output_path=plan.output_path,
                unsolved_path=plan.unsolved_path,
            )
            for plan in plans
        ]

    metadata = build_run_metadata(
        args=args,
        run_dir=run_dir,
        plans=plans,
        excluded_ids=excluded_ids,
        model_by_difficulty=model_by_difficulty,
        reasoning_by_difficulty=reasoning_by_difficulty,
    )
    batch_manifest = build_batch_manifest(plans)
    write_json(run_dir / "run_config.json", metadata)
    write_json(run_dir / "batch_manifest.json", batch_manifest)

    if args.dry_run:
        print(f"Dry run only. Run directory: {run_dir.resolve()}")
        for plan in plans:
            print(
                f"batch {plan.batch_index:03d} | {plan.difficulty:<6} | "
                f"model={plan.model:<12} | reasoning={plan.reasoning_effort:<6} | "
                f"problems={len(plan.assignments)}"
            )
        return

    index_entries = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    successful_repairs: list[dict[str, Any]] = []
    failed_repairs: list[dict[str, Any]] = []
    outcomes_summary: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=min(args.parallelism, len(plans))) as executor:
        futures = {
            executor.submit(
                run_worker,
                plan=plan,
                schema_path=schema_path,
                timeout_seconds=args.worker_timeout_seconds,
            ): plan
            for plan in plans
        }

        for completed, future in enumerate(as_completed(futures), start=1):
            outcome = future.result()
            repaired, failed = reconcile_batch(outcome=outcome, index_entries=index_entries)
            successful_repairs.extend(repaired)
            failed_repairs.extend(failed)
            write_json(INDEX_PATH, index_entries)

            outcomes_summary.append(
                {
                    "batch_index": outcome.plan.batch_index,
                    "difficulty": outcome.plan.difficulty,
                    "model": outcome.plan.model,
                    "reasoning_effort": outcome.plan.reasoning_effort,
                    "success": outcome.success,
                    "returncode": outcome.returncode,
                    "error": outcome.error,
                    "prompt_path": str(outcome.plan.prompt_path),
                    "stdout_path": str(outcome.plan.stdout_path),
                    "stderr_path": str(outcome.plan.stderr_path),
                    "output_path": str(outcome.plan.output_path),
                    "unsolved_path": str(outcome.plan.unsolved_path),
                    "successful_repairs": len(repaired),
                    "failed_repairs": len(failed),
                }
            )

            print(
                f"[{completed}/{len(plans)}] batch {outcome.plan.batch_index:03d} "
                f"{outcome.plan.difficulty}: repaired={len(repaired)} failed={len(failed)}"
            )

    successful_repairs.sort(key=lambda item: (item["difficulty"], item["id"]))
    failed_repairs.sort(key=lambda item: (item["difficulty"], item["id"]))
    outcomes_summary.sort(key=lambda item: item["batch_index"])

    aggregate = {
        "metadata": metadata,
        "selected_problem_ids": [
            assignment.record.id
            for plan in plans
            for assignment in plan.assignments
        ],
        "worker_outcomes": outcomes_summary,
        "successful_repairs": successful_repairs,
        "failed_repairs": failed_repairs,
    }
    write_json(run_dir / "successful_repairs.json", successful_repairs)
    write_json(run_dir / "failed_repairs.json", failed_repairs)
    write_json(run_dir / "aggregate.json", aggregate)

    print(f"\nRun directory: {run_dir.resolve()}")
    print(f"Successful repairs: {len(successful_repairs)}")
    print(f"Failed repairs: {len(failed_repairs)}")


if __name__ == "__main__":
    main()
