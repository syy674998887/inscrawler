"""Headless coverage for task-queue reordering and serial queue control."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

import inscrawler.paths as app_paths

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QModelIndex, QPoint, QPointF, QSize, Qt
from PySide6.QtGui import QMouseEvent, QPointingDevice
from PySide6.QtWidgets import QApplication, QListWidgetItem, QWidget

import inscrawler.downloads.checkpoints as downloads_checkpoints
import inscrawler.downloads.control as downloads_control
import inscrawler.downloads.models as downloads_models
import inscrawler.downloads.service as downloads_service
import inscrawler.notifications.service as notifications_service
import inscrawler.tasks.schema as tasks_schema
import inscrawler.ui.console as ui_console
import inscrawler.ui.controllers.execution as ui_controllers_execution
import inscrawler.ui.controllers.queue_controls as ui_controllers_queue_controls
import inscrawler.ui.controllers.queue_view as ui_controllers_queue_view
import inscrawler.ui.controllers.status as ui_controllers_status
import inscrawler.ui.controllers.task_store as ui_controllers_task_store
import inscrawler.ui.main_window as ui_main_window
import inscrawler.ui.preview.events as ui_preview_events
import inscrawler.ui.qt as ui_qt
import inscrawler.ui.widgets.queue as ui_widgets_queue
import inscrawler.ui.workers as ui_workers
from tests.queue_fixtures import make_queue_target, task_entry


class TaskQueueQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.queue = ui_widgets_queue.TaskQueueListWidget()

    def tearDown(self):
        self.queue.close()
        self.queue.deleteLater()
        self.app.processEvents()

    def add_row(self, task_id):
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, task_id)
        item.setSizeHint(QSize(120, 36))
        widget = QWidget()
        self.queue.addItem(item)
        self.queue.setItemWidget(item, widget)
        return item, widget

    def test_batch_add_defers_queue_wide_refresh_and_scroll_work(self):
        task = task_entry("batch-row", "completed")["task"]
        target = SimpleNamespace(
            _loading_saved_tasks=True,
            queue_items=[],
            queue_list=self.queue,
            current_queue_index=None,
            _queue_item_style=Mock(return_value=""),
            _queue_label_style=Mock(return_value=""),
            on_queue_item_delete=Mock(),
            _set_queue_status_icon=Mock(),
            show_task_log_dialog=Mock(),
            _next_queue_order=Mock(return_value=0),
            _refresh_queue_avatar=Mock(),
            _refresh_queue_item_styles=Mock(),
            set_queue_item_status=Mock(),
            update_clear_queue_button_state=Mock(),
            _restore_queue_scroll_position=Mock(),
        )

        ui_controllers_queue_view.QueueViewController.add_task_to_queue(
            target,
            task,
            "completed",
            completed_at="2026-01-01 00:00:00",
            save=False,
        )

        self.assertEqual(len(target.queue_items), 1)
        target.set_queue_item_status.assert_called_once_with(
            target.queue_items[0],
            "completed",
            save=False,
        )
        target._refresh_queue_avatar.assert_not_called()
        target._refresh_queue_item_styles.assert_not_called()
        target.update_clear_queue_button_state.assert_not_called()
        target._restore_queue_scroll_position.assert_not_called()

    def test_row_delete_saves_unfinished_rows_and_keeps_run_counts(self):
        history = task_entry("H", "completed")
        patrol = task_entry("P", kind="auto_patrol")
        counted = task_entry("A")
        kept_history = task_entry("K", "completed")
        later = task_entry("B")
        target = SimpleNamespace(
            queue_items=[history, patrol, counted, kept_history, later],
            queue_list=Mock(),
            # This run counted A and B only (see _count_batch_tasks), and one
            # earlier task of the run has completed.
            queue_running=True,
            queue_started=2,
            queue_completed=1,
            current_queue_index=None,
            _refresh_queue_dividers=Mock(),
            _refresh_queue_item_styles=Mock(),
            delete_task_file=Mock(),
            _close_log_dialog_for_entry=Mock(),
            update_clear_queue_button_state=Mock(),
            save_task=Mock(),
            save_tasks=Mock(),
        )
        controller = ui_controllers_queue_view.QueueViewController
        target._adjust_queue_counts_after_removal = MethodType(
            controller._adjust_queue_counts_after_removal, target
        )

        with patch.object(ui_console, "print"):
            controller._remove_queue_entry_at(target, 0)
            controller._remove_queue_entry_at(target, 0)
            self.assertEqual((target.queue_started, target.queue_completed), (2, 1))
            controller._remove_queue_entry_at(target, 0)

        self.assertEqual(target.queue_items, [kept_history, later])
        self.assertEqual((target.queue_started, target.queue_completed), (1, 1))
        self.assertEqual(target._refresh_queue_dividers.call_count, 3)
        target._refresh_queue_item_styles.assert_not_called()
        target.save_tasks.assert_not_called()
        saved = [
            entry["task"]["task_id"] for (entry,), _ in target.save_task.call_args_list
        ]
        # The remaining history row is never rewritten.
        self.assertNotIn("K", saved)
        self.assertEqual(sorted(saved), ["A", "A", "B", "B", "B", "P"])

    def load_saved_rows(self, directory):
        text_value = SimpleNamespace(text=lambda: "")
        loaded = []
        batch_states = []
        queue_items = []

        def add_task_to_queue(task, status, **kwargs):
            batch_states.append(loader._loading_saved_tasks)
            loaded.append((task["task_id"], status, kwargs))
            entry = {
                "task": task,
                "status": status,
                "logs": list(kwargs.get("logs") or []),
                "downloaded_files": list(kwargs.get("downloaded_files") or []),
                "downloaded_file_details": list(
                    kwargs.get("downloaded_file_details") or []
                ),
            }
            if kwargs.get("queue_order") is not None:
                entry["queue_order"] = kwargs["queue_order"]
            if kwargs.get("completed_at") is not None:
                entry["completed_at"] = kwargs["completed_at"]
            if kwargs.get("patrol_state") is not None:
                entry["patrol_state"] = kwargs["patrol_state"]
            queue_items.append(entry)

        loader = SimpleNamespace(
            _loading_saved_tasks=False,
            username=text_value,
            webhook=text_value,
            telegram_bot_token=text_value,
            telegram_chat_id=text_value,
            save_path=text_value,
            default_save_path=directory,
            queue_items=queue_items,
            _normalize_task_progress=lambda task: None,
            add_task_to_queue=add_task_to_queue,
            _finalize_loaded_task_batch=Mock(),
            _scroll_queue_to_bottom=lambda: None,
        )
        loader._task_file_path = MethodType(
            ui_controllers_task_store.TaskStoreController._task_file_path, loader
        )
        loader._next_queue_order = MethodType(
            ui_controllers_task_store.TaskStoreController._next_queue_order, loader
        )
        loader.save_task = MethodType(
            ui_controllers_task_store.TaskStoreController.save_task, loader
        )
        with patch.object(app_paths, "TASKS_DIR", directory):
            ui_controllers_task_store.TaskStoreController.load_saved_tasks(loader)
        self.assertFalse(loader._loading_saved_tasks)
        if loaded:
            self.assertTrue(all(batch_states))
            loader._finalize_loaded_task_batch.assert_called_once_with()
        else:
            self.assertEqual(batch_states, [])
            loader._finalize_loaded_task_batch.assert_not_called()
        return loaded

    def test_model_move_preserves_item_and_item_widget(self):
        rows = [self.add_row(task_id) for task_id in ("A", "B", "C")]

        moved = self.queue.model().moveRows(QModelIndex(), 1, 1, QModelIndex(), 0)

        self.assertTrue(moved)
        self.assertEqual(
            [self.queue.item(i).data(Qt.ItemDataRole.UserRole) for i in range(3)],
            ["B", "A", "C"],
        )
        for item, widget in rows:
            self.assertIs(self.queue.itemWidget(item), widget)

    def test_terminal_row_moves_to_history_tail_and_preserves_identity(self):
        rows = [self.add_row(task_id) for task_id in ("H", "B", "A")]
        entries = [
            task_entry("H", "completed"),
            task_entry("B"),
            task_entry("A", "completed"),
        ]
        entries[1]["queue_order"] = 0
        for entry, (item, widget) in zip(entries, rows):
            entry.update({"item": item, "widget": widget})

        active = entries[2]
        target = SimpleNamespace(
            queue_items=entries,
            queue_list=self.queue,
            current_queue_index=2,
            _refresh_queue_dividers=Mock(),
        )
        target._current_queue_entry = MethodType(
            ui_controllers_queue_view.QueueViewController._current_queue_entry, target
        )
        target._queue_index_for_entry = MethodType(
            ui_controllers_queue_view.QueueViewController._queue_index_for_entry, target
        )
        target._sync_queue_order_from_view = MethodType(
            ui_controllers_queue_view.QueueViewController._sync_queue_order_from_view,
            target,
        )

        moved = (
            ui_controllers_queue_view.QueueViewController._move_entry_to_history_tail(
                target, active
            )
        )

        self.assertTrue(moved)
        self.assertEqual(
            [entry["task"]["task_id"] for entry in target.queue_items],
            ["H", "A", "B"],
        )
        self.assertIs(target.queue_items[1], active)
        self.assertEqual(target.current_queue_index, 1)
        target._refresh_queue_dividers.assert_called_once_with()

    def test_row_gesture_separates_drag_from_detail_click(self):
        row = ui_widgets_queue.QueueItemWidget()
        row.set_drag_enabled(True)
        started = []
        moved = []
        dragged = []
        clicked = []
        row.drag_started.connect(lambda: started.append(True))
        row.drag_moved.connect(moved.append)
        row.drag_finished.connect(dragged.append)
        row.clicked.connect(lambda: clicked.append(True))
        self.assertEqual(row.cursor().shape(), Qt.CursorShape.ArrowCursor)
        device = QPointingDevice.primaryPointingDevice()
        press_pos = QPointF(2, 2)
        row.mousePressEvent(
            QMouseEvent(
                QEvent.Type.MouseButtonPress,
                press_pos,
                press_pos,
                press_pos,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
                device,
            )
        )
        move_pos = QPointF(QApplication.startDragDistance() + 5, 2)
        row.mouseMoveEvent(
            QMouseEvent(
                QEvent.Type.MouseMove,
                move_pos,
                move_pos,
                move_pos,
                Qt.MouseButton.NoButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
                device,
            )
        )
        self.assertEqual(row.cursor().shape(), Qt.CursorShape.ClosedHandCursor)
        release_global = QPointF(120, 150)
        row.mouseReleaseEvent(
            QMouseEvent(
                QEvent.Type.MouseButtonRelease,
                move_pos,
                move_pos,
                release_global,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
                device,
            )
        )

        self.assertEqual(started, [True])
        self.assertEqual(moved, [move_pos.toPoint()])
        self.assertEqual(dragged, [QPoint(120, 150)])
        self.assertEqual(clicked, [])
        self.assertEqual(row.cursor().shape(), Qt.CursorShape.ArrowCursor)

        # Once the threshold is crossed, a model move can reposition the row
        # under the pointer and reset local coordinates.  Drag updates must
        # continue based on the gesture state, not a fresh local-distance test.
        row.mousePressEvent(
            QMouseEvent(
                QEvent.Type.MouseButtonPress,
                press_pos,
                press_pos,
                press_pos,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
                device,
            )
        )
        row.mouseMoveEvent(
            QMouseEvent(
                QEvent.Type.MouseMove,
                move_pos,
                move_pos,
                move_pos,
                Qt.MouseButton.NoButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
                device,
            )
        )
        second_global = QPointF(220, 250)
        row.mouseMoveEvent(
            QMouseEvent(
                QEvent.Type.MouseMove,
                press_pos,
                press_pos,
                second_global,
                Qt.MouseButton.NoButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
                device,
            )
        )
        row.mouseReleaseEvent(
            QMouseEvent(
                QEvent.Type.MouseButtonRelease,
                press_pos,
                press_pos,
                second_global,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
                device,
            )
        )
        self.assertEqual(moved[-1], QPoint(220, 250))

        row.mousePressEvent(
            QMouseEvent(
                QEvent.Type.MouseButtonPress,
                press_pos,
                press_pos,
                press_pos,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
                device,
            )
        )
        row.mouseReleaseEvent(
            QMouseEvent(
                QEvent.Type.MouseButtonRelease,
                press_pos,
                press_pos,
                press_pos,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
                device,
            )
        )

        self.assertEqual(clicked, [True])
        self.assertEqual(started, [True, True])
        self.assertEqual(dragged, [QPoint(120, 150), QPoint(220, 250)])
        row.deleteLater()

    def test_locked_row_drag_attempt_never_opens_details(self):
        row = ui_widgets_queue.QueueItemWidget()
        row.set_drag_enabled(False)
        clicked = []
        dragged = []
        row.clicked.connect(lambda: clicked.append(True))
        row.drag_finished.connect(dragged.append)
        device = QPointingDevice.primaryPointingDevice()
        press_pos = QPointF(2, 2)
        move_pos = QPointF(QApplication.startDragDistance() + 5, 2)
        row.mousePressEvent(
            QMouseEvent(
                QEvent.Type.MouseButtonPress,
                press_pos,
                press_pos,
                press_pos,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
                device,
            )
        )
        row.mouseMoveEvent(
            QMouseEvent(
                QEvent.Type.MouseMove,
                move_pos,
                move_pos,
                move_pos,
                Qt.MouseButton.NoButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
                device,
            )
        )
        self.assertEqual(row.cursor().shape(), Qt.CursorShape.ArrowCursor)
        row.mouseReleaseEvent(
            QMouseEvent(
                QEvent.Type.MouseButtonRelease,
                move_pos,
                move_pos,
                move_pos,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
                device,
            )
        )

        self.assertEqual(clicked, [])
        self.assertEqual(dragged, [])
        row.deleteLater()

    def test_pointer_drag_previews_moves_and_commits_once_on_release(self):
        rows = [self.add_row(task_id) for task_id in ("A", "B", "C")]
        self.queue.resize(240, 160)
        self.queue.show()
        self.app.processEvents()
        previews = []
        commits = []
        self.queue.order_preview_changed.connect(lambda: previews.append(True))
        self.queue.order_changed.connect(lambda: commits.append(True))
        top = self.queue.viewport().mapToGlobal(
            self.queue.visualItemRect(rows[0][0]).center()
        )

        self.assertTrue(self.queue.begin_pointer_drag(rows[2][0]))
        self.assertTrue(self.queue._pointer_drag_timer.isActive())
        moved = self.queue.update_pointer_drag(rows[2][0], top)

        self.assertTrue(moved)
        self.assertEqual(
            [self.queue.item(i).data(Qt.ItemDataRole.UserRole) for i in range(3)],
            ["C", "A", "B"],
        )
        self.assertEqual(previews, [True])
        self.assertEqual(commits, [])
        self.assertTrue(self.queue.finish_pointer_drag(rows[2][0], top))
        self.assertFalse(self.queue._pointer_drag_timer.isActive())
        self.assertEqual(commits, [True])

        bottom = self.queue.viewport().mapToGlobal(
            self.queue.visualItemRect(rows[1][0]).center()
        )
        self.assertTrue(self.queue.begin_pointer_drag(rows[2][0]))
        moved = self.queue.update_pointer_drag(rows[2][0], bottom)
        self.assertTrue(moved)
        self.assertEqual(
            [self.queue.item(i).data(Qt.ItemDataRole.UserRole) for i in range(3)],
            ["A", "B", "C"],
        )
        self.assertEqual(previews, [True, True])
        self.assertEqual(commits, [True])
        self.assertTrue(self.queue.finish_pointer_drag(rows[2][0], bottom))
        self.assertEqual(commits, [True, True])

    def test_pointer_target_uses_y_when_mouse_is_outside_horizontally(self):
        rows = [self.add_row(task_id) for task_id in ("A", "B", "C")]
        self.queue.resize(240, 160)
        self.queue.show()
        self.app.processEvents()
        target = self.queue.viewport().mapToGlobal(
            self.queue.visualItemRect(rows[0][0]).center()
        )
        target.setX(self.queue.viewport().mapToGlobal(QPoint(0, 0)).x() - 40)

        self.assertTrue(self.queue.begin_pointer_drag(rows[2][0]))
        self.assertTrue(self.queue.update_pointer_drag(rows[2][0], target))
        self.assertEqual(
            [self.queue.item(i).data(Qt.ItemDataRole.UserRole) for i in range(3)],
            ["C", "A", "B"],
        )
        self.assertTrue(self.queue.finish_pointer_drag(rows[2][0], target))

    def test_ineligible_state_change_during_drag_still_stops_timer(self):
        item = QListWidgetItem()
        row = ui_widgets_queue.QueueItemWidget()
        self.queue.addItem(item)
        self.queue.setItemWidget(item, row)
        row.set_drag_enabled(True)
        row.drag_started.connect(lambda: self.queue.begin_pointer_drag(item))
        row.drag_finished.connect(
            lambda global_pos: self.queue.finish_pointer_drag(item, global_pos)
        )
        clicked = []
        row.clicked.connect(lambda: clicked.append(True))
        device = QPointingDevice.primaryPointingDevice()
        press_pos = QPointF(2, 2)
        move_pos = QPointF(QApplication.startDragDistance() + 5, 2)
        row.mousePressEvent(
            QMouseEvent(
                QEvent.Type.MouseButtonPress,
                press_pos,
                press_pos,
                press_pos,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
                device,
            )
        )
        row.mouseMoveEvent(
            QMouseEvent(
                QEvent.Type.MouseMove,
                move_pos,
                move_pos,
                move_pos,
                Qt.MouseButton.NoButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
                device,
            )
        )
        self.assertTrue(self.queue._pointer_drag_timer.isActive())
        self.assertEqual(row.cursor().shape(), Qt.CursorShape.ClosedHandCursor)
        row.set_drag_enabled(False)
        self.assertEqual(row.cursor().shape(), Qt.CursorShape.ClosedHandCursor)
        row.mouseReleaseEvent(
            QMouseEvent(
                QEvent.Type.MouseButtonRelease,
                move_pos,
                move_pos,
                move_pos,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
                device,
            )
        )

        self.assertFalse(self.queue._pointer_drag_timer.isActive())
        self.assertEqual(clicked, [])
        self.assertEqual(row.cursor().shape(), Qt.CursorShape.ArrowCursor)

    def test_pointer_timer_keeps_scrolling_without_new_mouse_moves(self):
        for index in range(20):
            self.add_row(str(index))
        self.queue.resize(240, 100)
        self.queue.show()
        self.app.processEvents()
        scrollbar = self.queue.verticalScrollBar()
        scroll_step = max(12, scrollbar.singleStep() * 2)
        self.assertGreater(scrollbar.maximum(), scroll_step)
        top_edge = self.queue.viewport().mapToGlobal(QPoint(20, 1))
        item = self.queue.item(10)
        self.assertTrue(self.queue.begin_pointer_drag(item))
        scrollbar.setValue(scrollbar.maximum())
        before = scrollbar.value()
        self.queue._pointer_drag_global_position = QPoint(top_edge)

        self.queue._continue_pointer_drag()
        after_first_tick = scrollbar.value()
        self.queue._continue_pointer_drag()
        after_second_tick = scrollbar.value()

        self.assertLess(after_first_tick, before)
        self.assertLess(after_second_tick, after_first_tick)
        self.queue.finish_pointer_drag(item, top_edge)
        self.assertFalse(self.queue._pointer_drag_timer.isActive())

    def test_gui_preview_preserves_identity_and_commit_saves_only_unfinished(self):
        rows = [self.add_row(task_id) for task_id in ("H", "A", "B", "C")]
        entries = []
        for (item, widget), task_id in zip(rows, ("H", "A", "B", "C")):
            status = "completed" if task_id == "H" else "pending"
            entry = task_entry(task_id, status)
            entry.update({"item": item, "widget": widget})
            entries.append(entry)
        for queue_order, entry in enumerate(entries[1:]):
            entry["queue_order"] = queue_order
        active = entries[1]
        self.queue.model().moveRows(QModelIndex(), 2, 1, QModelIndex(), 1)

        target = SimpleNamespace(
            queue_items=entries,
            queue_list=self.queue,
            current_queue_index=1,
            save_task=Mock(),
            _refresh_queue_dividers=Mock(),
        )
        target._current_queue_entry = MethodType(
            ui_controllers_queue_view.QueueViewController._current_queue_entry, target
        )
        target._queue_index_for_entry = MethodType(
            ui_controllers_queue_view.QueueViewController._queue_index_for_entry, target
        )
        target._sync_queue_order_from_view = MethodType(
            ui_controllers_queue_view.QueueViewController._sync_queue_order_from_view,
            target,
        )

        ui_controllers_queue_view.QueueViewController._on_queue_order_preview_changed(
            target
        )

        self.assertEqual(
            [entry["task"]["task_id"] for entry in entries],
            ["H", "B", "A", "C"],
        )
        self.assertEqual(target.current_queue_index, 2)
        self.assertIs(target.queue_items[target.current_queue_index], active)
        target.save_task.assert_not_called()
        target._refresh_queue_dividers.assert_not_called()

        ui_controllers_queue_view.QueueViewController._on_queue_order_changed(target)

        self.assertEqual(
            [
                call.args[0]["task"]["task_id"]
                for call in target.save_task.call_args_list
            ],
            ["B", "A", "C"],
        )
        self.assertEqual(
            [entry["queue_order"] for entry in target.queue_items[1:]],
            [0, 1, 2],
        )
        target._refresh_queue_dividers.assert_called_once_with()

    def test_invalid_view_model_state_is_never_persisted_on_commit(self):
        target = SimpleNamespace(
            queue_items=[task_entry("A")],
            queue_list=self.queue,
            save_task=Mock(),
            _refresh_queue_dividers=Mock(),
        )
        target._sync_queue_order_from_view = MethodType(
            ui_controllers_queue_view.QueueViewController._sync_queue_order_from_view,
            target,
        )

        ui_controllers_queue_view.QueueViewController._on_queue_order_changed(target)

        target.save_task.assert_not_called()
        target._refresh_queue_dividers.assert_not_called()

    def test_history_and_execution_order_round_trip_independently(self):
        pending_b = task_entry("bbbbbbbbbbbb", created_at="2025-02-01 00:00:00")
        pending_b["queue_order"] = 0
        pending_a = task_entry("aaaaaaaaaaaa", created_at="2025-01-01 00:00:00")
        pending_a["queue_order"] = 1
        older_completed = task_entry(
            "cccccccccccc", "completed", created_at="2024-01-01 00:00:00"
        )
        recent_completed = task_entry(
            "dddddddddddd", "completed", created_at="2023-01-01 00:00:00"
        )
        recent_completed["completed_at"] = "2024-02-01 00:00:00"
        entries = [pending_b, recent_completed, pending_a, older_completed]
        target = SimpleNamespace(queue_items=entries)
        target._task_file_path = MethodType(
            ui_controllers_task_store.TaskStoreController._task_file_path, target
        )
        target._next_queue_order = MethodType(
            ui_controllers_task_store.TaskStoreController._next_queue_order, target
        )
        target.save_task = MethodType(
            ui_controllers_task_store.TaskStoreController.save_task, target
        )
        target.save_tasks = MethodType(
            ui_controllers_task_store.TaskStoreController.save_tasks, target
        )

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch.object(app_paths, "TASKS_DIR", temp_dir),
        ):
            target.save_tasks()
            saved = {
                path.stem: json.loads(path.read_text(encoding="utf-8"))
                for path in Path(temp_dir).glob("*.json")
            }
            loaded = self.load_saved_rows(temp_dir)

        self.assertEqual(saved["bbbbbbbbbbbb"]["queue_order"], 0)
        self.assertEqual(saved["aaaaaaaaaaaa"]["queue_order"], 1)
        self.assertTrue(all(record["schema_version"] == 7 for record in saved.values()))
        self.assertTrue(
            all(record["task"]["task_kind"] == "download" for record in saved.values())
        )
        self.assertNotIn("queue_order", saved["cccccccccccc"])
        self.assertNotIn("queue_order", saved["dddddddddddd"])
        self.assertEqual(saved["cccccccccccc"]["completed_at"], "2024-01-01 00:00:00")
        self.assertEqual(saved["dddddddddddd"]["completed_at"], "2024-02-01 00:00:00")
        self.assertEqual(
            [task_id for task_id, _, _ in loaded],
            ["cccccccccccc", "dddddddddddd", "bbbbbbbbbbbb", "aaaaaaaaaaaa"],
        )
        self.assertEqual(
            [
                kwargs["queue_order"]
                for _, status, kwargs in loaded
                if status == "pending"
            ],
            [0, 1],
        )

    def test_load_rejects_schema_v6_without_rewriting_it(self):
        record = task_entry("aaaaaaaaaaaa", "completed")
        record["schema_version"] = 6

        with tempfile.TemporaryDirectory() as temp_dir:
            task_path = Path(temp_dir, "aaaaaaaaaaaa.json")
            original_text = json.dumps(record)
            task_path.write_text(original_text, encoding="utf-8")
            loaded = self.load_saved_rows(temp_dir)
            unchanged_text = task_path.read_text(encoding="utf-8")

        self.assertEqual(loaded, [])
        self.assertEqual(unchanged_text, original_text)

    def test_interrupted_downloads_restore_without_losing_stopped_state(self):
        records = [
            task_entry("111111111111", "running"),
            task_entry("222222222222", "deferred"),
            task_entry("333333333333", "stopped"),
        ]
        for queue_order, record in enumerate(records):
            record["queue_order"] = queue_order

        with tempfile.TemporaryDirectory() as temp_dir:
            for record in records:
                task_id = record["task"]["task_id"]
                Path(temp_dir, f"{task_id}.json").write_text(
                    json.dumps(record), encoding="utf-8"
                )
            loaded = self.load_saved_rows(temp_dir)

        self.assertEqual(
            [(task_id, status) for task_id, status, _kwargs in loaded],
            [
                ("111111111111", "pending"),
                ("222222222222", "pending"),
                ("333333333333", "stopped"),
            ],
        )
        self.assertEqual(
            [kwargs["queue_order"] for _task_id, _status, kwargs in loaded],
            [0, 1, 2],
        )

    def test_duplicate_queue_order_recovers_every_task_deterministically(self):
        records = [
            task_entry("aaaaaaaaaaaa", created_at="2025-03-01 00:00:00"),
            task_entry("ffffffffffff", created_at="2025-01-01 00:00:00"),
            task_entry("bbbbbbbbbbbb", created_at="2025-01-01 00:00:00"),
        ]
        for record in records:
            record["queue_order"] = 4

        with tempfile.TemporaryDirectory() as temp_dir:
            for record in records:
                task_id = record["task"]["task_id"]
                Path(temp_dir, f"{task_id}.json").write_text(
                    json.dumps(record), encoding="utf-8"
                )
            loaded = self.load_saved_rows(temp_dir)

        self.assertEqual(
            [task_id for task_id, _status, _kwargs in loaded],
            ["bbbbbbbbbbbb", "ffffffffffff", "aaaaaaaaaaaa"],
        )
        self.assertEqual(
            [kwargs["queue_order"] for _task_id, _status, kwargs in loaded],
            [0, 1, 2],
        )

    def test_completion_time_is_created_only_on_real_transition(self):
        target = SimpleNamespace(
            refresh_queue_item_label=Mock(),
            _set_queue_status_icon=Mock(),
            _refresh_queue_reorder_state=Mock(),
            _move_entry_to_history_tail=Mock(return_value=False),
            save_task=Mock(),
        )
        running = task_entry("running", "running")
        running["queue_order"] = 0

        ui_controllers_queue_view.QueueViewController.set_queue_item_status(
            target, running, "completed"
        )

        self.assertIn("completed_at", running)
        self.assertNotIn("queue_order", running)
        target._move_entry_to_history_tail.assert_called_once_with(running)

        already_completed = task_entry("already", "completed")
        original_completed_at = already_completed["completed_at"]
        ui_controllers_queue_view.QueueViewController.set_queue_item_status(
            target, already_completed, "completed"
        )

        self.assertEqual(already_completed["completed_at"], original_completed_at)
        self.assertEqual(target.save_task.call_count, 2)

        target._move_entry_to_history_tail.reset_mock()
        stopped_download = task_entry("stopped", "running")
        stopped_download["queue_order"] = 4
        ui_controllers_queue_view.QueueViewController.set_queue_item_status(
            target, stopped_download, "stopped"
        )

        self.assertEqual(stopped_download["queue_order"], 4)
        self.assertNotIn("completed_at", stopped_download)
        target._move_entry_to_history_tail.assert_not_called()

        stopped_patrol = task_entry("patrol", "running", kind="auto_patrol")
        ui_controllers_queue_view.QueueViewController.set_queue_item_status(
            target, stopped_patrol, "stopped"
        )

        self.assertEqual(stopped_patrol["queue_order"], 0)
        target._move_entry_to_history_tail.assert_not_called()

    def test_saved_task_batch_defers_queue_wide_status_refresh(self):
        target = SimpleNamespace(
            _loading_saved_tasks=True,
            refresh_queue_item_label=Mock(),
            _refresh_queue_reorder_state=Mock(),
            _move_entry_to_history_tail=Mock(),
            save_task=Mock(),
        )
        entry = task_entry("batch", "completed")

        ui_controllers_queue_view.QueueViewController.set_queue_item_status(
            target, entry, "completed", save=False
        )

        target.refresh_queue_item_label.assert_called_once_with(entry)
        target._refresh_queue_reorder_state.assert_not_called()
        target._move_entry_to_history_tail.assert_not_called()
        target.save_task.assert_not_called()

    def test_saved_task_batch_finalizes_queue_once(self):
        target = SimpleNamespace(
            _refresh_queue_item_styles=Mock(),
            _refresh_queue_reorder_state=Mock(),
            update_clear_queue_button_state=Mock(),
            _scroll_queue_to_bottom=Mock(),
        )

        ui_controllers_task_store.TaskStoreController._finalize_loaded_task_batch(
            target
        )

        target._refresh_queue_item_styles.assert_called_once_with()
        target._refresh_queue_reorder_state.assert_called_once_with()
        target.update_clear_queue_button_state.assert_called_once_with()
        target._scroll_queue_to_bottom.assert_called_once_with()

    def test_clear_queue_uses_one_bulk_ui_mutation_without_resaving(self):
        first = task_entry("first")
        second = task_entry("second")
        queue_items = [first, second]
        queue_list = Mock()
        log_dialog = Mock()
        message_box = object()
        target = SimpleNamespace(
            queue_items=queue_items,
            queue_list=queue_list,
            queue_paused=True,
            _thread_pipeline_stop_requested=True,
            _download_stop_reason="stale",
            _queue_handoff_target_id="stale-task",
            queue_started=2,
            queue_completed=1,
            current_queue_index=1,
            _log_dialog=log_dialog,
            is_downloading=Mock(return_value=False),
            tr=Mock(side_effect=lambda key: key),
            _create_standard_message_box=Mock(return_value=message_box),
            _exec_centered_dialog=Mock(
                return_value=ui_qt.QMessageBox.StandardButton.Yes,
            ),
            delete_task_file=Mock(),
            update_download_button_state=Mock(),
            update_clear_queue_button_state=Mock(),
            _remove_queue_entry_at=Mock(),
            save_tasks=Mock(),
        )

        with patch.object(ui_console, "print") as log:
            ui_controllers_queue_view.QueueViewController.clear_queue(target)

        self.assertIs(target.queue_items, queue_items)
        self.assertEqual(target.queue_items, [])
        queue_list.clear.assert_called_once_with()
        self.assertEqual(
            [call.args[0] for call in target.delete_task_file.call_args_list],
            [first, second],
        )
        log_dialog.close.assert_called_once_with()
        self.assertIsNone(target._log_dialog)
        target._remove_queue_entry_at.assert_not_called()
        target.save_tasks.assert_not_called()
        target.update_download_button_state.assert_called_once_with()
        self.assertFalse(target.queue_paused)
        self.assertFalse(target._thread_pipeline_stop_requested)
        self.assertIsNone(target._download_stop_reason)
        self.assertIsNone(target._queue_handoff_target_id)
        self.assertEqual(target.queue_started, 0)
        self.assertEqual(target.queue_completed, 0)
        self.assertIsNone(target.current_queue_index)
        log.assert_called_once_with("Task queue cleared (2 items)")


class QueueEligibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_target(self, entries, *, running=False, paused=False, current=0):
        target = SimpleNamespace(
            queue_items=entries,
            queue_running=running,
            queue_paused=paused,
            _download_stop_reason=None,
            current_queue_index=current,
            active_patrol_entry=None,
        )
        target._current_queue_entry = MethodType(
            ui_controllers_queue_view.QueueViewController._current_queue_entry, target
        )
        target._queue_entry_can_reorder = MethodType(
            ui_controllers_queue_view.QueueViewController._queue_entry_can_reorder,
            target,
        )
        target._queue_entry_is_drop_barrier = MethodType(
            ui_controllers_queue_view.QueueViewController._queue_entry_is_drop_barrier,
            target,
        )
        target._queue_entry_can_show_delete = MethodType(
            ui_controllers_queue_view.QueueViewController._queue_entry_can_show_delete,
            target,
        )
        return target

    def test_idle_allows_pending_downloads_and_patrols(self):
        pending = task_entry("P")
        target = self.make_target([pending])
        self.assertTrue(target._queue_entry_can_reorder(pending))
        for status in ("running", "completed", "deferred"):
            entry = task_entry(status, status)
            self.assertFalse(target._queue_entry_can_reorder(entry))
        patrol = task_entry("eeeeeeeeeeee", kind="auto_patrol")
        self.assertTrue(target._queue_entry_can_reorder(patrol))

        stopped_download = task_entry("S", "stopped")
        target.queue_items.append(stopped_download)
        self.assertTrue(target._queue_entry_can_reorder(stopped_download))
        self.assertTrue(target._queue_entry_can_show_delete(stopped_download))

        stopped_patrol = task_entry("dddddddddddd", "stopped", kind="auto_patrol")
        target.queue_items.append(stopped_patrol)
        self.assertTrue(target._queue_entry_can_reorder(stopped_patrol))
        self.assertTrue(target._queue_entry_can_show_delete(stopped_patrol))

        completed_patrol = task_entry("ffffffffffff", "completed", kind="auto_patrol")
        self.assertFalse(target._queue_entry_can_reorder(completed_patrol))
        self.assertTrue(target._queue_entry_can_show_delete(completed_patrol))

    def test_batch_count_includes_stopped_downloads(self):
        entries = [
            task_entry("A", "running"),
            task_entry("B", "pending"),
            task_entry("C", "stopped"),
            task_entry("D", "completed"),
            task_entry("E", "pending", kind="auto_patrol"),
        ]
        target = SimpleNamespace(
            queue_items=entries,
        )

        self.assertEqual(
            ui_controllers_execution.QueueExecutionController._count_batch_tasks(
                target
            ),
            3,
        )

    def test_finalize_restores_only_deferred_tasks(self):
        deferred = task_entry("D", "deferred")
        stopped = task_entry("S", "stopped")
        target = SimpleNamespace(
            queue_items=[deferred, stopped],
            _thread_pipeline_stop_requested=True,
            set_queue_item_status=Mock(
                side_effect=lambda entry, status: entry.__setitem__("status", status)
            ),
        )
        target._restore_deferred_tasks = MethodType(
            ui_controllers_execution.QueueExecutionController._restore_deferred_tasks,
            target,
        )

        ui_controllers_execution.QueueExecutionController._finalize_queue_run(target)

        self.assertEqual(deferred["status"], "pending")
        self.assertEqual(stopped["status"], "stopped")
        self.assertFalse(target._thread_pipeline_stop_requested)
        target.set_queue_item_status.assert_called_once_with(deferred, "pending")

    def test_serial_pause_unlocks_active_and_future_runnable_tasks(self):
        active = task_entry("A", "running")
        pending = task_entry("B")
        stopped_patrol = task_entry("P", "stopped", kind="auto_patrol")
        target = self.make_target(
            [active, pending, stopped_patrol], running=True, paused=False
        )
        self.assertFalse(target._queue_entry_can_reorder(active))
        self.assertTrue(target._queue_entry_can_reorder(pending))
        self.assertTrue(target._queue_entry_can_reorder(stopped_patrol))

        target.queue_paused = True
        self.assertTrue(target._queue_entry_can_reorder(active))
        self.assertTrue(target._queue_entry_can_reorder(pending))
        self.assertTrue(target._queue_entry_can_reorder(stopped_patrol))

    def test_only_completed_and_running_rows_are_drop_barriers(self):
        first = task_entry("A")
        deferred = task_entry("D", "deferred")
        last = task_entry("B")
        target = self.make_target([first, deferred, last])
        target._queue_drop_is_allowed = MethodType(
            ui_controllers_queue_view.QueueViewController._queue_drop_is_allowed, target
        )

        self.assertTrue(target._queue_drop_is_allowed(2, 0))
        self.assertFalse(target._queue_drop_is_allowed(1, 0))

        target.queue_items[1] = task_entry("C", "completed")
        self.assertFalse(target._queue_drop_is_allowed(2, 0))
        target.queue_items[1] = task_entry("R", "running")
        self.assertFalse(target._queue_drop_is_allowed(2, 0))

    def test_paused_active_row_is_not_a_drop_barrier(self):
        first = task_entry("A")
        active = task_entry("R", "running")
        last = task_entry("B")
        target = self.make_target(
            [first, active, last],
            running=True,
            paused=True,
            current=1,
        )
        target._queue_drop_is_allowed = MethodType(
            ui_controllers_queue_view.QueueViewController._queue_drop_is_allowed, target
        )

        self.assertTrue(target._queue_drop_is_allowed(2, 0))
        self.assertTrue(target._queue_drop_is_allowed(1, 3))

    def test_refresh_applies_item_flags_and_row_drag_state(self):
        queue = ui_widgets_queue.TaskQueueListWidget()
        item = QListWidgetItem()
        queue.addItem(item)
        widget = ui_widgets_queue.QueueItemWidget()
        entry = task_entry("A")
        entry.update({"item": item, "widget": widget})
        target = self.make_target([entry])
        target.tr = lambda key: key
        target._set_queue_status_icon = Mock()
        target._refresh_queue_reorder_state = MethodType(
            ui_controllers_queue_view.QueueViewController._refresh_queue_reorder_state,
            target,
        )

        target._refresh_queue_reorder_state()
        self.assertTrue(bool(item.flags() & Qt.ItemFlag.ItemIsDragEnabled))
        self.assertTrue(widget._drag_enabled)
        self.assertEqual(widget.cursor().shape(), Qt.CursorShape.ArrowCursor)
        self.assertEqual(widget.toolTip(), "queue_drag_tooltip")

        entry["status"] = "completed"
        target._refresh_queue_reorder_state()
        self.assertFalse(bool(item.flags() & Qt.ItemFlag.ItemIsDragEnabled))
        self.assertFalse(widget._drag_enabled)
        self.assertEqual(widget.toolTip(), "queue_drag_locked_tooltip")
        self.assertEqual(widget.cursor().shape(), Qt.CursorShape.ArrowCursor)

        entry["status"] = "running"
        target.queue_running = True
        target.queue_paused = True
        target._refresh_queue_reorder_state()
        self.assertTrue(bool(item.flags() & Qt.ItemFlag.ItemIsDragEnabled))
        self.assertTrue(widget._drag_enabled)

        queue.close()
        queue.deleteLater()
        widget.deleteLater()


class DownloadScopeProgressTests(unittest.TestCase):
    def test_queue_label_shows_only_logical_download_count(self):
        for scope_unit, media_type in (
            ("page", "posts"),
            ("full", "stories"),
            ("item", "reels"),
        ):
            with self.subTest(scope_unit=scope_unit):
                task = task_entry(
                    "alice",
                    scope_unit=scope_unit,
                    scope_limit=1,
                    downloaded_count=5,
                )["task"]
                task["download_media_type"] = media_type

                label = tasks_schema._format_task_label(task)

                self.assertEqual(
                    label,
                    f"alice - {media_type.capitalize()}: 5",
                )
                self.assertNotIn("/", label)
                self.assertNotIn(" item", label)
                self.assertNotIn(" full", label)

    @staticmethod
    def make_target(
        scope_unit,
        *,
        scope_limit=3,
        scope_completed=0,
        downloaded_count=0,
    ):
        entry = task_entry(
            "A",
            "running",
            scope_unit=scope_unit,
            scope_limit=scope_limit,
            scope_completed=scope_completed,
            downloaded_count=downloaded_count,
        )
        if scope_unit == "full":
            entry["task"]["download_media_type"] = "stories"
        entry["_run_completed_start"] = scope_completed
        entry["_run_scope_completed_count"] = 0
        entry["_run_downloaded_start"] = downloaded_count
        entry["_run_downloaded_count"] = 0
        entry["_run_finalized"] = False
        entry["label"] = Mock()
        target = SimpleNamespace(
            queue_items=[entry],
            current_queue_index=0,
            refresh_queue_item_label=Mock(),
            _refresh_log_dialog_status=Mock(),
            save_task=Mock(),
        )
        for name in (
            "_nonnegative_int",
            "_task_scope_unit",
            "_task_scope_limit",
            "_task_scope_completed",
            "_set_task_scope_completed",
            "_advance_download_scope_progress",
        ):
            setattr(target, name, MethodType(getattr(ui_main_window.GUI, name), target))
        return target, entry

    def test_item_callback_counts_media_without_advancing_scope(self):
        for scope_unit in ("page", "full"):
            with self.subTest(scope_unit=scope_unit):
                target, entry = self.make_target(
                    scope_unit,
                    scope_limit=1 if scope_unit == "full" else 3,
                    downloaded_count=4,
                )

                ui_preview_events.PreviewEvents.on_download_item_completed(target, {})
                self.assertEqual(entry["task"]["scope_completed"], 0)
                self.assertEqual(entry["task"]["downloaded_count"], 5)
                target.save_task.assert_called_once_with(entry)

                ui_preview_events.PreviewEvents.on_download_scope_completed(target, {})
                self.assertEqual(entry["task"]["scope_completed"], 1)
                self.assertEqual(entry["task"]["downloaded_count"], 5)
                self.assertEqual(target.save_task.call_count, 2)
                self.assertEqual(
                    target._refresh_log_dialog_status.call_count,
                    1,
                )
                entry["label"].setText.assert_called_once_with(
                    f"a - {entry['task']['download_media_type'].capitalize()}: 5"
                )
                target.refresh_queue_item_label.assert_not_called()

    def test_final_result_reconciles_downloaded_and_scope_counts(self):
        target, entry = self.make_target(
            "page",
            scope_limit=5,
            scope_completed=1,
            downloaded_count=4,
        )
        entry["_run_downloaded_count"] = 1

        ui_controllers_queue_view.QueueViewController.apply_download_result_to_task(
            target,
            entry,
            {"scope_completed_count": 2, "downloaded_count": 2},
        )

        self.assertEqual(entry["task"]["scope_completed"], 3)
        self.assertEqual(entry["task"]["downloaded_count"], 6)
        self.assertEqual(entry["_run_scope_completed_expected"], 2)
        self.assertEqual(entry["_run_downloaded_expected"], 2)
        self.assertTrue(entry["_run_finalized"])

    def test_final_result_and_late_callbacks_do_not_double_count(self):
        target, entry = self.make_target("page")

        ui_preview_events.PreviewEvents.on_download_item_completed(target, {})
        ui_preview_events.PreviewEvents.on_download_item_completed(target, {})
        ui_preview_events.PreviewEvents.on_download_scope_completed(target, {})
        ui_preview_events.PreviewEvents.on_download_scope_completed(target, {})
        ui_controllers_queue_view.QueueViewController.apply_download_result_to_task(
            target,
            entry,
            {"scope_completed_count": 2, "downloaded_count": 2},
        )
        ui_preview_events.PreviewEvents.on_download_item_completed(target, {})
        ui_preview_events.PreviewEvents.on_download_scope_completed(target, {})

        self.assertEqual(entry["task"]["scope_completed"], 2)
        self.assertEqual(entry["task"]["downloaded_count"], 2)
        self.assertEqual(entry["_run_scope_completed_count"], 2)
        self.assertEqual(entry["_run_downloaded_count"], 2)
        self.assertEqual(target.save_task.call_count, 4)


class SerialQueueControlTests(unittest.TestCase):
    def make_target(self):
        return make_queue_target()

    def test_resume_keeps_active_task_without_requesting_stop(self):
        target, _, _ = self.make_target()
        with (
            patch.object(downloads_control, "request_stop_downloads") as request_stop,
            patch.object(downloads_control, "resume_downloads") as resume,
        ):
            ui_controllers_queue_controls.QueueControls.resume_download_queue(target)

        request_stop.assert_not_called()
        resume.assert_called_once_with()
        self.assertFalse(target.queue_paused)
        self.assertFalse(target._thread_pipeline_stop_requested)
        target.start_task_by_index.assert_not_called()

    def test_pause_restores_deferred_tasks_immediately(self):
        target, active, _ = self.make_target()
        deferred = task_entry("D", "deferred")
        target.queue_items[:] = [active, deferred]
        target.current_queue_index = 0
        target.queue_paused = False

        with patch.object(downloads_control, "pause_downloads") as pause:
            ui_controllers_queue_controls.QueueControls.pause_download_queue(target)

        pause.assert_called_once_with()
        self.assertTrue(target.queue_paused)
        self.assertEqual(deferred["status"], "pending")

    def test_display_status_has_no_user_visible_stopped_state(self):
        target, active, _ = self.make_target()

        self.assertEqual(
            ui_controllers_queue_view.QueueViewController._queue_display_status(
                target, active
            ),
            "paused",
        )
        target._download_stop_reason = "handoff"
        self.assertEqual(
            ui_controllers_queue_view.QueueViewController._queue_display_status(
                target, active
            ),
            "switching",
        )
        legacy = task_entry("S", "stopped")
        self.assertEqual(
            ui_controllers_queue_view.QueueViewController._queue_display_status(
                target, legacy
            ),
            "pending",
        )

    def test_resume_reordered_queue_requests_cooperative_handoff(self):
        target, active, pending = self.make_target()
        target.queue_items[:] = [pending, active]
        target.current_queue_index = 1

        with patch.object(downloads_control, "request_stop_downloads") as request_stop:
            ui_controllers_queue_controls.QueueControls.resume_download_queue(target)

        request_stop.assert_called_once_with()
        self.assertFalse(target.queue_paused)
        self.assertEqual(target._download_stop_reason, "handoff")
        self.assertEqual(
            target._queue_handoff_target_id,
            pending["task"]["task_id"],
        )
        self.assertTrue(target._thread_pipeline_stop_requested)
        self.assertEqual(
            target._thread_pipeline_current_file_name,
            "active-file.jpg",
        )
        target.start_task_by_index.assert_not_called()

    def test_handoff_does_not_substitute_another_task_when_target_is_missing(self):
        target, _active, _pending = self.make_target()
        target._download_stop_reason = "handoff"
        target._queue_handoff_target_id = "missing-task"

        with patch.object(downloads_control, "clear_stop_request") as clear_stop:
            handled = target._start_queue_handoff_target()

        self.assertTrue(handled)
        clear_stop.assert_called_once_with()
        target.start_task_by_index.assert_not_called()
        target._finalize_queue_run.assert_called_once_with()
        self.assertFalse(target.queue_running)
        self.assertIsNone(target.current_queue_index)

    def test_handoff_marks_old_task_pending_and_starts_selected_task(self):
        target, active, pending = self.make_target()
        target.queue_items[:] = [pending, active]
        target.current_queue_index = 1
        target.queue_paused = False
        target._download_stop_reason = "handoff"
        target._queue_handoff_target_id = pending["task"]["task_id"]

        with patch.object(downloads_control, "clear_stop_request") as clear_stop:
            ui_controllers_execution.QueueExecutionController.handle_task_stopped(
                target,
                {
                    "status": downloads_models.status_code.STOPPED,
                    "downloaded_count": 1,
                },
            )

        clear_stop.assert_called_once_with()
        self.assertEqual(active["status"], "pending")
        self.assertTrue(target.queue_running)
        self.assertIsNone(target._download_stop_reason)
        self.assertIsNone(target._queue_handoff_target_id)
        self.assertEqual(target._thread_pipeline_current_file_name, "")
        target._finalize_queue_run.assert_not_called()
        target.start_task_by_index.assert_called_once_with(
            0,
            apply_user_switch_delay=True,
        )

    def test_blocking_error_cancels_handoff_without_starting_selected_task(self):
        target, active, pending = self.make_target()
        target.queue_items[:] = [pending, active]
        target.current_queue_index = 1
        target.queue_paused = False
        target._download_stop_reason = "handoff"
        target._queue_handoff_target_id = pending["task"]["task_id"]
        target.update_patrol_button_state = Mock()
        target.tr = Mock(side_effect=lambda key, **kwargs: key.format(**kwargs))

        with (
            patch.object(downloads_control, "clear_stop_request") as clear_stop,
            patch.object(notifications_service, "notify"),
        ):
            ui_controllers_execution.QueueExecutionController.handle_task_complete(
                target,
                success=False,
                error_msg="authorization expired",
                blocking_failure=True,
            )

        self.assertEqual(active["status"], "pending")
        self.assertFalse(target.queue_running)
        self.assertIsNone(target._download_stop_reason)
        self.assertIsNone(target._queue_handoff_target_id)
        clear_stop.assert_called_once_with()
        target.start_task_by_index.assert_not_called()

    def test_handoff_after_final_file_completes_old_task_and_starts_selected(self):
        target, active, pending = self.make_target()
        target.queue_items[:] = [pending, active]
        target.current_queue_index = 1
        target.queue_paused = False
        target._download_stop_reason = "handoff"
        target._queue_handoff_target_id = pending["task"]["task_id"]
        target.apply_download_result_to_task.side_effect = lambda entry, _result: entry[
            "task"
        ].__setitem__("scope_completed", entry["task"]["scope_limit"])

        with (
            patch.object(downloads_control, "clear_stop_request"),
            patch.object(
                downloads_checkpoints,
                "delete_task_resume_checkpoint",
            ) as delete_cursor,
        ):
            ui_controllers_execution.QueueExecutionController.handle_task_stopped(
                target,
                {
                    "status": downloads_models.status_code.STOPPED,
                    "scope_completed_count": active["task"]["scope_limit"],
                },
            )

        self.assertEqual(active["status"], "completed")
        self.assertEqual(target.queue_completed, 1)
        delete_cursor.assert_called_once_with(
            active["task"]["task_id"],
            active["task"]["download_media_type"],
        )
        target._finalize_queue_run.assert_not_called()
        target.start_task_by_index.assert_called_once_with(
            0,
            apply_user_switch_delay=True,
        )

    def test_application_exit_returns_all_tasks_to_pending(self):
        target, active, _ = self.make_target()
        target.download_thread = SimpleNamespace(
            isRunning=Mock(return_value=False),
            deleteLater=Mock(),
        )

        with patch.object(downloads_control, "clear_stop_request"):
            ui_controllers_execution.QueueExecutionController._stop_download_thread_for_exit(
                target
            )

        self.assertEqual(active["status"], "pending")
        self.assertFalse(target.queue_running)
        self.assertIsNone(target.current_queue_index)
        self.assertIsNone(target._download_stop_reason)
        self.assertEqual(target._thread_pipeline_current_file_name, "")

    def test_success_during_handoff_completes_old_task_and_starts_selected(self):
        target, active, pending = self.make_target()
        target.queue_items[:] = [pending, active]
        target.current_queue_index = 1
        target.queue_paused = False
        target._download_stop_reason = "handoff"
        target._queue_handoff_target_id = pending["task"]["task_id"]

        with patch.object(downloads_control, "clear_stop_request") as clear_stop:
            ui_controllers_execution.QueueExecutionController.handle_task_complete(
                target,
                success=True,
                result={
                    "status": downloads_models.status_code.SUCCESS,
                    "downloaded_count": 1,
                },
            )

        clear_stop.assert_called_once_with()
        self.assertEqual(active["status"], "completed")
        self.assertEqual(target.queue_completed, 1)
        self.assertIsNone(target._download_stop_reason)
        self.assertEqual(target._thread_pipeline_current_file_name, "")
        target._finalize_queue_run.assert_not_called()
        target.start_task_by_index.assert_called_once_with(
            0,
            apply_user_switch_delay=True,
        )

    def test_media_cap_completion_marks_task_complete_and_continues_queue(self):
        target, active, pending = self.make_target()
        target.queue_paused = False

        ui_controllers_execution.QueueExecutionController.handle_task_complete(
            target,
            success=True,
            result={
                "status": downloads_models.status_code.SUCCESS,
                "completion_reason": "media_request_cap",
                "downloaded_count": 2,
                "scope_completed_count": 0,
                "media_request_count": 2,
            },
        )

        self.assertEqual(active["status"], "completed")
        self.assertEqual(pending["status"], "pending")
        self.assertEqual(target.queue_completed, 1)
        self.assertTrue(target.queue_running)
        target.start_task_by_index.assert_called_once_with(
            1,
            apply_user_switch_delay=True,
        )

    def test_stop_during_initial_user_switch_delay_emits_stopped_not_error(self):
        thread = ui_workers.DownloadThread(
            "alice",
            "posts",
            1,
            apply_user_switch_delay=True,
            task_id="task-a",
        )
        finished = []
        errors = []
        thread.finished_signal.connect(finished.append)
        thread.error_signal.connect(errors.append)

        with patch.object(
            downloads_control,
            "delay",
            side_effect=downloads_models.DownloadStopRequested(),
        ):
            thread.run()

        self.assertEqual(errors, [])
        self.assertEqual(len(finished), 1)
        self.assertEqual(finished[0]["status"], downloads_models.status_code.STOPPED)

    def test_api_429_uses_dedicated_blocking_thread_signal(self):
        thread = ui_workers.DownloadThread(
            "alice",
            "posts",
            1,
            task_id="task-a",
            risk_run_id="run1234",
            queue_completed_before=4,
        )
        rate_limits = []
        errors = []
        thread.rate_limited_signal.connect(rate_limits.append)
        thread.error_signal.connect(errors.append)

        with patch.object(
            downloads_service,
            "download_user_content",
            return_value={
                "status": downloads_models.status_code.RATE_LIMITED,
                "message": "Instagram API returned HTTP 429",
                "downloaded_count": 0,
            },
        ) as worker:
            thread.run()

        self.assertEqual(rate_limits, ["Instagram API returned HTTP 429"])
        self.assertEqual(errors, [])
        self.assertEqual(worker.call_args.kwargs["risk_run_id"], "run1234")
        self.assertEqual(worker.call_args.kwargs["queue_completed_before"], 4)

    def test_loader_wide_failure_uses_terminal_result_signal(self):
        thread = ui_workers.DownloadThread(
            "alice",
            "posts",
            1,
            task_id="task-a",
        )
        finished = []
        errors = []
        thread.finished_signal.connect(finished.append)
        thread.error_signal.connect(errors.append)
        result = {
            "status": downloads_models.status_code.FAILED,
            "message": "Loader client not available",
            "blocking_failure": True,
        }

        with patch.object(
            downloads_service,
            "download_user_content",
            return_value=result,
        ):
            thread.run()

        self.assertEqual(finished, [result])
        self.assertEqual(errors, [])

    def test_terminal_loader_failure_pauses_queue_without_auth_label(self):
        target = SimpleNamespace(
            _active_console_task_id=Mock(return_value="task-a"),
            _finish_task_console_capture=Mock(),
            handle_task_stopped=Mock(),
            handle_task_complete=Mock(),
            update_api_status=Mock(),
        )
        result = {
            "status": downloads_models.status_code.FAILED,
            "message": "Instagram service unavailable",
            "blocking_failure": True,
        }

        ui_controllers_status.StatusController.download_finished(target, result)

        target.update_api_status.assert_called_once_with("error")
        target.handle_task_complete.assert_called_once_with(
            success=False,
            error_msg="Instagram service unavailable",
            blocking_failure=True,
        )
        target._finish_task_console_capture.assert_called_once_with("task-a")


if __name__ == "__main__":
    unittest.main()
