"""Video thumbnail sizing, sharing, and task-dialog cancellation contracts."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import subprocess

from PySide6.QtGui import QIcon, QImage
from PySide6.QtWidgets import QApplication, QWidget

import inscrawler.ui.console as ui_console
import inscrawler.ui.constants as ui_constants
import inscrawler.ui.controllers.logs as ui_controllers_logs
import inscrawler.ui.dialogs.patrol as ui_dialogs_patrol
import inscrawler.ui.dialogs.task as ui_dialogs_task
import inscrawler.ui.i18n.text as ui_i18n_text
import inscrawler.ui.preview.events as ui_preview_events
import inscrawler.ui.preview.panel as ui_preview_panel
import inscrawler.ui.preview.thumbnails as ui_preview_thumbnails


class _FakeThreadPool:
    def __init__(self, *, take_result=True):
        self.take_result = take_result
        self.started = []
        self.take_attempts = []

    def start(self, task):
        self.started.append(task)

    def tryTake(self, task):
        self.take_attempts.append(task)
        return self.take_result


class _PreviewWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.ui_language = "en"
        self._thumb_extractor = Mock()

    def tr(self, key, **kwargs):
        text = ui_i18n_text.UI_TEXT[self.ui_language].get(key, key)
        return text.format(**kwargs) if kwargs else text

    @staticmethod
    def _theme_tokens():
        return {
            "surface": "#ffffff",
            "base": "#f4f4f4",
            "border": "#d0d0d0",
            "muted": "#666666",
            "text": "#111111",
            "highlight": "#3874f2",
            "highlight_text": "#ffffff",
        }

    @staticmethod
    def _scrollbar_stylesheet():
        return ""

    @staticmethod
    def _preview_background_color():
        return "#f4f4f4"

    @staticmethod
    def _media_badge_label(media_kind):
        return media_kind

    @staticmethod
    def _video_cache_key(path):
        return ("video", os.path.normcase(os.path.abspath(path)))

    @staticmethod
    def _get_detail_video_cached_pixmap(_cache_key):
        return None

    @staticmethod
    def _open_media_file(_path):
        return None

    @staticmethod
    def _target_profile_picture_icon(_username, base_path=None):
        return QIcon()


class VideoThumbnailTaskTests(unittest.TestCase):
    def test_ffmpeg_caps_the_long_edge_at_640_without_upscaling(self):
        process = Mock()
        process.communicate.return_value = (b"", b"failure")
        process.returncode = 1
        task = ui_preview_thumbnails.VideoThumbnailTask(
            "sample.mp4", ("sample",), "ffmpeg"
        )

        with patch.object(subprocess, "Popen", return_value=process) as popen:
            task.run()

        command = popen.call_args.args[0]
        filter_index = command.index("-vf")
        self.assertEqual(ui_constants.VIDEO_THUMBNAIL_MAX_EDGE, 640)
        self.assertEqual(
            command[filter_index + 1],
            "scale='min(640,iw)':'min(640,ih)':force_original_aspect_ratio=decrease",
        )

    def test_cancel_kills_an_active_ffmpeg_process(self):
        process = Mock()
        process.poll.return_value = None
        task = ui_preview_thumbnails.VideoThumbnailTask(
            "sample.mp4", ("sample",), "ffmpeg"
        )
        task._process = process

        task.cancel()

        self.assertTrue(task._cancel_requested.is_set())
        process.kill.assert_called_once_with()


class VideoThumbnailExtractorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.extractors = []

    def tearDown(self):
        for extractor in self.extractors:
            extractor.deleteLater()
        self.app.processEvents()

    def _extractor(self, *, take_result=True):
        extractor = ui_preview_thumbnails.VideoThumbnailExtractor()
        extractor._ffmpeg_path = "ffmpeg"
        extractor._pool = _FakeThreadPool(take_result=take_result)
        self.extractors.append(extractor)
        return extractor

    def test_last_owner_cancels_a_queued_task(self):
        extractor = self._extractor()
        owner = object()
        cache_key = ("video", "queued")

        extractor.request("queued.mp4", cache_key, owner=owner)

        task = extractor._tasks[cache_key]
        self.assertFalse(task.autoDelete())
        self.assertEqual(extractor.release_owner(owner), 1)
        self.assertEqual(extractor._pool.take_attempts, [task])
        self.assertNotIn(cache_key, extractor._tasks)
        self.assertNotIn(cache_key, extractor._task_owners)

    def test_shared_task_survives_until_its_last_owner_releases_it(self):
        extractor = self._extractor()
        detail_owner = object()
        main_owner = object()
        cache_key = ("video", "shared")

        extractor.request("shared.mp4", cache_key, owner=detail_owner)
        extractor.request("shared.mp4", cache_key, owner=main_owner)

        self.assertEqual(len(extractor._pool.started), 1)
        self.assertEqual(extractor.release_owner(detail_owner), 0)
        self.assertEqual(extractor._pool.take_attempts, [])
        self.assertIn(cache_key, extractor._tasks)
        self.assertEqual(extractor._task_owners[cache_key], {main_owner})

        self.assertEqual(extractor.release_owner(main_owner), 1)
        self.assertNotIn(cache_key, extractor._tasks)

    def test_last_owner_cancels_a_running_task(self):
        extractor = self._extractor(take_result=False)
        owner = object()
        cache_key = ("video", "running")

        extractor.request("running.mp4", cache_key, owner=owner)
        task = extractor._tasks[cache_key]

        with patch.object(task, "cancel", wraps=task.cancel) as cancel:
            self.assertEqual(extractor.release_owner(owner), 1)

        cancel.assert_called_once_with()
        self.assertNotIn(cache_key, extractor._tasks)
        self.assertNotIn(cache_key, extractor._task_owners)

    def test_late_result_from_cancelled_task_cannot_remove_replacement(self):
        extractor = self._extractor(take_result=False)
        first_owner = object()
        second_owner = object()
        cache_key = ("video", "reopened")

        extractor.request("reopened.mp4", cache_key, owner=first_owner)
        cancelled_task = extractor._tasks[cache_key]
        extractor.release_owner(first_owner)
        extractor.request("reopened.mp4", cache_key, owner=second_owner)
        replacement_task = extractor._tasks[cache_key]

        image = QImage(8, 8, QImage.Format.Format_RGB32)
        image.fill(0)
        extractor._on_task_ready(
            cancelled_task,
            "reopened.mp4",
            cache_key,
            image,
            "",
        )

        self.assertIs(extractor._tasks[cache_key], replacement_task)
        self.assertEqual(extractor._task_owners[cache_key], {second_owner})

    def test_completed_task_delivers_its_thumbnail(self):
        extractor = self._extractor(take_result=False)
        owner = object()
        cache_key = ("video", "completed")
        received = []
        extractor.thumbnail_ready.connect(
            lambda path, key, pixmap: received.append((path, key, pixmap))
        )
        extractor.request("completed.mp4", cache_key, owner=owner)
        task = extractor._tasks[cache_key]

        image = QImage(8, 8, QImage.Format.Format_RGB32)
        image.fill(0)
        extractor._on_task_ready(task, "completed.mp4", cache_key, image, "")

        self.assertNotIn(cache_key, extractor._tasks)
        self.assertNotIn(cache_key, extractor._task_owners)
        self.assertEqual(len(received), 1)
        self.assertFalse(received[0][2].isNull())


class TaskPreviewCancellationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_detail_panel_submits_every_video_with_one_owner(self):
        window = _PreviewWindow()
        panel = ui_preview_panel.TaskMediaPreviewPanel(window, {"downloaded_files": []})
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                paths = []
                for index in range(18):
                    path = Path(temp_dir) / f"video-{index:02d}.mp4"
                    path.touch()
                    paths.append(str(path))

                panel.set_preview_images(paths)

            self.assertEqual(len(panel._preview_cells), 18)
            self.assertEqual(window._thumb_extractor.request.call_count, 18)
            for request_call in window._thumb_extractor.request.call_args_list:
                self.assertIs(
                    request_call.kwargs["owner"],
                    panel._video_thumbnail_owner,
                )
        finally:
            panel.close()
            panel.deleteLater()
            window.close()
            window.deleteLater()
            self.app.processEvents()

    def test_panel_releases_its_owner_only_once(self):
        window = _PreviewWindow()
        window._thumb_extractor.release_owner.return_value = 7
        panel = ui_preview_panel.TaskMediaPreviewPanel(window, {"downloaded_files": []})
        owner = panel._video_thumbnail_owner
        try:
            self.assertEqual(panel.release_video_thumbnail_requests(), 7)
            self.assertEqual(panel.release_video_thumbnail_requests(), 0)
            window._thumb_extractor.release_owner.assert_called_once_with(owner)
        finally:
            panel.close()
            panel.deleteLater()
            window.close()
            window.deleteLater()
            self.app.processEvents()

    def test_closed_panel_cannot_submit_from_its_deferred_initial_load(self):
        window = _PreviewWindow()
        with tempfile.TemporaryDirectory() as temp_dir:
            media_path = Path(temp_dir) / "deferred.mp4"
            media_path.touch()
            panel = ui_preview_panel.TaskMediaPreviewPanel(
                window,
                {"downloaded_files": [str(media_path)]},
            )

            panel.release_video_thumbnail_requests()
            self.app.processEvents()

            window._thumb_extractor.request.assert_not_called()
            self.assertEqual(panel._preview_cells, [])

        panel.close()
        panel.deleteLater()
        window.close()
        window.deleteLater()
        self.app.processEvents()

    def test_both_task_dialog_types_release_preview_requests_on_close(self):
        regular_entry = {
            "task": {
                "task_kind": "download",
                "target_user": "alice",
                "download_media_type": "posts",
                "downloaded_count": 0,
                "created_at": "2026-01-01 00:00:00",
            },
            "logs": [],
            "downloaded_files": [],
        }
        patrol_entry = {
            "task": {
                "task_kind": "auto_patrol",
                "target_users": [],
                "download_media_type": "posts",
                "page_limit": 1,
                "created_at": "2026-01-01 00:00:00",
            },
            "logs": [],
            "log_targets": [],
            "downloaded_files": [],
            "patrol_state": {"accounts": []},
        }

        for dialog_type, entry in (
            (ui_dialogs_task.TaskLogDialog, regular_entry),
            (ui_dialogs_patrol.AutoPatrolTaskDialog, patrol_entry),
        ):
            with self.subTest(dialog_type=dialog_type.__name__):
                window = _PreviewWindow()
                dialog = dialog_type(window, entry)
                owner = dialog._media_preview._video_thumbnail_owner
                dialog.show()
                self.app.processEvents()

                dialog.close()

                window._thumb_extractor.release_owner.assert_called_once_with(owner)
                dialog.deleteLater()
                window.close()
                window.deleteLater()
                self.app.processEvents()


class VideoThumbnailCacheDestinationTests(unittest.TestCase):
    @staticmethod
    def _path_key(path):
        return os.path.normcase(os.path.abspath(path))

    def _target(self, current_preview_images):
        return SimpleNamespace(
            current_preview_images=current_preview_images,
            cells=[],
            _preview_path_key=self._path_key,
            _store_home_video_cached_pixmap=Mock(),
            _store_detail_video_cached_pixmap=Mock(),
            _update_log_dialog_cells=Mock(),
        )

    def test_completed_thumbnail_uses_its_current_preview_tier(self):
        pixmap = Mock()
        pixmap.isNull.return_value = False
        home_path = os.path.abspath("home.mp4")
        cache_key = ("video", self._path_key(home_path))

        home_target = self._target([home_path])
        ui_preview_events.PreviewEvents._on_thumbnail_ready(
            home_target, home_path, cache_key, pixmap
        )

        home_target._store_home_video_cached_pixmap.assert_called_once_with(
            cache_key, pixmap
        )
        home_target._store_detail_video_cached_pixmap.assert_not_called()

        detail_target = self._target([])
        ui_preview_events.PreviewEvents._on_thumbnail_ready(
            detail_target, home_path, cache_key, pixmap
        )

        detail_target._store_detail_video_cached_pixmap.assert_called_once_with(
            cache_key, pixmap
        )
        detail_target._store_home_video_cached_pixmap.assert_not_called()

    def test_new_download_registers_home_before_notifying_open_detail(self):
        events = []
        image_path = os.path.abspath("new.mp4")
        entry = {"downloaded_files": []}
        target = SimpleNamespace(
            current_queue_index=0,
            queue_items=[entry],
            _normalize_preview_path=lambda path: path,
            _remember_preview_path=lambda _path: events.append("home") or True,
            _notify_log_dialog_new_image=(
                lambda _entry, _path: events.append("detail")
            ),
            _refresh_timer=SimpleNamespace(start=Mock()),
        )

        with patch.object(ui_console, "debug_print"):
            ui_controllers_logs.TaskLogController.on_new_image_downloaded(
                target, image_path
            )

        self.assertEqual(events, ["home", "detail"])
        self.assertEqual(entry["downloaded_files"], [image_path])


if __name__ == "__main__":
    unittest.main()
