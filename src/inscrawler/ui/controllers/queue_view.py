"""Queue rows, selection, drag ordering, and task presentation."""

from __future__ import annotations

import uuid
from datetime import datetime

import inscrawler.tasks.schema as tasks_schema
import inscrawler.ui.console as ui_console
import inscrawler.ui.dialogs.console as ui_dialogs_console
import inscrawler.ui.dialogs.patrol as ui_dialogs_patrol
import inscrawler.ui.dialogs.task as ui_dialogs_task
import inscrawler.ui.platform as ui_platform
import inscrawler.ui.qt as ui_qt
import inscrawler.ui.theme.icons as ui_theme_icons
import inscrawler.ui.widgets.queue as ui_widgets_queue


class QueueViewController:
    """Qt view concern composed explicitly by the main window."""

    def add_task(self):
        """添加任务到队列"""
        target_user = self._current_target_user()
        if not target_user:
            return

        self.target.setEditText(target_user)
        self.save_settings()
        task = self.build_task_from_ui(target_user)
        self.add_task_to_queue(task, "pending")
        if self.queue_running:
            self.queue_started += 1
        self.update_download_button_state()
        ui_console.print(
            f"Queued @{task['target_user']} {task['download_media_type'].capitalize()}"
        )

    def _first_runnable_index(self):
        for idx, entry in enumerate(self.queue_items):
            status = entry.get("status")
            task = entry.get("task") or {}
            if tasks_schema._is_runnable_task(task, status):
                return idx
        return None

    def _first_paused_queue_candidate(self):
        """Return the first executable row while the current row is paused.

        The active row is still persisted as ``running`` while paused, so it
        must participate in the ordering decision alongside pending/legacy
        stopped rows.
        """
        active_entry = self._current_queue_entry()
        for entry in getattr(self, "queue_items", []):
            if entry is active_entry and entry.get("status") == "running":
                return entry
            if tasks_schema._is_runnable_task(entry.get("task"), entry.get("status")):
                return entry
        return None

    def _current_queue_entry(self):
        index = getattr(self, "current_queue_index", None)
        if isinstance(index, int) and 0 <= index < len(self.queue_items):
            return self.queue_items[index]
        return None

    def _queue_entry_can_reorder(self, entry):
        if entry is None:
            return False
        if getattr(self, "_download_stop_reason", None) == "handoff":
            return False
        active_entry = self._current_queue_entry()
        if entry is active_entry and entry.get("status") == "running":
            return bool(getattr(self, "queue_paused", False))
        return tasks_schema._is_runnable_task(entry.get("task"), entry.get("status"))

    def _queue_entry_is_drop_barrier(self, entry):
        status = entry.get("status")
        if status == "completed":
            return True
        if status == "running":
            return not self._queue_entry_can_reorder(entry)
        return False

    def _queue_drop_is_allowed(self, source_row, destination_row):
        if source_row < 0 or source_row >= len(self.queue_items):
            return False
        destination_row = max(0, min(int(destination_row), len(self.queue_items)))
        if not self._queue_entry_can_reorder(self.queue_items[source_row]):
            return False
        if destination_row in (source_row, source_row + 1):
            return True

        if destination_row < source_row:
            crossed_rows = range(destination_row, source_row)
        else:
            crossed_rows = range(source_row + 1, destination_row)
        return all(
            not self._queue_entry_is_drop_barrier(self.queue_items[row])
            for row in crossed_rows
        )

    def _sync_queue_order_from_view(self):
        if len(self.queue_items) != self.queue_list.count():
            ui_console.print("Queue reorder ignored because view/model counts differ")
            return None

        active_entry = self._current_queue_entry()
        ordered = sorted(
            self.queue_items,
            key=lambda entry: self.queue_list.row(entry["item"]),
        )
        if any(self.queue_list.row(entry["item"]) < 0 for entry in ordered):
            ui_console.print(
                "Queue reorder ignored because an item is missing from the view"
            )
            return None
        if ordered == self.queue_items:
            return False

        self.queue_items[:] = ordered
        if active_entry is not None:
            self.current_queue_index = self._queue_index_for_entry(active_entry)
        return True

    def _on_queue_order_preview_changed(self):
        self._sync_queue_order_from_view()

    def _on_queue_order_changed(self):
        if self._sync_queue_order_from_view() is None:
            return
        queue_order = 0
        for entry in self.queue_items:
            if tasks_schema._uses_queue_order(entry.get("task"), entry.get("status")):
                entry["queue_order"] = queue_order
                self.save_task(entry)
                queue_order += 1
        self._refresh_queue_dividers()
        ui_console.print("Task queue order updated")

    def _move_entry_to_history_tail(self, entry):
        source_row = self._queue_index_for_entry(entry)
        if source_row is None:
            return False
        destination_row = next(
            (
                row
                for row, candidate in enumerate(self.queue_items)
                if tasks_schema._uses_queue_order(
                    candidate.get("task"), candidate.get("status")
                )
            ),
            len(self.queue_items),
        )
        if destination_row in (source_row, source_row + 1):
            return False
        if self.queue_list.model().moveRow(
            ui_qt.QModelIndex(), source_row, ui_qt.QModelIndex(), destination_row
        ):
            self._sync_queue_order_from_view()
            self._refresh_queue_dividers()
            return True
        return False

    def _move_entry_to_queue_tail(self, entry):
        """Move a deferred entry behind the remaining unfinished tasks."""
        source_row = self._queue_index_for_entry(entry)
        if source_row is None or source_row == len(self.queue_items) - 1:
            return False
        if self.queue_list.model().moveRow(
            ui_qt.QModelIndex(), source_row, ui_qt.QModelIndex(), len(self.queue_items)
        ):
            self._on_queue_order_changed()
            return True
        return False

    def _refresh_queue_reorder_state(self):
        tooltip = self.tr("queue_drag_tooltip")
        locked_tooltip = self.tr("queue_drag_locked_tooltip")
        for entry in self.queue_items:
            enabled = self._queue_entry_can_reorder(entry)
            widget = entry.get("widget")
            hovered = False
            if isinstance(widget, ui_widgets_queue.QueueItemWidget):
                widget.set_drag_enabled(enabled)
                widget.setToolTip(tooltip if enabled else locked_tooltip)
                hovered = widget.is_hovered()
            self._set_queue_status_icon(entry, hovered=hovered)
            item = entry.get("item")
            if item is not None:
                flags = item.flags()
                if enabled:
                    flags |= ui_qt.Qt.ItemFlag.ItemIsDragEnabled
                else:
                    flags &= ~ui_qt.Qt.ItemFlag.ItemIsDragEnabled
                item.setFlags(flags)

    def _queue_index_for_entry(self, target_entry):
        for idx, entry in enumerate(self.queue_items):
            if entry is target_entry:
                return idx
        return None

    def _queue_index_for_task_id(self, task_id):
        task_id = str(task_id or "")
        if not task_id:
            return None
        for idx, entry in enumerate(self.queue_items):
            if str((entry.get("task") or {}).get("task_id") or "") == task_id:
                return idx
        return None

    def _center_window_on_self(self, window):
        if window is None:
            return
        try:
            parent_rect = self.frameGeometry()
            child_rect = window.frameGeometry()
            if child_rect.width() <= 0 or child_rect.height() <= 0:
                child_rect.setSize(window.sizeHint())
            x = parent_rect.x() + (parent_rect.width() - child_rect.width()) // 2
            y = parent_rect.y() + (parent_rect.height() - child_rect.height()) // 2

            screen = None
            handle = self.windowHandle()
            if handle is not None:
                screen = handle.screen()
            if screen is None:
                screen = ui_qt.QApplication.screenAt(parent_rect.center())
            if screen is None:
                screen = ui_qt.QApplication.primaryScreen()
            if screen is not None:
                available = screen.availableGeometry()
                x = max(
                    available.left(), min(x, available.right() - child_rect.width() + 1)
                )
                y = max(
                    available.top(),
                    min(y, available.bottom() - child_rect.height() + 1),
                )

            window.move(x, y)
        except RuntimeError:
            pass

    def _exec_centered_dialog(self, dialog):
        self._center_window_on_self(dialog)
        ui_qt.QTimer.singleShot(
            0, lambda dialog=dialog: self._center_window_on_self(dialog)
        )
        return dialog.exec()

    def _show_centered_dialog(self, dialog):
        dialog.show()
        self._center_window_on_self(dialog)
        ui_qt.QTimer.singleShot(
            0, lambda dialog=dialog: self._center_window_on_self(dialog)
        )
        dialog.raise_()
        dialog.activateWindow()

    def _create_standard_message_box(
        self, icon, title, text, buttons, default_button=None, minimum_width=420
    ):
        msg = ui_qt.QMessageBox(self)
        msg.setIcon(icon)
        msg.setWindowTitle(title)
        msg.setText(text)
        msg.setStandardButtons(buttons)
        if default_button is not None:
            msg.setDefaultButton(default_button)
        msg.setMinimumWidth(minimum_width)
        msg.setWindowFlags(
            ui_qt.Qt.WindowType.Dialog
            | ui_qt.Qt.WindowType.CustomizeWindowHint
            | ui_qt.Qt.WindowType.WindowTitleHint
            | ui_qt.Qt.WindowType.WindowCloseButtonHint
            | ui_qt.Qt.WindowType.MSWindowsFixedSizeDialogHint
        )
        ui_platform._suppress_secondary_window_icon(msg)
        return msg

    def _show_message_box(
        self,
        icon,
        title,
        text,
        buttons=ui_qt.QMessageBox.StandardButton.Ok,
        default_button=None,
        minimum_width=420,
    ):
        msg = self._create_standard_message_box(
            icon,
            title,
            text,
            buttons,
            default_button=default_button,
            minimum_width=minimum_width,
        )
        return self._exec_centered_dialog(msg)

    def build_task_from_ui(self, target_user):
        download_media_type = self.type.currentText().lower()
        scope_unit = self._current_download_scope_unit()
        scope_limit = 1 if scope_unit == "full" else self.scope_limit.value()
        media_request_cap = (
            self.media_request_cap_spin.value()
            if self.media_request_cap_enabled
            else None
        )
        save_path_value = self.save_path.text().strip() or self.default_save_path
        return {
            "task_id": uuid.uuid4().hex[:12],
            "task_kind": "download",
            "target_user": target_user,
            "download_media_type": download_media_type,
            "scope_unit": scope_unit,
            "scope_limit": scope_limit,
            "scope_completed": 0,
            "downloaded_count": 0,
            "media_request_cap": media_request_cap,
            "save_path": save_path_value,
            "login_username": self.username.text().strip() or None,
            "notification": self.notification.currentData() or "",
            "webhook": self.webhook.text().strip() or None,
            "telegram_bot_token": self.telegram_bot_token.text().strip() or None,
            "telegram_chat_id": self.telegram_chat_id.text().strip() or None,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    def add_task_to_queue(
        self,
        task,
        status,
        logs=None,
        downloaded_files=None,
        downloaded_file_details=None,
        patrol_state=None,
        log_targets=None,
        queue_order=None,
        completed_at=None,
        save=True,
    ):
        loading_saved_tasks = bool(getattr(self, "_loading_saved_tasks", False))
        entry_logs = list(logs) if logs is not None else []
        is_patrol = tasks_schema._is_auto_patrol_task(task)
        if is_patrol:
            if log_targets is None or len(log_targets) != len(entry_logs):
                raise ValueError("patrol log_targets must align with logs")
            entry_log_targets = list(log_targets)
        elif log_targets is not None:
            raise ValueError("download tasks must not define log_targets")

        queue_scrollbar = self.queue_list.verticalScrollBar()
        queue_scroll_value = queue_scrollbar.value()
        row = len(self.queue_items)
        item = ui_qt.QListWidgetItem()
        item.setData(
            ui_qt.Qt.ItemDataRole.UserRole,
            str(task.get("task_id") or ""),
        )
        widget = ui_widgets_queue.QueueItemWidget()
        widget.setStyleSheet(self._queue_item_style())
        layout = ui_qt.QHBoxLayout(widget)
        layout.setContentsMargins(8, 8, 4, 10)
        layout.setSpacing(0)

        avatar_label = ui_qt.QLabel()
        avatar_label.setAttribute(
            ui_qt.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        avatar_label.setFixedSize(ui_qt.QSize(36, 28))
        avatar_label.setAlignment(
            ui_qt.Qt.AlignmentFlag.AlignLeft | ui_qt.Qt.AlignmentFlag.AlignVCenter
        )
        layout.addWidget(avatar_label)

        label = ui_qt.QLabel(tasks_schema._format_task_label(task))
        label.setAttribute(ui_qt.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        label.setStyleSheet(self._queue_label_style())
        label.setSizePolicy(
            ui_qt.QSizePolicy.Policy.Expanding, ui_qt.QSizePolicy.Policy.Preferred
        )
        layout.addWidget(label)

        date_label = ui_qt.QLabel(tasks_schema._format_task_date(task))
        date_label.setAttribute(
            ui_qt.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        date_label.setAlignment(
            ui_qt.Qt.AlignmentFlag.AlignRight | ui_qt.Qt.AlignmentFlag.AlignVCenter
        )
        date_label.setStyleSheet(self._queue_label_style())
        date_label.setMinimumWidth(86)
        date_label.setSizePolicy(
            ui_qt.QSizePolicy.Policy.Fixed, ui_qt.QSizePolicy.Policy.Preferred
        )
        layout.addWidget(date_label)

        status_btn = ui_qt.QToolButton()
        status_btn.setAutoRaise(True)
        status_btn.setFixedSize(ui_qt.QSize(30, 30))
        status_btn.setIconSize(
            ui_qt.QSize(
                ui_theme_icons.DEFAULT_ICON_SIZE, ui_theme_icons.DEFAULT_ICON_SIZE
            )
        )
        status_btn.setFocusPolicy(ui_qt.Qt.FocusPolicy.NoFocus)
        status_btn.setCursor(ui_qt.Qt.CursorShape.ArrowCursor)
        status_btn.clicked.connect(self.on_queue_item_delete)
        layout.addWidget(status_btn)

        self.queue_list.insertItem(row, item)
        self.queue_list.setItemWidget(item, widget)

        entry = {
            "task": task,
            "status": status,
            "item": item,
            "widget": widget,
            "avatar_label": avatar_label,
            "label": label,
            "date_label": date_label,
            "status_button": status_btn,
            "logs": entry_logs,
            "downloaded_files": list(downloaded_files)
            if downloaded_files
            else [],  # 记录此任务下载的文件路径
            "downloaded_file_details": list(downloaded_file_details)
            if downloaded_file_details
            else [],
            "patrol_state": patrol_state if patrol_state is not None else {},
        }
        if is_patrol:
            entry["log_targets"] = entry_log_targets
        self.queue_items.insert(row, entry)
        if tasks_schema._uses_queue_order(task, status):
            if type(queue_order) is int and queue_order >= 0:
                entry["queue_order"] = queue_order
            else:
                entry["queue_order"] = self._next_queue_order()
        elif status == "completed" and completed_at:
            entry["completed_at"] = completed_at
        if self.current_queue_index is not None and row <= self.current_queue_index:
            self.current_queue_index += 1
        status_btn.setProperty("queue_item", item)
        widget.hovered_changed.connect(
            lambda hovered, entry=entry: self._set_queue_status_icon(
                entry,
                hovered=hovered,
            )
        )
        widget.clicked.connect(lambda entry=entry: self.show_task_log_dialog(entry))
        widget.drag_started.connect(
            lambda item=item: self.queue_list.begin_pointer_drag(item)
        )
        widget.drag_moved.connect(
            lambda global_pos, item=item: self.queue_list.update_pointer_drag(
                item, global_pos
            )
        )
        widget.drag_finished.connect(
            lambda global_pos, item=item: self.queue_list.finish_pointer_drag(
                item, global_pos
            )
        )
        if not loading_saved_tasks:
            self._refresh_queue_avatar(entry)
            self._refresh_queue_item_styles()
        self.set_queue_item_status(entry, status, save=save)
        # Compute the size hint after stylesheet polish + final icon size are
        # applied — otherwise each row's reported height undershoots actual
        # render height by a few pixels, accumulating into visible blank space
        # at the bottom of the scrolled list.
        widget.ensurePolished()
        item.setSizeHint(ui_qt.QSize(0, widget.sizeHint().height()))
        if not loading_saved_tasks:
            self.update_clear_queue_button_state()
            self._restore_queue_scroll_position(queue_scrollbar, queue_scroll_value)
        return row

    def _restore_queue_scroll_position(self, scrollbar, value):
        """Keep queue inserts from jumping the user's current list position."""

        def restore():
            try:
                scrollbar.setValue(
                    max(scrollbar.minimum(), min(value, scrollbar.maximum()))
                )
            except RuntimeError:
                pass

        restore()
        ui_qt.QTimer.singleShot(0, restore)

    def _scroll_queue_to_bottom(self):
        def scroll():
            try:
                self.queue_list.scrollToBottom()
                scrollbar = self.queue_list.verticalScrollBar()
                scrollbar.setValue(scrollbar.maximum())
            except RuntimeError:
                pass

        scroll()
        ui_qt.QTimer.singleShot(0, scroll)
        ui_qt.QTimer.singleShot(100, scroll)

    def set_queue_item_status(self, entry, status, save=True):
        previous_status = entry.get("status")
        entry["status"] = status
        task = entry.get("task") or {}
        if status == "completed":
            if previous_status != "completed":
                entry["completed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            entry.pop("queue_order", None)
        elif tasks_schema._uses_queue_order(task, status):
            if type(entry.get("queue_order")) is not int or entry["queue_order"] < 0:
                entry["queue_order"] = self._next_queue_order()
            entry.pop("completed_at", None)
        else:
            entry.pop("queue_order", None)
            entry.pop("completed_at", None)
        self.refresh_queue_item_label(entry)
        if not getattr(self, "_loading_saved_tasks", False):
            self._refresh_queue_reorder_state()
            if status == "completed":
                self._move_entry_to_history_tail(entry)
        if save:
            self.save_task(entry)

    def _queue_entry_can_show_delete(self, entry):
        if getattr(self, "_download_stop_reason", None) == "handoff":
            return False
        status = entry.get("status")
        if tasks_schema._is_auto_patrol_task(entry.get("task")):
            return entry is not getattr(
                self, "active_patrol_entry", None
            ) and status in (
                "pending",
                "deferred",
                "completed",
                "stopped",
            )
        return status in ("pending", "stopped", "completed")

    def _queue_display_status(self, entry):
        status = entry.get("status")
        if status == "stopped":
            return "pending"
        if entry is self._current_queue_entry() and status == "running":
            if getattr(self, "_download_stop_reason", None) == "handoff":
                return "switching"
            if getattr(self, "queue_paused", False):
                return "paused"
        return status

    def _set_queue_status_icon(self, entry, hovered=None):
        """Show the inert status glyph, replaced by delete on eligible hover."""
        btn = entry.get("status_button")
        if btn is None:
            return
        if hovered is None:
            widget = entry.get("widget")
            hovered = (
                widget.is_hovered()
                if isinstance(widget, ui_widgets_queue.QueueItemWidget)
                else False
            )
        show_delete = bool(hovered and self._queue_entry_can_show_delete(entry))
        svg = (
            ui_theme_icons._QUEUE_DELETE_SVG
            if show_delete
            else ui_theme_icons._QUEUE_SVG.get(self._queue_display_status(entry))
        )
        icon = ui_theme_icons._make_svg_icon(svg) if svg else None
        if icon:
            btn.setIcon(icon)
            btn.setIconSize(
                ui_qt.QSize(
                    ui_theme_icons.DEFAULT_ICON_SIZE, ui_theme_icons.DEFAULT_ICON_SIZE
                )
            )
        else:
            btn.setIcon(ui_qt.QIcon())
        btn.setEnabled(True)
        btn.setAttribute(
            ui_qt.Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            not show_delete,
        )
        btn.setCursor(
            ui_qt.Qt.CursorShape.PointingHandCursor
            if show_delete
            else ui_qt.Qt.CursorShape.ArrowCursor
        )
        btn.setToolTip(self.tr("delete_task_tooltip") if show_delete else "")

    def refresh_queue_item_label(self, entry):
        task = entry["task"]
        if tasks_schema._is_auto_patrol_task(task):
            completed, total = tasks_schema._patrol_target_progress(entry)
            entry["label"].setText(
                self.tr(
                    "patrol_task_queue_label",
                    media=str(task.get("download_media_type") or "posts").capitalize(),
                    completed=completed,
                    total=total,
                )
            )
        else:
            entry["label"].setText(tasks_schema._format_task_label(task))
        self._refresh_queue_avatar(entry)
        date_label = entry.get("date_label")
        if date_label is not None:
            date_label.setText(tasks_schema._format_task_date(entry["task"]))

    def _nonnegative_int(self, value, default=0):
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return default

    def _task_scope_unit(self, task):
        return task["scope_unit"]

    def _task_scope_limit(self, task):
        return task["scope_limit"]

    def _task_scope_completed(self, task):
        return task["scope_completed"]

    def _set_task_scope_completed(self, task, count):
        task["scope_completed"] = min(task["scope_limit"], max(0, count))

    def _task_remaining_scope(self, task):
        return max(0, self._task_scope_limit(task) - self._task_scope_completed(task))

    def apply_download_result_to_task(self, entry, result):
        if not isinstance(result, dict):
            return

        task = entry.get("task") or {}
        downloaded_count = result.get("downloaded_count")
        if type(downloaded_count) is int and downloaded_count >= 0:
            observed = self._nonnegative_int(
                entry.get("_run_downloaded_count"),
                0,
            )
            reconciled = max(observed, downloaded_count)
            entry["_run_downloaded_count"] = reconciled
            entry["_run_downloaded_expected"] = downloaded_count
            task["downloaded_count"] = entry["_run_downloaded_start"] + reconciled

        scope_completed_count = result.get("scope_completed_count")
        if type(scope_completed_count) is int and scope_completed_count >= 0:
            run_start = self._nonnegative_int(
                entry.get("_run_completed_start"),
                self._task_scope_completed(task),
            )
            entry["_run_scope_completed_expected"] = scope_completed_count
            if (
                self._nonnegative_int(
                    entry.get("_run_scope_completed_count"),
                    0,
                )
                < scope_completed_count
            ):
                entry["_run_scope_completed_count"] = scope_completed_count
                target_count = min(
                    self._task_scope_limit(task),
                    run_start + scope_completed_count,
                )
                self._set_task_scope_completed(task, target_count)

        entry["_run_finalized"] = True

    def clear_queue(self):
        if self.is_downloading():
            self.update_clear_queue_button_state()
            return
        if not self.queue_items:
            self.update_clear_queue_button_state()
            return

        msg = self._create_standard_message_box(
            ui_qt.QMessageBox.Icon.Question,
            self.tr("confirm_clear_queue_title"),
            self.tr("confirm_clear_queue_message"),
            ui_qt.QMessageBox.StandardButton.Yes | ui_qt.QMessageBox.StandardButton.No,
            default_button=ui_qt.QMessageBox.StandardButton.No,
        )
        reply = self._exec_centered_dialog(msg)
        if reply != ui_qt.QMessageBox.StandardButton.Yes:
            return

        removed_entries = tuple(self.queue_items)

        # The task log dialog can only refer to one queue entry.  Close it once
        # before QListWidget destroys the row widgets instead of checking it
        # again for every removed task.
        log_dialog = getattr(self, "_log_dialog", None)
        if log_dialog is not None:
            try:
                log_dialog.close()
            except RuntimeError:
                pass
            self._log_dialog = None

        # Clear the in-memory model and its Qt view as one batch.  The old path
        # reused the single-row remover, which refreshed the whole queue and
        # rewrote every remaining task file after each deletion.
        self.queue_items.clear()
        self.queue_list.clear()

        # Task JSON files and resume checkpoints are stored per task, so their
        # cleanup is necessarily per entry.  This loop performs only that
        # required persistence work; there is no remaining task list to save.
        for entry in removed_entries:
            self.delete_task_file(entry)

        self.queue_paused = False
        self._thread_pipeline_stop_requested = False
        self._download_stop_reason = None
        self._queue_handoff_target_id = None
        self.queue_started = 0
        self.queue_completed = 0
        self.current_queue_index = None
        self.update_download_button_state()
        ui_console.print(f"Task queue cleared ({len(removed_entries)} items)")

    def _close_log_dialog_for_entry(self, entry):
        if hasattr(self, "_log_dialog") and self._log_dialog is not None:
            try:
                if self._log_dialog._task_entry is entry:
                    self._log_dialog.close()
                    self._log_dialog = None
            except RuntimeError:
                self._log_dialog = None

    def _adjust_queue_counts_after_removal(self, entry):
        # Only unfinished download rows were counted into this run (see
        # _count_batch_tasks); history and patrol rows never were.
        if (
            self.queue_running
            and self.queue_started > 0
            and entry["status"] in ("pending", "stopped")
            and not tasks_schema._is_auto_patrol_task(entry.get("task"))
        ):
            self.queue_started = max(self.queue_completed, self.queue_started - 1)

    def _remove_queue_entry_at(self, row):
        if row < 0 or row >= len(self.queue_items):
            return None
        removed = self.queue_items.pop(row)
        self.queue_list.takeItem(row)
        self._adjust_queue_counts_after_removal(removed)
        if self.current_queue_index is not None:
            if row < self.current_queue_index:
                self.current_queue_index -= 1
            elif row == self.current_queue_index:
                self.current_queue_index = None
        self._refresh_queue_dividers()
        self.delete_task_file(removed)
        self._close_log_dialog_for_entry(removed)
        self.update_clear_queue_button_state()
        # Other rows are unchanged; still persist the unfinished rows' queue
        # order, which loading renumbers only in memory.
        for entry in self.queue_items:
            if tasks_schema._uses_queue_order(entry["task"], entry["status"]):
                self.save_task(entry)
        ui_console.print(
            f"Removed task {tasks_schema._task_display_name(removed.get('task'))}"
        )
        return removed

    def on_queue_item_delete(self):
        sender = self.sender()
        item = sender.property("queue_item")
        if item is None:
            return
        row = self.queue_list.row(item)
        if row < 0 or row >= len(self.queue_items):
            return
        entry = self.queue_items[row]
        if self._queue_entry_can_show_delete(entry):
            self._remove_queue_entry_at(row)

    def _clear_log_dialog_reference(self, dialog):
        if getattr(self, "_log_dialog", None) is dialog:
            self._log_dialog = None

    def show_task_log_dialog(self, entry):
        """显示任务日志对话框"""
        bridge = getattr(self, "_console_capture_bridge", None)
        if bridge is not None:
            bridge.flush()
        # 关闭已有的日志对话框
        if hasattr(self, "_log_dialog") and self._log_dialog is not None:
            try:
                self._log_dialog.close()
            except RuntimeError:
                pass
        if tasks_schema._is_auto_patrol_task(entry.get("task")):
            dialog = ui_dialogs_patrol.AutoPatrolTaskDialog(self, entry)
        else:
            dialog = ui_dialogs_task.TaskLogDialog(self, entry)
        dialog.destroyed.connect(
            lambda _object=None, owner=dialog: self._clear_log_dialog_reference(owner)
        )
        self._log_dialog = dialog
        self._show_centered_dialog(dialog)

    def _refresh_open_task_dialog(self, entry=None):
        dialog = getattr(self, "_log_dialog", None)
        if dialog is None:
            return
        try:
            if entry is not None and dialog._task_entry is not entry:
                return
            if entry is not None and hasattr(dialog, "refresh_from_entry"):
                dialog.refresh_from_entry()
                return
            if hasattr(dialog, "_refresh_theme"):
                dialog._refresh_theme()
            if hasattr(dialog, "_retranslate"):
                dialog._retranslate()
        except RuntimeError:
            self._log_dialog = None

    def show_console_log_dialog(self):
        """显示当前会话的控制台日志。"""
        bridge = getattr(self, "_console_capture_bridge", None)
        if bridge is not None:
            bridge.flush()
        dialog = getattr(self, "_console_log_dialog", None)
        try:
            if dialog is not None:
                dialog.retranslate()
                dialog.refresh_theme()
                dialog.set_logs(self.console_logs)
            else:
                dialog = ui_dialogs_console.ConsoleLogDialog(self)
                self._console_log_dialog = dialog
            self._show_centered_dialog(dialog)
        except RuntimeError:
            self._console_log_dialog = ui_dialogs_console.ConsoleLogDialog(self)
            self._show_centered_dialog(self._console_log_dialog)

    def _append_console_log(self, message):
        if message is None:
            return
        line = str(message)
        self.console_logs.append(line)
        dialog = getattr(self, "_console_log_dialog", None)
        if dialog is None:
            return
        try:
            if dialog.isVisible():
                dialog.append_log(line)
        except RuntimeError:
            self._console_log_dialog = None
