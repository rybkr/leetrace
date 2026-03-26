from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Finalize an interrupted problem-repair run by restoring incomplete "
            "batch backups and writing aggregate artifacts from completed batches."
        )
    )
    parser.add_argument(
        "run_dir",
        type=Path,
        help="Path to the interrupted repair run directory.",
    )
    return parser.parse_args()


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_lines(path: Path, values: list[str]) -> None:
    path.write_text("".join(f"{value}\n" for value in values), encoding="utf-8")


def batch_dir_for(run_dir: Path, batch_entry: dict[str, Any]) -> Path:
    return run_dir / f"batch_{int(batch_entry['batch_index']):03d}_{batch_entry['difficulty'].lower()}"


def restore_incomplete_batch(batch_dir: Path, batch_entry: dict[str, Any]) -> tuple[list[str], list[str]]:
    restored: list[str] = []
    missing_backups: list[str] = []
    for problem_id, problem_path_str in zip(
        batch_entry.get("problem_ids", []),
        batch_entry.get("problem_paths", []),
    ):
        backup_path = batch_dir / "backups" / f"{problem_id}.json"
        problem_path = Path(problem_path_str)
        if not backup_path.exists():
            missing_backups.append(problem_id)
            continue
        shutil.copy2(backup_path, problem_path)
        restored.append(problem_id)
    return restored, missing_backups


def main() -> None:
    args = parse_args()
    run_dir = args.run_dir.resolve()
    batch_manifest_path = run_dir / "batch_manifest.json"
    run_config_path = run_dir / "run_config.json"

    if not batch_manifest_path.exists():
        raise FileNotFoundError(f"Missing batch manifest: {batch_manifest_path}")
    if not run_config_path.exists():
        raise FileNotFoundError(f"Missing run config: {run_config_path}")

    batch_manifest = json.loads(batch_manifest_path.read_text(encoding="utf-8"))
    run_config = json.loads(run_config_path.read_text(encoding="utf-8"))

    successful_repairs: list[dict[str, Any]] = []
    failed_repairs: list[dict[str, Any]] = []
    worker_outcomes: list[dict[str, Any]] = []
    completed_problem_ids: list[str] = []
    planned_problem_ids: list[str] = []
    interrupted_problem_ids: list[str] = []
    remaining_medium_ids: list[str] = []
    holdout_ids_easy_medium: list[str] = []

    for batch_entry in batch_manifest:
        batch_index = int(batch_entry["batch_index"])
        difficulty = batch_entry["difficulty"]
        batch_dir = batch_dir_for(run_dir, batch_entry)
        output_path = batch_dir / "worker.result.json"
        prompt_path = batch_dir / "worker.prompt.txt"
        stdout_path = batch_dir / "worker.stdout.log"
        stderr_path = batch_dir / "worker.stderr.log"
        unsolved_path = Path(batch_entry["unsolved_path"])
        problem_ids = list(batch_entry.get("problem_ids", []))
        problem_paths = list(batch_entry.get("problem_paths", []))
        solution_paths = list(batch_entry.get("solution_paths", []))

        planned_problem_ids.extend(problem_ids)

        if output_path.exists():
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            completed_problem_ids.extend(problem_ids)
            solution_by_id = dict(zip(problem_ids, solution_paths))
            problem_path_by_id = dict(zip(problem_ids, problem_paths))

            for item in payload.get("updated", []):
                problem_id = item.get("id")
                if not problem_id:
                    continue
                successful_repairs.append(
                    {
                        "id": problem_id,
                        "difficulty": difficulty,
                        "batch_index": batch_index,
                        "test_count": int(item.get("test_count", 0)),
                        "notes": item.get("notes", ""),
                        "problem_path": problem_path_by_id.get(problem_id, ""),
                        "solution_path": solution_by_id.get(problem_id, ""),
                    }
                )

            for item in payload.get("unsolved", []):
                problem_id = item.get("id")
                if not problem_id:
                    continue
                failed_repairs.append(
                    {
                        "id": problem_id,
                        "difficulty": difficulty,
                        "batch_index": batch_index,
                        "reason": item.get("reason", "Marked unsolved by worker."),
                    }
                )
                if difficulty in {"Easy", "Medium"}:
                    holdout_ids_easy_medium.append(problem_id)

            worker_outcomes.append(
                {
                    "batch_index": batch_index,
                    "difficulty": difficulty,
                    "model": batch_entry.get("model"),
                    "reasoning_effort": batch_entry.get("reasoning_effort"),
                    "success": True,
                    "returncode": 0,
                    "error": None,
                    "prompt_path": str(prompt_path),
                    "stdout_path": str(stdout_path),
                    "stderr_path": str(stderr_path),
                    "output_path": str(output_path),
                    "unsolved_path": str(unsolved_path),
                    "successful_repairs": len(payload.get("updated", [])),
                    "failed_repairs": len(payload.get("unsolved", [])),
                    "status": "completed",
                }
            )
            continue

        restored_ids, missing_backups = restore_incomplete_batch(batch_dir, batch_entry)
        interrupted_problem_ids.extend(problem_ids)
        if difficulty == "Medium":
            remaining_medium_ids.extend(problem_ids)

        worker_outcomes.append(
            {
                "batch_index": batch_index,
                "difficulty": difficulty,
                "model": batch_entry.get("model"),
                "reasoning_effort": batch_entry.get("reasoning_effort"),
                "success": False,
                "returncode": None,
                "error": "Interrupted before batch reconciliation.",
                "prompt_path": str(prompt_path),
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
                "output_path": str(output_path),
                "unsolved_path": str(unsolved_path),
                "successful_repairs": 0,
                "failed_repairs": 0,
                "status": "interrupted",
                "restored_problem_ids": restored_ids,
                "missing_backup_ids": missing_backups,
            }
        )

    successful_repairs.sort(key=lambda item: (item["difficulty"], item["id"]))
    failed_repairs.sort(key=lambda item: (item["difficulty"], item["id"]))
    worker_outcomes.sort(key=lambda item: item["batch_index"])

    remaining_medium_and_holdout_ids = sorted(set(remaining_medium_ids) | set(holdout_ids_easy_medium))

    aggregate = {
        "metadata": {
            **run_config,
            "finalized_from_interrupted_run": True,
            "planned_problem_count": len(planned_problem_ids),
            "completed_problem_count": len(completed_problem_ids),
            "interrupted_problem_count": len(interrupted_problem_ids),
        },
        "selected_problem_ids": completed_problem_ids,
        "planned_problem_ids": planned_problem_ids,
        "interrupted_problem_ids": interrupted_problem_ids,
        "worker_outcomes": worker_outcomes,
        "successful_repairs": successful_repairs,
        "failed_repairs": failed_repairs,
    }

    write_json(run_dir / "successful_repairs.json", successful_repairs)
    write_json(run_dir / "failed_repairs.json", failed_repairs)
    write_json(run_dir / "aggregate.json", aggregate)
    write_json(run_dir / "interrupted_problem_ids.json", sorted(set(interrupted_problem_ids)))
    write_json(run_dir / "remaining_medium_ids.json", sorted(set(remaining_medium_ids)))
    write_json(run_dir / "holdout_ids_easy_medium.json", sorted(set(holdout_ids_easy_medium)))
    write_json(
        run_dir / "remaining_medium_and_holdout_ids.json",
        remaining_medium_and_holdout_ids,
    )
    write_lines(run_dir / "remaining_medium_and_holdout_ids.txt", remaining_medium_and_holdout_ids)

    print(f"Run directory: {run_dir}")
    print(f"Completed batches: {sum(1 for item in worker_outcomes if item['status'] == 'completed')}")
    print(f"Interrupted batches restored: {sum(1 for item in worker_outcomes if item['status'] == 'interrupted')}")
    print(f"Successful repairs saved: {len(successful_repairs)}")
    print(f"Holdouts saved: {len(failed_repairs)}")
    print(f"Remaining medium IDs: {len(set(remaining_medium_ids))}")
    print(f"Remaining medium + holdout IDs: {len(remaining_medium_and_holdout_ids)}")


if __name__ == "__main__":
    main()
