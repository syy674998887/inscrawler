import json
import tempfile
import unittest
from pathlib import Path

from tools import clean_task_debug_logs as cleaner


class CleanTaskDebugLogTests(unittest.TestCase):
    def test_matches_only_the_six_exact_debug_log_shapes(self):
        matching = [
            "[gui] Video thumbnail started: newest.mp4",
            "[gui] Video thumbnail completed: newest.mp4",
            "[gui] Preview added: newest.mp4",
            "[gui] Created media cell (11, 2) with total: 36",
            "[gui] [layout] locked min window height = 720px",
            "[gui] Loaded 45 preview history item(s)",
        ]
        for line in matching:
            with self.subTest(line=line):
                self.assertTrue(cleaner.is_task_debug_log(line))

        non_matching = [
            "[fetch] Video thumbnail started: newest.mp4",
            "Observer quoted: [gui] Preview added: newest.mp4",
            "[gui] Created media cell for a tutorial",
            "[gui] Loaded preview history",
            None,
        ]
        for line in non_matching:
            with self.subTest(line=line):
                self.assertFalse(cleaner.is_task_debug_log(line))

    def test_clean_task_filters_log_targets_at_the_same_indexes(self):
        data = {
            "logs": [
                "[gui] Video thumbnail started: first.mp4",
                "keep alice",
                "[gui] Preview added: second.mp4",
                "keep global",
            ],
            "log_targets": ["alice", "alice", "bob", None],
            "downloaded_files": ["first.mp4", "second.mp4"],
        }

        removed = cleaner.clean_task(data)

        self.assertEqual(removed, 2)
        self.assertEqual(data["logs"], ["keep alice", "keep global"])
        self.assertEqual(data["log_targets"], ["alice", None])
        self.assertEqual(data["downloaded_files"], ["first.mp4", "second.mp4"])
        self.assertEqual(cleaner.clean_task(data), 0)

    def test_clean_task_removes_signal_warning_and_its_source_line(self):
        warning = (
            "B:\\AI\\inscrawler\\gui.py:9937: RuntimeWarning: Failed to "
            'disconnect (None) from signal "log_signal(QString)".'
        )
        shifted_warning = (
            "B:\\AI\\inscrawler\\gui.py:8675: RuntimeWarning: Failed to "
            'disconnect (None) from signal "log_signal(QString)".'
        )
        data = {
            "logs": [
                "keep before",
                warning,
                "  sig.disconnect()",
                "keep middle",
                shifted_warning,
                "  return",
                "keep after",
            ],
            "log_targets": [
                "alice",
                "alice",
                "alice",
                None,
                "bob",
                "bob",
                None,
            ],
        }

        removed = cleaner.clean_task(data)

        self.assertEqual(removed, 4)
        self.assertEqual(
            data["logs"],
            ["keep before", "keep middle", "keep after"],
        )
        self.assertEqual(data["log_targets"], ["alice", None, None])

    def test_signal_warning_match_is_anchored_to_the_known_runtime_warning(self):
        warning = (
            "B:\\AI\\inscrawler\\gui.py:9937: RuntimeWarning: Failed to "
            'disconnect (None) from signal "log_signal(QString)".'
        )

        self.assertTrue(cleaner.is_signal_disconnect_warning(warning))
        self.assertFalse(
            cleaner.is_signal_disconnect_warning(f"Observer quoted: {warning}")
        )
        self.assertFalse(
            cleaner.is_signal_disconnect_warning(
                warning.replace("log_signal", "message_signal")
            )
        )

    def test_misaligned_log_targets_abort_without_mutating_the_task(self):
        data = {
            "logs": ["[gui] Preview added: item.mp4", "keep"],
            "log_targets": ["alice"],
        }
        original = json.loads(json.dumps(data))

        with self.assertRaises(ValueError):
            cleaner.clean_task(data)

        self.assertEqual(data, original)

    def test_apply_creates_exact_backups_and_atomically_replaces_tasks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tasks_dir = Path(temp_dir) / "tasks"
            tasks_dir.mkdir()
            task_path = tasks_dir / "task.json"
            original = {
                "logs": [
                    "keep",
                    "[gui] Video thumbnail completed: clip.mp4",
                ],
                "status": "completed",
            }
            original_bytes = (json.dumps(original) + "\n").encode("utf-8")
            task_path.write_bytes(original_bytes)

            plans, scanned, removed = cleaner.build_cleanup_plan(tasks_dir)
            self.assertEqual((scanned, removed, len(plans)), (1, 1, 1))

            backup_dir = cleaner.apply_cleanup_plan(plans)

            self.assertIsNotNone(backup_dir)
            self.assertEqual((backup_dir / task_path.name).read_bytes(), original_bytes)
            cleaned = json.loads(task_path.read_text(encoding="utf-8"))
            self.assertEqual(cleaned, {"logs": ["keep"], "status": "completed"})
            self.assertEqual(cleaner.build_cleanup_plan(tasks_dir)[2], 0)

    def test_apply_aborts_if_a_task_changes_after_scan(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tasks_dir = Path(temp_dir)
            task_path = tasks_dir / "task.json"
            task_path.write_text(
                json.dumps({"logs": ["[gui] Preview added: clip.mp4"]}),
                encoding="utf-8",
            )
            plans, _scanned, _removed = cleaner.build_cleanup_plan(tasks_dir)
            changed = {"logs": ["new worker state that must survive", "extra"]}
            task_path.write_text(json.dumps(changed), encoding="utf-8")

            with self.assertRaises(RuntimeError):
                cleaner.apply_cleanup_plan(plans)

            self.assertEqual(
                json.loads(task_path.read_text(encoding="utf-8")),
                changed,
            )
            self.assertFalse(list(tasks_dir.glob("debug-log-cleanup-backup-*")))


if __name__ == "__main__":
    unittest.main()
