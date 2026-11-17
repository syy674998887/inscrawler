"""Persistence contracts without a Qt application or real task directory."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from inscrawler.tasks import repository


def pending_entry(task_id="aaaaaaaaaaaa", order=0):
    return {
        "task": {
            "task_id": task_id,
            "task_kind": "download",
            "target_user": "alice",
            "download_media_type": "posts",
            "scope_unit": "page",
            "scope_limit": 1,
            "scope_completed": 0,
            "downloaded_count": 0,
            "media_request_cap": None,
            "notification": "",
            "created_at": "2026-01-01 00:00:00",
        },
        "status": "pending",
        "logs": [],
        "queue_order": order,
        "downloaded_files": [],
        "downloaded_file_details": [],
    }


class TaskRepositoryTests(unittest.TestCase):
    def test_round_trip_omits_credentials_and_ui_objects(self):
        entry = pending_entry()
        for key in repository.RUNTIME_TASK_FIELDS:
            entry["task"][key] = "runtime-only-value"
        entry["widget"] = object()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "aaaaaaaaaaaa.json"
            repository.write_record(path, repository.task_record(entry))
            result = repository.load_records(directory, Mock())
            self.assertEqual(result[0]["task"]["target_user"], "alice")
            self.assertEqual(result[0]["_runtime_queue_order"], 0)
            self.assertNotIn("runtime-only-value", path.read_text(encoding="utf-8"))
            self.assertNotIn("widget", json.loads(path.read_text(encoding="utf-8")))
            self.assertIn("login_username", entry["task"])

    def test_invalid_schema_does_not_replace_existing_task(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "aaaaaaaaaaaa.json"
            data = repository.task_record(pending_entry())
            repository.write_record(path, data)
            original = path.read_bytes()
            data["schema_version"] = 6
            with self.assertRaises(ValueError):
                repository.write_record(path, data)
            self.assertEqual(path.read_bytes(), original)

    def test_replace_failure_preserves_existing_record(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "aaaaaaaaaaaa.json"
            data = repository.task_record(pending_entry())
            repository.write_record(path, data)
            original = path.read_bytes()
            data["logs"].append("new log")
            with patch.object(repository.os, "replace", side_effect=OSError("busy")):
                with self.assertRaises(OSError):
                    repository.write_record(path, data)
            self.assertEqual(path.read_bytes(), original)

    def test_invalid_records_are_reported_and_do_not_block_queue_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository.write_record(
                root / "aaaaaaaaaaaa.json",
                repository.task_record(pending_entry(order=8)),
            )
            repository.write_record(
                root / "bbbbbbbbbbbb.json",
                repository.task_record(pending_entry("bbbbbbbbbbbb", 2)),
            )
            (root / "bad.json").write_text("{invalid", encoding="utf-8")
            (root / "ignored.tmp").write_text("incomplete", encoding="utf-8")
            invalid = Mock()
            records = repository.load_records(root, invalid)
            self.assertEqual(
                [r["task"]["task_id"] for r in records],
                ["bbbbbbbbbbbb", "aaaaaaaaaaaa"],
            )
            self.assertEqual([r["queue_order"] for r in records], [2, 8])
            self.assertEqual([r["_runtime_queue_order"] for r in records], [0, 1])
            invalid.assert_called_once()
            self.assertEqual(invalid.call_args.args[0], "bad.json")
