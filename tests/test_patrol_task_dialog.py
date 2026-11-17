"""Headless UI contracts for auto-patrol task presentation."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPoint, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QWidget

import inscrawler.tasks.schema as tasks_schema
import inscrawler.ui.controllers.queue_view as ui_controllers_queue_view
import inscrawler.ui.dialogs.console as ui_dialogs_console
import inscrawler.ui.dialogs.patrol as ui_dialogs_patrol
import inscrawler.ui.dialogs.task as ui_dialogs_task
import inscrawler.ui.i18n.text as ui_i18n_text
import inscrawler.ui.preview.cell as ui_preview_cell
import inscrawler.ui.qt as ui_qt

_COMPLETED_PATROL_STATES = {"no_updates", "updated"}


def _patrol_entry(
    states=("waiting",),
    *,
    targets=None,
    checked_count=4,
    updates_found_count=2,
    total_downloaded_count=7,
):
    if targets is None:
        targets = tuple(f"user{index}" for index in range(len(states)))
    targets = tuple(targets)
    accounts = []
    for index, username in enumerate(targets):
        state = states[index] if index < len(states) else "waiting"
        accounts.append(
            {
                "target_user": username,
                "state": state,
                "checked_count": checked_count,
                "updates_found_count": updates_found_count,
                "total_downloaded_count": total_downloaded_count,
                "message": "",
            }
        )
    return {
        "schema_version": 7,
        "task": {
            "task_id": "aaaaaaaaaaaa",
            "task_kind": "auto_patrol",
            "target_users": list(targets),
            "download_media_type": "posts",
            "page_limit": 5,
            "save_path": "data",
            "notification": "",
            "created_at": "2026-01-01 00:00:00",
        },
        "status": "running",
        "logs": ["Patrol started"],
        "log_targets": [None],
        "downloaded_files": [],
        "downloaded_file_details": [],
        "patrol_state": {
            "total_downloaded": total_downloaded_count * len(accounts),
            "accounts": accounts,
        },
    }


def _download_entry():
    return {
        "task": {
            "task_id": "bbbbbbbbbbbb",
            "task_kind": "download",
            "target_user": "alice",
            "download_media_type": "posts",
            "scope_unit": "page",
            "scope_limit": 5,
            "scope_completed": 0,
            "media_request_cap": None,
            "downloaded_count": 5,
            "notification": "",
            "created_at": "2026-01-01 00:00:00",
        },
        "status": "running",
        "logs": [],
        "downloaded_files": [],
        "downloaded_file_details": [],
    }


class _DialogWindow(QWidget):
    """Small parent implementing only the services used by task dialogs."""

    def __init__(self, language="zh"):
        super().__init__()
        self.ui_language = language
        self.max_preview_images = 15
        self.avatar_requests = []
        self._thumb_extractor = Mock()

    def tr(self, key, **kwargs):
        text = ui_i18n_text.UI_TEXT[self.ui_language].get(key, key)
        return text.format(**kwargs) if kwargs else text

    def _theme_tokens(self):
        return {
            "surface": "#ffffff",
            "base": "#f4f4f4",
            "border": "#d0d0d0",
            "muted": "#666666",
            "text": "#111111",
            "highlight": "#3874f2",
            "highlight_text": "#ffffff",
        }

    def _scrollbar_stylesheet(self):
        return ""

    def _preview_background_color(self):
        return "#f4f4f4"

    def _media_badge_label(self, media_kind):
        return {"image": "图片", "video": "视频"}.get(media_kind, "图片")

    @staticmethod
    def _video_cache_key(path):
        return ("video", os.path.normcase(os.path.abspath(path)))

    @staticmethod
    def _get_detail_video_cached_pixmap(_cache_key):
        return None

    @staticmethod
    def _open_media_file(_path):
        return None

    def _target_profile_picture_icon(self, username, base_path=None):
        self.avatar_requests.append((username, base_path))
        # A null icon deliberately exercises the reserved avatar slot used
        # when no local profile picture exists.
        return QIcon()


class _TaskDialogOwner(_DialogWindow):
    _clear_log_dialog_reference = (
        ui_controllers_queue_view.QueueViewController._clear_log_dialog_reference
    )
    show_task_log_dialog = (
        ui_controllers_queue_view.QueueViewController.show_task_log_dialog
    )

    def __init__(self):
        super().__init__()
        self._log_dialog = None

    @staticmethod
    def _show_centered_dialog(dialog):
        dialog.show()


class _QueueHarness:
    refresh_queue_item_label = (
        ui_controllers_queue_view.QueueViewController.refresh_queue_item_label
    )

    def __init__(self, language="zh"):
        self.ui_language = language

    def tr(self, key, **kwargs):
        text = ui_i18n_text.UI_TEXT[self.ui_language].get(key, key)
        return text.format(**kwargs) if kwargs else text

    def _refresh_queue_avatar(self, _entry):
        return None


class PatrolCopyAndProgressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_auto_patrol_copy_is_consistent_in_both_languages(self):
        expected = {
            "en": {
                "add_patrol_task": "Add Auto Patrol",
                "patrol_choose_targets": "Patrol Target Accounts ({selected}/{total})",
                "patrol_task_title": "Task Details",
                "patrol_target_metrics": "Checked: {checked}    Downloaded: {downloaded}",
                "patrol_task_queue_label": "Auto Patrol - {media}: {completed}/{total}",
            },
            "zh": {
                "add_patrol_task": "添加自动巡逻",
                "patrol_choose_targets": "巡逻目标账号 ({selected}/{total})",
                "patrol_task_title": "任务详情",
                "patrol_target_metrics": "检查: {checked}    下载: {downloaded}",
                "patrol_task_queue_label": "自动巡逻 - {media}: {completed}/{total}",
            },
        }

        for language, values in expected.items():
            with self.subTest(language=language):
                for key, value in values.items():
                    self.assertEqual(ui_i18n_text.UI_TEXT[language][key], value)

    def test_only_terminal_account_states_count_as_completed_targets(self):
        all_states = (
            "no_updates",
            "updated",
            "failed",
            "waiting",
            "checking",
            "stopped",
        )
        for state in all_states:
            with self.subTest(state=state):
                entry = _patrol_entry((state,), targets=("alice",))
                expected_completed = int(state in _COMPLETED_PATROL_STATES)
                self.assertEqual(
                    tasks_schema._patrol_target_progress(entry),
                    (expected_completed, 1),
                )

    def test_progress_total_comes_from_task_target_snapshot(self):
        entry = _patrol_entry(
            ("no_updates", "updated", "failed"),
            targets=("alice", "bob", "charlie", "dora", "eve"),
        )
        # The helper intentionally receives fewer explicit states than target
        # accounts; the remaining snapshot targets are waiting.
        self.assertEqual(tasks_schema._patrol_target_progress(entry), (2, 5))

    def test_queue_label_shows_completed_over_total_without_download_count(self):
        entry = _patrol_entry(
            ("no_updates", "updated", "waiting", "checking", "stopped"),
            targets=("alice", "bob", "charlie", "dora", "eve"),
        )
        entry["label"] = QLabel()

        _QueueHarness("zh").refresh_queue_item_label(entry)

        self.assertEqual(entry["label"].text(), "自动巡逻 - Posts: 2/5")
        self.assertNotIn("已下载", entry["label"].text())

    def test_failed_target_remains_unfinished_in_the_queue_progress(self):
        entry = _patrol_entry(
            ("failed", "waiting"),
            targets=("alice", "bob"),
        )
        entry["label"] = QLabel()

        _QueueHarness("en").refresh_queue_item_label(entry)

        self.assertEqual(entry["label"].text(), "Auto Patrol - Posts: 0/2")
        self.assertNotIn("downloaded", entry["label"].text().lower())


class PatrolTaskDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.windows = []
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.close()
            dialog.deleteLater()
        for window in self.windows:
            window.close()
            window.deleteLater()
        self.app.processEvents()

    def _window(self, language="zh"):
        window = _DialogWindow(language)
        self.windows.append(window)
        return window

    def _patrol_dialog(self, entry=None, language="zh"):
        dialog = ui_dialogs_patrol.AutoPatrolTaskDialog(
            self._window(language),
            entry or _patrol_entry(("waiting",), targets=("alice",)),
        )
        self.dialogs.append(dialog)
        return dialog

    def _download_dialog(self, entry=None):
        dialog = ui_dialogs_task.TaskLogDialog(
            self._window("zh"), entry or _download_entry()
        )
        self.dialogs.append(dialog)
        return dialog

    def test_task_dialogs_suppress_the_owner_window_icon(self):
        pixmap = QPixmap(8, 8)
        pixmap.fill(QColor("#3874f2"))
        icon = QIcon(pixmap)

        patrol_owner = self._window("zh")
        patrol_owner.setWindowIcon(icon)
        patrol_dialog = ui_dialogs_patrol.AutoPatrolTaskDialog(
            patrol_owner,
            _patrol_entry(("waiting",), targets=("alice",)),
        )
        self.dialogs.append(patrol_dialog)

        download_owner = self._window("zh")
        download_owner.setWindowIcon(icon)
        download_dialog = ui_dialogs_task.TaskLogDialog(
            download_owner, _download_entry()
        )
        self.dialogs.append(download_dialog)

        self.assertTrue(patrol_dialog.windowIcon().isNull())
        self.assertTrue(download_dialog.windowIcon().isNull())

    @staticmethod
    def _non_divider_widgets(layout):
        widgets = []
        for index in range(layout.count()):
            widget = layout.itemAt(index).widget()
            if widget is None:
                continue
            if isinstance(widget, QFrame) and widget.maximumWidth() == 1:
                continue
            widgets.append(widget)
        return widgets

    def test_patrol_details_use_three_named_horizontal_panels(self):
        dialog = self._patrol_dialog()

        self.assertEqual(dialog._left_panel.objectName(), "patrolLogPanel")
        self.assertEqual(dialog._targets_panel.objectName(), "patrolTargetsPanel")
        self.assertEqual(dialog._media_panel.objectName(), "patrolMediaPanel")
        self.assertEqual(
            self._non_divider_widgets(dialog.layout()),
            [dialog._left_panel, dialog._targets_panel, dialog._media_panel],
        )
        self.assertLess(
            dialog.layout().indexOf(dialog._left_panel),
            dialog.layout().indexOf(dialog._targets_panel),
        )
        self.assertLess(
            dialog.layout().indexOf(dialog._targets_panel),
            dialog.layout().indexOf(dialog._media_panel),
        )
        layout = dialog.layout()
        left_index = layout.indexOf(dialog._left_panel)
        targets_index = layout.indexOf(dialog._targets_panel)
        media_index = layout.indexOf(dialog._media_panel)
        self.assertEqual(
            (
                layout.stretch(left_index),
                layout.stretch(targets_index),
                layout.stretch(media_index),
            ),
            (2, 1, 2),
        )
        self.assertEqual(
            dialog._left_panel.minimumWidth(),
            dialog._media_panel.minimumWidth(),
        )
        self.assertEqual(
            dialog._left_panel.minimumWidth(),
            dialog._targets_panel.minimumWidth() * 2,
        )
        dialog.show()
        self.app.processEvents()
        self.assertLessEqual(
            abs(dialog._left_panel.width() - dialog._media_panel.width()),
            1,
        )
        self.assertLessEqual(
            abs(dialog._left_panel.width() - (dialog._targets_panel.width() * 2)),
            2,
        )

    def test_regular_task_details_remain_two_columns(self):
        dialog = self._download_dialog()
        content_layout = dialog.layout().itemAt(0).layout()

        columns = self._non_divider_widgets(content_layout)
        self.assertEqual(len(columns), 2)
        self.assertIs(columns[-1], dialog._media_preview)
        self.assertFalse(hasattr(dialog, "_targets_panel"))

    def test_full_download_task_details_show_downloaded_count(self):
        for media_type in ("stories", "highlights"):
            with self.subTest(media_type=media_type):
                entry = _download_entry()
                entry["task"].update(
                    {
                        "download_media_type": media_type,
                        "scope_unit": "full",
                        "scope_limit": 1,
                    }
                )
                dialog = ui_dialogs_task.TaskLogDialog(self._window("zh"), entry)
                self.dialogs.append(dialog)

                self.assertFalse(dialog._limit_caption.isHidden())
                self.assertFalse(dialog._limit_value.isHidden())
                self.assertEqual(dialog._limit_caption.text(), "下载数量:")
                self.assertEqual(dialog._limit_value.text(), "5")

    def test_download_task_details_show_only_configured_download_limit(self):
        limited_entry = _download_entry()
        limited_entry["task"]["media_request_cap"] = 12
        limited = self._download_dialog(limited_entry)

        self.assertFalse(limited._request_cap_caption.isHidden())
        self.assertFalse(limited._request_cap_value.isHidden())
        self.assertEqual(limited._request_cap_caption.text(), "下载限制:")
        self.assertEqual(limited._request_cap_value.text(), "12")

        unlimited = self._download_dialog(_download_entry())
        self.assertTrue(unlimited._request_cap_caption.isHidden())
        self.assertTrue(unlimited._request_cap_value.isHidden())

    def test_paged_patrol_details_show_detection_pages(self):
        dialog = self._patrol_dialog()

        self.assertFalse(dialog._depth_caption.isHidden())
        self.assertFalse(dialog._depth_value.isHidden())
        self.assertEqual(dialog._depth_caption.text(), "检测页数:")
        self.assertEqual(dialog._depth_value.text(), "5")

    def test_full_patrol_media_details_omit_the_page_row(self):
        for media_type in ("stories", "highlights"):
            with self.subTest(media_type=media_type):
                entry = _patrol_entry()
                entry["task"]["download_media_type"] = media_type
                entry["task"].pop("page_limit")
                dialog = self._patrol_dialog(entry)

                self.assertTrue(dialog._depth_caption.isHidden())
                self.assertTrue(dialog._depth_value.isHidden())
                self.assertEqual(dialog._depth_caption.text(), "")
                self.assertEqual(dialog._depth_value.text(), "")

    def test_patrol_title_and_summary_omit_downloaded_info(self):
        dialog = self._patrol_dialog()

        self.assertEqual(dialog.windowTitle(), "任务详情")
        self.assertFalse(hasattr(dialog, "_downloaded_caption"))
        self.assertFalse(hasattr(dialog, "_downloaded_value"))
        summary_text = " ".join(
            label.text() for label in dialog._summary_frame.findChildren(QLabel)
        )
        self.assertNotIn("已下载", summary_text)
        self.assertNotIn("Downloaded", summary_text)

    def test_target_row_uses_two_lines_and_hides_an_empty_avatar_slot(self):
        entry = _patrol_entry(
            ("waiting",),
            targets=("alice",),
            checked_count=5,
            updates_found_count=2,
            total_downloaded_count=2,
        )
        dialog = self._patrol_dialog(entry)
        item = dialog._targets_list.item(0)
        row = dialog._targets_list.itemWidget(item)

        self.assertIsNotNone(row)
        self.assertEqual(row._avatar_label.objectName(), "patrolTargetAvatar")
        self.assertEqual(row._username_label.objectName(), "patrolTargetUsername")
        self.assertEqual(row._status_label.objectName(), "patrolTargetStatus")
        self.assertEqual(row._metrics_label.objectName(), "patrolTargetMetrics")
        self.assertEqual(row._username_label.text(), "alice")
        self.assertNotIn("@", row._username_label.text())
        self.assertEqual(row._status_label.text(), "等待检查")
        self.assertEqual(row._metrics_label.text(), "检查: 5    下载: 2")
        self.assertTrue(row._avatar_label.isHidden())
        self.assertEqual(row._top_line_layout.spacing(), 0)

        avatar_size = row._avatar_label.minimumSize()
        self.assertGreater(avatar_size.width(), 0)
        self.assertGreater(avatar_size.height(), 0)
        self.assertEqual(
            row._avatar_label.minimumSize(), row._avatar_label.maximumSize()
        )

        self.assertTrue(row._username_label.alignment() & Qt.AlignmentFlag.AlignLeft)
        self.assertTrue(row._status_label.alignment() & Qt.AlignmentFlag.AlignRight)
        self.assertTrue(row._status_label.alignment() & Qt.AlignmentFlag.AlignVCenter)
        self.assertTrue(row._metrics_label.alignment() & Qt.AlignmentFlag.AlignLeft)
        self.assertEqual(item.sizeHint().height(), 60)

        row_text = " ".join(label.text() for label in row.findChildren(QLabel))
        self.assertNotIn("↓", row_text)
        self.assertNotIn("已下载", row_text)

    def test_target_avatar_never_flashes_as_a_top_level_window(self):
        class TopLevelLabelShowWatcher(QObject):
            def __init__(self):
                super().__init__()
                self.widgets = []

            def eventFilter(self, obj, event):
                if (
                    event.type() == QEvent.Type.Show
                    and isinstance(obj, QLabel)
                    and obj.isWindow()
                ):
                    self.widgets.append(obj)
                return False

        window = self._window("zh")
        avatar = QPixmap(28, 28)
        avatar.fill(QColor("#3874f2"))
        window._target_profile_picture_icon = lambda *_args, **_kwargs: QIcon(avatar)
        watcher = TopLevelLabelShowWatcher()
        self.app.installEventFilter(watcher)
        try:
            dialog = ui_dialogs_patrol.AutoPatrolTaskDialog(
                window,
                _patrol_entry(("waiting",), targets=("alice",)),
            )
            self.dialogs.append(dialog)
        finally:
            self.app.removeEventFilter(watcher)

        self.assertEqual(watcher.widgets, [])

    def test_target_list_removes_global_padding_and_uses_subtle_row_highlight(self):
        dialog = self._patrol_dialog()
        margins = dialog._targets_list.contentsMargins()
        viewport_margins = dialog._targets_list.viewportMargins()

        self.assertEqual(
            (margins.left(), margins.top(), margins.right(), margins.bottom()),
            (0, 0, 0, 0),
        )
        self.assertEqual(
            (
                viewport_margins.left(),
                viewport_margins.top(),
                viewport_margins.right(),
                viewport_margins.bottom(),
            ),
            (0, 0, 0, 0),
        )
        self.assertEqual(dialog._targets_list.spacing(), 0)
        stylesheet = dialog._targets_list.styleSheet()
        self.assertIn("padding: 0", stylesheet)
        self.assertIn("::item:hover", stylesheet)
        self.assertIn("border-left: 2px solid", stylesheet)
        self.assertEqual(
            dialog._targets_list.selectionMode(),
            ui_qt.QAbstractItemView.SelectionMode.SingleSelection,
        )
        row = dialog._targets_list.itemWidget(dialog._targets_list.item(0))
        self.assertTrue(
            row.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        )

    def test_target_avatar_uses_the_task_save_path(self):
        entry = _patrol_entry(("waiting",), targets=("alice",))
        entry["task"]["save_path"] = "custom-patrol-root"
        dialog = self._patrol_dialog(entry)

        self.assertEqual(
            dialog._window.avatar_requests,
            [("alice", "custom-patrol-root")],
        )

    def test_live_count_updates_only_the_matching_target_row(self):
        entry = _patrol_entry(("checking", "waiting"), targets=("alice", "bob"))
        dialog = self._patrol_dialog(entry)
        alice_row, bob_row = dialog._target_rows
        avatar_requests = list(dialog._window.avatar_requests)
        account = entry["patrol_state"]["accounts"][0]
        account["total_downloaded_count"] = 8

        dialog.refresh_target("alice", account)

        self.assertIs(dialog._target_rows[0], alice_row)
        self.assertIs(dialog._target_rows[1], bob_row)
        self.assertEqual(alice_row._metrics_label.text(), "检查: 4    下载: 8")
        self.assertEqual(dialog._window.avatar_requests, avatar_requests)

    def test_live_logs_follow_the_tail_but_keep_a_scrolled_up_position(self):
        console_owner = self._window()
        console_owner.console_logs = []
        console = ui_dialogs_console.ConsoleLogDialog(console_owner)
        self.dialogs.append(console)
        for dialog in (console, self._patrol_dialog(), self._download_dialog()):
            with self.subTest(type(dialog).__name__):
                dialog.show()
                self.app.processEvents()
                for index in range(200):
                    dialog.append_log(f"[fetch] line {index}")
                self.app.processEvents()
                scrollbar = dialog._log_edit.verticalScrollBar()
                self.assertGreater(scrollbar.maximum(), 0)
                self.assertEqual(scrollbar.value(), scrollbar.maximum())

                scrollbar.setValue(0)
                dialog.append_log("[download] live line")
                self.app.processEvents()
                self.assertEqual(scrollbar.value(), 0)

    def test_media_preview_belongs_to_the_rightmost_panel(self):
        dialog = self._patrol_dialog()

        self.assertIs(dialog._media_preview.parentWidget(), dialog._media_panel)
        self.assertIs(dialog._targets_list.parentWidget(), dialog._targets_panel)
        self.assertIsNot(dialog._media_panel, dialog._targets_panel)

    def test_media_badge_is_inset_when_a_hidden_cell_gets_content(self):
        cell = ui_preview_cell.MediaCell()
        try:
            cell.set_fixed_size(QSize(220, 220))
            cell.set_placeholder("图片", path="queued-preview.jpg")

            self.assertEqual(cell._type_label.pos(), QPoint(5, 5))
            self.assertTrue(cell._type_label.isVisibleTo(cell))
            self.assertTrue(cell.rect().contains(cell._type_label.geometry()))
        finally:
            cell.close()
            cell.deleteLater()

    def test_media_preview_cells_start_at_the_top_left(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            media_path = Path(temp_dir) / "completed.mp4"
            media_path.touch()
            entry = _patrol_entry(("updated",), targets=("alice",))
            entry["downloaded_files"] = [str(media_path)]
            dialog = self._patrol_dialog(entry)

            dialog.show()
            dialog._media_preview._load_task_preview_images()
            self.app.processEvents()

            alignment = dialog._media_preview._preview_grid.alignment()
            self.assertTrue(alignment & Qt.AlignmentFlag.AlignTop)
            self.assertTrue(alignment & Qt.AlignmentFlag.AlignLeft)
            cell = dialog._preview_cells[0]
            self.assertLessEqual(cell.geometry().top(), 1)
            self.assertLessEqual(cell.geometry().left(), 1)

    def test_task_details_delete_on_close_without_clearing_a_new_dialog(self):
        owner = _TaskDialogOwner()
        self.windows.append(owner)

        owner.show_task_log_dialog(_download_entry())
        first = owner._log_dialog
        first_destroyed = []
        first.destroyed.connect(lambda *_args: first_destroyed.append(True))
        self.assertTrue(first.testAttribute(Qt.WidgetAttribute.WA_DeleteOnClose))

        owner.show_task_log_dialog(_patrol_entry(("waiting",), targets=("alice",)))
        second = owner._log_dialog
        second_destroyed = []
        second.destroyed.connect(lambda *_args: second_destroyed.append(True))
        self.assertTrue(second.testAttribute(Qt.WidgetAttribute.WA_DeleteOnClose))

        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.assertEqual(first_destroyed, [True])
        self.assertIs(owner._log_dialog, second)

        second.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.assertEqual(second_destroyed, [True])
        self.assertIsNone(owner._log_dialog)

    def test_regular_task_preview_is_newest_first_and_live_media_is_prepended(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = []
            for name in ("oldest.mp4", "middle.mp4", "newest.mp4"):
                path = Path(temp_dir) / name
                path.touch()
                paths.append(str(path))

            entry = _download_entry()
            entry["downloaded_files"] = paths[:2]
            dialog = self._download_dialog(entry)
            dialog._media_preview._load_task_preview_images()
            self.app.processEvents()

            existing_cells = list(dialog._preview_cells)
            self.assertEqual(
                [cell._current_path for cell in existing_cells],
                [
                    os.path.normcase(os.path.abspath(paths[1])),
                    os.path.normcase(os.path.abspath(paths[0])),
                ],
            )

            entry["downloaded_files"].append(paths[2])
            dialog.add_preview_image(paths[2])
            self.app.processEvents()

            self.assertEqual(
                [cell._current_path for cell in dialog._preview_cells],
                [
                    os.path.normcase(os.path.abspath(paths[2])),
                    os.path.normcase(os.path.abspath(paths[1])),
                    os.path.normcase(os.path.abspath(paths[0])),
                ],
            )
            self.assertIs(dialog._preview_cells[1], existing_cells[0])
            self.assertIs(dialog._preview_cells[2], existing_cells[1])

    def test_patrol_media_preview_loads_all_parent_task_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = []
            for index in range(18):
                path = Path(temp_dir) / f"completed-{index:02d}.mp4"
                path.touch()
                paths.append(str(path))
            entry = _patrol_entry(("updated",), targets=("alice",))
            entry["downloaded_files"] = paths

            dialog = self._patrol_dialog(entry)
            dialog._media_preview._load_task_preview_images()
            self.app.processEvents()

            self.assertEqual(dialog._window.max_preview_images, 15)
            self.assertEqual(len(dialog._preview_cells), 18)

    def test_target_click_filters_preview_and_second_click_restores_all(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir) / "original-patrol-root"
            alice_paths = [
                base_path / "alice" / "highlights" / "Trip" / "alice-1.mp4",
                base_path / "alice" / "highlights" / "Trip" / "alice-2.mp4",
            ]
            bob_path = base_path / "bob" / "highlights" / "Food" / "bob-1.mp4"
            for path in (*alice_paths, bob_path):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()

            entry = _patrol_entry(
                ("updated", "updated"),
                targets=("alice", "bob"),
            )
            entry["task"]["download_media_type"] = "highlights"
            entry["task"].pop("page_limit")
            # Deliberately use a different current save path. Restored patrol
            # media must still be attributable from its canonical directory.
            entry["task"]["save_path"] = str(Path(temp_dir) / "new-root")
            entry["downloaded_files"] = [
                str(alice_paths[0]),
                str(bob_path),
                str(alice_paths[1]),
            ]

            dialog = self._patrol_dialog(entry)
            preview_cells_alias = dialog._preview_cells
            alice_item = dialog._targets_list.item(0)

            # Select before the panel's queued initial load runs. That queued
            # load must not restore the unfiltered dataset afterward.
            dialog._targets_list.itemClicked.emit(alice_item)
            self.app.processEvents()

            self.assertTrue(alice_item.isSelected())
            self.assertEqual(dialog._selected_target_username, "alice")
            self.assertIs(dialog._preview_cells, preview_cells_alias)
            self.assertEqual(
                [cell._current_path for cell in dialog._preview_cells],
                [
                    os.path.normcase(os.path.abspath(path))
                    for path in reversed(alice_paths)
                ],
            )
            for cell in dialog._preview_cells:
                self.assertEqual(cell._type_label.pos(), QPoint(5, 5))

            dialog._targets_list.itemClicked.emit(alice_item)
            self.app.processEvents()

            self.assertFalse(alice_item.isSelected())
            self.assertIsNone(dialog._selected_target_username)
            self.assertEqual(
                [cell._current_path for cell in dialog._preview_cells],
                [
                    os.path.normcase(os.path.abspath(alice_paths[1])),
                    os.path.normcase(os.path.abspath(bob_path)),
                    os.path.normcase(os.path.abspath(alice_paths[0])),
                ],
            )
            for cell in dialog._preview_cells:
                self.assertEqual(cell._type_label.pos(), QPoint(5, 5))

    def test_target_click_filters_logs_by_saved_owner(self):
        entry = _patrol_entry(
            ("updated", "updated"),
            targets=("alice", "bob"),
        )
        original_logs = [
            "global text mentioning @alice is not guessed",
            "[fetch] alice runtime",
            "[gui] global runtime",
            "[download] bob runtime",
        ]
        entry["logs"] = list(original_logs)
        entry["log_targets"] = [None, "alice", None, "bob"]
        dialog = self._patrol_dialog(entry)

        self.assertEqual(dialog._log_edit.toPlainText(), "\n".join(original_logs))

        dialog._targets_list.itemClicked.emit(dialog._targets_list.item(0))
        self.assertEqual(dialog._log_edit.toPlainText(), "[fetch] alice runtime")

        dialog._targets_list.itemClicked.emit(dialog._targets_list.item(1))
        self.assertEqual(dialog._log_edit.toPlainText(), "[download] bob runtime")

        dialog._targets_list.blank_clicked.emit()
        self.assertEqual(dialog._log_edit.toPlainText(), "\n".join(original_logs))
        self.assertEqual(entry["logs"], original_logs)

    def test_live_patrol_logs_obey_selected_target_filter(self):
        entry = _patrol_entry(
            ("checking", "waiting"),
            targets=("alice", "bob"),
        )
        entry["logs"] = []
        entry["log_targets"] = []
        dialog = self._patrol_dialog(entry)
        dialog._targets_list.itemClicked.emit(dialog._targets_list.item(0))

        for line, owner in (
            ("bob hidden while Alice is selected", "bob"),
            ("global hidden while a target is selected", None),
            ("alice visible immediately", "alice"),
        ):
            entry["logs"].append(line)
            entry["log_targets"].append(owner)
            dialog.append_log(line, target_username=owner)

        self.assertEqual(dialog._log_edit.toPlainText(), "alice visible immediately")

        dialog._targets_list.itemClicked.emit(dialog._targets_list.item(1))
        self.assertEqual(
            dialog._log_edit.toPlainText(),
            "bob hidden while Alice is selected",
        )

        dialog._targets_list.blank_clicked.emit()
        self.assertEqual(
            dialog._log_edit.toPlainText(),
            "\n".join(entry["logs"]),
        )

    def test_target_filter_survives_status_refresh_and_blank_clear(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir)
            alice_path = base_path / "alice" / "posts" / "alice.mp4"
            bob_path = base_path / "bob" / "posts" / "bob.mp4"
            for path in (alice_path, bob_path):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            entry = _patrol_entry(
                ("checking", "waiting"),
                targets=("alice", "bob"),
            )
            entry["task"]["save_path"] = str(base_path)
            entry["downloaded_files"] = [str(alice_path), str(bob_path)]
            entry["logs"] = ["alice log", "bob log"]
            entry["log_targets"] = ["alice", "bob"]
            dialog = self._patrol_dialog(entry)
            dialog._media_preview._load_task_preview_images()

            dialog._targets_list.itemClicked.emit(dialog._targets_list.item(1))
            entry["patrol_state"]["accounts"][1]["state"] = "checking"
            dialog.refresh_from_entry()
            self.app.processEvents()

            selected_item = dialog._targets_list.item(1)
            self.assertTrue(selected_item.isSelected())
            self.assertEqual(dialog._selected_target_username, "bob")
            self.assertEqual(len(dialog._preview_cells), 1)
            self.assertEqual(
                dialog._preview_cells[0]._current_path,
                os.path.normcase(os.path.abspath(bob_path)),
            )
            self.assertEqual(dialog._log_edit.toPlainText(), "bob log")

            dialog._targets_list.blank_clicked.emit()
            self.app.processEvents()

            self.assertIsNone(dialog._selected_target_username)
            self.assertFalse(dialog._targets_list.selectedItems())
            self.assertEqual(len(dialog._preview_cells), 2)
            self.assertEqual(dialog._log_edit.toPlainText(), "alice log\nbob log")

    def test_live_patrol_media_obeys_selected_target_filter(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir)
            first_alice = base_path / "alice" / "posts" / "alice-1.mp4"
            first_bob = base_path / "bob" / "posts" / "bob-1.mp4"
            for path in (first_alice, first_bob):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            entry = _patrol_entry(
                ("updated", "updated"),
                targets=("alice", "bob"),
            )
            entry["task"]["save_path"] = str(base_path)
            entry["downloaded_files"] = [str(first_alice), str(first_bob)]
            dialog = self._patrol_dialog(entry)
            dialog._targets_list.itemClicked.emit(dialog._targets_list.item(0))

            new_bob = base_path / "bob" / "posts" / "bob-2.mp4"
            new_alice = base_path / "alice" / "posts" / "alice-2.mp4"
            for path in (new_bob, new_alice):
                path.touch()
                entry["downloaded_files"].append(str(path))

            dialog.add_preview_image(str(new_bob), target_username="bob")
            dialog.add_preview_image(str(new_alice), target_username="alice")
            self.app.processEvents()

            self.assertEqual(
                [cell._current_path for cell in dialog._preview_cells],
                [
                    os.path.normcase(os.path.abspath(new_alice)),
                    os.path.normcase(os.path.abspath(first_alice)),
                ],
            )

            dialog._targets_list.itemClicked.emit(dialog._targets_list.item(0))
            self.app.processEvents()
            self.assertEqual(
                [cell._current_path for cell in dialog._preview_cells],
                [
                    os.path.normcase(os.path.abspath(new_alice)),
                    os.path.normcase(os.path.abspath(new_bob)),
                    os.path.normcase(os.path.abspath(first_bob)),
                    os.path.normcase(os.path.abspath(first_alice)),
                ],
            )

    def test_patrol_media_preview_has_no_top_header_like_regular_task(self):
        dialog = self._patrol_dialog()
        regular_dialog = self._download_dialog()

        self.assertFalse(hasattr(dialog, "_media_header"))
        media_layout = dialog._media_panel.layout()
        self.assertEqual(media_layout.count(), 1)
        self.assertIs(media_layout.itemAt(0).widget(), dialog._media_preview)

        # The regular task preview is likewise inserted directly into its
        # right-hand column, without a label consuming the first row.
        regular_content = regular_dialog.layout().itemAt(0).layout()
        self.assertIs(
            self._non_divider_widgets(regular_content)[-1],
            regular_dialog._media_preview,
        )

    def test_patrol_media_preview_accepts_a_live_completed_file(self):
        dialog = self._patrol_dialog()

        with tempfile.TemporaryDirectory() as temp_dir:
            media_path = Path(temp_dir) / "completed.mp4"
            media_path.touch()

            self.assertEqual(dialog._preview_cells, [])
            dialog.add_preview_image(str(media_path))
            self.app.processEvents()

            self.assertEqual(len(dialog._preview_cells), 1)
            self.assertEqual(
                dialog._preview_cells[0]._current_path,
                os.path.normcase(os.path.abspath(media_path)),
            )
            dialog._window._thumb_extractor.request.assert_called_once()


if __name__ == "__main__":
    unittest.main()
