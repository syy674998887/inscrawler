"""Queue start, pause, stop, and task-handoff interactions."""

from __future__ import annotations

import inscrawler.downloads.control as downloads_control
import inscrawler.tasks.schema as tasks_schema
import inscrawler.ui.console as ui_console
import inscrawler.ui.qt as ui_qt


class QueueControls:
    """Qt view concern composed explicitly by the main window."""

    def update_patrol_button_state(self):
        if not hasattr(self, "patrol_btn"):
            return
        self.patrol_btn.setText(self.tr("add_patrol_task"))
        self.patrol_btn.setEnabled(
            not self._safe_exit_in_progress and not self._force_exit
        )
        variant = "primary"
        if self.patrol_btn.property("variant") != variant:
            self.patrol_btn.setProperty("variant", variant)
            self.patrol_btn.style().unpolish(self.patrol_btn)
            self.patrol_btn.style().polish(self.patrol_btn)
        controls_enabled = not self._safe_exit_in_progress and not self._force_exit
        self.patrol_media_type.setEnabled(controls_enabled)
        page_limit_enabled = (
            controls_enabled
            and self._patrol_media_type_value()
            in tasks_schema._PAGED_PATROL_MEDIA_TYPES
        )
        self.patrol_page_limit_label.setEnabled(page_limit_enabled)
        self.patrol_page_limit.setEnabled(page_limit_enabled)
        self.patrol_targets_btn.setEnabled(controls_enabled)

    def start_download(self):
        """唯一任务按钮：启动队首任务，或控制当前根任务。"""
        if self._safe_exit_in_progress or self._force_exit:
            return
        if self._download_stop_reason is not None:
            return
        patrol_active = (
            self.active_patrol_entry is not None or self._patrol_thread_is_active()
        )
        if self.queue_paused:
            self.resume_download_queue()
            return
        if self.patrol_enabled or patrol_active:
            if not self.patrol_enabled:
                return
            self.pause_download_queue()
            return
        if self.queue_running:
            self.pause_download_queue()
            return

        if self._download_thread_is_running():
            return

        idx = self._first_runnable_index()
        if idx is not None:
            entry = self.queue_items[idx]
            self.queue_paused = False
            downloads_control.resume_downloads()
            started = self.start_task_by_index(idx)
            self.update_download_button_state()
            if started:
                ui_console.print(
                    f"Starting {tasks_schema._task_display_name(entry.get('task'))}"
                )
            return

        msg = self._create_standard_message_box(
            ui_qt.QMessageBox.Icon.Warning,
            self.tr("dialog_warning_title"),
            self.tr("warning_no_pending_tasks"),
            ui_qt.QMessageBox.StandardButton.Ok,
        )
        self._exec_centered_dialog(msg)

    def pause_download_queue(self):
        patrol_active = bool(
            getattr(self, "patrol_enabled", False)
            and (
                getattr(self, "active_patrol_entry", None) is not None
                or (
                    hasattr(self, "_patrol_thread_is_active")
                    and self._patrol_thread_is_active()
                )
            )
        )
        if (
            not (self.queue_running or patrol_active)
            or self.queue_paused
            or self._download_stop_reason is not None
        ):
            return
        self.queue_paused = True
        downloads_control.pause_downloads()
        self._restore_deferred_tasks()
        self.update_download_button_state()
        ui_console.print("Download pause requested")

    def _begin_queue_handoff(self, active_entry, target_entry):
        """Safely yield the paused serial worker to the selected queue row."""
        if (
            active_entry is None
            or target_entry is None
            or target_entry is active_entry
            or active_entry.get("status") != "running"
        ):
            return False
        target_id = str((target_entry.get("task") or {}).get("task_id") or "")
        if not target_id:
            return False

        patrol_handoff = active_entry is getattr(self, "active_patrol_entry", None)
        self.queue_paused = False
        self._download_stop_reason = "handoff"
        self._queue_handoff_target_id = target_id
        self._thread_pipeline_stop_requested = True
        if patrol_handoff:
            self.patrol_enabled = False
            self._patrol_delay_before_start = False
        # Wake a worker already waiting at a pause checkpoint so it observes
        # Stop immediately. Work started before Pause may still finish first.
        downloads_control.request_stop_downloads()

        self.update_download_button_state()
        ui_console.print("Queue task switch requested: stopping at the next safe point")
        return True

    def _cancel_queue_handoff(self):
        if self._download_stop_reason != "handoff":
            return False
        self._download_stop_reason = None
        self._queue_handoff_target_id = None
        self._thread_pipeline_stop_requested = False
        self.queue_paused = False
        downloads_control.resume_downloads()
        try:
            downloads_control.clear_stop_request()
        except Exception:
            pass
        return True

    def _start_queue_handoff_target(self):
        """Start the row snapshotted when Start Task was pressed."""
        if self._download_stop_reason != "handoff":
            return False
        target_id = self._queue_handoff_target_id
        self._download_stop_reason = None
        self._queue_handoff_target_id = None
        self._thread_pipeline_stop_requested = False
        self.queue_paused = False
        downloads_control.resume_downloads()
        try:
            downloads_control.clear_stop_request()
        except Exception:
            pass

        def can_start(entry):
            return bool(
                tasks_schema._is_runnable_task(entry.get("task"), entry.get("status"))
            )

        target_index = self._queue_index_for_task_id(target_id)
        if target_index is not None:
            target_entry = self.queue_items[target_index]
            if not can_start(target_entry):
                target_index = None
        if target_index is not None:
            self.start_task_by_index(
                target_index,
                apply_user_switch_delay=True,
            )
            return True

        self.queue_running = False
        self.current_queue_index = None
        self._finalize_queue_run()
        self.update_api_status("idle")
        self.update_download_status("idle")
        self.update_download_button_state()
        return True

    def resume_download_queue(self):
        patrol_active = bool(
            getattr(self, "patrol_enabled", False)
            and (
                getattr(self, "active_patrol_entry", None) is not None
                or (
                    hasattr(self, "_patrol_thread_is_active")
                    and self._patrol_thread_is_active()
                )
            )
        )
        if not self.queue_paused or self._download_stop_reason is not None:
            return

        active_entry = self._current_queue_entry()
        if active_entry is not None and active_entry.get("status") == "running":
            first_entry = self._first_paused_queue_candidate()
            if first_entry is not None and first_entry is not active_entry:
                self._begin_queue_handoff(active_entry, first_entry)
                return

        self.queue_paused = False
        downloads_control.resume_downloads()
        ui_console.print("Download resume requested")

        if patrol_active:
            self.update_download_button_state()
            return

        if not self._download_thread_is_running():
            next_index = self._first_runnable_index()
            if next_index is not None:
                self.start_task_by_index(next_index, apply_user_switch_delay=True)
                return
            self.queue_running = False
            self.current_queue_index = None
            ui_console._gui_log_callback = None
            self._finalize_queue_run()
            self.update_api_status("idle")
            self.update_download_status("idle")

        self.update_download_button_state()

    def update_download_button_state(self):
        """Update the single control for the currently active root task."""
        is_running = self.queue_running
        switching = self._download_stop_reason == "handoff"
        patrol_active = (
            self.active_patrol_entry is not None or self._patrol_thread_is_active()
        )
        active_task = is_running or patrol_active or self.queue_paused
        self.download_btn.setEnabled(
            not switching
            and not self._safe_exit_in_progress
            and not self._force_exit
            and (not patrol_active or self.patrol_enabled)
        )
        variant = (
            "busy"
            if switching or (active_task and not self.queue_paused)
            else "primary"
        )

        if switching:
            self.download_btn.setText(self.tr("switching_task"))
        elif active_task and self.queue_paused:
            self.download_btn.setText(self.tr("start_download"))
        elif active_task:
            self.download_btn.setText(self.tr("pause_download"))
        else:
            self.download_btn.setText(self.tr("start_download"))

        if self.download_btn.property("variant") != variant:
            self.download_btn.setProperty("variant", variant)
            self.download_btn.style().unpolish(self.download_btn)
            self.download_btn.style().polish(self.download_btn)
        self._refresh_thread_pipeline()
        self._refresh_queue_reorder_state()
        self.update_clear_queue_button_state()
        self.update_patrol_button_state()

    def update_clear_queue_button_state(self):
        if not hasattr(self, "clear_queue_btn"):
            return
        self.clear_queue_btn.setEnabled(
            bool(self.queue_items) and not self.is_downloading()
        )
