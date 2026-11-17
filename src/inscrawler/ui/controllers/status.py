"""Translate task and download status into desktop UI state."""

from __future__ import annotations

import os

import inscrawler.downloads.models as downloads_models
import inscrawler.ui.console as ui_console
import inscrawler.ui.qt as ui_qt
import inscrawler.ui.widgets.pipeline as ui_widgets_pipeline


class StatusController:
    """Qt view concern composed explicitly by the main window."""

    @ui_qt.Slot(dict)
    def _on_thread_pipeline_file_activity(self, event):
        owner = self.sender()
        if owner is None or (
            owner is not getattr(self, "download_thread", None)
            and owner is not getattr(self, "patrol_thread", None)
        ):
            return
        if not isinstance(event, dict):
            return
        event_type = event.get("event")
        if event_type == "started":
            filename = os.path.basename(str(event.get("path") or ""))
            if not filename:
                return
        elif event_type == "finished":
            # A queued started/finished pair can be consumed before Qt paints
            # even once.  Keep the last started filename visible until the
            # next file replaces it or an explicit task lifecycle boundary
            # clears it.
            return
        else:
            return
        if filename == getattr(self, "_thread_pipeline_current_file_name", ""):
            return
        self._thread_pipeline_current_file_name = filename
        self._refresh_thread_pipeline()

    def _refresh_thread_pipeline(self):
        pipeline = getattr(self, "thread_pipeline", None)
        if pipeline is None:
            return

        running = self._thread_pipeline_is_running()
        paused = bool(self.queue_paused and running)
        stopping = bool(
            running
            and (self._safe_exit_in_progress or self._thread_pipeline_stop_requested)
        )
        current_entry = None
        patrol_active = self._patrol_thread_is_active()
        if patrol_active:
            patrol_entry = getattr(self, "active_patrol_entry", None)
            if isinstance(patrol_entry, dict):
                current_entry = patrol_entry
        if current_entry is None and running:
            current_index = getattr(self, "current_queue_index", None)
            queue_items = getattr(self, "queue_items", ())
            if isinstance(current_index, int) and 0 <= current_index < len(queue_items):
                candidate = queue_items[current_index]
                if isinstance(candidate, dict):
                    if candidate.get("status") == "running":
                        current_entry = candidate
        current_file = ""
        if current_entry is not None:
            current_file = getattr(
                self,
                "_thread_pipeline_current_file_name",
                "",
            )
        state = ui_widgets_pipeline._build_thread_pipeline_state(
            running=running,
            paused=paused,
            stopping=stopping,
            phase=self._thread_pipeline_phase,
            api_status=self._api_status_state,
            download_status=self._download_status_state,
            translate=self.tr,
            api_status_meta=self._API_STATUS_META,
            download_status_meta=self._DOWNLOAD_STATUS_META,
            current_file=current_file,
        )
        pipeline.set_pipeline(**state)

    def update_api_status(self, api_type):
        self._api_status_state = api_type or "idle"
        if self._api_status_state == "idle":
            if self._download_status_state == "idle":
                self._thread_pipeline_phase = "idle"
        elif self._api_status_state in self._PREPARE_API_STATUSES:
            self._thread_pipeline_phase = "prepare"
            self._thread_pipeline_last_active_phase = "prepare"
        elif (
            self._api_status_state in self._TERMINAL_API_STATUSES
            and self._thread_pipeline_last_active_phase in ("prepare", "download")
        ):
            self._thread_pipeline_phase = self._thread_pipeline_last_active_phase
        else:
            self._thread_pipeline_phase = "fetch"
            self._thread_pipeline_last_active_phase = "fetch"
        self._refresh_thread_pipeline()

    def update_download_status(self, download_type):
        self._download_status_state = download_type or "idle"
        if self._download_status_state == "idle":
            self._thread_pipeline_phase = "idle"
        else:
            self._thread_pipeline_phase = "download"
            self._thread_pipeline_last_active_phase = "download"
        self._refresh_thread_pipeline()

    def showEvent(self, event):
        super().showEvent(event)
        self._update_window_frame()
        ui_qt.QTimer.singleShot(0, self, self._apply_window_icon)
        ui_qt.QTimer.singleShot(0, self._sync_system_title_bar)

    def download_finished(self, result):
        """下载完成"""
        task_id = self._active_console_task_id()
        try:
            if (
                isinstance(result, dict)
                and result.get("status") == downloads_models.status_code.STOPPED
            ):
                self.handle_task_stopped(result)
                return
            if isinstance(result, dict) and result.get("blocking_failure"):
                message = result.get("message", "Download could not start")
                ui_console.print(f"Download blocked for this queue run: {message}")
                self.update_api_status("error")
                self.handle_task_complete(
                    success=False,
                    error_msg=message,
                    blocking_failure=True,
                )
                return
            self.handle_task_complete(success=True, result=result)
        finally:
            self._finish_task_console_capture(task_id)

    def download_authorization_required(self, error_msg):
        """身份验证失败时暂停队列，等待人工处理"""
        task_id = self._active_console_task_id()
        full_message = self.tr("auth_required_message", error=error_msg)
        try:
            ui_console.print(full_message)
            self.update_api_status("auth_required")
            self.handle_task_complete(
                success=False, error_msg=full_message, blocking_failure=True
            )
        finally:
            self._finish_task_console_capture(task_id)
        if self._safe_exit_in_progress:
            return
        self._show_message_box(
            ui_qt.QMessageBox.Icon.Critical,
            self.tr("dialog_auth_required_title"),
            full_message,
        )

    def download_rate_limited(self, error_msg):
        """Instagram API 明确暂时限制访问时立即停止本轮队列。"""
        task_id = self._active_console_task_id()
        full_message = self.tr("rate_limited_message", error=error_msg)
        try:
            ui_console.print(full_message)
            self.update_api_status("rate_limited")
            self.handle_task_complete(
                success=False,
                error_msg=full_message,
                blocking_failure=True,
            )
        finally:
            self._finish_task_console_capture(task_id)
        if self._safe_exit_in_progress:
            return
        self._show_message_box(
            ui_qt.QMessageBox.Icon.Critical,
            self.tr("dialog_rate_limited_title"),
            full_message,
        )

    def download_iphone_quality_unavailable(self, error_msg):
        """iPhone 画质不可用时暂停队列，避免静默回退普通画质。"""
        task_id = self._active_console_task_id()
        full_message = self.tr("iphone_quality_unavailable_message", error=error_msg)
        try:
            ui_console.print(full_message)
            self.update_api_status("iphone_quality_unavailable")
            self.handle_task_complete(
                success=False, error_msg=full_message, blocking_failure=True
            )
        finally:
            self._finish_task_console_capture(task_id)
        if self._safe_exit_in_progress:
            return
        self._show_message_box(
            ui_qt.QMessageBox.Icon.Critical,
            self.tr("dialog_iphone_quality_title"),
            full_message,
        )

    def download_error(self, error_msg):
        """下载错误"""
        task_id = self._active_console_task_id()
        try:
            ui_console.print(f"Download error: {error_msg}")
            self.update_api_status("error")
            self.handle_task_complete(success=False, error_msg=error_msg)
        finally:
            self._finish_task_console_capture(task_id)
