from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPAIR_SCRIPT = (
    PROJECT_ROOT / "problem_repair_scripts" / "repair_problems_with_codex.py"
)
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "codex_problem_repair_runs"
DEFAULT_SOURCE_RUN_DIR = (
    PROJECT_ROOT
    / "codex_problem_repair_runs"
    / "remaining_mediums_and_holdouts_gpt54_high_fast_v2"
)
DEFAULT_EXCLUDE_RUN_DIRS = [
    PROJECT_ROOT / "codex_problem_repair_runs" / "smoke_single_easy_v2",
    PROJECT_ROOT / "codex_problem_repair_runs" / "pilot_20_each_v1",
    PROJECT_ROOT
    / "codex_problem_repair_campaigns"
    / "full_run_gpt54_campaign"
    / "round_01",
]
DEFAULT_HOLDOUT_RUN_DIRS = [
    PROJECT_ROOT
    / "codex_problem_repair_runs"
    / "holdout_followup_v1"
    / "medium_holdouts",
    PROJECT_ROOT
    / "codex_problem_repair_runs"
    / "holdout_followup_v1"
    / "easy_holdouts",
]
MANUAL_SUCCESS_IDS = ["roman-to-integer", "reverse-nodes-in-k-group"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a focused follow-up repair pass over either the explicit Easy/Medium "
            "holdouts or the untargeted remaining Easy problems, using follow-up prompt "
            "guidance and per-difficulty reasoning."
        )
    )
    parser.add_argument(
        "--mode",
        choices=["holdouts", "untargeted-easy"],
        default="holdouts",
        help="Which follow-up slice to run. Default: holdouts.",
    )
    parser.add_argument(
        "--source-run-dir",
        type=Path,
        default=DEFAULT_SOURCE_RUN_DIR,
        help="Run directory whose remaining holdout IDs should be retried.",
    )
    parser.add_argument(
        "--campaign-name",
        default=None,
        help="Optional output directory name. Defaults to a timestamped holdout_followup_* name.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="Root directory for follow-up run artifacts.",
    )
    parser.add_argument(
        "--model",
        default="gpt-5.4",
        help="Model to use for both Easy and Medium follow-up phases. Default: gpt-5.4.",
    )
    parser.add_argument(
        "--parallelism",
        type=int,
        default=3,
        help="Concurrent Codex workers for each phase. Default: 3.",
    )
    parser.add_argument(
        "--medium-batch-size",
        type=int,
        default=10,
        help="Batch size for Medium holdouts. Default: 10.",
    )
    parser.add_argument(
        "--easy-batch-size",
        type=int,
        default=10,
        help="Batch size for Easy follow-up batches. Default: 10.",
    )
    parser.add_argument(
        "--medium-timeout-seconds",
        type=int,
        default=2700,
        help="Per-worker timeout for Medium holdouts. Default: 2700.",
    )
    parser.add_argument(
        "--easy-timeout-seconds",
        type=int,
        default=1800,
        help="Per-worker timeout for Easy holdouts. Default: 1800.",
    )
    parser.add_argument(
        "--exclude-run-dir",
        type=Path,
        action="append",
        default=[],
        help="Additional successful-run directories to exclude. Repeatable.",
    )
    parser.add_argument(
        "--holdout-run-dir",
        type=Path,
        action="append",
        default=[],
        help=(
            "Run directory whose failed_repairs should be treated as explicit holdouts "
            "when mode=untargeted-easy. Repeatable."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Write config and include files, then print the phase commands without running them.",
    )
    return parser.parse_args()


def ensure_valid_args(args: argparse.Namespace) -> None:
    if args.parallelism < 1:
        raise ValueError("--parallelism must be at least 1.")
    if args.medium_batch_size < 1:
        raise ValueError("--medium-batch-size must be at least 1.")
    if args.easy_batch_size < 1:
        raise ValueError("--easy-batch-size must be at least 1.")
    if args.medium_timeout_seconds < 60:
        raise ValueError("--medium-timeout-seconds must be at least 60.")
    if args.easy_timeout_seconds < 60:
        raise ValueError("--easy-timeout-seconds must be at least 60.")


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def write_lines(path: Path, values: list[str]) -> None:
    path.write_text("".join(f"{value}\n" for value in values), encoding="utf-8")


def make_campaign_dir(args: argparse.Namespace) -> Path:
    name = args.campaign_name or datetime.now().strftime(
        "holdout_followup_%Y%m%d_%H%M%S"
    )
    campaign_dir = args.output_root / name
    campaign_dir.mkdir(parents=True, exist_ok=True)
    return campaign_dir


def load_remaining_ids(source_run_dir: Path) -> list[str]:
    json_path = source_run_dir / "remaining_medium_and_holdout_ids.json"
    txt_path = source_run_dir / "remaining_medium_and_holdout_ids.txt"

    if json_path.exists():
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError(f"Expected a JSON array in {json_path}")
        return [str(item).strip() for item in payload if str(item).strip()]

    if txt_path.exists():
        return [
            line.strip()
            for line in txt_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    raise FileNotFoundError(
        "Could not find remaining holdout IDs. Expected one of: "
        f"{json_path}, {txt_path}"
    )


def load_difficulty_by_id() -> dict[str, str]:
    index = json.loads(
        (PROJECT_ROOT / "problems" / "index.json").read_text(encoding="utf-8")
    )
    return {
        item["id"]: item["difficulty"]
        for item in index
        if item.get("id") and item.get("difficulty") in {"Easy", "Medium", "Hard"}
    }


def load_failed_ids(run_dirs: list[Path]) -> set[str]:
    failed_ids: set[str] = set()
    for run_dir in run_dirs:
        failed_path = run_dir / "failed_repairs.json"
        aggregate_path = run_dir / "aggregate.json"
        payload: list[dict[str, Any]]
        if failed_path.exists():
            payload = json.loads(failed_path.read_text(encoding="utf-8"))
        elif aggregate_path.exists():
            payload = json.loads(aggregate_path.read_text(encoding="utf-8")).get(
                "failed_repairs", []
            )
        else:
            continue
        for item in payload:
            problem_id = item.get("id")
            if problem_id:
                failed_ids.add(problem_id)
    return failed_ids


def load_successful_ids(run_dirs: list[Path]) -> set[str]:
    successful_ids: set[str] = set()
    for run_dir in run_dirs:
        aggregate_path = run_dir / "aggregate.json"
        if not aggregate_path.exists():
            continue
        payload = json.loads(aggregate_path.read_text(encoding="utf-8"))
        for item in payload.get("successful_repairs", []):
            problem_id = item.get("id")
            if problem_id:
                successful_ids.add(problem_id)
    successful_ids.update(MANUAL_SUCCESS_IDS)
    return successful_ids


def build_phase_command(
    *,
    campaign_dir: Path,
    phase_name: str,
    include_path: Path,
    difficulty: str,
    model: str,
    reasoning_effort: str,
    batch_size: int,
    timeout_seconds: int,
    parallelism: int,
    exclude_run_dirs: list[Path],
) -> list[str]:
    cmd = [
        "python3",
        str(REPAIR_SCRIPT),
        "--output-root",
        str(campaign_dir),
        "--run-name",
        phase_name,
        "--include-id-file",
        str(include_path),
        "--difficulty",
        difficulty,
        "--model",
        model,
        "--reasoning-effort",
        reasoning_effort,
        "--prompt-variant",
        "holdout_followup",
        "--batch-size",
        str(batch_size),
        "--parallelism",
        str(parallelism),
        "--worker-timeout-seconds",
        str(timeout_seconds),
    ]
    for run_dir in exclude_run_dirs:
        cmd.extend(["--exclude-run-dir", str(run_dir)])
    return cmd


def run_phase(cmd: list[str]) -> None:
    process = subprocess.run(cmd, cwd=PROJECT_ROOT)
    if process.returncode != 0:
        raise RuntimeError(
            f"Phase failed with exit code {process.returncode}: {' '.join(cmd)}"
        )


def main() -> None:
    args = parse_args()
    ensure_valid_args(args)

    source_run_dir = args.source_run_dir.resolve()
    if not source_run_dir.exists():
        raise FileNotFoundError(f"Missing source run dir: {source_run_dir}")

    difficulty_by_id = load_difficulty_by_id()
    easy_ids: list[str] = []
    medium_ids: list[str] = []
    unknown_ids: list[str] = []
    explicit_holdout_ids: set[str] = set()
    remaining_ids: list[str]

    if args.mode == "untargeted-easy":
        holdout_run_dirs = []
        seen_holdout_dirs: set[Path] = set()
        for path in [*DEFAULT_HOLDOUT_RUN_DIRS, *args.holdout_run_dir]:
            resolved = path.resolve()
            if resolved not in seen_holdout_dirs:
                seen_holdout_dirs.add(resolved)
                holdout_run_dirs.append(resolved)
        explicit_holdout_ids = load_failed_ids(holdout_run_dirs)
        repaired_ids = load_successful_ids(
            [
                *DEFAULT_EXCLUDE_RUN_DIRS,
                source_run_dir,
                *holdout_run_dirs,
                *args.exclude_run_dir,
            ]
        )
        remaining_ids = sorted(
            problem_id
            for problem_id, difficulty in difficulty_by_id.items()
            if difficulty == "Easy"
            and problem_id not in repaired_ids
            and problem_id not in explicit_holdout_ids
        )
    else:
        remaining_ids = load_remaining_ids(source_run_dir)

    for problem_id in remaining_ids:
        difficulty = difficulty_by_id.get(problem_id)
        if difficulty == "Easy":
            easy_ids.append(problem_id)
        elif difficulty == "Medium":
            if args.mode == "holdouts":
                medium_ids.append(problem_id)
        else:
            unknown_ids.append(problem_id)

    if unknown_ids:
        raise ValueError(
            f"Found IDs outside Easy/Medium or missing from index: {unknown_ids[:10]}"
        )

    campaign_dir = make_campaign_dir(args)
    easy_ids_path = campaign_dir / "easy_holdout_ids.txt"
    medium_ids_path = campaign_dir / "medium_holdout_ids.txt"
    write_lines(easy_ids_path, easy_ids)
    write_lines(medium_ids_path, medium_ids)

    exclude_run_dirs = []
    seen_excludes: set[Path] = set()
    for path in [*DEFAULT_EXCLUDE_RUN_DIRS, source_run_dir, *args.exclude_run_dir]:
        resolved = path.resolve()
        if resolved not in seen_excludes:
            seen_excludes.add(resolved)
            exclude_run_dirs.append(resolved)

    medium_cmd = build_phase_command(
        campaign_dir=campaign_dir,
        phase_name="medium_holdouts",
        include_path=medium_ids_path,
        difficulty="Medium",
        model=args.model,
        reasoning_effort="high",
        batch_size=args.medium_batch_size,
        timeout_seconds=args.medium_timeout_seconds,
        parallelism=args.parallelism,
        exclude_run_dirs=exclude_run_dirs,
    )
    easy_cmd = build_phase_command(
        campaign_dir=campaign_dir,
        phase_name="easy_holdouts" if args.mode == "holdouts" else "easy_untargeted",
        include_path=easy_ids_path,
        difficulty="Easy",
        model=args.model,
        reasoning_effort="medium",
        batch_size=args.easy_batch_size,
        timeout_seconds=args.easy_timeout_seconds,
        parallelism=args.parallelism,
        exclude_run_dirs=exclude_run_dirs,
    )

    config = {
        "campaign_dir": str(campaign_dir.resolve()),
        "mode": args.mode,
        "source_run_dir": str(source_run_dir),
        "model": args.model,
        "parallelism": args.parallelism,
        "medium_batch_size": args.medium_batch_size,
        "easy_batch_size": args.easy_batch_size,
        "medium_timeout_seconds": args.medium_timeout_seconds,
        "easy_timeout_seconds": args.easy_timeout_seconds,
        "prompt_variant": "holdout_followup",
        "counts": {
            "remaining_total": len(remaining_ids),
            "medium": len(medium_ids),
            "easy": len(easy_ids),
            "explicit_holdout_ids": len(explicit_holdout_ids),
        },
        "exclude_run_dirs": [str(path) for path in exclude_run_dirs],
        "explicit_holdout_ids": sorted(explicit_holdout_ids),
        "commands": {
            "medium_holdouts": medium_cmd,
            "easy_holdouts": easy_cmd,
        },
    }
    write_json(campaign_dir / "followup_config.json", config)

    print(f"Campaign directory: {campaign_dir.resolve()}")
    if args.mode == "holdouts":
        print(f"Medium holdouts: {len(medium_ids)}")
        print(f"Easy holdouts: {len(easy_ids)}")
    else:
        print(f"Explicit holdouts excluded: {len(explicit_holdout_ids)}")
        print(f"Untargeted Easy problems: {len(easy_ids)}")

    if args.dry_run:
        print("\nMedium command:")
        print(" ".join(medium_cmd))
        print("\nEasy command:")
        print(" ".join(easy_cmd))
        return

    if args.mode == "holdouts" and medium_ids:
        run_phase(medium_cmd)
    if easy_ids:
        run_phase(easy_cmd)


if __name__ == "__main__":
    main()
