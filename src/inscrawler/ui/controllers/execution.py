"""Launch queued workers and coordinate completion and handoffs."""

from __future__ import annotations

import os
import uuid

import inscrawler.diagnostics.console as diagnostics_console
import inscrawler.downloads.checkpoints as downloads_checkpoints
import inscrawler.downloads.control as downloads_control
import inscrawler.downloads.models as downloads_models
import inscrawler.downloads.runtime as downloads_runtime
import inscrawler.downloads.service as downloads_service
import inscrawler.notifications.service as notifications_service
import inscrawler.paths as app_paths
import inscrawler.tasks.schema as tasks_schema
import inscrawler.ui.console as ui_console
import inscrawler.ui.controllers.logs as ui_controllers_logs
import inscrawler.ui.preview.cell as ui_preview_cell
import inscrawler.ui.qt as ui_qt
import inscrawler.ui.workers as ui_workers


class QueueExecutionController:
    """Qt view concern composed explicitly by the main window."""

    def _count_batch_tasks(self):
        """统计当前这一轮队列应处理的任务总数"""
        runnable_statuses = ("pending", "running", "stopped")
        return sum(
            1
            for entry in self.queue_items
            if not tasks_schema._is_auto_patrol_task(entry.get("task"))
            and entry["status"] in runnable_statuses
        )

    def _shutdown_download_thread(self):
        """拆掉已结束的 DownloadThread：解绑回调、断开信号并等待退出。
        调用点：任务切换前。调用后 self.download_thread 置空，
        避免 'Destroyed while running' 告警与上一轮信号串到新任务日志。
        """
        self._clear_thread_pipeline_current_file()
        try:
            downloads_control.resume_downloads()
        except Exception:
            pass
        # Step 1: 先断开 Python 层日志回调，阻止下载模块的 print 继续往上一轮信号 emit
        ui_console._gui_log_callback = None
        try:
            diagnostics_console.log_callback = None
            downloads_runtime.runtime.file_activity_callback = None
        except Exception:
            pass

        thread = getattr(self, "download_thread", None)
        if thread is None:
            return

        # Step 2: 断开所有信号 → Qt 事件队列里残留的 emit 不再送达任何 slot
        for signal_name in (
            "log_signal",
            "message_signal",
            "image_signal",
            "profile_picture_signal",
            "file_detail_signal",
            "file_activity_signal",
            "item_completed_signal",
            "scope_completed_signal",
            "status_signal",
            "download_status_signal",
            "auth_required_signal",
            "rate_limited_signal",
            "iphone_quality_unavailable_signal",
            "finished_signal",
            "error_signal",
        ):
            sig = getattr(thread, signal_name, None)
            if sig is None:
                continue
            if signal_name == "log_signal" and not thread._gui_log_signal_connected:
                continue
            try:
                sig.disconnect()
            except (TypeError, RuntimeError):
                pass
            if signal_name == "log_signal":
                thread._gui_log_signal_connected = False

        # 自定义完成信号在 run() 返回前发出；等待线程真正退出后再释放对象。
        try:
            if thread.isRunning():
                thread.wait()
        except RuntimeError:
            pass

        # Step 4: 安排 C++ 对象清理；Python 引用置空，让下一次创建干净
        try:
            thread.deleteLater()
        except RuntimeError:
            pass
        self.download_thread = None

    def _validate_task_start(self, task):
        """Check local login inputs immediately before a queued task starts."""
        username_text = str(task.get("login_username") or self.username.text()).strip()
        if not username_text:
            self._show_message_box(
                ui_qt.QMessageBox.Icon.Warning,
                self.tr("dialog_warning_title"),
                self.tr("warning_username_required"),
            )
            return False

        session_path = os.path.join(app_paths.SESSION_DIR, username_text.lower())
        if not os.path.exists(session_path) and not self.session_cookie.text().strip():
            self._show_message_box(
                ui_qt.QMessageBox.Icon.Warning,
                self.tr("dialog_warning_title"),
                self.tr("warning_no_session", username=username_text),
            )
            return False

        if tasks_schema._is_auto_patrol_task(task) and not task.get("target_users"):
            self._show_message_box(
                ui_qt.QMessageBox.Icon.Warning,
                self.tr("dialog_warning_title"),
                self.tr("warning_no_patrol_targets"),
            )
            return False
        return True

    def start_task_by_index(self, index, apply_user_switch_delay=False):
        # 上一个任务的线程/信号必须先拆干净，否则上一轮 emit 会串到这一轮 current_queue_index
        self._shutdown_download_thread()

        self._download_stop_reason = None
        self._queue_handoff_target_id = None
        self._thread_pipeline_stop_requested = False
        self._api_status_state = "idle"
        self._download_status_state = "idle"
        self._thread_pipeline_phase = "idle"
        self._thread_pipeline_last_active_phase = "idle"

        entry = self.queue_items[index]
        task = entry["task"]
        if not self._validate_task_start(task):
            self.queue_running = False
            self.queue_paused = False
            self.current_queue_index = None
            self._finalize_queue_run()
            self.update_api_status("idle")
            self.update_download_status("idle")
            self.update_download_button_state()
            return False

        if tasks_schema._is_auto_patrol_task(task):
            self.queue_running = False
            self.queue_paused = False
            self.current_queue_index = index
            self.active_patrol_entry = entry
            self.patrol_enabled = True
            self._patrol_delay_before_start = bool(apply_user_switch_delay)
            ui_controllers_logs.TaskLogController._begin_task_console_capture(
                self, entry
            )
            self._start_patrol_thread()
            self._refresh_thread_pipeline()
            return True
        self.current_queue_index = index
        console_task_id = (
            ui_controllers_logs.TaskLogController._begin_task_console_capture(
                self, entry
            )
        )
        try:
            downloads_control.clear_stop_request()
        except Exception:
            pass

        if entry.get("logs"):
            if self._console_capture_hub is not None:
                ui_console.print("Task restarted")
            else:
                restart_log = "[gui] Task restarted"
                self._append_task_entry_log(entry, restart_log)

        remaining_scope = self._task_remaining_scope(task)
        entry["_run_completed_start"] = self._task_scope_completed(task)
        entry["_run_downloaded_start"] = task["downloaded_count"]
        entry["_run_downloaded_count"] = 0
        entry.pop("_run_downloaded_expected", None)
        entry["_run_scope_completed_count"] = 0
        entry["_run_finalized"] = False
        entry.pop("_run_scope_completed_expected", None)

        self.set_queue_item_status(entry, "running")

        if not self.queue_running:
            self.queue_paused = False
            downloads_control.resume_downloads()
            self.queue_running = True
            self.queue_started = self._count_batch_tasks()
            self.queue_completed = 0
            self._risk_run_id = uuid.uuid4().hex[:8]

        if remaining_scope <= 0:
            try:
                self.handle_task_complete(
                    success=True,
                    result={
                        "status": downloads_models.status_code.SUCCESS,
                        "downloaded_count": 0,
                        "scope_completed_count": 0,
                    },
                )
            finally:
                self._finish_task_console_capture(console_task_id)
            return True

        scope_unit = self._task_scope_unit(task)
        media_request_cap_snapshot = task["media_request_cap"]
        self.download_thread = ui_workers.DownloadThread(
            task["target_user"],
            task["download_media_type"],
            remaining_scope,
            scope_unit=scope_unit,
            media_request_cap=media_request_cap_snapshot,
            apply_user_switch_delay=apply_user_switch_delay,
            task_id=task["task_id"],
            risk_run_id=getattr(self, "_risk_run_id", None),
            queue_completed_before=self.queue_completed,
        )
        capture_from_streams = self._console_capture_hub is not None
        download_settings = dict(
            username_input=task.get("login_username")
            or self.username.text().strip()
            or None,
            session_cookie_input=self.session_cookie.text(),
            notification_input=task["notification"],
            system_notification_callback_input=self.system_notification_requested.emit,
            webhook_input=task["webhook"],
            telegram_bot_token_input=task["telegram_bot_token"],
            telegram_chat_id_input=task["telegram_chat_id"],
            download_base_path_input=task["save_path"],
            iphone_support_input=self.iphone_support,
        )
        if not capture_from_streams:
            download_settings["log_callback_input"] = (
                self.download_thread.log_signal.emit
            )
        download_settings.update(
            profile_picture_sync_input=self.profile_picture_sync_enabled,
            image_download_callback_input=self.download_thread.image_signal.emit,
            profile_picture_callback_input=self.download_thread.profile_picture_signal.emit,
            file_download_callback_input=self.download_thread.file_detail_signal.emit,
            file_activity_callback_input=self.download_thread.file_activity_signal.emit,
            item_completed_callback_input=self.download_thread.item_completed_signal.emit,
            scope_completed_callback_input=self.download_thread.scope_completed_signal.emit,
            api_status_callback_input=self.download_thread.status_signal.emit,
            download_status_callback_input=self.download_thread.download_status_signal.emit,
        )
        downloads_service.config_settings(**download_settings)
        if capture_from_streams:
            diagnostics_console.log_callback = None
        # 注入 gui.py print 日志回调
        ui_console._gui_log_callback = (
            None if capture_from_streams else self.download_thread.log_signal.emit
        )

        self.update_download_button_state()
        self.download_thread.message_signal.connect(lambda msg: ui_console.print(msg))
        self.download_thread.image_signal.connect(self.on_new_image_downloaded)
        self.download_thread.profile_picture_signal.connect(
            self.on_profile_picture_downloaded
        )
        self.download_thread.file_detail_signal.connect(self.on_new_file_detail)
        self.download_thread.file_activity_signal.connect(
            self._on_thread_pipeline_file_activity
        )
        self.download_thread.item_completed_signal.connect(
            self.on_download_item_completed
        )
        self.download_thread.scope_completed_signal.connect(
            self.on_download_scope_completed
        )
        self.download_thread.status_signal.connect(self.update_api_status)
        self.download_thread.download_status_signal.connect(self.update_download_status)
        self.download_thread.auth_required_signal.connect(
            self.download_authorization_required
        )
        self.download_thread.rate_limited_signal.connect(self.download_rate_limited)
        self.download_thread.iphone_quality_unavailable_signal.connect(
            self.download_iphone_quality_unavailable
        )
        self.download_thread.finished_signal.connect(self.download_finished)
        self.download_thread.error_signal.connect(self.download_error)
        if not capture_from_streams:
            self.download_thread.log_signal.connect(self._append_console_log)
            self.download_thread._gui_log_signal_connected = True
            self.download_thread.log_signal.connect(self._on_task_log)
        cap_text = (
            str(media_request_cap_snapshot)
            if media_request_cap_snapshot is not None
            else "disabled"
        )
        ui_console.print(
            f"Starting scope: {remaining_scope} {scope_unit}(s); "
            f"download limit: {cap_text}"
        )
        self.download_thread.start()
        self._set_queue_status_icon(entry)
        return True

    def _download_thread_is_running(self):
        thread = getattr(self, "download_thread", None)
        if thread is None:
            return False
        try:
            return thread.isRunning()
        except RuntimeError:
            return False

    def _stop_patrol_thread_for_exit(self):
        """Cooperatively stop auto patrol before destroying the GUI."""
        thread = getattr(self, "patrol_thread", None)
        self._download_stop_reason = "exit"
        self._queue_handoff_target_id = None
        self.patrol_enabled = False
        self._patrol_delay_before_start = False
        if thread is None:
            self._finish_active_patrol_task(
                "pending", "Auto patrol paused on application exit"
            )
            self._download_stop_reason = None
            return

        self._safe_exit_in_progress = True
        self._refresh_thread_pipeline()
        app = ui_qt.QApplication.instance()
        try:
            if thread.isRunning():
                try:
                    downloads_control.request_stop_downloads()
                except Exception:
                    pass
                while True:
                    try:
                        if not thread.isRunning():
                            break
                        thread.wait(100)
                    except RuntimeError:
                        break
                    if app is not None:
                        app.processEvents()
            if app is not None:
                app.processEvents()
        finally:
            self._cleanup_patrol_thread()
            self._finish_active_patrol_task(
                "pending", "Auto patrol paused on application exit"
            )
            self._download_stop_reason = None
            self._queue_handoff_target_id = None
            self._safe_exit_in_progress = False
            self._refresh_thread_pipeline()

    def _stop_download_thread_for_exit(self):
        """真实退出时协作式停止下载线程，避免 terminate 截断文件写入。"""
        thread = getattr(self, "download_thread", None)
        if thread is None:
            return

        self._safe_exit_in_progress = True
        self._download_stop_reason = "exit"
        self._thread_pipeline_stop_requested = True
        self._refresh_thread_pipeline()
        app = ui_qt.QApplication.instance()
        try:
            if thread.isRunning():
                try:
                    downloads_control.request_stop_downloads()
                except Exception:
                    pass
                while True:
                    try:
                        if not thread.isRunning():
                            break
                        thread.wait(100)
                    except RuntimeError:
                        break
                    if app is not None:
                        app.processEvents()
            if app is not None:
                app.processEvents()
        finally:
            self._safe_exit_in_progress = False
            if self.current_queue_index is not None and self.current_queue_index < len(
                self.queue_items
            ):
                entry = self.queue_items[self.current_queue_index]
                if entry.get("status") == "running":
                    self.set_queue_item_status(entry, "pending")
                    self._refresh_log_dialog_status(entry)
                self.current_queue_index = None
            self.queue_running = False
            self.queue_paused = False
            ui_console._gui_log_callback = None
            try:
                downloads_runtime.runtime.file_activity_callback = None
                downloads_control.clear_stop_request()
            except Exception:
                pass
            self._download_stop_reason = None
            self._queue_handoff_target_id = None
            self._thread_pipeline_stop_requested = False
            try:
                thread.deleteLater()
            except RuntimeError:
                pass
            self.download_thread = None
            self._clear_thread_pipeline_current_file(refresh=False)
            self._refresh_thread_pipeline()

    def is_downloading(self):
        return (
            self.queue_running
            or self._download_thread_is_running()
            or self._patrol_thread_is_active()
            or self.patrol_enabled
            or self.active_patrol_entry is not None
        )

    def _restore_deferred_tasks(self):
        """Make deferred rows unfinished again without ending the queue run."""
        for entry in getattr(self, "queue_items", []):
            if entry["status"] == "deferred":
                self.set_queue_item_status(entry, "pending")

    def _finalize_queue_run(self):
        """队列全部结束后：保留 completed，仅将 deferred 恢复为下次可运行的 pending。"""
        self._thread_pipeline_stop_requested = False
        self._restore_deferred_tasks()

    def handle_task_stopped(self, result=None):
        self._clear_thread_pipeline_current_file()
        handoff_requested = self._download_stop_reason == "handoff"
        current_entry = self._current_queue_entry()
        current_index = self._queue_index_for_entry(current_entry)
        if current_index is None:
            self.current_queue_index = None
            self.queue_paused = False
            ui_console._gui_log_callback = None
            if handoff_requested:
                self._start_queue_handoff_target()
                return
            self.queue_running = False
            self._download_stop_reason = None
            self._queue_handoff_target_id = None
            self._thread_pipeline_stop_requested = False
            try:
                downloads_control.clear_stop_request()
            except Exception:
                pass
            self.update_download_button_state()
            return

        self.current_queue_index = current_index
        self._refresh_target_account_icon(
            current_entry.get("task", {}).get("target_user")
        )
        self.apply_download_result_to_task(current_entry, result)
        completed_after_stop = (
            self._task_remaining_scope(current_entry.get("task")) == 0
        )

        if completed_after_stop:
            status = "completed"
        else:
            # A safely interrupted task is unfinished from the user's point
            # of view.  Its progress/cursor remain on the task itself.
            status = "pending"
        if completed_after_stop:
            self.queue_completed += 1
            task = current_entry.get("task") or {}
            try:
                downloads_checkpoints.delete_task_resume_checkpoint(
                    task.get("task_id"),
                    task.get("download_media_type"),
                )
            except Exception as e:
                ui_console.print(f"Failed to delete completed task resume cursor: {e}")
        self.set_queue_item_status(current_entry, status)
        self._refresh_log_dialog_status(current_entry)
        self.queue_paused = False
        self.current_queue_index = None
        ui_console._gui_log_callback = None
        if handoff_requested:
            self._start_queue_handoff_target()
            return

        self.queue_running = False
        downloads_control.resume_downloads()
        try:
            downloads_control.clear_stop_request()
        except Exception:
            pass
        self._download_stop_reason = None
        self._queue_handoff_target_id = None
        self._thread_pipeline_stop_requested = False
        self._finalize_queue_run()
        self.update_download_button_state()
        self.update_api_status("idle")
        self.update_download_status("idle")

    def handle_task_complete(
        self,
        success,
        error_msg=None,
        auth_required=False,
        blocking_failure=False,
        result=None,
    ):
        self._clear_thread_pipeline_current_file()
        handoff_requested = self._download_stop_reason == "handoff"
        if (
            self.current_queue_index is None
            or self.current_queue_index < 0
            or self.current_queue_index >= len(self.queue_items)
        ):
            if handoff_requested:
                self._start_queue_handoff_target()
            return

        current_entry = self.queue_items[self.current_queue_index]
        self._refresh_target_account_icon(
            current_entry.get("task", {}).get("target_user")
        )
        if self._safe_exit_in_progress and not success:
            self.set_queue_item_status(current_entry, "pending")
            self._refresh_log_dialog_status(current_entry)
            self.queue_running = False
            self.queue_paused = False
            downloads_control.resume_downloads()
            self.current_queue_index = None
            ui_console._gui_log_callback = None
            self.update_download_button_state()
            self.update_api_status("idle")
            self.update_download_status("idle")
            return

        if success:
            self.apply_download_result_to_task(current_entry, result)
            self.queue_completed += 1
            self.set_queue_item_status(current_entry, "completed")
            # 更新已打开的日志窗口状态
            self._refresh_log_dialog_status(current_entry)
            if handoff_requested:
                self.queue_paused = False
                self.current_queue_index = None
                ui_console._gui_log_callback = None
                self._start_queue_handoff_target()
                return
            if self._safe_exit_in_progress:
                self.queue_running = False
                self.queue_paused = False
                downloads_control.resume_downloads()
                self.current_queue_index = None
                ui_console._gui_log_callback = None
                self.update_download_button_state()
                self.update_api_status("idle")
                self.update_download_status("idle")
                return
            next_index = self._first_runnable_index()
            if next_index is not None:
                if self.queue_paused:
                    self.current_queue_index = None
                    ui_console._gui_log_callback = None
                    self.update_api_status("idle")
                    self.update_download_status("idle")
                    self.update_download_button_state()
                    return
                self.start_task_by_index(next_index, apply_user_switch_delay=True)
                return

            self.queue_running = False
            self.queue_paused = False
            downloads_control.resume_downloads()
            self.current_queue_index = None
            ui_console._gui_log_callback = None
            self._finalize_queue_run()
            self.update_download_button_state()
            self.update_api_status("idle")
            if self.queue_started > 0:
                notifications_service.notify(
                    self.tr(
                        "notify_queue_complete",
                        completed=self.queue_completed,
                        started=self.queue_started,
                    ),
                    background=True,
                )
            shutdown_hook = getattr(self, "_maybe_request_shutdown_after_queue", None)
            if callable(shutdown_hook):
                shutdown_hook()
            return

        if handoff_requested and not (auth_required or blocking_failure):
            self.set_queue_item_status(current_entry, "pending")
            self._refresh_log_dialog_status(current_entry)
            self.queue_paused = False
            self.current_queue_index = None
            ui_console._gui_log_callback = None
            self._start_queue_handoff_target()
            return

        if auth_required or blocking_failure:
            self._cancel_queue_handoff()
            self.save_task(current_entry)
            self._refresh_log_dialog_status(current_entry)
            self.set_queue_item_status(current_entry, "pending")
            self.queue_running = False
            self.queue_paused = False
            downloads_control.resume_downloads()
            self.current_queue_index = None
            ui_console._gui_log_callback = None
            self._finalize_queue_run()
            self.update_download_button_state()
            notifications_service.notify(
                self.tr("notify_queue_paused", error=error_msg), background=True
            )
            return

        # 失败时：将任务移到队列末尾标记为deferred（本轮不再重试，下次启动恢复为pending）
        failed_task = current_entry["task"]
        failed_logs = current_entry.get("logs", [])
        failed_downloaded_files = current_entry.get("downloaded_files", [])
        failed_downloaded_file_details = current_entry.get(
            "downloaded_file_details", []
        )
        failed_task_name = f"@{failed_task['target_user']} {failed_task['download_media_type'].capitalize()}"
        ui_console.print(
            f"Task failed and was deferred to the queue tail: {failed_task_name} (error: {error_msg})"
        )

        # 从当前位置删除已有条目
        self.queue_items.pop(self.current_queue_index)
        self.queue_list.takeItem(self.current_queue_index)

        # 以 deferred 状态重新添加到队列末尾（保留日志）
        self.add_task_to_queue(
            failed_task,
            "deferred",
            logs=failed_logs,
            downloaded_files=failed_downloaded_files,
            downloaded_file_details=failed_downloaded_file_details,
            save=False,
        )
        self.save_tasks()
        if self.queue_paused:
            self._restore_deferred_tasks()

        # 从头查找下一个可运行任务，因为失败条目已移动到队尾。
        next_index = self._first_runnable_index()

        if next_index is not None:
            if self.queue_paused:
                self.current_queue_index = None
                ui_console._gui_log_callback = None
                self.update_api_status("idle")
                self.update_download_status("idle")
                self.update_download_button_state()
                return
            self.start_task_by_index(next_index, apply_user_switch_delay=True)
            return

        # 没有更多可运行任务，结束本轮。
        self.queue_running = False
        self.queue_paused = False
        downloads_control.resume_downloads()
        self.current_queue_index = None
        ui_console._gui_log_callback = None
        deferred_count = sum(1 for e in self.queue_items if e["status"] == "deferred")
        self._finalize_queue_run()
        self.update_download_button_state()
        self.update_api_status("idle")
        notifications_service.notify(
            self.tr(
                "notify_queue_complete_with_deferred",
                completed=self.queue_completed,
                started=self.queue_started,
                deferred=deferred_count,
            ),
            background=True,
        )
        shutdown_hook = getattr(self, "_maybe_request_shutdown_after_queue", None)
        if callable(shutdown_hook):
            shutdown_hook()

    def _refresh_log_dialog_status(self, entry):
        """更新已打开的日志窗口的状态标签"""
        if hasattr(self, "_log_dialog") and self._log_dialog is not None:
            try:
                if (
                    self._log_dialog.isVisible()
                    and self._log_dialog._task_entry is entry
                ):
                    self._log_dialog._retranslate()
            except RuntimeError:
                self._log_dialog = None

    def _open_media_file(self, path):
        """使用系统默认程序打开媒体文件"""
        if not path or not os.path.exists(path):
            return
        ui_qt.QDesktopServices.openUrl(ui_qt.QUrl.fromLocalFile(path))

    def ensure_enough_cells(self, needed_count):
        """确保有足够的媒体单元 - 支持动态扩展"""
        while len(self.cells) < needed_count:
            cell_index = len(self.cells)
            row = cell_index // 3
            col = cell_index % 3

            # 创建新的媒体单元并设置固定尺寸
            cell = ui_preview_cell.MediaCell()
            cell.set_theme_background(self._preview_background_color())
            if hasattr(self, "cell_size"):
                cell.set_fixed_size(self.cell_size)
            cell.clicked.connect(self._open_media_file)

            self.grid.addWidget(cell, row, col)
            self.cells.append(cell)

            ui_console.debug_print(
                f"Created media cell ({row}, {col}) with total: {cell_index + 1}"
            )
