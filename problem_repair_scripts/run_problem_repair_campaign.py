from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPAIR_SCRIPT = (
    PROJECT_ROOT / "problem_repair_scripts" / "repair_problems_with_codex.py"
)
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "codex_problem_repair_campaigns"


@dataclass
class RoundSummary:
    round_index: int
    run_dir: Path
    selected_count: int
    successful_count: int
    failed_count: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the LeetRace problem repair pipeline over the remaining dataset "
            "and automatically retry holdouts in later rounds."
        )
    )
    parser.add_argument(
        "--campaign-name",
        default=None,
        help="Optional campaign directory name. Defaults to a timestamped name.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="Root directory for campaign artifacts.",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=3,
        help="Maximum number of repair rounds to run. Default: 3.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=20,
        help="Problems per Codex worker batch. Default: 20.",
    )
    parser.add_argument(
        "--parallelism",
        type=int,
        default=3,
        help="Maximum concurrent Codex workers. Default: 3.",
    )
    parser.add_argument(
        "--worker-timeout-seconds",
        type=int,
        default=1200,
        help="Per-worker timeout passed through to the repair runner. Default: 1200.",
    )
    parser.add_argument(
        "--base-exclude-run-dir",
        type=Path,
        action="append",
        default=[],
        help="Exclude successful repairs from a prior run dir. Repeatable.",
    )
    parser.add_argument(
        "--base-exclude-id",
        action="append",
        default=[],
        help="Exclude a specific already-repaired problem ID. Repeatable.",
    )
    parser.add_argument(
        "--easy-model",
        default="gpt-5.4-mini",
        help="Model for Easy batches. Default: gpt-5.4-mini.",
    )
    parser.add_argument(
        "--medium-model",
        default="gpt-5.4",
        help="Model for Medium batches. Default: gpt-5.4.",
    )
    parser.add_argument(
        "--hard-model",
        default="gpt-5.4",
        help="Model for Hard batches. Default: gpt-5.4.",
    )
    return parser.parse_args()


def ensure_valid_args(args: argparse.Namespace) -> None:
    if args.max_rounds < 1:
        raise ValueError("--max-rounds must be at least 1.")
    if args.batch_size < 1:
        raise ValueError("--batch-size must be at least 1.")
    if args.parallelism < 1:
        raise ValueError("--parallelism must be at least 1.")
    if args.worker_timeout_seconds < 60:
        raise ValueError("--worker-timeout-seconds must be at least 60.")


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def write_lines(path: Path, values: list[str]) -> None:
    path.write_text("".join(f"{value}\n" for value in values), encoding="utf-8")


def make_campaign_dir(args: argparse.Namespace) -> Path:
    name = args.campaign_name or datetime.now().strftime("campaign_%Y%m%d_%H%M%S")
    campaign_dir = args.output_root / name
    campaign_dir.mkdir(parents=True, exist_ok=True)
    return campaign_dir


def run_round(
    *,
    args: argparse.Namespace,
    campaign_dir: Path,
    round_index: int,
    include_ids: list[str] | None,
    exclude_run_dirs: list[Path],
    exclude_ids: list[str],
) -> RoundSummary:
    round_name = f"round_{round_index:02d}"
    round_dir = campaign_dir / round_name
    round_dir.mkdir(parents=True, exist_ok=True)

    include_file = round_dir / "include_ids.txt"
    if include_ids is not None:
        write_lines(include_file, include_ids)

    cmd = [
        "python3",
        str(REPAIR_SCRIPT),
        "--output-root",
        str(campaign_dir),
        "--run-name",
        round_name,
        "--batch-size",
        str(args.batch_size),
        "--parallelism",
        str(args.parallelism),
        "--worker-timeout-seconds",
        str(args.worker_timeout_seconds),
        "--easy-model",
        args.easy_model,
        "--medium-model",
        args.medium_model,
        "--hard-model",
        args.hard_model,
    ]
    for run_dir in exclude_run_dirs:
        cmd.extend(["--exclude-run-dir", str(run_dir)])
    for problem_id in exclude_ids:
        cmd.extend(["--exclude-id", problem_id])
    if include_ids is not None:
        cmd.extend(["--include-id-file", str(include_file)])

    stdout_path = round_dir / "launcher.stdout.log"
    stderr_path = round_dir / "launcher.stderr.log"
    process = subprocess.run(
        cmd,
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
    )
    stdout_path.write_text(process.stdout, encoding="utf-8")
    stderr_path.write_text(process.stderr, encoding="utf-8")

    if process.returncode != 0:
        raise RuntimeError(
            f"Repair round {round_index} failed with exit code {process.returncode}. "
            f"See {stderr_path}"
        )

    aggregate_path = round_dir / "aggregate.json"
    if not aggregate_path.exists():
        raise FileNotFoundError(
            f"Missing aggregate for round {round_index}: {aggregate_path}"
        )

    aggregate = json.loads(aggregate_path.read_text(encoding="utf-8"))
    return RoundSummary(
        round_index=round_index,
        run_dir=round_dir,
        selected_count=len(aggregate.get("selected_problem_ids", [])),
        successful_count=len(aggregate.get("successful_repairs", [])),
        failed_count=len(aggregate.get("failed_repairs", [])),
    )


def main() -> None:
    args = parse_args()
    ensure_valid_args(args)

    campaign_dir = make_campaign_dir(args)
    manifest = {
        "project_root": str(PROJECT_ROOT.resolve()),
        "campaign_dir": str(campaign_dir.resolve()),
        "max_rounds": args.max_rounds,
        "batch_size": args.batch_size,
        "parallelism": args.parallelism,
        "worker_timeout_seconds": args.worker_timeout_seconds,
        "models": {
            "Easy": args.easy_model,
            "Medium": args.medium_model,
            "Hard": args.hard_model,
        },
        "base_exclude_run_dirs": [
            str(path.resolve()) for path in args.base_exclude_run_dir
        ],
        "base_exclude_ids": sorted(set(args.base_exclude_id)),
    }
    write_json(campaign_dir / "campaign_config.json", manifest)

    exclude_run_dirs = list(args.base_exclude_run_dir)
    exclude_ids = sorted(set(args.base_exclude_id))
    include_ids: list[str] | None = None
    round_summaries: list[dict[str, Any]] = []
    final_failed_ids: list[str] = []

    for round_index in range(1, args.max_rounds + 1):
        summary = run_round(
            args=args,
            campaign_dir=campaign_dir,
            round_index=round_index,
            include_ids=include_ids,
            exclude_run_dirs=exclude_run_dirs,
            exclude_ids=exclude_ids,
        )
        exclude_run_dirs.append(summary.run_dir)

        aggregate = json.loads(
            (summary.run_dir / "aggregate.json").read_text(encoding="utf-8")
        )
        failed_ids = sorted(
            {item["id"] for item in aggregate.get("failed_repairs", [])}
        )
        final_failed_ids = failed_ids
        round_summaries.append(
            {
                "round_index": round_index,
                "run_dir": str(summary.run_dir),
                "selected_count": summary.selected_count,
                "successful_count": summary.successful_count,
                "failed_count": summary.failed_count,
            }
        )

        print(
            f"[round {round_index}] selected={summary.selected_count} "
            f"repaired={summary.successful_count} failed={summary.failed_count}"
        )

        if summary.failed_count == 0:
            break
        if summary.successful_count == 0:
            print(
                f"[round {round_index}] no progress on remaining holdouts; stopping retries.",
                file=sys.stderr,
            )
            break

        include_ids = failed_ids

    successful_total = 0
    # failed_total = 0
    successful_ids: set[str] = set()
    for round_item in round_summaries:
        aggregate = json.loads(
            (Path(round_item["run_dir"]) / "aggregate.json").read_text(encoding="utf-8")
        )
        successful_total += len(aggregate.get("successful_repairs", []))
        # failed_total = len(aggregate.get("failed_repairs", []))
        successful_ids.update(
            item["id"] for item in aggregate.get("successful_repairs", [])
        )

    campaign_summary = {
        "campaign_dir": str(campaign_dir.resolve()),
        "rounds": round_summaries,
        "successful_total": successful_total,
        "final_failed_ids": final_failed_ids,
        "successful_ids": sorted(successful_ids),
    }
    write_json(campaign_dir / "campaign_summary.json", campaign_summary)

    print(f"\nCampaign directory: {campaign_dir.resolve()}")
    print(f"Successful repairs across rounds: {successful_total}")
    print(f"Remaining holdouts: {len(final_failed_ids)}")


if __name__ == "__main__":
    main()
