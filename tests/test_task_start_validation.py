"""Offline GUI coverage for login checks at task execution boundaries."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import inscrawler.diagnostics.console as diagnostics_console
import inscrawler.downloads.control as downloads_control
import inscrawler.downloads.models as downloads_models
import inscrawler.downloads.service as downloads_service
import inscrawler.notifications.service as notifications_service
import inscrawler.paths as app_paths
import inscrawler.ui.console as ui_console
import inscrawler.ui.workers as ui_workers
import tests.test_main_window_minimum_height as window_fixture


class TaskStartValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        window_fixture.MainWindowMinimumHeightTests.setUpClass()

    def setUp(self):
        self.fixture = window_fixture.MainWindowMinimumHeightTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.window = self.fixture.window
        self.session_dir = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(app_paths, "SESSION_DIR", self.session_dir))
        self.enterContext(patch.object(self.window, "save_settings"))
        self.warning = self.enterContext(patch.object(self.window, "_show_message_box"))
        self.patrol_targets = self.enterContext(
            patch.object(
                self.window, "_load_patrol_target_snapshot", return_value=["alice"]
            )
        )
        self.configure = self.enterContext(
            patch.object(downloads_service, "config_settings")
        )
        self.download_worker = self.enterContext(
            patch.object(ui_workers, "DownloadThread")
        )
        self.patrol_worker = self.enterContext(patch.object(ui_workers, "PatrolThread"))
        self.download_worker.return_value.isRunning.return_value = False
        self.patrol_worker.return_value.isRunning.return_value = False
        self.notify = self.enterContext(patch.object(notifications_service, "notify"))
        self.shutdown = self.enterContext(
            patch.object(self.window, "_maybe_request_shutdown_after_queue")
        )
        self.enterContext(patch.object(ui_console, "_gui_log_callback", None))
        self.enterContext(patch.object(ui_console, "print"))
        self.enterContext(patch.object(diagnostics_console, "log_callback", None))
        for name in ("resume_downloads", "pause_downloads", "clear_stop_request"):
            self.enterContext(patch.object(downloads_control, name))
        self.window.username.clear()
        self.window.session_cookie.clear()
        self.window.save_path.setText(self.fixture.temp_tasks.name)

    def _queue_tasks(self):
        self.window.target.setEditText("alice")
        self.window.add_task()
        self.window.add_patrol_task()
        self.assertEqual(len(self.window.queue_items), 2)

    def _assert_not_started(self):
        self.download_worker.assert_not_called()
        self.patrol_worker.assert_not_called()
        self.configure.assert_not_called()
        self.notify.assert_not_called()
        self.shutdown.assert_not_called()
        self.assertTrue(
            all(entry["status"] == "pending" for entry in self.window.queue_items)
        )
        self.assertFalse(self.window.queue_running)
        self.assertFalse(self.window.queue_paused)
        self.assertIsNone(self.window.current_queue_index)

    def _finish_download(self):
        self.window.handle_task_complete(
            success=True,
            result={
                "status": downloads_models.status_code.SUCCESS,
                "scope_completed_count": 1,
                "downloaded_count": 0,
            },
        )

    def _assert_download_then_patrol_start(self):
        self.window.start_download()
        self.download_worker.return_value.start.assert_called_once_with()
        self.assertEqual(self.configure.call_args.kwargs["username_input"], "owner")
        self._finish_download()
        self.patrol_worker.return_value.start.assert_called_once_with()
        self.assertEqual(self.configure.call_count, 2)
        self.assertEqual(self.configure.call_args.kwargs["username_input"], "owner")
        self.assertEqual(self.window.queue_items[0]["status"], "completed")
        self.assertEqual(self.window.queue_items[1]["status"], "running")
        self.warning.assert_not_called()

    def test_adding_both_task_types_does_not_require_login(self):
        self._queue_tasks()

        self.warning.assert_not_called()
        self._assert_not_started()
        for entry in self.window.queue_items:
            self.assertIsNone(entry["task"]["login_username"])
            self.assertTrue(
                Path(self.window._task_file_path(entry["task"]["task_id"])).is_file()
            )

    def test_adding_tasks_still_requires_download_or_patrol_targets(self):
        self.window.target.setEditText("")
        self.window.add_task()
        self.assertEqual(
            self.warning.call_args.args[2], self.window.tr("warning_target_required")
        )
        self.patrol_targets.return_value = []
        self.window.add_patrol_task()
        self.assertEqual(
            self.warning.call_args.args[2], self.window.tr("warning_no_patrol_targets")
        )
        self.assertEqual(self.window.queue_items, [])

    def test_starting_either_task_without_username_keeps_it_pending(self):
        self._queue_tasks()
        self.window.shutdown_after_queue = True
        for index in range(2):
            with self.subTest(
                task_kind=self.window.queue_items[index]["task"]["task_kind"]
            ):
                self.assertFalse(self.window.start_task_by_index(index))
                self.assertEqual(
                    self.warning.call_args.args[2],
                    self.window.tr("warning_username_required"),
                )
                self._assert_not_started()

    def test_starting_either_task_without_cookie_or_session_keeps_it_pending(self):
        self._queue_tasks()
        self.window.username.setText("owner")
        for index in range(2):
            with self.subTest(
                task_kind=self.window.queue_items[index]["task"]["task_kind"]
            ):
                self.assertFalse(self.window.start_task_by_index(index))
                self.assertEqual(
                    self.warning.call_args.args[2],
                    self.window.tr("warning_no_session", username="owner"),
                )
                self._assert_not_started()

    def test_cookie_filled_after_queueing_is_used_by_both_workers(self):
        self._queue_tasks()
        self.window.username.setText("owner")
        self.window.session_cookie.setText("sessionid=test-cookie")

        self._assert_download_then_patrol_start()

        for call in self.configure.call_args_list:
            self.assertEqual(
                call.kwargs["session_cookie_input"], "sessionid=test-cookie"
            )

    def test_saved_session_allows_both_workers_without_cookie_input(self):
        self._queue_tasks()
        self.window.username.setText("owner")
        Path(self.session_dir, "owner").touch()

        self._assert_download_then_patrol_start()

        for call in self.configure.call_args_list:
            self.assertEqual(call.kwargs["session_cookie_input"], "")

    def test_automatic_next_task_checks_login_before_starting(self):
        self._queue_tasks()
        self.window.username.setText("owner")
        self.window.session_cookie.setText("sessionid=test-cookie")
        self.window.start_download()
        self.window.session_cookie.clear()

        self._finish_download()

        self.patrol_worker.assert_not_called()
        self.assertEqual(self.window.queue_items[1]["status"], "pending")
        self.assertFalse(self.window.queue_running)
        self.assertIsNone(self.window.current_queue_index)
        self.assertEqual(
            self.warning.call_args.args[2],
            self.window.tr("warning_no_session", username="owner"),
        )
        self.notify.assert_not_called()
        self.shutdown.assert_not_called()

    def test_resume_between_tasks_checks_login_before_creating_a_worker(self):
        self._queue_tasks()
        self.window.queue_paused = True

        self.window.start_download()

        self._assert_not_started()
        self.assertEqual(
            self.warning.call_args.args[2], self.window.tr("warning_username_required")
        )

    def test_resuming_an_existing_worker_keeps_its_authenticated_session(self):
        self._queue_tasks()
        self.window.username.setText("owner")
        self.window.session_cookie.setText("sessionid=test-cookie")
        self.window.start_download()
        self.download_worker.return_value.isRunning.return_value = True
        self.window.pause_download_queue()
        self.window.username.clear()
        self.window.session_cookie.clear()

        self.window.start_download()

        self.assertFalse(self.window.queue_paused)
        self.download_worker.assert_called_once()
        self.configure.assert_called_once()
        self.warning.assert_not_called()
