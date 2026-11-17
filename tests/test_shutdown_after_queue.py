"""Contracts for the one-shot shut-down-after-queue control."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

import inscrawler.paths as app_paths

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

import inscrawler.downloads.control as downloads_control
import inscrawler.downloads.models as downloads_models
import inscrawler.notifications.service as notifications_service
import inscrawler.ui.console as ui_console
import inscrawler.ui.constants as ui_constants
import inscrawler.ui.controllers.desktop as ui_controllers_desktop
import inscrawler.ui.controllers.execution as ui_controllers_execution
import inscrawler.ui.controllers.patrol as ui_controllers_patrol
import inscrawler.ui.controllers.targets as ui_controllers_targets
import inscrawler.ui.controllers.task_store as ui_controllers_task_store
import inscrawler.ui.dialogs.shutdown as ui_dialogs_shutdown
import inscrawler.ui.i18n.text as ui_i18n_text
import inscrawler.ui.main_window as ui_main_window
import inscrawler.ui.platform as ui_platform
import inscrawler.ui.preferences as ui_preferences
import inscrawler.ui.preview.controller as ui_preview_controller
from tests.queue_fixtures import make_queue_target, task_entry


class ShutdownCommandTests(unittest.TestCase):
    def test_shutdown_command_is_forced_and_immediate_after_the_app_countdown(self):
        self.assertEqual(ui_constants.SHUTDOWN_COUNTDOWN_SECONDS, 60)
        self.assertEqual(
            ui_platform._system_shutdown_command(),
            ["shutdown", "/s", "/f", "/t", "0"],
        )


class ShutdownCountdownDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _make_window(self, language="en"):
        window = SimpleNamespace(ui_language=language)
        window.tr = lambda key, **kwargs: (
            ui_i18n_text.UI_TEXT[language][key].format(**kwargs)
            if kwargs
            else ui_i18n_text.UI_TEXT[language][key]
        )
        window.windowIcon = Mock(return_value=self.app.windowIcon())
        return window

    def test_countdown_accepts_when_it_reaches_zero(self):
        elapsed = []
        cancelled = []
        dialog_results_seen_by_callback = []
        dialog = None

        def on_elapsed():
            elapsed.append(True)
            dialog_results_seen_by_callback.append(dialog.result())

        dialog = ui_dialogs_shutdown.ShutdownCountdownDialog(
            self._make_window(),
            2,
            on_elapsed,
            lambda: cancelled.append(True),
        )
        dialog._tick()
        self.assertEqual(dialog._remaining, 1)
        dialog._tick()
        self.assertTrue(dialog._finished_naturally)
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(elapsed, [True])
        self.assertEqual(
            dialog_results_seen_by_callback,
            [QDialog.DialogCode.Accepted],
        )
        self.assertEqual(cancelled, [])
        dialog.close()
        dialog.deleteLater()
        self.app.processEvents()

    def test_finished_countdown_cannot_fire_or_cancel_twice(self):
        elapsed = []
        cancelled = []
        dialog = ui_dialogs_shutdown.ShutdownCountdownDialog(
            self._make_window(),
            1,
            lambda: elapsed.append(True),
            lambda: cancelled.append(True),
        )

        dialog._tick()
        dialog._tick()
        dialog.cancel_shutdown()
        dialog.reject()
        dialog.start_countdown()

        self.assertEqual(elapsed, [True])
        self.assertEqual(cancelled, [])
        self.assertFalse(dialog._timer.isActive())
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        dialog.close()
        dialog.deleteLater()
        self.app.processEvents()

    def test_cancel_button_stops_only_the_local_countdown(self):
        elapsed = []
        cancelled = []
        dialog = ui_dialogs_shutdown.ShutdownCountdownDialog(
            self._make_window(),
            60,
            lambda: elapsed.append(True),
            lambda: cancelled.append(True),
        )
        dialog.cancel_shutdown()
        self.assertEqual(cancelled, [True])
        self.assertEqual(elapsed, [])
        self.assertTrue(dialog._cancelled)
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        dialog.deleteLater()
        self.app.processEvents()


class ShutdownAfterQueueHookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _bind(self, target):
        for name in (
            "set_shutdown_after_queue",
            "_on_shutdown_after_queue_changed",
            "_maybe_request_shutdown_after_queue",
            "_ensure_system_shutdown_supported",
            "_request_system_shutdown_now",
            "_cancel_local_shutdown_countdown",
        ):
            setattr(target, name, MethodType(getattr(ui_main_window.GUI, name), target))
        return target

    def _make_target(self, **overrides):
        target = SimpleNamespace(
            shutdown_after_queue=True,
            _shutdown_countdown_dialog=None,
            _safe_exit_in_progress=False,
            _force_exit=False,
            _console_capture_bridge=None,
            flush_preview_history=Mock(),
            save_tasks=Mock(),
            ui_language="en",
            shutdown_after_queue_check=None,
            tr=lambda key, **kwargs: (
                ui_i18n_text.UI_TEXT["en"][key].format(**kwargs)
                if kwargs
                else ui_i18n_text.UI_TEXT["en"][key]
            ),
            _show_message_box=Mock(),
            _open_shutdown_countdown_dialog=Mock(),
            _send_shutdown_countdown_notify=Mock(),
        )
        target.__dict__.update(overrides)
        return self._bind(target)

    def test_default_one_shot_starts_only_the_local_countdown(self):
        target = self._make_target()

        with (
            patch.object(ui_platform, "_run_system_shutdown_command") as run,
            patch.object(ui_controllers_desktop, "os") as os_mod,
        ):
            os_mod.name = "nt"
            fired = ui_controllers_desktop.DesktopController._maybe_request_shutdown_after_queue(
                target
            )

        self.assertTrue(fired)
        self.assertFalse(target.shutdown_after_queue)
        target._send_shutdown_countdown_notify.assert_called_once_with(
            "Queue finished. Forced shutdown in 60 seconds. Unsaved changes may be lost."
        )
        target._open_shutdown_countdown_dialog.assert_called_once_with(60)
        run.assert_not_called()

    def test_countdown_opens_before_notification_dispatch(self):
        target = self._make_target()
        order = []
        target._open_shutdown_countdown_dialog = lambda seconds: order.append(
            ("dialog", seconds)
        )
        target._send_shutdown_countdown_notify = lambda message: order.append(
            ("notify", message)
        )

        with patch.object(ui_controllers_desktop, "os") as os_mod:
            os_mod.name = "nt"
            ui_controllers_desktop.DesktopController._maybe_request_shutdown_after_queue(
                target
            )

        self.assertEqual(
            order,
            [
                ("dialog", 60),
                (
                    "notify",
                    "Queue finished. Forced shutdown in 60 seconds. Unsaved changes may be lost.",
                ),
            ],
        )

    def test_disabled_switch_does_not_arm_shutdown(self):
        target = self._make_target(shutdown_after_queue=False)
        with patch.object(ui_platform, "_run_system_shutdown_command") as run:
            fired = ui_controllers_desktop.DesktopController._maybe_request_shutdown_after_queue(
                target
            )

        self.assertFalse(fired)
        run.assert_not_called()
        target._send_shutdown_countdown_notify.assert_not_called()
        target._open_shutdown_countdown_dialog.assert_not_called()

    def test_nonzero_immediate_shutdown_exit_is_reported(self):
        target = self._make_target()
        completed = SimpleNamespace(
            returncode=1190, stdout="", stderr="already scheduled"
        )
        with (
            patch.object(
                ui_platform, "_run_system_shutdown_command", return_value=completed
            ),
            patch.object(ui_controllers_desktop, "os") as os_mod,
        ):
            os_mod.name = "nt"
            fired = (
                ui_controllers_desktop.DesktopController._request_system_shutdown_now(
                    target
                )
            )

        self.assertFalse(fired)
        target._show_message_box.assert_called_once()

    def test_non_windows_hosts_do_not_call_shutdown_exe(self):
        target = self._make_target()
        with (
            patch.object(ui_controllers_desktop, "os") as os_mod,
            patch.object(ui_platform, "_run_system_shutdown_command") as run,
        ):
            os_mod.name = "posix"
            fired = ui_controllers_desktop.DesktopController._maybe_request_shutdown_after_queue(
                target
            )

        self.assertFalse(fired)
        run.assert_not_called()
        target._send_shutdown_countdown_notify.assert_not_called()
        target._show_message_box.assert_called_once()
        target._open_shutdown_countdown_dialog.assert_not_called()

    def test_immediate_shutdown_is_requested_only_when_explicitly_called(self):
        target = self._make_target(shutdown_after_queue=False)
        completed = SimpleNamespace(returncode=0, stdout="", stderr="")
        with (
            patch.object(
                ui_platform, "_run_system_shutdown_command", return_value=completed
            ) as run,
            patch.object(ui_controllers_desktop, "os") as os_mod,
        ):
            os_mod.name = "nt"
            self.assertTrue(
                ui_controllers_desktop.DesktopController._request_system_shutdown_now(
                    target
                )
            )

        run.assert_called_once_with(["shutdown", "/s", "/f", "/t", "0"])

    def test_pre_shutdown_exception_is_reported_without_running_shutdown(self):
        target = self._make_target()
        target.flush_preview_history.side_effect = RuntimeError("flush failed")
        with (
            patch.object(ui_platform, "_run_system_shutdown_command") as run,
            patch.object(ui_controllers_desktop, "os") as os_mod,
        ):
            os_mod.name = "nt"
            self.assertFalse(target._request_system_shutdown_now())

        run.assert_not_called()
        target._show_message_box.assert_called_once()
        self.assertIn("flush failed", target._show_message_box.call_args.args[2])

    def test_countdown_start_failure_keeps_the_one_shot_enabled(self):
        target = self._make_target()
        target._open_shutdown_countdown_dialog.side_effect = RuntimeError(
            "dialog failed"
        )

        with patch.object(ui_controllers_desktop, "os") as os_mod:
            os_mod.name = "nt"
            fired = ui_controllers_desktop.DesktopController._maybe_request_shutdown_after_queue(
                target
            )

        self.assertFalse(fired)
        self.assertTrue(target.shutdown_after_queue)
        target._send_shutdown_countdown_notify.assert_not_called()
        target._show_message_box.assert_called_once()

    def test_countdown_notification_uses_background_dispatch(self):
        target = self._make_target()

        with patch.object(notifications_service, "notify") as notify:
            worker = ui_controllers_desktop.DesktopController._send_shutdown_countdown_notify(
                target,
                "Queue finished. Forced shutdown in 60 seconds. Unsaved changes may be lost.",
            )

        self.assertIs(worker, notify.return_value)
        notify.assert_called_once_with(
            "Queue finished. Forced shutdown in 60 seconds. Unsaved changes may be lost.",
            background=True,
        )


class ShutdownAfterQueueQueueEndTests(unittest.TestCase):
    def make_target(self):
        target, active, pending = make_queue_target()
        target.tr = Mock(side_effect=lambda key, **kwargs: key)
        return target, active, pending

    def _attach_shutdown_hook(self, target, hook):
        target.shutdown_after_queue = True
        target._maybe_request_shutdown_after_queue = hook
        return target

    def test_successful_queue_end_requests_shutdown(self):
        target, active, pending = self.make_target()
        target.queue_items[:] = [active]
        target.queue_paused = False
        hook = Mock()
        self._attach_shutdown_hook(target, hook)

        with patch.object(notifications_service, "notify"):
            ui_controllers_execution.QueueExecutionController.handle_task_complete(
                target,
                success=True,
                result={
                    "status": downloads_models.status_code.SUCCESS,
                    "downloaded_count": 1,
                },
            )

        hook.assert_called_once_with()

    def test_remaining_pending_task_does_not_request_shutdown(self):
        target, active, pending = self.make_target()
        target.queue_paused = False
        hook = Mock()
        self._attach_shutdown_hook(target, hook)

        ui_controllers_execution.QueueExecutionController.handle_task_complete(
            target,
            success=True,
            result={
                "status": downloads_models.status_code.SUCCESS,
                "downloaded_count": 1,
            },
        )

        hook.assert_not_called()
        target.start_task_by_index.assert_called_once()

    def test_deferred_failures_still_request_shutdown_when_the_queue_is_empty(self):
        target, active, pending = self.make_target()
        target.queue_items[:] = [active]
        target.queue_list = Mock()
        target.add_task_to_queue = Mock()
        target.queue_paused = False
        hook = Mock()
        self._attach_shutdown_hook(target, hook)

        with patch.object(notifications_service, "notify"):
            ui_controllers_execution.QueueExecutionController.handle_task_complete(
                target,
                success=False,
                error_msg="cdn failed",
                result={"status": downloads_models.status_code.FAILED},
            )

        hook.assert_called_once_with()
        target.start_task_by_index.assert_not_called()

    def test_blocking_auth_failure_does_not_request_shutdown(self):
        target, active, pending = self.make_target()
        target.queue_paused = False
        hook = Mock()
        self._attach_shutdown_hook(target, hook)

        with patch.object(notifications_service, "notify"):
            ui_controllers_execution.QueueExecutionController.handle_task_complete(
                target,
                success=False,
                error_msg="login required",
                auth_required=True,
            )

        hook.assert_not_called()

    def test_patrol_queue_end_requests_shutdown(self):
        entry = task_entry("aaaaaaaaaaaa", "running", kind="auto_patrol")
        target = SimpleNamespace(
            active_patrol_entry=entry,
            queue_items=[entry],
            queue_paused=False,
            _safe_exit_in_progress=False,
            patrol_enabled=True,
            _download_stop_reason=None,
            _cleanup_patrol_thread=Mock(),
            _finish_active_patrol_task=Mock(),
            _first_runnable_index=Mock(return_value=None),
            start_task_by_index=Mock(),
            _finalize_queue_run=Mock(),
            update_api_status=Mock(),
            update_download_status=Mock(),
            update_patrol_button_state=Mock(),
            update_download_button_state=Mock(),
            shutdown_after_queue=True,
        )
        hook = Mock()
        target._maybe_request_shutdown_after_queue = hook

        with patch.object(downloads_control, "resume_downloads"):
            ui_controllers_patrol.PatrolController._handle_patrol_finished(
                target,
                {
                    "status": downloads_models.status_code.SUCCESS,
                    "message": "Patrol completed",
                },
            )

        hook.assert_called_once_with()

    def test_patrol_does_not_request_shutdown_when_pending_downloads_remain(self):
        entry = task_entry("aaaaaaaaaaaa", "running", kind="auto_patrol")
        pending = task_entry("B")
        target = SimpleNamespace(
            active_patrol_entry=entry,
            queue_items=[entry, pending],
            queue_paused=False,
            _safe_exit_in_progress=False,
            patrol_enabled=True,
            _download_stop_reason=None,
            _cleanup_patrol_thread=Mock(),
            _finish_active_patrol_task=Mock(),
            _first_runnable_index=Mock(return_value=1),
            start_task_by_index=Mock(),
            _finalize_queue_run=Mock(),
            update_api_status=Mock(),
            update_download_status=Mock(),
            update_patrol_button_state=Mock(),
            update_download_button_state=Mock(),
            shutdown_after_queue=True,
        )
        hook = Mock()
        target._maybe_request_shutdown_after_queue = hook

        with patch.object(downloads_control, "resume_downloads"):
            ui_controllers_patrol.PatrolController._handle_patrol_finished(
                target,
                {
                    "status": downloads_models.status_code.SUCCESS,
                    "message": "Patrol completed",
                },
            )

        target.start_task_by_index.assert_called_once_with(
            1, apply_user_switch_delay=True
        )
        hook.assert_not_called()

    def test_patrol_blocking_failure_does_not_request_shutdown(self):
        entry = task_entry("aaaaaaaaaaaa", "running", kind="auto_patrol")
        target = SimpleNamespace(
            active_patrol_entry=entry,
            queue_items=[entry],
            queue_paused=False,
            _safe_exit_in_progress=False,
            patrol_enabled=True,
            _download_stop_reason=None,
            _cleanup_patrol_thread=Mock(),
            _finish_active_patrol_task=Mock(),
            _move_entry_to_queue_tail=Mock(),
            _first_runnable_index=Mock(return_value=None),
            start_task_by_index=Mock(),
            _finalize_queue_run=Mock(),
            update_api_status=Mock(),
            update_download_status=Mock(),
            update_patrol_button_state=Mock(),
            update_download_button_state=Mock(),
            _show_message_box=Mock(),
            tr=Mock(side_effect=lambda key, **kwargs: key),
            shutdown_after_queue=True,
        )
        hook = Mock()
        target._maybe_request_shutdown_after_queue = hook

        with patch.object(downloads_control, "resume_downloads"):
            ui_controllers_patrol.PatrolController._handle_patrol_finished(
                target,
                {
                    "status": downloads_models.status_code.RATE_LIMITED,
                    "message": "HTTP 429",
                },
            )

        hook.assert_not_called()


class ShutdownAfterQueueGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.original_app_stylesheet = self.app.styleSheet()
        self.original_app_palette = QPalette(self.app.palette())
        self.original_app_font = QFont(self.app.font())
        self.window = None
        self.temp_dir = tempfile.TemporaryDirectory()
        self.env_path = os.path.join(self.temp_dir.name, ".env")
        patches = (
            patch.object(app_paths, "TASKS_DIR", self.temp_dir.name),
            patch.object(app_paths, "ENV_FILE", self.env_path),
            patch.object(app_paths, "CACHE_DIR", self.temp_dir.name),
            patch.object(
                app_paths,
                "PREVIEW_HISTORY_FILE",
                os.path.join(self.temp_dir.name, "preview_history.json"),
            ),
            patch.object(
                ui_controllers_desktop.DesktopController,
                "_init_system_tray",
                lambda _self: None,
            ),
            patch.object(
                ui_preferences.PreferencesController,
                "load_env_settings",
                lambda _self: None,
            ),
            patch.object(
                ui_controllers_targets.TargetController,
                "load_target",
                lambda _self: None,
            ),
            patch.object(
                ui_preview_controller.PreviewController,
                "load_preview_history",
                lambda _self: None,
            ),
            patch.object(
                ui_controllers_task_store.TaskStoreController,
                "load_saved_tasks",
                lambda _self: None,
            ),
        )
        self.patchers = list(patches)
        for patcher in self.patchers:
            patcher.start()
        self.window = ui_main_window.GUI(capture_console=False)

    def tearDown(self):
        if self.window is not None:
            self.window._thumb_extractor.shutdown()
            self.window._image_thumb_extractor.shutdown()
            if self.window._shutdown_countdown_dialog is not None:
                self.window._shutdown_countdown_dialog.close()
            self.window.hide()
            self.window.deleteLater()
        self.app.processEvents()
        ui_console._console_log_callback = None
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp_dir.cleanup()
        self.app.setStyleSheet(self.original_app_stylesheet)
        self.app.setPalette(self.original_app_palette)
        self.app.setFont(self.original_app_font)
        self.app.processEvents()

    def test_switch_defaults_off_and_is_not_written_with_saved_settings(self):
        window = self.window
        self.assertFalse(window.shutdown_after_queue)
        self.assertFalse(window.shutdown_after_queue_check.isChecked())

        window.set_shutdown_after_queue(True)
        self.assertTrue(window.shutdown_after_queue)
        self.assertTrue(window.shutdown_after_queue_check.isChecked())

        written = {}

        def capture(updates):
            written.update(updates)

        with (
            patch.object(window, "_write_env_settings", side_effect=capture),
            patch.object(window, "_normalize_target_values", return_value=[]),
            patch.object(window, "_refresh_all_target_account_icons"),
        ):
            window.save_settings()

        self.assertNotIn("shutdown_after_queue", written)
        self.assertTrue(window.shutdown_after_queue)

    def test_all_dialog_cancel_routes_never_schedule_windows_shutdown(self):
        window = self.window
        for action in ("button", "escape", "reject", "close"):
            with (
                self.subTest(action=action),
                patch.object(
                    window, "_ensure_system_shutdown_supported", return_value=True
                ),
                patch.object(window, "_send_shutdown_countdown_notify"),
                patch.object(window, "_cancel_local_shutdown_countdown") as cancel,
                patch.object(ui_platform, "_run_system_shutdown_command") as run,
            ):
                window.set_shutdown_after_queue(True)
                self.assertTrue(window._maybe_request_shutdown_after_queue())
                dialog = window._shutdown_countdown_dialog
                self.assertIsNotNone(dialog)
                try:
                    dialog._remaining = 1
                    if action == "button":
                        dialog._cancel_button.click()
                    elif action == "escape":
                        QTest.keyClick(dialog, Qt.Key.Key_Escape)
                    elif action == "reject":
                        dialog.reject()
                    else:
                        dialog.close()
                    self.app.processEvents()

                    self.assertIsNone(window._shutdown_countdown_dialog)
                    self.assertFalse(dialog.isVisible())
                    self.assertFalse(dialog._timer.isActive())
                    dialog._tick()
                    dialog.reject()
                    dialog.start_countdown()
                    dialog._tick()
                    self.assertFalse(dialog._timer.isActive())
                    run.assert_not_called()
                    cancel.assert_called_once_with()
                finally:
                    dialog._timer.stop()
                    dialog.deleteLater()
                    self.app.processEvents()

    def test_dialog_start_failure_clears_local_countdown_state(self):
        window = self.window

        with (
            patch.object(
                window,
                "_show_centered_dialog",
                side_effect=RuntimeError("show failed"),
            ),
            patch.object(ui_platform, "_run_system_shutdown_command") as run,
        ):
            with self.assertRaisesRegex(RuntimeError, "show failed"):
                window._open_shutdown_countdown_dialog(60)
            self.app.processEvents()

        self.assertIsNone(window._shutdown_countdown_dialog)
        run.assert_not_called()

    def test_real_dialog_requests_forced_shutdown_only_after_all_60_ticks(self):
        window = self.window
        window.set_shutdown_after_queue(True)
        completed = SimpleNamespace(returncode=0, stdout="", stderr="")
        countdown_state_seen_by_command = []

        def run_shutdown(command):
            countdown_state_seen_by_command.append(window._shutdown_countdown_dialog)
            return completed

        with (
            patch.object(
                window,
                "_ensure_system_shutdown_supported",
                return_value=True,
            ),
            patch.object(window, "_send_shutdown_countdown_notify"),
            patch.object(
                ui_platform,
                "_run_system_shutdown_command",
                side_effect=run_shutdown,
            ) as run,
        ):
            self.assertTrue(window._maybe_request_shutdown_after_queue())
            dialog = window._shutdown_countdown_dialog
            self.assertEqual(dialog._remaining, 60)
            run.assert_not_called()

            for _ in range(59):
                dialog._tick()
                run.assert_not_called()
            self.assertEqual(dialog._remaining, 1)
            dialog._tick()
            dialog._tick()
            self.app.processEvents()

        run.assert_called_once_with(["shutdown", "/s", "/f", "/t", "0"])
        self.assertEqual(countdown_state_seen_by_command, [None])
        self.assertIsNone(window._shutdown_countdown_dialog)

    def test_pending_task_logs_and_preview_history_are_saved_before_forced_shutdown(
        self,
    ):
        window = self.window
        entry = task_entry("aaaaaaaaaaaa", "completed")
        window.queue_items = [entry]
        task_path = window._task_file_path(entry["task"]["task_id"])
        preview_path = os.path.join(self.temp_dir.name, "photo.jpg")
        with open(preview_path, "wb") as image_file:
            image_file.write(b"preview test fixture")
        window.current_preview_images = [preview_path]
        window._preview_history_save_timer.start()

        bridge = ui_console._ConsoleCaptureBridge(window)
        bridge.record_ready.connect(lambda record: entry["logs"].append(record))
        window._console_capture_bridge = bridge
        worker = threading.Thread(target=bridge.submit, args=("Final task log",))
        worker.start()
        worker.join()
        self.assertEqual(entry["logs"], [])
        self.assertFalse(os.path.exists(task_path))
        self.assertFalse(os.path.exists(app_paths.PREVIEW_HISTORY_FILE))
        saved_at_request = []

        def run_shutdown(command):
            with open(task_path, encoding="utf-8") as task_file:
                saved_task = json.load(task_file)
            with open(app_paths.PREVIEW_HISTORY_FILE, encoding="utf-8") as history_file:
                saved_history = json.load(history_file)
            saved_at_request.append((saved_task, saved_history))
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with (
            patch.object(
                window, "_ensure_system_shutdown_supported", return_value=True
            ),
            patch.object(
                ui_platform, "_run_system_shutdown_command", side_effect=run_shutdown
            ) as run,
            patch.object(window, "_show_message_box") as show_error,
        ):
            requested = window._request_system_shutdown_now()

        self.assertTrue(requested)
        show_error.assert_not_called()
        run.assert_called_once_with(["shutdown", "/s", "/f", "/t", "0"])
        self.assertEqual(len(saved_at_request), 1)
        saved_task, saved_history = saved_at_request[0]
        self.assertEqual(saved_task["status"], "completed")
        self.assertEqual(saved_task["logs"], ["Final task log"])
        self.assertEqual(saved_history, [preview_path])
        self.assertFalse(window._preview_history_save_timer.isActive())

    def test_ignored_env_value_does_not_turn_the_switch_on(self):
        for patcher in self.patchers:
            if patcher.attribute == "load_env_settings":
                patcher.stop()
                self.patchers.remove(patcher)
                break
        self.window._thumb_extractor.shutdown()
        self.window._image_thumb_extractor.shutdown()
        self.window.hide()
        self.window.deleteLater()
        self.window = None
        self.app.processEvents()

        with open(self.env_path, "w", encoding="utf-8") as env_file:
            env_file.write("shutdown_after_queue=true\n")
            env_file.write("username=\n")
            env_file.write("save_path=\n")

        self.window = ui_main_window.GUI(capture_console=False)
        self.assertFalse(self.window.shutdown_after_queue)
        self.assertFalse(self.window.shutdown_after_queue_check.isChecked())


if __name__ == "__main__":
    unittest.main()
