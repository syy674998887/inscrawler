"""Contracts for mirroring the real process console into GUI/task logs."""

import io
import os
import threading
import unittest
from collections import deque
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import inscrawler.ui.console as ui_console
import inscrawler.ui.controllers.logs as ui_controllers_logs
import inscrawler.ui.main_window as ui_main_window
import inscrawler.ui.workers as ui_workers


def _bind_log_routing(target):
    for name in (
        "_append_console_log",
        "_console_task_entry",
        "_append_task_entry_log",
        "_on_patrol_task_log",
    ):
        setattr(target, name, MethodType(getattr(ui_main_window.GUI, name), target))
    return target


class ConsoleCaptureHubTests(unittest.TestCase):
    def setUp(self):
        self._original_capture_hub = ui_console._console_capture_hub
        self._original_debug_logging = ui_console._debug_logging_enabled
        self._original_gui_log_callback = ui_console._gui_log_callback
        self._original_console_log_callback = ui_console._console_log_callback

    def tearDown(self):
        ui_console._console_capture_hub = self._original_capture_hub
        ui_console._debug_logging_enabled = self._original_debug_logging
        ui_console._gui_log_callback = self._original_gui_log_callback
        ui_console._console_log_callback = self._original_console_log_callback

    def test_stdout_and_stderr_are_mirrored_as_one_ordered_line_stream(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        records = []
        hub = ui_console._ConsoleCaptureHub(records.append, stdout, stderr)

        hub.stdout.writelines(("boot", "\n"))
        hub.begin_task("download-1")
        hub.stdout.write("[fetch] first")
        hub.stdout.write("\n\n")
        hub.set_target("download-1", "@alice")
        hub.stderr.write("warning\r\n")
        hub.set_target("download-1", None)
        hub.stdout.write("tail")
        hub.end_task("download-1")
        hub.stderr.write("after\n")

        self.assertEqual(stdout.getvalue(), "boot\n[fetch] first\n\ntail")
        self.assertEqual(stderr.getvalue(), "warning\r\nafter\n")
        self.assertEqual(
            [record["line"] for record in records],
            ["boot", "[fetch] first", "", "warning", "tail", "after"],
        )
        self.assertEqual(
            [record["stream"] for record in records],
            ["stdout", "stdout", "stdout", "stderr", "stdout", "stderr"],
        )
        self.assertEqual(
            [record["task_id"] for record in records],
            [None, "download-1", "download-1", "download-1", "download-1", None],
        )
        self.assertEqual(
            [record["target_username"] for record in records],
            [None, None, None, "alice", None, None],
        )

    def test_debug_filter_is_applied_before_the_unified_stream(self):
        stdout = io.StringIO()
        records = []
        hub = ui_console._ConsoleCaptureHub(records.append, stdout, io.StringIO())
        hub.begin_task("task-1")
        legacy_task_log = Mock()
        legacy_console_log = Mock()

        def mirrored_print(*args, **kwargs):
            separator = kwargs.get("sep", " ")
            ending = kwargs.get("end", "\n")
            hub.stdout.write(separator.join(str(value) for value in args) + ending)

        with (
            patch.object(ui_console, "_console_capture_hub", hub),
            patch.object(ui_console, "_module_print", side_effect=mirrored_print),
            patch.object(ui_console, "_gui_log_callback", legacy_task_log),
            patch.object(ui_console, "_console_log_callback", legacy_console_log),
        ):
            ui_console._debug_logging_enabled = False
            ui_console.debug_print("hidden")
            ui_console.print("normal")
            ui_console._debug_logging_enabled = True
            ui_console.debug_print("visible")

        self.assertEqual(stdout.getvalue(), "[gui] normal\n[gui] visible\n")
        self.assertEqual(
            [(record["line"], record["task_id"]) for record in records],
            [("[gui] normal", "task-1"), ("[gui] visible", "task-1")],
        )
        legacy_task_log.assert_not_called()
        legacy_console_log.assert_not_called()

    def test_patrol_target_events_define_the_account_log_window(self):
        records = []
        hub = ui_console._ConsoleCaptureHub(
            records.append, io.StringIO(), io.StringIO()
        )
        hub.begin_task("patrol-1")
        thread = ui_workers.PatrolThread(
            ["alice"],
            "highlights",
            patrol_task_id="patrol-1",
        )

        with patch.object(ui_console, "_console_capture_hub", hub):
            thread._handle_target_event(
                {
                    "event": "target_started",
                    "target_username": "alice",
                    "target_index": 0,
                }
            )
            hub.stdout.write("inside alice\n")
            thread._handle_target_event(
                {
                    "event": "target_finished",
                    "target_username": "alice",
                    "target_index": 0,
                    "result": {},
                }
            )
            hub.stdout.write("after alice\n")

        self.assertEqual(
            [(record["line"], record["target_username"]) for record in records],
            [("inside alice", "alice"), ("after alice", None)],
        )

    def test_main_thread_write_drains_earlier_worker_record_first(self):
        bridge = ui_console._ConsoleCaptureBridge()
        delivered = []
        bridge.record_ready.connect(delivered.append)

        worker = threading.Thread(
            target=lambda: bridge.submit({"line": "worker first"})
        )
        worker.start()
        worker.join()
        self.assertEqual(delivered, [])

        bridge.submit({"line": "main second"})

        self.assertEqual(
            [record["line"] for record in delivered],
            ["worker first", "main second"],
        )


class ConsoleRecordRoutingTests(unittest.TestCase):
    def _routing_target(self, entries):
        return _bind_log_routing(
            SimpleNamespace(
                queue_items=list(entries),
                console_logs=deque(),
                _console_log_dialog=None,
                _log_dialog=None,
            )
        )

    def test_every_record_reaches_console_and_task_scopes_receive_their_subset(self):
        ordinary = {
            "task": {"task_id": "ordinary-1", "task_kind": "download"},
            "logs": [],
        }
        patrol = {
            "task": {"task_id": "patrol-1", "task_kind": "auto_patrol"},
            "logs": [],
            "log_targets": [],
        }
        target = self._routing_target((ordinary, patrol))

        for record in (
            {"line": "outside tasks", "task_id": None, "target_username": None},
            {"line": "ordinary line", "task_id": "ordinary-1", "target_username": None},
            {"line": "patrol global", "task_id": "patrol-1", "target_username": None},
            {"line": "patrol alice", "task_id": "patrol-1", "target_username": "alice"},
        ):
            ui_controllers_logs.TaskLogController._on_console_capture_record(
                target, record
            )

        self.assertEqual(
            list(target.console_logs),
            [
                "outside tasks",
                "ordinary line",
                "patrol global",
                "patrol alice",
            ],
        )
        self.assertEqual(ordinary["logs"], ["ordinary line"])
        self.assertEqual(patrol["logs"], ["patrol global", "patrol alice"])
        self.assertEqual(patrol["log_targets"], [None, "alice"])

    def test_task_finish_flushes_the_last_partial_line_before_saving(self):
        entry = {
            "task": {"task_id": "ordinary-1", "task_kind": "download"},
            "logs": [],
        }
        target = self._routing_target((entry,))
        saved_logs = []
        target.save_task = Mock(
            side_effect=lambda saved_entry: saved_logs.append(list(saved_entry["logs"]))
        )
        target._console_capture_hub = None
        hub = ui_console._ConsoleCaptureHub(
            lambda record: (
                ui_controllers_logs.TaskLogController._on_console_capture_record(
                    target, record
                )
            ),
            io.StringIO(),
            io.StringIO(),
        )
        target._console_capture_hub = hub

        with patch.object(ui_console, "_console_capture_hub", hub):
            hub.begin_task("ordinary-1")
            hub.stdout.write("final fragment")
            ui_controllers_logs.TaskLogController._finish_task_console_capture(
                target, "ordinary-1"
            )

        self.assertEqual(entry["logs"], ["final fragment"])
        self.assertEqual(saved_logs, [["final fragment"]])
        self.assertIsNone(hub.task_id)

    def test_console_and_patrol_histories_are_not_truncated_at_5000_lines(self):
        patrol = {
            "task": {"task_id": "patrol-1", "task_kind": "auto_patrol"},
            "logs": [],
            "log_targets": [],
        }
        target = self._routing_target((patrol,))

        for index in range(5001):
            target._append_console_log(str(index))
            target._on_patrol_task_log(patrol, str(index), target_username="alice")

        self.assertEqual(len(target.console_logs), 5001)
        self.assertEqual(len(patrol["logs"]), 5001)
        self.assertEqual(len(patrol["log_targets"]), 5001)


if __name__ == "__main__":
    unittest.main()
