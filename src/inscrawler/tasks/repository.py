"""Strict task records and atomic JSON persistence, independent of Qt."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable
from pathlib import Path

from inscrawler.tasks import schema

RUNTIME_TASK_FIELDS = (
    "login_username",
    "save_path",
    "webhook",
    "telegram_bot_token",
    "telegram_chat_id",
)


def task_record(entry: dict) -> dict:
    """Build the existing schema-v7 record without UI or credential fields."""
    task = entry["task"].copy()
    for key in RUNTIME_TASK_FIELDS:
        task.pop(key, None)
    data = {
        "schema_version": schema.TASK_SCHEMA_VERSION,
        "task": task,
        "status": entry["status"],
        "logs": entry["logs"],
        "downloaded_files": list(entry.get("downloaded_files", [])),
        "downloaded_file_details": list(entry.get("downloaded_file_details", [])),
    }
    if schema._uses_queue_order(task, entry["status"]):
        data["queue_order"] = entry.get("queue_order")
    if entry["status"] == "completed":
        data["completed_at"] = entry.get("completed_at")
    if schema._is_auto_patrol_task(task):
        data["patrol_state"] = entry["patrol_state"]
        data["log_targets"] = list(entry["log_targets"])
    return data


def write_record(path: str | Path, data: dict) -> None:
    """Validate before replacing a task file; readers see only complete JSON."""
    path = Path(path)
    schema._validate_saved_task_data(data, expected_task_id=path.stem)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(path.name + ".tmp")
    with temp_path.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    os.replace(temp_path, path)


def next_queue_order(entries: Iterable[dict]) -> int:
    orders = [
        entry.get("queue_order")
        for entry in entries
        if schema._uses_queue_order(entry.get("task"), entry.get("status"))
        and type(entry.get("queue_order")) is int
        and entry["queue_order"] >= 0
    ]
    return max(orders, default=-1) + 1


def ordered_records(items: Iterable[dict]) -> list[dict]:
    """History precedes unfinished tasks; normalize only the runtime queue order."""
    history, unfinished = [], []
    for item in items:
        destination = (
            unfinished
            if schema._uses_queue_order(item.get("task"), item.get("status"))
            else history
        )
        destination.append(item)
    history.sort(
        key=lambda item: (
            item["completed_at"],
            item["task"]["created_at"],
            item["task"]["task_id"],
        )
    )
    unfinished.sort(
        key=lambda item: (
            item["queue_order"],
            item["task"]["created_at"],
            item["task"]["task_id"],
        )
    )
    # Return detached top-level records so sorting does not mutate caller state.
    return history + [
        dict(item, _runtime_queue_order=order) for order, item in enumerate(unfinished)
    ]


def load_records(
    directory: str | Path, on_invalid: Callable[[str, Exception], None]
) -> list[dict]:
    directory = Path(directory)
    if not directory.exists():
        return []
    items = []
    for path in sorted(directory.iterdir()):
        if not path.name.endswith(".json"):
            continue
        try:
            with path.open("r", encoding="utf-8") as stream:
                data = json.load(stream)
            schema._validate_saved_task_data(data, expected_task_id=path.stem)
        except (OSError, json.JSONDecodeError, ValueError) as error:
            on_invalid(path.name, error)
            continue
        items.append(data)
    return ordered_records(items)
