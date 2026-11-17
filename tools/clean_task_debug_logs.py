"""Remove selected historical log noise from saved task records.

The default mode is read-only. Close the GUI and pass ``--apply`` to update
task files. Every changed JSON file is copied to a timestamped backup directory
before any task file is replaced.
"""

import argparse
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from inscrawler import paths

TASKS_DIR = Path(paths.TASKS_DIR)
DEBUG_LOG_PATTERNS = (
    re.compile(r"\[gui\] Video thumbnail started: .*"),
    re.compile(r"\[gui\] Video thumbnail completed: .*"),
    re.compile(r"\[gui\] Preview added: .*"),
    re.compile(r"\[gui\] Created media cell \(\d+, \d+\) with total: \d+"),
    re.compile(r"\[gui\] \[layout\] locked min window height = \d+px"),
    re.compile(r"\[gui\] Loaded \d+ preview history item\(s\)"),
)
SIGNAL_DISCONNECT_WARNING_PATTERN = re.compile(
    r"(?:[A-Za-z]:[\\/]|/)[^\r\n]*[\\/]gui\.py:\d+: RuntimeWarning: "
    r"Failed to disconnect \(None\) "
    r'from signal "log_signal\(QString\)"\.'
)


@dataclass(frozen=True)
class CleanupPlan:
    path: Path
    data: dict
    signature: tuple[int, int, int, int]
    original_bytes: bytes


def is_task_debug_log(value):
    return isinstance(value, str) and any(
        pattern.fullmatch(value) for pattern in DEBUG_LOG_PATTERNS
    )


def is_signal_disconnect_warning(value):
    return (
        isinstance(value, str)
        and SIGNAL_DISCONNECT_WARNING_PATTERN.fullmatch(value) is not None
    )


def _removable_log_indexes(logs):
    indexes = {index for index, line in enumerate(logs) if is_task_debug_log(line)}
    for index, line in enumerate(logs):
        if not is_signal_disconnect_warning(line):
            continue
        indexes.add(index)
        source_index = index + 1
        if (
            source_index < len(logs)
            and isinstance(logs[source_index], str)
            and logs[source_index].startswith("  ")
        ):
            indexes.add(source_index)
    return indexes


def clean_task(data):
    """Remove selected noise and keep patrol log ownership aligned."""
    logs = data.get("logs")
    if not isinstance(logs, list):
        return 0

    removable_indexes = _removable_log_indexes(logs)
    keep_indexes = [
        index for index in range(len(logs)) if index not in removable_indexes
    ]
    removed = len(logs) - len(keep_indexes)
    if not removed:
        return 0

    cleaned_targets = None
    if "log_targets" in data:
        log_targets = data["log_targets"]
        if not isinstance(log_targets, list):
            raise ValueError("log_targets must be a list when debug logs are removed")
        if len(log_targets) != len(logs):
            raise ValueError(
                "logs and log_targets must have equal lengths before cleanup"
            )
        cleaned_targets = [log_targets[index] for index in keep_indexes]

    data["logs"] = [logs[index] for index in keep_indexes]
    if cleaned_targets is not None:
        data["log_targets"] = cleaned_targets
    return removed


def file_signature(path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def build_cleanup_plan(tasks_dir=TASKS_DIR):
    tasks_path = Path(tasks_dir)
    if not tasks_path.is_dir():
        raise FileNotFoundError(f"Task directory does not exist: {tasks_path}")

    plans = []
    scanned = removed = 0
    for path in sorted(tasks_path.glob("*.json")):
        scanned += 1
        signature = file_signature(path)
        original_bytes = path.read_bytes()
        if file_signature(path) != signature:
            raise RuntimeError(f"Task changed while it was being scanned: {path}")
        data = json.loads(original_bytes.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"Task JSON root must be an object: {path}")
        try:
            removed_from_file = clean_task(data)
        except ValueError as exc:
            raise ValueError(f"Invalid task log structure in {path}: {exc}") from exc
        if not removed_from_file:
            continue
        plans.append(CleanupPlan(path, data, signature, original_bytes))
        removed += removed_from_file
    return plans, scanned, removed


def _validate_unchanged(plans):
    for plan in plans:
        if file_signature(plan.path) != plan.signature:
            raise RuntimeError(
                f"Task changed after scan; no task files were written: {plan.path}"
            )


def _create_backup_dir(tasks_dir):
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = Path(tasks_dir) / (
        f"debug-log-cleanup-backup-{timestamp}-{uuid.uuid4().hex[:8]}"
    )
    backup_dir.mkdir(exist_ok=False)
    return backup_dir


def _write_json(path, data):
    temp_path = path.with_name(f".{path.name}.clean-{uuid.uuid4().hex}.tmp")
    try:
        with temp_path.open("x", encoding="utf-8", newline="\n") as file_obj:
            json.dump(data, file_obj, indent=2, ensure_ascii=False)
            file_obj.write("\n")
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def apply_cleanup_plan(plans):
    plans = list(plans)
    if not plans:
        return None

    _validate_unchanged(plans)
    backup_dir = _create_backup_dir(plans[0].path.resolve().parent)
    for plan in plans:
        backup_path = backup_dir / plan.path.name
        with backup_path.open("xb") as file_obj:
            file_obj.write(plan.original_bytes)

    _validate_unchanged(plans)
    for plan in plans:
        _write_json(plan.path, plan.data)
    return backup_dir


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Remove selected GUI debug chatter and obsolete signal-disconnect "
            "warnings from saved task JSON files."
        ),
        epilog="Close the GUI before using --apply so it cannot save stale logs back.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Back up and replace affected task files; otherwise perform a dry-run.",
    )
    args = parser.parse_args()

    backup_dir = None
    try:
        plans, scanned, removed = build_cleanup_plan()
        if args.apply:
            backup_dir = apply_cleanup_plan(plans)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))

    print(f"Scanned task files: {scanned}")
    print(f"Changed task files: {len(plans)}")
    print(f"Removed debug log entries: {removed}")
    print(f"Mode: {'apply' if args.apply else 'dry-run'}")
    if backup_dir is not None:
        print(f"Backup directory: {backup_dir}")


if __name__ == "__main__":
    main()
