"""Route captured logs and media notifications to the active task."""

from __future__ import annotations

import os

import inscrawler.tasks.schema as tasks_schema
import inscrawler.ui.console as ui_console
import inscrawler.ui.qt as ui_qt


class TaskLogController:
    """Qt view concern composed explicitly by the main window."""

    def _console_task_entry(self, task_id):
        task_id = str(task_id or "").strip()
        if not task_id:
            return None
        for entry in self.queue_items:
            task = entry.get("task") or {}
            if str(task.get("task_id") or "").strip() == task_id:
                return entry
        return None

    def _append_task_entry_log(self, entry, message):
        if entry is None or message is None:
            return
        line = str(message)
        entry["logs"].append(line)
        dialog = getattr(self, "_log_dialog", None)
        if dialog is None:
            return
        try:
            if dialog.isVisible() and dialog._task_entry is entry:
                dialog.append_log(line)
        except RuntimeError:
            self._log_dialog = None

    @ui_qt.Slot(object)
    def _on_console_capture_record(self, record):
        if not isinstance(record, dict) or "line" not in record:
            return
        line = str(record["line"])
        self._append_console_log(line)

        entry = self._console_task_entry(record.get("task_id"))
        if entry is None:
            return
        if tasks_schema._is_auto_patrol_task(entry.get("task")):
            self._on_patrol_task_log(
                entry,
                line,
                target_username=record.get("target_username"),
            )
        else:
            self._append_task_entry_log(entry, line)

    def _begin_task_console_capture(self, entry):
        if getattr(self, "_console_capture_hub", None) is None or entry is None:
            return None
        task_id = str((entry.get("task") or {}).get("task_id") or "").strip()
        if not task_id:
            return None
        ui_console._begin_console_task_scope(task_id)
        return task_id

    def _active_console_task_id(self):
        hub = getattr(self, "_console_capture_hub", None)
        return hub.task_id if hub is not None else None

    def _finish_task_console_capture(self, task_id):
        task_id = str(task_id or "").strip()
        if not task_id:
            return
        ui_console._end_console_task_scope(task_id)
        bridge = getattr(self, "_console_capture_bridge", None)
        if bridge is not None:
            bridge.flush()
        entry = self._console_task_entry(task_id)
        if entry is not None:
            self.save_task(entry)

    def _on_task_log(self, message):
        """收集日志到当前运行任务，并实时转发到日志窗口"""
        if self.current_queue_index is None:
            return
        if self.current_queue_index >= len(self.queue_items):
            return
        entry = self.queue_items[self.current_queue_index]
        self._append_task_entry_log(entry, message)

    def on_profile_picture_downloaded(self, username, image_path):
        """账号头像文件更新时，只刷新账号下拉头像，不加入媒体预览。"""
        try:
            username = str(username or "").strip().lstrip("@")
            if not username:
                return
            self._refresh_target_account_icon(username)
            ui_console.print(f"Account avatar refreshed: @{username}")
        except Exception as e:
            ui_console.print(f"Failed to refresh account avatar: {e}")

    def on_new_image_downloaded(self, image_path):
        """新文件下载完成时的实时预览"""
        try:
            notify_entry = None
            normalized = None
            # 记录到当前任务的 downloaded_files
            if self.current_queue_index is not None and self.current_queue_index < len(
                self.queue_items
            ):
                entry = self.queue_items[self.current_queue_index]
                normalized = self._normalize_preview_path(image_path)
                if normalized and normalized not in entry["downloaded_files"]:
                    entry["downloaded_files"].append(normalized)
                    notify_entry = entry

            preview_added = self._remember_preview_path(image_path)
            if notify_entry is not None:
                # 先登记首页预览，再让已打开的详情页请求缩略图。
                self._notify_log_dialog_new_image(notify_entry, normalized)

            if preview_added:
                ui_console.debug_print(f"Preview added: {os.path.basename(image_path)}")
                # 防抖：快速连续下载只在最后一次 100ms 后刷新一次
                self._refresh_timer.start()
            else:
                ui_console.print(
                    f"Skipped invalid file: {os.path.basename(image_path)}"
                )
        except Exception as e:
            ui_console.print(f"Failed to update live preview: {e}")
