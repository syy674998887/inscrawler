"""Keep detail thumbnails scoped to their cells instead of the home cache."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QSize
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QScrollArea, QWidget

from inscrawler.ui.dialogs.patrol import AutoPatrolTaskDialog
from inscrawler.ui.dialogs.task import TaskLogDialog
from inscrawler.ui.preview.cell import MediaCell
from inscrawler.ui.preview.controller import PreviewController
from inscrawler.ui.preview.events import PreviewEvents
from tests.test_video_thumbnail_lifecycle import _PreviewWindow


class _ImagePreviewWindow(_PreviewWindow, PreviewController, PreviewEvents):
    def __init__(self):
        super().__init__()
        self._image_pixmap_cache = {}
        self._image_thumb_inflight = set()
        self._image_thumb_extractor = Mock()
        self.current_preview_images = []
        self.cells = []
        self.cell_size = QSize(100, 100)
        self._log_dialog = None
        self.scroll = QScrollArea(self)
        self.container = QWidget(self)
        self.ensure_enough_cells = Mock()


class ImageThumbnailLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.window = _ImagePreviewWindow()

    def tearDown(self):
        if self.window._log_dialog is not None:
            self.window._log_dialog.close()
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def _path(self, name):
        path = Path(self.temp.name) / f"{name}.jpg"
        path.touch()
        return str(path)

    def _dialog(self, paths, dialog_type=TaskLogDialog):
        entry = {
            "task": {
                "task_kind": (
                    "auto_patrol" if dialog_type is AutoPatrolTaskDialog else "download"
                ),
                "target_user": "example",
                "target_users": [],
                "download_media_type": "posts",
                "downloaded_count": 0,
                "page_limit": 1,
                "created_at": "2026-01-01 00:00:00",
            },
            "logs": [],
            "log_targets": [],
            "downloaded_files": [],
            "patrol_state": {"accounts": []},
        }
        dialog = dialog_type(self.window, entry)
        self.window._log_dialog = dialog
        dialog.show()
        self.app.processEvents()
        dialog._media_preview.set_preview_images(paths)
        return dialog

    def _deliver(self, path, cache_key):
        image = QImage(32, 32, QImage.Format.Format_RGB32)
        image.fill(0)
        self.window._on_image_thumbnail_ready(path, cache_key, image)

    def _home_cell(self, path):
        cell = MediaCell()
        cell.setParent(self.window)
        key = self.window._image_cache_key(path, self.window.cell_size)
        cell.set_placeholder("image", path, key)
        self.window.cells = [cell]
        self.window.current_preview_images = [path]
        self._deliver(path, key)
        return cell, key

    def test_sequential_detail_dialogs_and_late_results_do_not_grow_home_cache(self):
        _, home_key = self._home_cell(self._path("home"))
        for index, dialog_type in enumerate(
            (TaskLogDialog, AutoPatrolTaskDialog, TaskLogDialog)
        ):
            path = self._path(f"detail-{index}")
            dialog = self._dialog([path], dialog_type)
            cell = dialog._preview_cells[0]
            key = cell._current_cache_key
            self._deliver(path, key)
            self.assertFalse(cell._is_placeholder)
            dialog.close()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.window._log_dialog = None
            self._deliver(path, key)
            self.assertEqual(set(self.window._image_pixmap_cache), {home_key})

    def test_resize_discards_old_results_and_keeps_current_detail_visible(self):
        path = self._path("resized")
        dialog = self._dialog([path])
        cell = dialog._preview_cells[0]
        old_key = cell._current_cache_key
        self._deliver(path, old_key)

        dialog.resize(dialog.width() + 300, dialog.height())
        self.app.processEvents()
        self.app.processEvents()
        new_key = cell._current_cache_key
        self.assertNotEqual(old_key, new_key)
        self._deliver(path, old_key)
        self.assertEqual(cell._current_cache_key, new_key)
        self._deliver(path, new_key)
        self.assertFalse(cell._is_placeholder)
        self.assertEqual(self.window._image_pixmap_cache, {})

    def test_switching_detail_dataset_does_not_keep_previous_images(self):
        previous = self._path("previous")
        current = self._path("current")
        dialog = self._dialog([previous], AutoPatrolTaskDialog)
        previous_key = dialog._preview_cells[0]._current_cache_key
        self._deliver(previous, previous_key)

        dialog._media_preview.set_preview_images([current])
        cell = dialog._preview_cells[0]
        self._deliver(previous, previous_key)
        self.assertTrue(cell._is_placeholder)
        self._deliver(current, cell._current_cache_key)
        self.assertFalse(cell._is_placeholder)
        self.assertEqual(self.window._image_pixmap_cache, {})

    def test_home_refresh_preserves_the_current_detail_picture(self):
        _, home_key = self._home_cell(self._path("home"))
        path = self._path("detail")
        dialog = self._dialog([path])
        cell = dialog._preview_cells[0]
        self._deliver(path, cell._current_cache_key)
        pixmap_key = cell._original_pixmap.cacheKey()

        self.window.refresh_preview_images()

        self.assertFalse(cell._is_placeholder)
        self.assertEqual(cell._original_pixmap.cacheKey(), pixmap_key)
        self.assertEqual(set(self.window._image_pixmap_cache), {home_key})


if __name__ == "__main__":
    unittest.main()
