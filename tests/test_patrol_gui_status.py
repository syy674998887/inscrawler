"""Headless regression tests for auto-patrol parent task status."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import inscrawler.paths as app_paths

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt

import inscrawler.config as config
import inscrawler.downloads.checkpoints as downloads_checkpoints
import inscrawler.downloads.control as downloads_control
import inscrawler.downloads.models as downloads_models
import inscrawler.tasks.patrol as tasks_patrol
import inscrawler.tasks.schema as tasks_schema
import inscrawler.ui.console as ui_console
import inscrawler.ui.constants as ui_constants
import inscrawler.ui.controllers.execution as ui_controllers_execution
import inscrawler.ui.controllers.patrol as ui_controllers_patrol
import inscrawler.ui.controllers.queue_controls as ui_controllers_queue_controls
import inscrawler.ui.controllers.queue_view as ui_controllers_queue_view
import inscrawler.ui.controllers.task_store as ui_controllers_task_store
import inscrawler.ui.i18n.text as ui_i18n_text
import inscrawler.ui.main_window as ui_main_window
import inscrawler.ui.preferences as ui_preferences
import inscrawler.ui.theme.icons as ui_theme_icons
import inscrawler.ui.workers as ui_workers


def patrol_account(username, state="waiting", *, downloaded=0):
    return {
        "target_user": username,
        "state": state,
        "checked_count": 0,
        "updates_found_count": 0,
        "total_downloaded_count": downloaded,
        "message": "",
    }


def patrol_entry(status="running", *, targets=("alice", "bob")):
    accounts = [patrol_account(username) for username in targets]
    entry = {
        "schema_version": 7,
        "task": {
            "task_id": "aaaaaaaaaaaa",
            "task_kind": "auto_patrol",
            "target_users": list(targets),
            "download_media_type": "posts",
            "page_limit": 5,
            "notification": "",
            "created_at": "2026-01-01 00:00:00",
        },
        "status": status,
        "logs": [],
        "log_targets": [],
        "downloaded_files": [],
        "downloaded_file_details": [],
        "patrol_state": {
            "total_downloaded": 0,
            "accounts": accounts,
        },
    }
    if status in ("pending", "running", "deferred", "stopped"):
        entry["queue_order"] = 0
    elif status == "completed":
        entry["completed_at"] = entry["task"]["created_at"]
        for account in entry["patrol_state"]["accounts"]:
            account["state"] = "no_updates"
    return entry


def file_detail(path, *, size=1):
    return {
        "path": path,
        "filename": Path(path).name,
        "extension": Path(path).suffix.lower(),
        "media_kind": "image",
        "download_category": "posts",
        "segment_index": 1,
        "segment_total": 1,
        "source": "iphone",
        "source_url_host": "scontent.example.com",
        "shortcode": "ExampleCode",
        "media_id": 123456789,
        "typename": "GraphImage",
        "media_date_utc": "2026-01-01T00:00:00",
        "media_date_local": "2025-12-31T19:00:00-05:00",
        "downloaded_at": "2026-01-01 00:01:00",
        "size_bytes": size,
        "response_content_type": "image/jpeg",
        "response_content_length": str(size),
        "response_status_code": 200,
    }


def item_completed_detail(*, media_id=123456789, shortcode="ExampleCode"):
    return {
        "download_category": "posts",
        "shortcode": shortcode,
        "media_id": media_id,
        "typename": "GraphImage",
        "media_date_utc": "2026-01-01T00:00:00",
        "media_date_local": "2025-12-31T19:00:00-05:00",
        "completed_at": "2026-01-01 00:01:01",
    }


class _GuiMethodHarness(SimpleNamespace):
    """Bind unprovided GUI helpers without constructing the full window."""

    def __getattr__(self, name):
        method = getattr(ui_main_window.GUI, name, None)
        if callable(method):
            return method.__get__(self, type(self))
        raise AttributeError(name)


class PatrolParentStatusTests(unittest.TestCase):
    def test_worker_runs_exactly_one_patrol_pass(self):
        result = {
            "status": downloads_models.status_code.SUCCESS,
            "message": "Patrol completed",
            "downloaded_count": 0,
        }
        finished = []
        thread = ui_workers.PatrolThread(
            ["alice", "bob"],
            "posts",
            5,
            delay_before_first_target=True,
        )
        thread.finished_signal.connect(finished.append)
        messages = []
        thread.message_signal.connect(messages.append)

        with (
            patch.object(downloads_control, "delay") as delay,
            patch.object(downloads_control, "is_stop_requested", return_value=False),
            patch.object(
                tasks_patrol,
                "patrol_targets_once",
                return_value=result,
            ) as patrol_once,
        ):
            thread.run()

        delay.assert_called_once_with("user_switch")
        patrol_once.assert_called_once()
        self.assertEqual(patrol_once.call_args.args[2], 5)
        self.assertIn("detection pages 5", messages[0])
        self.assertEqual(finished[0]["status"], downloads_models.status_code.SUCCESS)

    def test_full_media_worker_has_no_page_depth_and_forwards_none(self):
        for media_type in ("stories", "highlights"):
            with self.subTest(media_type=media_type):
                messages = []
                thread = ui_workers.PatrolThread(["alice"], media_type, None)
                thread.message_signal.connect(messages.append)

                with (
                    patch.object(
                        downloads_control, "is_stop_requested", return_value=False
                    ),
                    patch.object(
                        tasks_patrol,
                        "patrol_targets_once",
                        return_value={
                            "status": downloads_models.status_code.SUCCESS,
                            "message": "Patrol completed",
                            "downloaded_count": 0,
                        },
                    ) as patrol_once,
                ):
                    thread.run()

                self.assertIsNone(patrol_once.call_args.args[2])
                self.assertNotIn("depth", messages[0].lower())
                self.assertNotIn("page", messages[0].lower())

    def test_full_media_worker_rejects_a_page_limit(self):
        for media_type in ("stories", "highlights"):
            with self.subTest(media_type=media_type):
                with self.assertRaisesRegex(
                    ValueError,
                    "page_limit is only valid for paged patrols",
                ):
                    ui_workers.PatrolThread(["alice"], media_type, 1)

    def test_worker_has_no_delay_after_single_pass(self):
        thread = ui_workers.PatrolThread(["alice", "bob"], "posts", 5)

        with (
            patch.object(downloads_control, "delay") as delay,
            patch.object(downloads_control, "is_stop_requested", return_value=False),
            patch.object(
                tasks_patrol,
                "patrol_targets_once",
                return_value={
                    "status": downloads_models.status_code.SUCCESS,
                    "message": "Patrol completed",
                    "downloaded_count": 0,
                },
            ),
        ):
            thread.run()

        delay.assert_not_called()

    def test_worker_forwards_parent_id_and_original_target_indexes(self):
        thread = ui_workers.PatrolThread(
            ["bob", "dave"],
            "posts",
            5,
            patrol_task_id="0123456789ab",
            target_indexes=[1, 3],
        )

        with (
            patch.object(downloads_control, "is_stop_requested", return_value=False),
            patch.object(
                tasks_patrol,
                "patrol_targets_once",
                return_value={
                    "status": downloads_models.status_code.SUCCESS,
                    "message": "Patrol completed",
                    "downloaded_count": 0,
                },
            ) as patrol_once,
        ):
            thread.run()

        call_kwargs = patrol_once.call_args.kwargs
        self.assertEqual(call_kwargs["patrol_task_id"], "0123456789ab")
        self.assertEqual(call_kwargs["target_indexes"], (1, 3))

    def test_worker_emits_each_completed_item_media_once(self):
        thread = ui_workers.PatrolThread(["alice"], "posts", 5)
        events = []
        thread.item_completed_signal.connect(
            lambda event: events.append(("item_completed", event))
        )
        thread.target_finished_signal.connect(
            lambda event: events.append(("target_finished", event))
        )

        thread._handle_target_event(
            {
                "event": "target_started",
                "target_username": "alice",
            }
        )
        completed_file = file_detail("complete.jpg")
        next_file = file_detail("next.jpg")
        next_file.update({"media_id": 987654321, "shortcode": "Next"})
        thread.capture_file_detail(completed_file)
        thread.capture_file_detail(next_file)

        thread.capture_item_completed(item_completed_detail())

        self.assertEqual([name for name, _event in events], ["item_completed"])
        completed_event = events[0][1]
        self.assertEqual(completed_event["target_username"], "alice")
        self.assertEqual(completed_event["downloaded_files"], ["complete.jpg"])
        self.assertEqual(
            completed_event["downloaded_file_details"],
            [completed_file],
        )
        self.assertEqual(thread._target_capture["file_details"], [next_file])

        thread.capture_item_completed(
            item_completed_detail(media_id=987654321, shortcode="Next")
        )

        next_event = events[1][1]
        self.assertEqual(next_event["downloaded_files"], ["next.jpg"])
        self.assertEqual(next_event["downloaded_file_details"], [next_file])
        self.assertEqual(thread._target_capture["file_details"], [])

        thread._handle_target_event(
            {
                "event": "target_finished",
                "target_username": "alice",
                "media_type": "posts",
                "result": {
                    "status": downloads_models.status_code.SUCCESS,
                    "downloaded_count": 2,
                },
            }
        )

        self.assertEqual(
            [name for name, _event in events],
            ["item_completed", "item_completed", "target_finished"],
        )
        target_event = events[-1][1]
        self.assertNotIn("downloaded_files", target_event)
        self.assertNotIn("downloaded_file_details", target_event)

    def test_worker_does_not_complete_media_from_image_or_file_detail_alone(self):
        thread = ui_workers.PatrolThread(["alice"], "posts", 5)
        completed_events = []
        image_events = []
        thread.item_completed_signal.connect(completed_events.append)
        thread.image_signal.connect(image_events.append)
        thread._handle_target_event(
            {
                "event": "target_started",
                "target_username": "alice",
            }
        )

        thread.capture_image("incomplete.jpg")
        thread.capture_file_detail(file_detail("incomplete.jpg"))

        self.assertEqual(image_events, ["incomplete.jpg"])
        self.assertEqual(completed_events, [])

    def test_worker_success_completes_parent_and_starts_next_task(self):
        entry = patrol_entry()
        next_entry = {
            "task": {"task_kind": "download"},
            "status": "pending",
        }
        target = SimpleNamespace(
            active_patrol_entry=entry,
            queue_items=[entry, next_entry],
            _safe_exit_in_progress=False,
            patrol_enabled=True,
            _cleanup_patrol_thread=Mock(),
            _finish_active_patrol_task=Mock(),
            _first_runnable_index=Mock(return_value=1),
            start_task_by_index=Mock(),
            _finalize_queue_run=Mock(),
            update_api_status=Mock(),
            update_download_status=Mock(),
            update_patrol_button_state=Mock(),
            update_download_button_state=Mock(),
        )

        with patch.object(
            downloads_checkpoints,
            "delete_patrol_resume_checkpoints",
        ) as delete_cursors:
            ui_controllers_patrol.PatrolController.on_patrol_finished(
                target,
                {
                    "status": downloads_models.status_code.SUCCESS,
                    "message": "Patrol completed",
                },
            )

        target._finish_active_patrol_task.assert_called_once_with(
            "completed", "Patrol completed"
        )
        target.start_task_by_index.assert_called_once_with(
            1, apply_user_switch_delay=True
        )
        delete_cursors.assert_called_once_with(
            "aaaaaaaaaaaa",
            "posts",
            2,
        )
        target._finalize_queue_run.assert_not_called()

    def test_success_during_safe_exit_still_completes_parent(self):
        entry = patrol_entry()
        next_entry = {
            "task": {"task_kind": "download"},
            "status": "pending",
        }
        target = SimpleNamespace(
            active_patrol_entry=entry,
            queue_items=[entry, next_entry],
            _safe_exit_in_progress=True,
            patrol_enabled=True,
            _cleanup_patrol_thread=Mock(),
            _finish_active_patrol_task=Mock(),
            _first_runnable_index=Mock(return_value=1),
            start_task_by_index=Mock(),
            _finalize_queue_run=Mock(),
            update_api_status=Mock(),
            update_download_status=Mock(),
            update_patrol_button_state=Mock(),
            update_download_button_state=Mock(),
        )

        ui_controllers_patrol.PatrolController.on_patrol_finished(
            target,
            {
                "status": downloads_models.status_code.SUCCESS,
                "message": "Patrol completed",
            },
        )

        target._finish_active_patrol_task.assert_called_once_with(
            "completed", "Patrol completed"
        )
        target.start_task_by_index.assert_not_called()
        target._finalize_queue_run.assert_called_once_with()

    def test_success_after_stop_request_does_not_start_next_task(self):
        entry = patrol_entry()
        next_entry = {
            "task": {"task_kind": "download"},
            "status": "pending",
        }
        target = SimpleNamespace(
            active_patrol_entry=entry,
            queue_items=[entry, next_entry],
            _safe_exit_in_progress=False,
            patrol_enabled=False,
            _cleanup_patrol_thread=Mock(),
            _finish_active_patrol_task=Mock(),
            _first_runnable_index=Mock(return_value=1),
            start_task_by_index=Mock(),
            _finalize_queue_run=Mock(),
            update_api_status=Mock(),
            update_download_status=Mock(),
            update_patrol_button_state=Mock(),
            update_download_button_state=Mock(),
        )

        ui_controllers_patrol.PatrolController.on_patrol_finished(
            target,
            {
                "status": downloads_models.status_code.SUCCESS,
                "message": "Patrol completed",
            },
        )

        target._finish_active_patrol_task.assert_called_once_with(
            "completed", "Patrol completed"
        )
        target.start_task_by_index.assert_not_called()
        target._finalize_queue_run.assert_called_once_with()

    def test_patrol_finishing_while_paused_keeps_queue_paused(self):
        entry = patrol_entry()
        next_entry = patrol_entry("pending")
        next_entry["task"]["task_id"] = "bbbbbbbbbbbb"
        target = SimpleNamespace(
            active_patrol_entry=entry,
            queue_items=[entry, next_entry],
            queue_paused=True,
            _download_stop_reason=None,
            _safe_exit_in_progress=False,
            patrol_enabled=True,
            _cleanup_patrol_thread=Mock(),
            _finish_active_patrol_task=Mock(),
            _restore_deferred_tasks=Mock(),
            _first_runnable_index=Mock(return_value=1),
            start_task_by_index=Mock(),
            _finalize_queue_run=Mock(),
            update_api_status=Mock(),
            update_download_status=Mock(),
            update_patrol_button_state=Mock(),
            update_download_button_state=Mock(),
        )

        with patch.object(downloads_checkpoints, "delete_patrol_resume_checkpoints"):
            ui_controllers_patrol.PatrolController.on_patrol_finished(
                target,
                {
                    "status": downloads_models.status_code.SUCCESS,
                    "message": "Patrol completed while paused",
                },
            )

        self.assertTrue(target.queue_paused)
        target._finish_active_patrol_task.assert_called_once_with(
            "completed",
            "Patrol completed while paused",
        )
        target._restore_deferred_tasks.assert_called_once_with()
        target.start_task_by_index.assert_not_called()
        target._finalize_queue_run.assert_not_called()

    def test_legacy_stopped_parent_displays_as_unfinished(self):
        entry = patrol_entry("stopped")
        target = SimpleNamespace(
            active_patrol_entry=None,
            current_queue_index=None,
        )

        self.assertEqual(
            ui_controllers_queue_view.QueueViewController._queue_display_status(
                target, entry
            ),
            "pending",
        )
        self.assertTrue(
            ui_controllers_queue_view.QueueViewController._queue_entry_can_show_delete(
                target, entry
            )
        )

    def test_worker_stop_returns_parent_to_pending(self):
        entry = patrol_entry()
        target = SimpleNamespace(
            active_patrol_entry=entry,
            _safe_exit_in_progress=False,
            patrol_enabled=True,
            _cleanup_patrol_thread=Mock(),
            _finish_active_patrol_task=Mock(),
            _finalize_queue_run=Mock(),
            update_api_status=Mock(),
            update_download_status=Mock(),
            update_patrol_button_state=Mock(),
            update_download_button_state=Mock(),
        )

        ui_controllers_patrol.PatrolController.on_patrol_finished(
            target,
            {
                "status": downloads_models.status_code.STOPPED,
                "message": "Patrol stopped after 3 target(s)",
            },
        )

        self.assertFalse(target.patrol_enabled)
        target._finish_active_patrol_task.assert_called_once_with(
            "pending", "Patrol stopped after 3 target(s)"
        )

    def test_terminal_worker_errors_defer_parent(self):
        for status in (
            downloads_models.status_code.AUTHORIZATION_REQUIRED,
            downloads_models.status_code.RATE_LIMITED,
            downloads_models.status_code.IPHONE_QUALITY_UNAVAILABLE,
            downloads_models.status_code.FAILED,
        ):
            for next_index in (None, 1):
                with self.subTest(status=status, next_index=next_index):
                    entry = patrol_entry()
                    target = SimpleNamespace(
                        active_patrol_entry=entry,
                        _safe_exit_in_progress=False,
                        patrol_enabled=True,
                        _cleanup_patrol_thread=Mock(),
                        _finish_active_patrol_task=Mock(),
                        _move_entry_to_queue_tail=Mock(),
                        _first_runnable_index=Mock(return_value=next_index),
                        start_task_by_index=Mock(),
                        _finalize_queue_run=Mock(),
                        _maybe_request_shutdown_after_queue=Mock(),
                        update_api_status=Mock(),
                        update_download_status=Mock(),
                        update_patrol_button_state=Mock(),
                        update_download_button_state=Mock(),
                        _show_message_box=Mock(),
                        tr=Mock(side_effect=lambda key, **_kwargs: key),
                    )

                    ui_controllers_patrol.PatrolController.on_patrol_finished(
                        target,
                        {"status": status, "message": "Patrol unavailable"},
                    )

                    self.assertFalse(target.patrol_enabled)
                    target._finish_active_patrol_task.assert_called_once_with(
                        "deferred", "Patrol unavailable"
                    )
                    target._move_entry_to_queue_tail.assert_called_once_with(entry)
                    if status == downloads_models.status_code.FAILED:
                        target._show_message_box.assert_not_called()
                        if next_index is not None:
                            target.start_task_by_index.assert_called_once_with(
                                next_index, apply_user_switch_delay=True
                            )
                            target._finalize_queue_run.assert_not_called()
                            target._maybe_request_shutdown_after_queue.assert_not_called()
                        else:
                            target.start_task_by_index.assert_not_called()
                            target._finalize_queue_run.assert_called_once_with()
                            target._maybe_request_shutdown_after_queue.assert_called_once_with()
                    else:
                        target._show_message_box.assert_called_once()
                        target.start_task_by_index.assert_not_called()
                        target._maybe_request_shutdown_after_queue.assert_not_called()

    def test_loader_wide_failure_stops_queue_without_deferring_parent(self):
        entry = patrol_entry()
        next_entry = {
            "task": {"task_kind": "download"},
            "status": "pending",
        }
        target = SimpleNamespace(
            active_patrol_entry=entry,
            queue_items=[entry, next_entry],
            _safe_exit_in_progress=False,
            patrol_enabled=True,
            _cleanup_patrol_thread=Mock(),
            _finish_active_patrol_task=Mock(),
            _first_runnable_index=Mock(return_value=1),
            start_task_by_index=Mock(),
            _finalize_queue_run=Mock(),
            update_api_status=Mock(),
            update_download_status=Mock(),
            update_patrol_button_state=Mock(),
            update_download_button_state=Mock(),
        )

        ui_controllers_patrol.PatrolController.on_patrol_finished(
            target,
            {
                "status": downloads_models.status_code.FAILED,
                "message": "Loader client not available",
                "blocking_failure": True,
            },
        )

        target._finish_active_patrol_task.assert_called_once_with(
            "pending", "Loader client not available"
        )
        target.update_api_status.assert_any_call("error")
        target.start_task_by_index.assert_not_called()
        target._finalize_queue_run.assert_called_once_with()

    @staticmethod
    def _patrol_builder(media_type, page_limit=3):
        empty_text = SimpleNamespace(text=lambda: "")
        return _GuiMethodHarness(
            save_path=empty_text,
            default_save_path="data",
            patrol_media_type=SimpleNamespace(currentData=lambda: media_type),
            patrol_page_limit=SimpleNamespace(value=lambda: page_limit),
            username=empty_text,
            notification=SimpleNamespace(currentData=lambda: ""),
            webhook=empty_text,
            telegram_bot_token=empty_text,
            telegram_chat_id=empty_text,
        )

    def test_build_patrol_task_saves_pages_only_for_paged_media(self):
        for media_type in ("posts", "reels", "tagged"):
            with self.subTest(media_type=media_type):
                task = ui_controllers_patrol.PatrolController.build_patrol_task_from_ui(
                    self._patrol_builder(media_type), ["alice"]
                )
                self.assertEqual(task["page_limit"], 3)

        for media_type in ("stories", "highlights"):
            with self.subTest(media_type=media_type):
                task = ui_controllers_patrol.PatrolController.build_patrol_task_from_ui(
                    self._patrol_builder(media_type), ["alice"]
                )
                self.assertNotIn("page_limit", task)
                self.assertNotIn("scan_limit", task)

    @staticmethod
    def _settings_loader_target():
        return SimpleNamespace(
            username=Mock(),
            user_agent=Mock(),
            session_cookie=Mock(),
            webhook=Mock(),
            telegram_bot_token=Mock(),
            telegram_chat_id=Mock(),
            save_path=Mock(),
            default_save_path="data",
            notification=Mock(findData=Mock(return_value=0)),
            _update_notification_fields_visibility=Mock(),
            set_iphone_support=Mock(),
            set_profile_picture_sync_enabled=Mock(),
            profile_picture_sync_enabled=False,
            set_media_request_cap_enabled=Mock(),
            media_request_cap_enabled=False,
            set_debug_logging_enabled=Mock(),
            debug_logging_enabled=False,
            set_video_preview_cache_mb=Mock(),
            video_preview_cache_mb=ui_constants.DEFAULT_VIDEO_PREVIEW_CACHE_MB,
            _normalize_iphone_support_value=lambda value: (
                str(value).strip().lower() in ("1", "true", "yes", "on", "enabled")
            ),
            _normalize_media_request_cap=lambda value: max(1, min(10000, int(value))),
            set_max_preview_images=Mock(),
            _set_main_preview_thumbnails_visible=Mock(),
            patrol_media_type=Mock(),
            patrol_page_limit=Mock(),
            _normalize_theme_key=Mock(return_value="dark"),
            _normalize_language_key=Mock(return_value="zh"),
            _normalize_close_behavior_key=Mock(return_value="tray"),
            apply_ui_preferences=Mock(),
            set_close_behavior=Mock(),
        )

    def test_settings_read_only_the_new_patrol_page_limit_key(self):
        for env_values, expected in (
            ({"patrol_scan_limit": "99"}, 1),
            ({"patrol_scan_limit": "99", "patrol_page_limit": "4"}, 4),
        ):
            with self.subTest(env_values=env_values):
                target = self._settings_loader_target()
                target.patrol_media_type.findData.return_value = 0
                with (
                    patch.object(os.path, "exists", return_value=True),
                    patch.object(
                        config,
                        "_read_dotenv_file",
                        return_value=env_values,
                    ),
                ):
                    ui_preferences.PreferencesController.load_env_settings(target)

                target.patrol_page_limit.setValue.assert_called_once_with(expected)
                target.set_video_preview_cache_mb.assert_called_once_with(
                    str(ui_constants.DEFAULT_VIDEO_PREVIEW_CACHE_MB),
                    persist=False,
                )
                target.set_profile_picture_sync_enabled.assert_called_once_with(
                    "false",
                    persist=False,
                )
                target._set_main_preview_thumbnails_visible.assert_called_once_with(
                    True,
                    persist=False,
                )

    def test_add_patrol_task_only_appends_pending_task(self):
        task = patrol_entry("pending")["task"]
        state = patrol_entry("pending")["patrol_state"]
        target = SimpleNamespace(
            _load_patrol_target_snapshot=Mock(return_value=["alice", "bob"]),
            save_settings=Mock(),
            build_patrol_task_from_ui=Mock(return_value=task),
            _new_patrol_state=Mock(return_value=state),
            add_task_to_queue=Mock(),
            _scroll_queue_to_bottom=Mock(),
            update_download_button_state=Mock(),
            start_download=Mock(),
            _start_patrol_thread=Mock(),
        )

        ui_controllers_patrol.PatrolController.add_patrol_task(target)

        target.add_task_to_queue.assert_called_once_with(
            task, "pending", patrol_state=state, log_targets=[]
        )
        target.start_download.assert_not_called()
        target._start_patrol_thread.assert_not_called()

    def test_start_task_dispatches_pending_patrol(self):
        entry = patrol_entry("pending")
        entry["task"]["login_username"] = "owner"
        target = SimpleNamespace(
            queue_items=[entry],
            _shutdown_download_thread=Mock(),
            _validate_task_start=Mock(return_value=True),
            queue_running=True,
            queue_paused=True,
            current_queue_index=None,
            active_patrol_entry=None,
            patrol_enabled=False,
            _patrol_delay_before_start=False,
            _start_patrol_thread=Mock(),
            _refresh_thread_pipeline=Mock(),
            _download_stop_reason="previous-error",
            _thread_pipeline_stop_requested=True,
            _api_status_state="error",
            _download_status_state="downloading_posts",
            _thread_pipeline_phase="download",
            _thread_pipeline_last_active_phase="download",
        )

        ui_controllers_execution.QueueExecutionController.start_task_by_index(
            target, 0, apply_user_switch_delay=True
        )

        self.assertFalse(target.queue_running)
        self.assertFalse(target.queue_paused)
        self.assertEqual(target.current_queue_index, 0)
        self.assertIs(target.active_patrol_entry, entry)
        self.assertTrue(target.patrol_enabled)
        self.assertTrue(target._patrol_delay_before_start)
        self.assertIsNone(target._download_stop_reason)
        self.assertFalse(target._thread_pipeline_stop_requested)
        self.assertEqual(target._api_status_state, "idle")
        self.assertEqual(target._download_status_state, "idle")
        self.assertEqual(target._thread_pipeline_phase, "idle")
        self.assertEqual(target._thread_pipeline_last_active_phase, "idle")
        target._start_patrol_thread.assert_called_once_with()
        target._refresh_thread_pipeline.assert_called_once_with()

    def test_start_task_preserves_stopped_patrol_pass_state(self):
        entry = patrol_entry("stopped")
        entry["task"]["login_username"] = "owner"
        account = entry["patrol_state"]["accounts"][0]
        account.update(
            {
                "state": "stopped",
                "checked_count": 4,
                "updates_found_count": 2,
                "total_downloaded_count": 3,
                "message": "Interrupted",
            }
        )
        entry["patrol_state"]["total_downloaded"] = 3
        target = SimpleNamespace(
            queue_items=[entry],
            _shutdown_download_thread=Mock(),
            _validate_task_start=Mock(return_value=True),
            queue_running=False,
            queue_paused=False,
            current_queue_index=None,
            active_patrol_entry=None,
            patrol_enabled=False,
            _patrol_delay_before_start=False,
            _start_patrol_thread=Mock(),
            _refresh_thread_pipeline=Mock(),
        )

        ui_controllers_execution.QueueExecutionController.start_task_by_index(target, 0)

        self.assertEqual(account["state"], "stopped")
        self.assertEqual(account["checked_count"], 4)
        self.assertEqual(account["updates_found_count"], 2)
        self.assertEqual(account["total_downloaded_count"], 3)
        self.assertEqual(account["message"], "Interrupted")
        self.assertEqual(entry["patrol_state"]["total_downloaded"], 3)
        target._start_patrol_thread.assert_called_once_with()

    def test_queue_preserves_patrol_order(self):
        patrol = patrol_entry("pending")
        download_entry = {
            "task": {"task_kind": "download"},
            "status": "pending",
        }
        target = SimpleNamespace(queue_items=[patrol, download_entry])

        self.assertEqual(
            ui_controllers_queue_view.QueueViewController._first_runnable_index(target),
            0,
        )

    def test_stopped_patrol_and_download_are_serial_runnable(self):
        stopped_patrol = patrol_entry("stopped")
        stopped_download = {
            "task": {"task_kind": "download"},
            "status": "stopped",
        }
        pending_download = {
            "task": {"task_kind": "download"},
            "status": "pending",
        }
        target = SimpleNamespace(
            queue_items=[stopped_patrol, stopped_download, pending_download],
        )

        self.assertEqual(
            ui_controllers_queue_view.QueueViewController._first_runnable_index(target),
            0,
        )

        target.queue_items[1:] = [pending_download, stopped_download]
        self.assertEqual(
            ui_controllers_queue_view.QueueViewController._first_runnable_index(target),
            0,
        )

        target.queue_items[1:] = [stopped_download, pending_download]
        self.assertEqual(
            ui_controllers_queue_view.QueueViewController._first_runnable_index(target),
            0,
        )

    def test_invalid_patrol_start_does_not_leave_queue_running(self):
        entry = patrol_entry("pending")
        target = SimpleNamespace(
            queue_items=[entry],
            _shutdown_download_thread=Mock(),
            _validate_task_start=Mock(return_value=False),
            queue_running=True,
            queue_paused=True,
            current_queue_index=0,
            _finalize_queue_run=Mock(),
            update_api_status=Mock(),
            update_download_status=Mock(),
            update_download_button_state=Mock(),
            _start_patrol_thread=Mock(),
        )

        ui_controllers_execution.QueueExecutionController.start_task_by_index(target, 0)

        self.assertFalse(target.queue_running)
        self.assertFalse(target.queue_paused)
        self.assertIsNone(target.current_queue_index)
        target._start_patrol_thread.assert_not_called()

    def test_main_task_button_pauses_active_patrol(self):
        entry = patrol_entry("running")
        target = SimpleNamespace(
            _safe_exit_in_progress=False,
            _force_exit=False,
            _download_stop_reason=None,
            patrol_enabled=True,
            active_patrol_entry=entry,
            _patrol_thread_is_active=Mock(return_value=True),
            queue_paused=False,
            pause_download_queue=Mock(),
            resume_download_queue=Mock(),
            start_task_by_index=Mock(),
        )

        ui_controllers_queue_controls.QueueControls.start_download(target)

        target.pause_download_queue.assert_called_once_with()
        target.resume_download_queue.assert_not_called()
        target.start_task_by_index.assert_not_called()

    def test_main_task_button_resumes_paused_patrol(self):
        entry = patrol_entry("running")
        target = SimpleNamespace(
            _safe_exit_in_progress=False,
            _force_exit=False,
            _download_stop_reason=None,
            patrol_enabled=True,
            active_patrol_entry=entry,
            _patrol_thread_is_active=Mock(return_value=True),
            queue_paused=True,
            pause_download_queue=Mock(),
            resume_download_queue=Mock(),
            start_task_by_index=Mock(),
        )

        ui_controllers_queue_controls.QueueControls.start_download(target)

        target.resume_download_queue.assert_called_once_with()
        target.pause_download_queue.assert_not_called()
        target.start_task_by_index.assert_not_called()

    def test_patrol_uses_shared_pause_and_resume_without_queue_handoff(self):
        entry = patrol_entry("running")
        target = _GuiMethodHarness(
            queue_running=False,
            queue_paused=False,
            _download_stop_reason=None,
            patrol_enabled=True,
            active_patrol_entry=entry,
            _patrol_thread_is_active=Mock(return_value=True),
            _download_thread_is_running=Mock(return_value=False),
            update_download_button_state=Mock(),
            _refresh_queue_reorder_state=Mock(),
            start_task_by_index=Mock(),
        )

        with (
            patch.object(downloads_control, "pause_downloads") as pause,
            patch.object(downloads_control, "resume_downloads") as resume,
        ):
            target.pause_download_queue()
            self.assertTrue(target.queue_paused)
            pause.assert_called_once_with()

            target.resume_download_queue()

        self.assertFalse(target.queue_paused)
        resume.assert_called_once_with()
        target.start_task_by_index.assert_not_called()

    def test_reordered_paused_patrol_requests_safe_handoff(self):
        active = patrol_entry("running")
        pending = patrol_entry("pending")
        pending["task"]["task_id"] = "bbbbbbbbbbbb"
        target = _GuiMethodHarness(
            queue_items=[pending, active],
            current_queue_index=1,
            queue_running=False,
            queue_paused=True,
            _download_stop_reason=None,
            _queue_handoff_target_id=None,
            _thread_pipeline_stop_requested=False,
            patrol_enabled=True,
            active_patrol_entry=active,
            _patrol_delay_before_start=False,
            _safe_exit_in_progress=False,
            _force_exit=False,
            _patrol_thread_is_active=Mock(return_value=True),
            update_download_button_state=Mock(),
            _refresh_queue_reorder_state=Mock(),
        )

        with patch.object(downloads_control, "request_stop_downloads") as stop:
            target.resume_download_queue()

        stop.assert_called_once_with()
        self.assertFalse(target.queue_paused)
        self.assertFalse(target.patrol_enabled)
        self.assertEqual(target._download_stop_reason, "handoff")
        self.assertEqual(target._queue_handoff_target_id, "bbbbbbbbbbbb")

    def test_stopped_patrol_handoff_returns_old_parent_to_pending(self):
        active = patrol_entry("running")
        pending = patrol_entry("pending")
        pending["task"]["task_id"] = "bbbbbbbbbbbb"
        target = _GuiMethodHarness(
            queue_items=[pending, active],
            current_queue_index=1,
            queue_running=False,
            queue_paused=False,
            _download_stop_reason="handoff",
            _queue_handoff_target_id="bbbbbbbbbbbb",
            _thread_pipeline_stop_requested=True,
            active_patrol_entry=active,
            patrol_enabled=False,
            _safe_exit_in_progress=False,
            _cleanup_patrol_thread=Mock(),
            _finish_active_patrol_task=Mock(),
            _finalize_queue_run=Mock(),
            start_task_by_index=Mock(),
            update_api_status=Mock(),
            update_download_status=Mock(),
            update_patrol_button_state=Mock(),
            update_download_button_state=Mock(),
        )

        with patch.object(downloads_control, "clear_stop_request") as clear_stop:
            target.on_patrol_finished(
                {
                    "status": downloads_models.status_code.STOPPED,
                    "message": "Patrol paused for queue switch",
                }
            )

        target._finish_active_patrol_task.assert_called_once_with(
            "pending",
            "Patrol paused for queue switch",
        )
        clear_stop.assert_called_once_with()
        target.start_task_by_index.assert_called_once_with(
            0,
            apply_user_switch_delay=True,
        )
        target._finalize_queue_run.assert_not_called()

    def test_patrol_main_button_uses_shared_download_text(self):
        entry = patrol_entry("running")
        for paused, expected_text, expected_variant in (
            (False, "暂停下载", "busy"),
            (True, "开始任务", "primary"),
        ):
            with self.subTest(paused=paused):
                button = Mock()
                button.property.return_value = expected_variant
                target = _GuiMethodHarness(
                    queue_running=False,
                    queue_paused=paused,
                    _download_stop_reason=None,
                    patrol_enabled=True,
                    active_patrol_entry=entry,
                    _safe_exit_in_progress=False,
                    _force_exit=False,
                    download_btn=button,
                    _patrol_thread_is_active=Mock(return_value=True),
                    tr=Mock(
                        side_effect=lambda key, **kwargs: ui_i18n_text.UI_TEXT["zh"][
                            key
                        ].format(**kwargs)
                    ),
                    _refresh_thread_pipeline=Mock(),
                    _refresh_queue_reorder_state=Mock(),
                    update_clear_queue_button_state=Mock(),
                    update_patrol_button_state=Mock(),
                )

                target.update_download_button_state()

                button.setText.assert_called_once_with(expected_text)

    def test_active_patrol_status_icon_is_display_only(self):
        entry = patrol_entry("running")
        status_button = Mock()
        entry["status_button"] = status_button
        target = _GuiMethodHarness(
            queue_items=[entry],
            current_queue_index=0,
            active_patrol_entry=entry,
            patrol_enabled=True,
            queue_paused=True,
            _download_stop_reason=None,
            _safe_exit_in_progress=False,
            _force_exit=False,
        )

        with patch.object(ui_theme_icons, "_make_svg_icon", return_value=Mock()):
            target._set_queue_status_icon(entry)

        self.assertFalse(hasattr(ui_main_window.GUI, "on_queue_item_action"))
        self.assertFalse(hasattr(ui_main_window.GUI, "stop_download_task"))
        status_button.setCursor.assert_called_once_with(Qt.CursorShape.ArrowCursor)
        status_button.setToolTip.assert_called_once_with("")
        status_button.setAttribute.assert_called_once_with(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )

    def test_delete_replaces_status_in_the_same_button_on_hover(self):
        entry = patrol_entry("completed")
        status_button = Mock()
        entry["status_button"] = status_button
        target = _GuiMethodHarness(
            queue_items=[entry],
            current_queue_index=None,
            active_patrol_entry=None,
            _download_stop_reason=None,
            queue_paused=False,
            tr=lambda key: key,
        )
        status_icon = object()
        delete_icon = object()

        with patch.object(
            ui_theme_icons,
            "_make_svg_icon",
            side_effect=lambda svg: (
                delete_icon if svg == ui_theme_icons._QUEUE_DELETE_SVG else status_icon
            ),
        ):
            target._set_queue_status_icon(entry, hovered=False)
            status_button.setIcon.assert_called_once_with(status_icon)
            status_button.setAttribute.assert_called_once_with(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents,
                True,
            )

            status_button.reset_mock()
            target._set_queue_status_icon(entry, hovered=True)

        status_button.setIcon.assert_called_once_with(delete_icon)
        status_button.setAttribute.assert_called_once_with(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            False,
        )
        status_button.setCursor.assert_called_once_with(
            Qt.CursorShape.PointingHandCursor
        )
        status_button.setToolTip.assert_called_once_with("delete_task_tooltip")

    def test_completed_item_persists_and_notifies_before_target_finish_without_repeat(
        self,
    ):
        entry = patrol_entry(targets=("alice",))
        account = entry["patrol_state"]["accounts"][0]
        account["state"] = "checking"
        preview_dialog = SimpleNamespace(
            isVisible=Mock(return_value=True),
            _task_entry=entry,
            add_preview_image=Mock(),
            refresh_target=Mock(),
        )
        target = _GuiMethodHarness(
            active_patrol_entry=entry,
            queue_items=[entry],
            _log_dialog=preview_dialog,
            _patrol_account_for_entry=Mock(return_value=account),
            _normalize_preview_path=lambda path: str(path or ""),
            _preview_path_key=lambda path: str(path or "").casefold(),
            refresh_queue_item_label=Mock(),
            _refresh_open_task_dialog=Mock(),
            save_task=Mock(),
        )
        details = [
            file_detail("A.jpg", size=10),
            file_detail("B.jpg", size=20),
        ]
        completed_event = {
            "target_username": "alice",
            # One carousel/media item may publish multiple completed files.
            "downloaded_files": ["A.jpg", "B.jpg"],
            "downloaded_file_details": details,
        }

        ui_controllers_patrol.PatrolController.on_patrol_item_completed(
            target, entry, completed_event
        )

        self.assertEqual(entry["downloaded_files"], ["A.jpg", "B.jpg"])
        self.assertEqual(entry["downloaded_file_details"], details)
        self.assertEqual(
            preview_dialog.add_preview_image.call_args_list,
            [
                call("A.jpg", target_username="alice"),
                call("B.jpg", target_username="alice"),
            ],
        )
        self.assertEqual(account["total_downloaded_count"], 1)
        self.assertEqual(entry["patrol_state"]["total_downloaded"], 1)
        target.refresh_queue_item_label.assert_not_called()
        target._refresh_open_task_dialog.assert_not_called()
        preview_dialog.refresh_target.assert_called_once_with("alice", account)
        target.save_task.assert_called_once_with(entry)

        # Target completion only updates target state/counts. Media was already
        # published by item_completed and must not be notified a second time.
        target_event = {
            "target_username": "alice",
            "status": downloads_models.status_code.SUCCESS,
            "downloaded_count": 1,
            "checked_count": 5,
            "updates_found_count": 1,
        }
        ui_controllers_patrol.PatrolController.on_patrol_target_finished(
            target, entry, target_event
        )

        self.assertEqual(len(entry["downloaded_files"]), 2)
        self.assertEqual(len(entry["downloaded_file_details"]), 2)
        self.assertEqual(account["total_downloaded_count"], 1)
        self.assertEqual(entry["patrol_state"]["total_downloaded"], 1)
        self.assertEqual(
            preview_dialog.add_preview_image.call_args_list,
            [
                call("A.jpg", target_username="alice"),
                call("B.jpg", target_username="alice"),
            ],
        )

    def test_target_finish_reconciles_live_count_against_attempt_baseline(self):
        entry = patrol_entry(targets=("alice",))
        account = entry["patrol_state"]["accounts"][0]
        account["total_downloaded_count"] = 3
        entry["patrol_state"]["total_downloaded"] = 3
        target = _GuiMethodHarness(
            queue_items=[entry],
            _thread_pipeline_current_file_name="",
            _refresh_thread_pipeline=Mock(),
            refresh_queue_item_label=Mock(),
            _refresh_open_task_dialog=Mock(),
            save_task=Mock(),
        )

        ui_controllers_patrol.PatrolController.on_patrol_target_started(
            target,
            entry,
            {
                "target_username": "alice",
                "target_index": 0,
            },
        )
        for media_id in (1, 2):
            ui_controllers_patrol.PatrolController.on_patrol_item_completed(
                target,
                entry,
                {
                    "target_username": "alice",
                    "target_index": 0,
                    "completed_item": {"media_id": media_id},
                    "downloaded_files": [],
                    "downloaded_file_details": [],
                },
            )

        self.assertEqual(account["total_downloaded_count"], 5)
        self.assertEqual(entry["patrol_state"]["total_downloaded"], 5)

        ui_controllers_patrol.PatrolController.on_patrol_target_finished(
            target,
            entry,
            {
                "target_username": "alice",
                "target_index": 0,
                "status": downloads_models.status_code.FAILED,
                "downloaded_count": 0,
                "checked_count": 2,
                "updates_found_count": 2,
            },
        )

        self.assertEqual(account["total_downloaded_count"], 5)
        self.assertEqual(entry["patrol_state"]["total_downloaded"], 5)
        self.assertNotIn("_patrol_target_attempts", entry)

    def test_incomplete_success_result_keeps_target_retryable(self):
        for counter_name in ("download_failed_count", "unresolved_count"):
            with self.subTest(counter_name=counter_name):
                entry = patrol_entry(targets=("alice",))
                account = entry["patrol_state"]["accounts"][0]
                account["state"] = "checking"
                target = _GuiMethodHarness(
                    queue_items=[entry],
                    _thread_pipeline_current_file_name="",
                    _refresh_thread_pipeline=Mock(),
                    refresh_queue_item_label=Mock(),
                    _refresh_open_task_dialog=Mock(),
                    save_task=Mock(),
                )
                event = {
                    "target_username": "alice",
                    "target_index": 0,
                    "status": downloads_models.status_code.SUCCESS,
                    "downloaded_count": 1,
                    "checked_count": 2,
                    "updates_found_count": 2,
                    counter_name: 1,
                }

                ui_controllers_patrol.PatrolController.on_patrol_target_finished(
                    target, entry, event
                )

                self.assertEqual(account["state"], "failed")
                self.assertEqual(account["total_downloaded_count"], 1)
                self.assertNotIn(
                    account["state"],
                    tasks_schema._PATROL_COMPLETED_ACCOUNT_STATES,
                )

    def test_patrol_log_target_ownership_follows_runtime_target_boundaries(self):
        entry = patrol_entry(targets=("alice",))
        entry["logs"] = ["global line mentioning @alice stays unowned"]
        entry["log_targets"] = [None]
        account = entry["patrol_state"]["accounts"][0]
        target = _GuiMethodHarness(
            queue_items=[entry],
            _thread_pipeline_current_file_name="",
            _refresh_thread_pipeline=Mock(),
            _patrol_account_for_entry=Mock(return_value=account),
            refresh_queue_item_label=Mock(),
            _refresh_open_task_dialog=Mock(),
            save_task=Mock(),
        )

        ui_controllers_patrol.PatrolController.on_patrol_target_started(
            target,
            entry,
            {
                "target_username": "@alice",
                "target_index": 0,
            },
        )
        ui_controllers_patrol.PatrolController._on_patrol_task_log(
            target, entry, "[fetch] exact raw target line"
        )
        ui_controllers_patrol.PatrolController.on_patrol_target_finished(
            target,
            entry,
            {
                "target_username": "alice",
                "target_index": 0,
                "status": downloads_models.status_code.SUCCESS,
                "downloaded_count": 0,
                "checked_count": 1,
                "updates_found_count": 0,
                "no_updates_confirmed": True,
            },
        )
        ui_controllers_patrol.PatrolController._on_patrol_task_log(
            target, entry, "[gui] global finish line"
        )

        self.assertEqual(
            entry["logs"],
            [
                "global line mentioning @alice stays unowned",
                "[fetch] exact raw target line",
                "[gui] global finish line",
            ],
        )
        self.assertEqual(entry["log_targets"], [None, "alice", None])
        self.assertNotIn("_patrol_active_log_target", entry)

    def test_patrol_log_targets_are_persisted(self):
        entry = patrol_entry(targets=("alice",))
        original_logs = ["global line", "[download] alice line"]
        original_owners = [None, "alice"]
        entry["logs"] = list(original_logs)
        entry["log_targets"] = list(original_owners)
        entry["_patrol_active_log_target"] = "alice"
        target = _GuiMethodHarness(queue_items=[entry])

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(
                app_paths,
                "TASKS_DIR",
                temp_dir,
            ),
        ):
            ui_controllers_task_store.TaskStoreController.save_task(target, entry)
            saved = json.loads(
                Path(temp_dir, "aaaaaaaaaaaa.json").read_text(encoding="utf-8")
            )

        self.assertEqual(entry["logs"], original_logs)
        self.assertEqual(saved["logs"], original_logs)
        self.assertEqual(saved["log_targets"], original_owners)
        self.assertNotIn("_patrol_active_log_target", saved)

    def test_target_restart_resets_only_attempt_counters(self):
        entry = patrol_entry("stopped", targets=("alice", "bob"))
        current = entry["patrol_state"]["accounts"][0]
        current.update(
            {
                "state": "stopped",
                "checked_count": 4,
                "updates_found_count": 2,
                "total_downloaded_count": 3,
                "message": "Interrupted",
            }
        )
        future = entry["patrol_state"]["accounts"][1]
        target = _GuiMethodHarness(
            queue_items=[entry],
            _thread_pipeline_current_file_name="previous-target.jpg",
            _refresh_thread_pipeline=Mock(),
            refresh_queue_item_label=Mock(),
            _refresh_open_task_dialog=Mock(),
        )

        ui_controllers_patrol.PatrolController.on_patrol_target_started(
            target,
            entry,
            {
                "target_username": "alice",
                "target_index": 0,
            },
        )

        self.assertEqual(current["state"], "checking")
        self.assertEqual(current["checked_count"], 0)
        self.assertEqual(current["updates_found_count"], 0)
        self.assertEqual(current["total_downloaded_count"], 3)
        self.assertEqual(current["message"], "")
        self.assertEqual(future["state"], "waiting")
        self.assertEqual(target._thread_pipeline_current_file_name, "")

    def test_target_finish_ignores_media_payload(self):
        entry = patrol_entry(targets=("alice",))
        account = entry["patrol_state"]["accounts"][0]
        account["state"] = "checking"
        account["total_downloaded_count"] = 1
        entry["patrol_state"]["total_downloaded"] = 1
        entry["downloaded_files"] = ["A.jpg"]
        entry["downloaded_file_details"] = [file_detail("A.jpg")]
        target = _GuiMethodHarness(
            queue_items=[entry],
            _thread_pipeline_current_file_name="finished-target.jpg",
            _refresh_thread_pipeline=Mock(),
            _patrol_account_for_entry=Mock(return_value=account),
            _notify_log_dialog_new_image=Mock(),
            refresh_queue_item_label=Mock(),
            _refresh_open_task_dialog=Mock(),
            save_task=Mock(),
        )
        event = {
            "target_username": "alice",
            "status": downloads_models.status_code.SUCCESS,
            "downloaded_count": 2,
            "checked_count": 5,
            "updates_found_count": 2,
            "downloaded_files": ["a.JPG", "B.jpg", "b.JPG"],
            "downloaded_file_details": [
                file_detail("a.JPG", size=10),
                file_detail("B.jpg", size=20),
                file_detail("b.JPG", size=21),
            ],
        }

        ui_controllers_patrol.PatrolController.on_patrol_target_finished(
            target, entry, event
        )

        self.assertEqual(entry["downloaded_files"], ["A.jpg"])
        self.assertEqual(entry["downloaded_file_details"], [file_detail("A.jpg")])
        self.assertNotIn("round_count", entry["task"])
        self.assertNotIn("completed_count", entry["task"])
        self.assertEqual(entry["patrol_state"]["total_downloaded"], 3)
        self.assertEqual(account["state"], "updated")
        self.assertEqual(account["total_downloaded_count"], 3)
        target._notify_log_dialog_new_image.assert_not_called()
        target.save_task.assert_called_once_with(entry)
        self.assertEqual(target._thread_pipeline_current_file_name, "")

    def test_finishing_active_patrol_task_clears_last_filename(self):
        entry = patrol_entry("running", targets=("alice",))
        target = _GuiMethodHarness(
            active_patrol_entry=entry,
            queue_items=[entry],
            current_queue_index=0,
            _thread_pipeline_current_file_name="last-patrol-file.jpg",
            _refresh_thread_pipeline=Mock(),
            set_queue_item_status=Mock(
                side_effect=lambda item, status: item.__setitem__("status", status)
            ),
            _refresh_open_task_dialog=Mock(),
        )

        ui_controllers_patrol.PatrolController._finish_active_patrol_task(
            target,
            "stopped",
            "Auto patrol stopped",
        )

        self.assertEqual(target._thread_pipeline_current_file_name, "")
        self.assertIsNone(target.active_patrol_entry)
        self.assertIsNone(target.current_queue_index)
        target.set_queue_item_status.assert_called_once_with(entry, "stopped")

    def test_resumed_formal_download_keeps_updated_state_when_run_adds_zero(self):
        entry = patrol_entry(targets=("alice",))
        account = entry["patrol_state"]["accounts"][0]
        account.update(
            {
                "state": "checking",
                "total_downloaded_count": 2,
            }
        )
        entry["patrol_state"]["total_downloaded"] = 2
        target = _GuiMethodHarness(
            queue_items=[entry],
            _normalize_preview_path=lambda path: str(path or ""),
            _preview_path_key=lambda path: str(path or "").casefold(),
            refresh_queue_item_label=Mock(),
            _refresh_open_task_dialog=Mock(),
            save_task=Mock(),
        )

        ui_controllers_patrol.PatrolController.on_patrol_target_finished(
            target,
            entry,
            {
                "target_username": "alice",
                "target_index": 0,
                "status": downloads_models.status_code.SUCCESS,
                "downloaded_count": 0,
                "checked_count": 3,
                "updates_found_count": 0,
                "no_updates_confirmed": True,
            },
        )

        self.assertEqual(account["state"], "updated")
        self.assertEqual(account["total_downloaded_count"], 2)
        self.assertEqual(entry["patrol_state"]["total_downloaded"], 2)

    def test_patrol_avatar_refreshes_matching_task_without_persisting_media(self):
        entry = patrol_entry(targets=("alice",))
        entry["task"]["save_path"] = "patrol-data"
        target = _GuiMethodHarness(
            queue_items=[entry],
            _refresh_target_account_icon=Mock(),
            _refresh_open_task_dialog=Mock(),
        )

        ui_controllers_patrol.PatrolController.on_patrol_profile_picture_downloaded(
            target,
            entry,
            "@alice",
            "patrol-data/alice/profile/avatar.jpg",
        )

        target._refresh_target_account_icon.assert_called_once_with(
            "alice",
            "patrol-data",
        )
        target._refresh_open_task_dialog.assert_called_once_with(entry)
        self.assertEqual(entry["downloaded_files"], [])
        self.assertEqual(entry["downloaded_file_details"], [])

    def test_live_patrol_image_is_not_persisted_before_item_completion(self):
        entry = patrol_entry()
        target = SimpleNamespace(
            active_patrol_entry=entry,
            queue_items=[entry],
            _remember_preview_path=Mock(side_effect=(True, False)),
            _refresh_timer=SimpleNamespace(start=Mock()),
            save_task=Mock(),
        )

        ui_controllers_patrol.PatrolController.on_patrol_image_downloaded(
            target, "A.jpg"
        )
        ui_controllers_patrol.PatrolController.on_patrol_image_downloaded(
            target, "a.JPG"
        )

        self.assertEqual(entry["downloaded_files"], [])
        target._refresh_timer.start.assert_called_once_with()
        target.save_task.assert_not_called()

    def test_interrupted_saved_patrol_loads_as_pending(self):
        record = patrol_entry("running")
        record["patrol_state"]["accounts"][0]["state"] = "checking"
        loaded = []
        text_value = SimpleNamespace(text=lambda: "")
        target = SimpleNamespace(
            username=text_value,
            webhook=text_value,
            telegram_bot_token=text_value,
            telegram_chat_id=text_value,
            save_path=text_value,
            default_save_path="data",
            _normalize_task_progress=lambda task: None,
            add_task_to_queue=lambda task, status, **kwargs: loaded.append(
                (status, kwargs)
            ),
            _finalize_loaded_task_batch=Mock(),
            _scroll_queue_to_bottom=lambda: None,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            Path(temp_dir, "aaaaaaaaaaaa.json").write_text(
                json.dumps(record), encoding="utf-8"
            )
            with patch.object(app_paths, "TASKS_DIR", temp_dir):
                ui_controllers_task_store.TaskStoreController.load_saved_tasks(target)

        self.assertEqual(loaded[0][0], "pending")
        self.assertEqual(loaded[0][1]["queue_order"], 0)
        self.assertEqual(loaded[0][1]["log_targets"], record["log_targets"])
        accounts = loaded[0][1]["patrol_state"]["accounts"]
        self.assertEqual(accounts[0]["state"], "stopped")
        self.assertEqual(accounts[1]["state"], "waiting")
        self.assertFalse(target._loading_saved_tasks)
        target._finalize_loaded_task_batch.assert_called_once_with()

    def test_non_v7_patrol_records_are_rejected_without_rewriting_files(self):
        cases = []

        version_six = patrol_entry("running")
        version_six["schema_version"] = 6
        cases.append(("schema_v6", version_six, "schema_version: must be 7"))

        old_field = patrol_entry("running")
        old_field["task"]["legacy_scan_limit_items"] = old_field["task"].pop(
            "page_limit"
        )
        cases.append(
            (
                "old_field",
                old_field,
                "task: missing field(s): page_limit",
            )
        )

        for name, record, expected_error in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp_dir:
                task_path = Path(temp_dir, "aaaaaaaaaaaa.json")
                original_text = json.dumps(record)
                task_path.write_text(original_text, encoding="utf-8")
                target = SimpleNamespace()
                with (
                    patch.object(app_paths, "TASKS_DIR", temp_dir),
                    patch.object(ui_console, "print") as print_mock,
                ):
                    ui_controllers_task_store.TaskStoreController.load_saved_tasks(
                        target
                    )

                self.assertEqual(
                    task_path.read_text(encoding="utf-8"),
                    original_text,
                )
                printed = "\n".join(
                    str(call.args[0]) for call in print_mock.call_args_list if call.args
                )
                self.assertIn("Skipping invalid saved task", printed)
                self.assertIn(expected_error, printed)

    def test_delete_patrol_task_cleans_all_child_cursors(self):
        entry = patrol_entry("stopped", targets=("alice", "bob", "carol"))
        target = SimpleNamespace(
            _task_file_path=Mock(return_value="missing-task.json"),
        )

        with patch.object(
            downloads_checkpoints,
            "delete_patrol_resume_checkpoints",
        ) as delete_cursors:
            ui_controllers_task_store.TaskStoreController.delete_task_file(
                target, entry
            )

        delete_cursors.assert_called_once_with(
            "aaaaaaaaaaaa",
            "posts",
            3,
        )

    def test_exit_without_worker_returns_parent_to_pending(self):
        target = SimpleNamespace(
            patrol_thread=None,
            patrol_enabled=True,
            _patrol_delay_before_start=True,
            _download_stop_reason=None,
            _queue_handoff_target_id=None,
            _finish_active_patrol_task=Mock(),
        )

        ui_controllers_execution.QueueExecutionController._stop_patrol_thread_for_exit(
            target
        )

        target._finish_active_patrol_task.assert_called_once_with(
            "pending", "Auto patrol paused on application exit"
        )


if __name__ == "__main__":
    unittest.main()
