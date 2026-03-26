"""Validate all problems in problems/ and remove any that fail their reference solution.

This script validates each problem by running its reference solution against its test cases
using the project's sandbox execution engine (server/sandbox.py).

Reference solutions come from the HuggingFace newfacade/LeetCodeDataset dataset (the same
source used to generate the problem files). If the dataset is not available, the script falls
back to an existing validation_report.json produced by a prior run.

Classification of each problem:
  - verified:    reference solution passes ALL test cases  -> KEPT
  - partial:     reference solution passes SOME test cases -> REMOVED
  - error:       reference solution crashed / timed out    -> REMOVED
  - untestable:  all test cases assert == None (in-place)  -> REMOVED
  - unmatched:   no reference solution found               -> REMOVED (cannot validate)

Usage:
    python scripts/validate_problems.py                   # use existing report if available
    python scripts/validate_problems.py --fresh           # re-run full validation
    python scripts/validate_problems.py --workers 8       # parallel workers for fresh run
    python scripts/validate_problems.py --dry-run         # show what would be removed, no changes
"""

import argparse
import json
import re
import sys
import time
from multiprocessing import Pool
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from server.sandbox import _run_sync  # noqa: E402

PROBLEMS_DIR = PROJECT_ROOT / "problems"
INDEX_PATH = PROBLEMS_DIR / "index.json"
REPORT_PATH = PROBLEMS_DIR / "validation_report.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def is_all_none(test_cases: list[str]) -> bool:
    """Return True if every test case asserts == None (in-place mutation problems)."""
    if not test_cases:
        return False
    for tc in test_cases:
        if not re.search(r"==\s*None\s*$", tc.strip()):
            return False
    return True


def detect_any_order(description: str) -> bool:
    """Return True if the problem description mentions results can be in 'any order'."""
    return "any order" in description.lower()


def slugify(title: str) -> str:
    """Convert a problem title to a filename-safe slug (same as generate.py)."""
    slug = title.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


# ---------------------------------------------------------------------------
# Fresh validation (requires HuggingFace datasets package)
# ---------------------------------------------------------------------------


def validate_one(args: tuple) -> dict:
    """Validate a single problem against its reference solution via the sandbox.

    Args is (problem_id, solution_code, any_order) — structured for multiprocessing.Pool.
    Returns a result dict with keys: id, category, passed, total, pass_rate, error.
    """
    problem_id, solution_code, any_order = args

    problem_path = PROBLEMS_DIR / f"{problem_id}.json"
    if not problem_path.exists():
        return {
            "id": problem_id,
            "category": "error",
            "passed": 0,
            "total": 0,
            "pass_rate": 0.0,
            "error": "Problem file not found",
        }

    problem = json.loads(problem_path.read_text())
    test_cases = problem.get("test_cases", [])
    entry_point = problem.get("entry_point", "")
    preamble = problem.get("preamble", "")

    if not test_cases:
        return {
            "id": problem_id,
            "category": "error",
            "passed": 0,
            "total": 0,
            "pass_rate": 0.0,
            "error": "No test cases",
        }

    if is_all_none(test_cases):
        return {
            "id": problem_id,
            "category": "untestable",
            "passed": 0,
            "total": len(test_cases),
            "pass_rate": 0.0,
            "error": "All test cases assert == None",
        }

    result = _run_sync(
        code=solution_code,
        entry_point=entry_point,
        test_cases=test_cases,
        preamble=preamble,
        any_order=any_order,
    )

    passed = result.get("passed", 0)
    total = result.get("total", len(test_cases))
    pass_rate = passed / total if total > 0 else 0.0
    error = result.get("error")

    if passed == total and total > 0:
        category = "verified"
    elif passed > 0:
        category = "partial"
    else:
        category = "error"

    return {
        "id": problem_id,
        "category": category,
        "passed": passed,
        "total": total,
        "pass_rate": round(pass_rate, 4),
        "error": error,
    }


def run_fresh_validation(index: list[dict], workers: int) -> list[dict]:
    """Download HuggingFace reference solutions and run sandbox validation.

    Requires: uv pip install datasets
    """
    try:
        from datasets import load_dataset
    except ImportError:
        print(
            "ERROR: HuggingFace datasets package is not installed.\n"
            "Install it with:  uv pip install datasets\n"
            "Or omit --fresh to use the existing validation_report.json instead.",
            file=sys.stderr,
        )
        sys.exit(1)

    print("Downloading LeetCodeDataset from HuggingFace...")
    ds = load_dataset("newfacade/LeetCodeDataset", split="train")

    # Build map: slug -> reference solution code
    ref_solutions: dict[str, str] = {}
    for row in ds:
        task_id = row.get("task_id", "")
        completion = row.get("completion", "")
        if task_id and completion:
            slug = slugify(task_id)
            if slug:
                ref_solutions[slug] = completion
    print(f"Found {len(ref_solutions)} reference solutions in HuggingFace dataset")

    # Load problem descriptions for any_order detection
    problem_descriptions: dict[str, str] = {}
    for entry in index:
        pid = entry["id"]
        prob_path = PROBLEMS_DIR / f"{pid}.json"
        if prob_path.exists():
            prob_data = json.loads(prob_path.read_text())
            problem_descriptions[pid] = prob_data.get("description", "")

    # Static analysis: classify untestable problems without running sandbox
    print("\n--- Static Analysis ---")
    results: list[dict] = []
    work_items: list[tuple] = []
    unmatched_count = 0

    for entry in index:
        pid = entry["id"]
        prob_path = PROBLEMS_DIR / f"{pid}.json"

        if not prob_path.exists():
            results.append(
                {
                    "id": pid,
                    "category": "error",
                    "passed": 0,
                    "total": 0,
                    "pass_rate": 0.0,
                    "error": "Problem file not found",
                }
            )
            continue

        prob_data = json.loads(prob_path.read_text())
        test_cases = prob_data.get("test_cases", [])

        if is_all_none(test_cases):
            results.append(
                {
                    "id": pid,
                    "category": "untestable",
                    "passed": 0,
                    "total": len(test_cases),
                    "pass_rate": 0.0,
                    "error": "All test cases assert == None",
                }
            )
            continue

        if pid not in ref_solutions:
            unmatched_count += 1
            results.append(
                {
                    "id": pid,
                    "category": "unmatched",
                    "passed": 0,
                    "total": 0,
                    "pass_rate": 0.0,
                    "error": "No reference solution in HuggingFace dataset",
                }
            )
            continue

        any_order = detect_any_order(problem_descriptions.get(pid, ""))
        work_items.append((pid, ref_solutions[pid], any_order))

    untestable_count = sum(1 for r in results if r["category"] == "untestable")
    print(f"Untestable (all-None assertions): {untestable_count}")
    print(f"Unmatched (no reference solution): {unmatched_count}")
    print(f"To validate via sandbox: {len(work_items)}")

    # Run sandbox validation in parallel
    print(
        f"\n--- Sandbox Validation ({len(work_items)} problems, {workers} workers) ---"
    )
    start = time.time()
    validated: list[dict] = []

    with Pool(processes=workers) as pool:
        total_items = len(work_items)
        for i, result in enumerate(pool.imap_unordered(validate_one, work_items), 1):
            validated.append(result)
            if i % 100 == 0 or i == total_items:
                pct = i * 100 // total_items
                cats: dict[str, int] = {}
                for r in validated:
                    cats[r["category"]] = cats.get(r["category"], 0) + 1
                status = " | ".join(f"{k}: {v}" for k, v in sorted(cats.items()))
                print(f"  [{i}/{total_items}] {pct}%  {status}")

    elapsed = time.time() - start
    print(f"Sandbox validation completed in {elapsed:.1f}s")
    results.extend(validated)
    return results


# ---------------------------------------------------------------------------
# Load existing report
# ---------------------------------------------------------------------------


def load_existing_report() -> list[dict] | None:
    """Load validation results from problems/validation_report.json if it exists."""
    if not REPORT_PATH.exists():
        return None
    data = json.loads(REPORT_PATH.read_text())
    problems = data.get("problems", [])
    if not problems:
        return None
    generated_at = data.get("generated_at", "unknown")
    print(f"Using existing validation report (generated {generated_at})")
    print(f"  Summary: {json.dumps(data.get('summary', {}))}")
    return problems


# ---------------------------------------------------------------------------
# Removal logic
# ---------------------------------------------------------------------------


def remove_failing_problems(
    results: list[dict],
    index: list[dict],
    dry_run: bool,
) -> dict:
    """Remove problem files and index entries for every non-verified problem.

    Returns a summary dict with counts of removed/kept problems by category.
    """
    # Build lookup: id -> result category
    id_to_result: dict[str, dict] = {r["id"]: r for r in results}

    # Identify problems to remove (anything not "verified")
    to_remove: list[dict] = []
    to_keep: list[dict] = []

    for entry in index:
        pid = entry["id"]
        result = id_to_result.get(pid)

        if result is None:
            # Not in validation results — treat as unvalidated, keep it
            print(f"  WARNING: {pid} has no validation result, keeping it")
            to_keep.append(entry)
            continue

        if result["category"] == "verified":
            to_keep.append(entry)
        else:
            to_remove.append(entry)

    # Build category summary for what's being removed
    removal_summary: dict[str, list[str]] = {}
    for entry in to_remove:
        pid = entry["id"]
        result = id_to_result.get(pid, {})
        cat = result.get("category", "unknown")
        removal_summary.setdefault(cat, []).append(pid)

    print("\n--- Removal Plan ---")
    print(f"  Problems to keep (verified): {len(to_keep)}")
    print(f"  Problems to remove:          {len(to_remove)}")
    for cat, ids in sorted(removal_summary.items()):
        print(f"    {cat}: {len(ids)}")

    if dry_run:
        print("\n[DRY RUN] No changes made. Pass without --dry-run to apply removals.")
        return {
            "kept": len(to_keep),
            "removed": len(to_remove),
            "by_category": {k: len(v) for k, v in removal_summary.items()},
            "dry_run": True,
        }

    # Remove individual problem JSON files
    removed_files = 0
    failed_removes: list[str] = []
    for entry in to_remove:
        pid = entry["id"]
        prob_path = PROBLEMS_DIR / f"{pid}.json"
        if prob_path.exists():
            try:
                prob_path.unlink()
                removed_files += 1
            except OSError as e:
                print(f"  ERROR removing {prob_path}: {e}", file=sys.stderr)
                failed_removes.append(pid)
        else:
            print(f"  WARNING: {pid}.json not found (already removed?)")

    # Rewrite index.json with only the kept entries
    # Preserve the test_count and verified fields for kept entries
    kept_with_verified = []
    for entry in to_keep:
        entry_copy = dict(entry)
        entry_copy["verified"] = True  # all kept entries are verified
        kept_with_verified.append(entry_copy)

    INDEX_PATH.write_text(json.dumps(kept_with_verified, indent=2) + "\n")

    print("\n--- Changes Applied ---")
    print(f"  Removed {removed_files} problem JSON files from {PROBLEMS_DIR}")
    if failed_removes:
        print(f"  FAILED to remove: {failed_removes}")
    print(f"  Rewrote {INDEX_PATH} with {len(kept_with_verified)} entries")

    return {
        "kept": len(to_keep),
        "removed": len(to_remove),
        "removed_files": removed_files,
        "by_category": {k: len(v) for k, v in removal_summary.items()},
        "dry_run": False,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Validate problems and remove those whose reference solution fails."
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Re-run full validation via HuggingFace (requires: uv pip install datasets). "
        "Without this flag, the existing validation_report.json is used if present.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Parallel workers for fresh sandbox validation (default: 8)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be removed without actually removing anything.",
    )
    args = parser.parse_args()

    # Load index
    if not INDEX_PATH.exists():
        print(f"ERROR: {INDEX_PATH} not found.", file=sys.stderr)
        sys.exit(1)

    index = json.loads(INDEX_PATH.read_text())
    print(f"Loaded {len(index)} problems from {INDEX_PATH}")

    # Get validation results
    if args.fresh:
        print("\n--- Fresh Validation Mode ---")
        results = run_fresh_validation(index, args.workers)
    else:
        print("\n--- Using Existing Validation Report ---")
        results = load_existing_report()
        if results is None:
            print(
                "No existing validation_report.json found.\n"
                "Run with --fresh to perform full validation via HuggingFace dataset.\n"
                "Requires: uv pip install datasets",
                file=sys.stderr,
            )
            sys.exit(1)

    # Tally results by category
    categories: dict[str, int] = {}
    for r in results:
        cat = r.get("category", "unknown")
        categories[cat] = categories.get(cat, 0) + 1

    print(f"\n--- Validation Summary ({len(results)} problems) ---")
    for cat in ["verified", "partial", "untestable", "unmatched", "error", "unknown"]:
        count = categories.get(cat, 0)
        if count:
            print(f"  {cat}: {count}")

    # Remove failing problems and update index
    summary = remove_failing_problems(results, index, dry_run=args.dry_run)

    # Write or update the validation report (only in non-dry-run fresh mode)
    if args.fresh and not args.dry_run:
        report = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "summary": categories,
            "total": len(results),
            "problems": sorted(results, key=lambda r: r["id"]),
        }
        REPORT_PATH.write_text(json.dumps(report, indent=2) + "\n")
        print(f"\nUpdated validation report: {REPORT_PATH}")

    print("\n=== Final Result ===")
    print(f"  Total problems tested:  {len(results)}")
    print(f"  Verified (kept):        {summary['kept']}")
    print(f"  Removed:                {summary['removed']}")
    for cat, count in sorted(summary.get("by_category", {}).items()):
        print(f"    {cat}: {count}")
    if summary.get("dry_run"):
        print("\n  [DRY RUN] No files were modified.")
    else:
        print(f"\n  Index now contains {summary['kept']} verified problems.")


if __name__ == "__main__":
    main()
