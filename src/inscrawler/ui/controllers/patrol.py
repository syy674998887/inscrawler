"""Create patrol tasks and reflect worker results in the queue."""

from __future__ import annotations

import uuid
from datetime import datetime

import inscrawler.diagnostics.console as diagnostics_console
import inscrawler.downloads.checkpoints as downloads_checkpoints
import inscrawler.downloads.control as downloads_control
import inscrawler.downloads.models as downloads_models
import inscrawler.downloads.runtime as downloads_runtime
import inscrawler.downloads.service as downloads_service
import inscrawler.tasks.schema as tasks_schema
import inscrawler.ui.console as ui_console
import inscrawler.ui.controllers.logs as ui_controllers_logs
import inscrawler.ui.qt as ui_qt
import inscrawler.ui.workers as ui_workers


class PatrolController:
    """Qt view concern composed explicitly by the main window."""

    def _load_patrol_target_snapshot(self):
        return list(self._selected_patrol_targets())

    def _new_patrol_state(self, targets):
        return {
            "total_downloaded": 0,
            "accounts": [
                {
                    "target_user": username,
                    "state": "waiting",
                    "checked_count": 0,
                    "updates_found_count": 0,
                    "total_downloaded_count": 0,
                    "message": "",
                }
                for username in targets
            ],
        }

    def build_patrol_task_from_ui(self, targets):
        save_path_value = self.save_path.text().strip() or self.default_save_path
        media_type = self._patrol_media_type_value()
        task = {
            "task_id": uuid.uuid4().hex[:12],
            "task_kind": "auto_patrol",
            "target_users": list(targets),
            "download_media_type": media_type,
            "save_path": save_path_value,
            "login_username": self.username.text().strip() or None,
            "notification": self.notification.currentData() or "",
            "webhook": self.webhook.text().strip() or None,
            "telegram_bot_token": self.telegram_bot_token.text().strip() or None,
            "telegram_chat_id": self.telegram_chat_id.text().strip() or None,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        if media_type in tasks_schema._PAGED_PATROL_MEDIA_TYPES:
            task["page_limit"] = self.patrol_page_limit.value()
        return task

    def _patrol_thread_is_running(self):
        thread = getattr(self, "patrol_thread", None)
        if thread is None:
            return False
        try:
            return thread.isRunning()
        except RuntimeError:
            return False

    def _patrol_thread_is_active(self):
        """True until the worker and all queued completion signals are cleaned up."""
        return getattr(self, "patrol_thread", None) is not None

    def _patrol_media_type_value(self):
        return str(self.patrol_media_type.currentData() or "posts").strip().lower()

    def add_patrol_task(self):
        """Append an immutable patrol snapshot without starting any worker."""
        targets = self._load_patrol_target_snapshot()
        if not targets:
            self._show_message_box(
                ui_qt.QMessageBox.Icon.Warning,
                self.tr("dialog_warning_title"),
                self.tr("warning_no_patrol_targets"),
            )
            return

        self.save_settings()
        task = self.build_patrol_task_from_ui(targets)
        self.add_task_to_queue(
            task,
            "pending",
            patrol_state=self._new_patrol_state(targets),
            log_targets=[],
        )
        self._scroll_queue_to_bottom()
        self.update_download_button_state()
        queued_message = (
            f"Queued auto patrol: {task['download_media_type'].capitalize()}"
        )
        if "page_limit" in task:
            queued_message += f", detection pages {task['page_limit']}"
        ui_console.print(f"{queued_message}, {len(targets)} fixed target(s)")

    def _start_patrol_thread(self):
        if not self.patrol_enabled or self._patrol_thread_is_active():
            self.update_patrol_button_state()
            return
        entry = self.active_patrol_entry
        if entry is None or entry not in self.queue_items:
            self.patrol_enabled = False
            self.active_patrol_entry = None
            self.update_patrol_button_state()
            return
        if self.queue_running:
            self.update_patrol_button_state()
            return
        if self._download_thread_is_running():
            ui_qt.QTimer.singleShot(250, self._start_patrol_thread)
            return
        task = entry.get("task") or {}
        all_targets = list(task.get("target_users") or [])
        accounts = (entry.get("patrol_state") or {}).get("accounts") or []
        targets = []
        target_indexes = []
        for target_index, username in enumerate(all_targets):
            account = accounts[target_index] if target_index < len(accounts) else {}
            if account.get("state") in tasks_schema._PATROL_COMPLETED_ACCOUNT_STATES:
                continue
            targets.append(username)
            target_indexes.append(target_index)

        if not targets:
            message = "Patrol completed: all targets were already finished"
            self._patrol_delay_before_start = False
            self.on_patrol_finished(
                {
                    "status": downloads_models.status_code.SUCCESS,
                    "message": message,
                    "downloaded_count": 0,
                }
            )
            return

        # Retire the completed manual worker before assigning patrol callbacks;
        # otherwise late queued signals from that worker could land in patrol logs.
        if getattr(self, "download_thread", None) is not None:
            self._shutdown_download_thread()

        try:
            downloads_control.clear_stop_request()
        except Exception:
            pass

        media_type = str(task.get("download_media_type") or "posts").strip().lower()
        page_limit = task.get("page_limit")
        delay_before_first_target = self._patrol_delay_before_start
        self._patrol_delay_before_start = False
        thread = ui_workers.PatrolThread(
            targets,
            media_type,
            page_limit,
            delay_before_first_target=delay_before_first_target,
            patrol_task_id=task.get("task_id"),
            target_indexes=target_indexes,
        )
        self.patrol_thread = thread

        capture_from_streams = self._console_capture_hub is not None
        download_settings = dict(
            username_input=task.get("login_username")
            or self.username.text().strip()
            or None,
            session_cookie_input=self.session_cookie.text(),
            notification_input=task.get("notification", ""),
            system_notification_callback_input=self.system_notification_requested.emit,
            webhook_input=task.get("webhook"),
            telegram_bot_token_input=task.get("telegram_bot_token"),
            telegram_chat_id_input=task.get("telegram_chat_id"),
            image_download_callback_input=thread.capture_image,
            profile_picture_callback_input=thread.profile_picture_signal.emit,
            file_download_callback_input=thread.capture_file_detail,
            file_activity_callback_input=thread.file_activity_signal.emit,
            item_completed_callback_input=thread.capture_item_completed,
            api_status_callback_input=thread.status_signal.emit,
            download_status_callback_input=thread.download_status_signal.emit,
            download_base_path_input=task.get("save_path") or self.default_save_path,
            iphone_support_input=self.iphone_support,
            profile_picture_sync_input=self.profile_picture_sync_enabled,
        )
        if not capture_from_streams:
            download_settings["log_callback_input"] = thread.capture_download_log
        downloads_service.config_settings(**download_settings)
        if capture_from_streams:
            diagnostics_console.log_callback = None
        # GUI print() may run on the main thread; keep it out of the worker's
        # per-target capture buffer while still forwarding it to patrol logs.
        ui_console._gui_log_callback = (
            None if capture_from_streams else thread.log_signal.emit
        )

        thread.message_signal.connect(lambda message: ui_console.print(message))
        thread.image_signal.connect(
            lambda path, owner=thread: (
                self.on_patrol_image_downloaded(path)
                if self.patrol_thread is owner
                else None
            )
        )
        thread.file_activity_signal.connect(self._on_thread_pipeline_file_activity)
        thread.profile_picture_signal.connect(
            lambda username, path, owner=thread, patrol_entry=entry: (
                self.on_patrol_profile_picture_downloaded(
                    patrol_entry,
                    username,
                    path,
                )
                if self.patrol_thread is owner
                and self.active_patrol_entry is patrol_entry
                else None
            )
        )
        thread.status_signal.connect(
            lambda status, owner=thread: (
                self.update_api_status(status) if self.patrol_thread is owner else None
            )
        )
        thread.download_status_signal.connect(
            lambda status, owner=thread: (
                self.update_download_status(status)
                if self.patrol_thread is owner
                else None
            )
        )
        thread.target_started_signal.connect(
            lambda event, owner=thread, patrol_entry=entry: (
                self.on_patrol_target_started(patrol_entry, event)
                if self.patrol_thread is owner
                and self.active_patrol_entry is patrol_entry
                else None
            )
        )
        thread.item_completed_signal.connect(
            lambda event, owner=thread, patrol_entry=entry: (
                self.on_patrol_item_completed(patrol_entry, event)
                if self.patrol_thread is owner
                and self.active_patrol_entry is patrol_entry
                else None
            )
        )
        thread.target_finished_signal.connect(
            lambda event, owner=thread, patrol_entry=entry: (
                self.on_patrol_target_finished(patrol_entry, event)
                if self.patrol_thread is owner
                and self.active_patrol_entry is patrol_entry
                else None
            )
        )
        thread.finished_signal.connect(
            lambda result, owner=thread: (
                self.on_patrol_finished(result) if self.patrol_thread is owner else None
            )
        )
        if not capture_from_streams:
            thread.log_signal.connect(self._append_console_log)
            thread._gui_log_signal_connected = True
            thread.log_signal.connect(
                lambda message, owner=thread, patrol_entry=entry: (
                    self._on_patrol_task_log(patrol_entry, message)
                    if self.patrol_thread is owner
                    and self.active_patrol_entry is patrol_entry
                    else None
                )
            )

        self.set_queue_item_status(entry, "running")
        self.update_download_button_state()
        started_message = f"Auto patrol started: {media_type.capitalize()}"
        if page_limit is not None:
            started_message += f", detection pages {page_limit}"
        ui_console.print(f"{started_message}, {len(targets)} remaining target(s)")
        thread.start()

    def _finish_active_patrol_task(self, queue_status, message=""):
        self._clear_thread_pipeline_current_file()
        entry = self.active_patrol_entry
        if entry is None:
            return
        entry.pop("_patrol_active_log_target", None)
        entry.pop("_patrol_target_attempts", None)
        patrol_state = entry.setdefault("patrol_state", {})
        for account in patrol_state.get("accounts") or []:
            if account.get("state") == "checking":
                account["state"] = (
                    "stopped"
                    if queue_status in ("pending", "stopped", "deferred")
                    else "failed"
                )
        self.set_queue_item_status(entry, queue_status)
        self._refresh_open_task_dialog(entry)
        if self._current_queue_entry() is entry:
            self.current_queue_index = None
        self.active_patrol_entry = None

    def _cleanup_patrol_thread(self):
        thread = getattr(self, "patrol_thread", None)
        if thread is not None:
            for signal_name in (
                "message_signal",
                "image_signal",
                "profile_picture_signal",
                "file_activity_signal",
                "status_signal",
                "download_status_signal",
                "target_started_signal",
                "item_completed_signal",
                "target_finished_signal",
                "finished_signal",
                "log_signal",
            ):
                signal = getattr(thread, signal_name, None)
                if signal is None:
                    continue
                if signal_name == "log_signal" and not thread._gui_log_signal_connected:
                    continue
                try:
                    signal.disconnect()
                except (TypeError, RuntimeError):
                    pass
                if signal_name == "log_signal":
                    thread._gui_log_signal_connected = False
            try:
                if thread.isRunning():
                    thread.wait()
            except RuntimeError:
                pass
            try:
                thread.deleteLater()
            except RuntimeError:
                pass
        self.patrol_thread = None
        self._clear_thread_pipeline_current_file(refresh=False)
        self._thread_pipeline_stop_requested = False
        ui_console._gui_log_callback = None
        try:
            diagnostics_console.log_callback = None
            downloads_runtime.runtime.file_activity_callback = None
            downloads_control.clear_stop_request()
        except Exception:
            pass
        self._refresh_thread_pipeline()

    def _patrol_account_for_entry(self, entry, username=None, target_index=None):
        key = str(username or "").strip().lstrip("@").casefold()
        accounts = (entry.get("patrol_state") or {}).get("accounts") or []
        if type(target_index) is int and 0 <= target_index < len(accounts):
            account = accounts[target_index]
            account_key = str(account.get("target_user") or "").casefold()
            if not key or account_key == key:
                return account
        for account in accounts:
            if str(account.get("target_user") or "").casefold() == key:
                return account
        return None

    def _patrol_nonnegative_int(self, value):
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    def _sync_patrol_total_downloaded(self, entry):
        patrol_state = entry.setdefault("patrol_state", {})
        patrol_state["total_downloaded"] = sum(
            self._patrol_nonnegative_int(account.get("total_downloaded_count"))
            for account in patrol_state.get("accounts") or []
            if isinstance(account, dict)
        )
        return patrol_state["total_downloaded"]

    def _patrol_downloaded_path_keys(self, entry):
        keys = entry.get("_patrol_downloaded_path_keys")
        if keys is None:
            keys = {
                self._preview_path_key(path)
                for path in entry.get("downloaded_files", [])
                if path
            }
            entry["_patrol_downloaded_path_keys"] = keys
        return keys

    def _merge_patrol_completed_media(self, entry, event):
        """Persist complete patrol-item media and notify an open task preview."""
        changed = False
        target_username = str(event.get("target_username") or "").strip().lstrip("@")
        downloaded_files = entry.setdefault("downloaded_files", [])
        existing_paths = self._patrol_downloaded_path_keys(entry)
        for path in event.get("downloaded_files") or []:
            normalized = self._normalize_preview_path(path)
            path_key = self._preview_path_key(normalized)
            if path_key and path_key not in existing_paths:
                existing_paths.add(path_key)
                downloaded_files.append(normalized)
                self._notify_log_dialog_new_image(
                    entry,
                    normalized,
                    target_username=target_username or None,
                )
                changed = True

        details = entry.setdefault("downloaded_file_details", [])
        detail_indexes = {
            self._preview_path_key(value.get("path")): index
            for index, value in enumerate(details)
            if isinstance(value, dict) and value.get("path")
        }
        for detail in event.get("downloaded_file_details") or []:
            if not isinstance(detail, dict):
                continue
            payload = dict(detail)
            normalized = self._normalize_preview_path(payload.get("path"))
            path_key = self._preview_path_key(normalized)
            if normalized:
                payload["path"] = normalized
            if path_key in detail_indexes:
                index = detail_indexes[path_key]
                if details[index] != payload:
                    details[index] = payload
                    changed = True
            else:
                if path_key:
                    detail_indexes[path_key] = len(details)
                details.append(payload)
                changed = True
        return changed

    def _on_patrol_task_log(
        self, entry, message, target_username=ui_console._LOG_TARGET_UNSET
    ):
        if message is None:
            return
        line = str(message)
        logs = entry["logs"]
        owners = entry["log_targets"]
        if target_username is ui_console._LOG_TARGET_UNSET:
            target_username = entry.get("_patrol_active_log_target")
        owner = str(target_username or "").strip().lstrip("@").strip()
        logs.append(line)
        owners.append(owner or None)
        dialog = getattr(self, "_log_dialog", None)
        if dialog is None:
            return
        try:
            if dialog.isVisible() and dialog._task_entry is entry:
                if hasattr(dialog, "append_log"):
                    dialog.append_log(line, target_username=owner or None)
        except RuntimeError:
            self._log_dialog = None

    def on_patrol_image_downloaded(self, image_path):
        """Refresh the main preview; completed item events persist patrol media."""
        try:
            if self._remember_preview_path(image_path):
                self._refresh_timer.start()
        except Exception as e:
            ui_console.print(f"Failed to update patrol preview: {e}")

    def on_patrol_profile_picture_downloaded(self, entry, username, image_path):
        """Refresh patrol avatars without treating the profile file as media."""
        if entry not in self.queue_items:
            return
        try:
            username = str(username or "").strip().lstrip("@")
            if not username:
                return
            if self._patrol_account_for_entry(entry, username) is None:
                return
            task = entry.get("task") or {}
            self._refresh_target_account_icon(username, task.get("save_path"))
            self._refresh_open_task_dialog(entry)
            ui_console.print(f"Patrol avatar refreshed: @{username}")
        except Exception as e:
            ui_console.print(f"Failed to refresh patrol avatar: {e}")

    def on_patrol_target_started(self, entry, event):
        if entry not in self.queue_items or not isinstance(event, dict):
            return
        username = str(event.get("target_username") or "").strip().lstrip("@")
        if username:
            entry["_patrol_active_log_target"] = username
        else:
            entry.pop("_patrol_active_log_target", None)
        # A patrol worker spans multiple accounts.  Do not carry the previous
        # account's last transferred filename into the next account.
        self._clear_thread_pipeline_current_file()
        account = self._patrol_account_for_entry(
            entry,
            username,
            event.get("target_index"),
        )
        if account is not None:
            account_username = (
                str(account.get("target_user") or username).strip().lstrip("@")
            )
            entry.setdefault("_patrol_target_attempts", {})[
                account_username.casefold()
            ] = {
                "baseline": self._patrol_nonnegative_int(
                    account.get("total_downloaded_count")
                ),
                "completed": 0,
            }
            account["state"] = "checking"
            account["checked_count"] = 0
            account["updates_found_count"] = 0
            account["message"] = ""
        self.refresh_queue_item_label(entry)
        self._refresh_open_task_dialog(entry)

    def on_patrol_item_completed(self, entry, event):
        """Publish one fully completed patrol item before the target pass ends."""
        if entry not in self.queue_items or not isinstance(event, dict):
            return
        changed = self._merge_patrol_completed_media(entry, event)
        username = str(event.get("target_username") or "").strip().lstrip("@")
        account = self._patrol_account_for_entry(
            entry,
            username,
            event.get("target_index"),
        )
        if account is not None:
            account_username = (
                str(account.get("target_user") or username).strip().lstrip("@")
            )
            attempts = entry.setdefault("_patrol_target_attempts", {})
            attempt = attempts.setdefault(
                account_username.casefold(),
                {
                    "baseline": self._patrol_nonnegative_int(
                        account.get("total_downloaded_count")
                    ),
                    "completed": 0,
                },
            )
            attempt["completed"] = (
                self._patrol_nonnegative_int(attempt.get("completed")) + 1
            )
            account["total_downloaded_count"] = (
                self._patrol_nonnegative_int(attempt.get("baseline"))
                + attempt["completed"]
            )
            self._sync_patrol_total_downloaded(entry)
            dialog = getattr(self, "_log_dialog", None)
            try:
                if (
                    dialog is not None
                    and dialog._task_entry is entry
                    and hasattr(dialog, "refresh_target")
                ):
                    dialog.refresh_target(account_username, account)
            except RuntimeError:
                self._log_dialog = None
            changed = True
        if changed:
            self.save_task(entry)

    def on_patrol_target_finished(self, entry, event):
        if entry not in self.queue_items or not isinstance(event, dict):
            return
        entry.pop("_patrol_active_log_target", None)
        # Clear before the inter-account delay so the completed account's
        # filename is not presented as activity for the next account.
        self._clear_thread_pipeline_current_file()
        username = str(event.get("target_username") or "").strip().lstrip("@")
        downloaded_count = self._patrol_nonnegative_int(event.get("downloaded_count"))
        item_status = event.get("status", downloads_models.status_code.FAILED)

        account = self._patrol_account_for_entry(
            entry,
            username,
            event.get("target_index"),
        )
        if account is not None:
            account_username = (
                str(account.get("target_user") or username).strip().lstrip("@")
            )
            attempts = entry.get("_patrol_target_attempts")
            attempt = (
                attempts.pop(account_username.casefold(), None)
                if isinstance(attempts, dict)
                else None
            )
            if isinstance(attempt, dict):
                cumulative_downloaded = self._patrol_nonnegative_int(
                    attempt.get("baseline")
                ) + max(
                    downloaded_count,
                    self._patrol_nonnegative_int(attempt.get("completed")),
                )
            else:
                cumulative_downloaded = (
                    self._patrol_nonnegative_int(account.get("total_downloaded_count"))
                    + downloaded_count
                )
            incomplete_result = any(
                self._patrol_nonnegative_int(event.get(key))
                for key in ("download_failed_count", "unresolved_count")
            )
            if (
                item_status == downloads_models.status_code.SUCCESS
                and not incomplete_result
            ):
                if cumulative_downloaded:
                    account["state"] = "updated"
                elif event.get("no_updates_confirmed"):
                    account["state"] = "no_updates"
                else:
                    account["state"] = "failed"
            elif item_status in (
                downloads_models.status_code.STOPPED,
                downloads_models.status_code.AUTHORIZATION_REQUIRED,
                downloads_models.status_code.RATE_LIMITED,
                downloads_models.status_code.IPHONE_QUALITY_UNAVAILABLE,
            ):
                account["state"] = "stopped"
            else:
                account["state"] = "failed"
            for key in ("checked_count", "updates_found_count"):
                account[key] = self._patrol_nonnegative_int(event.get(key))
            account["total_downloaded_count"] = cumulative_downloaded
            account["message"] = str(event.get("message") or "")

        attempts = entry.get("_patrol_target_attempts")
        if isinstance(attempts, dict) and not attempts:
            entry.pop("_patrol_target_attempts", None)
        self._sync_patrol_total_downloaded(entry)

        self.refresh_queue_item_label(entry)
        self._refresh_open_task_dialog(entry)
        self.save_task(entry)

    def on_patrol_finished(self, result):
        hub = getattr(self, "_console_capture_hub", None)
        entry = getattr(self, "active_patrol_entry", None)
        task_id = (
            str(((entry or {}).get("task") or {}).get("task_id") or "").strip() or None
        )
        if hub is None or hub.task_id != task_id:
            task_id = None
        try:
            return PatrolController._handle_patrol_finished(self, result)
        finally:
            if task_id:
                ui_controllers_logs.TaskLogController._finish_task_console_capture(
                    self, task_id
                )

    def _handle_patrol_finished(self, result):
        result = result if isinstance(result, dict) else {}
        status = result.get("status", downloads_models.status_code.FAILED)
        message = result.get("message", "Patrol stopped")
        entry = self.active_patrol_entry
        was_queue_paused = getattr(self, "queue_paused", False)
        handoff_requested = getattr(self, "_download_stop_reason", None) == "handoff"
        allow_handoff = self.patrol_enabled and not self._safe_exit_in_progress
        self.queue_paused = False
        downloads_control.resume_downloads()
        self._cleanup_patrol_thread()
        self.update_api_status("idle")
        self.update_download_status("idle")
        self.patrol_enabled = False

        if (
            self._safe_exit_in_progress
            and status != downloads_models.status_code.SUCCESS
        ):
            self._cancel_queue_handoff()
            self._finish_active_patrol_task("pending", message)
            return

        if status == downloads_models.status_code.SUCCESS:
            if entry is not None:
                task = entry.get("task") or {}
                try:
                    downloads_checkpoints.delete_patrol_resume_checkpoints(
                        task.get("task_id"),
                        task.get("download_media_type"),
                        len(task.get("target_users") or []),
                    )
                except Exception as e:
                    ui_console.print(f"Failed to clean completed patrol cursors: {e}")
            self._finish_active_patrol_task("completed", message)
            ui_console.print(message)
            blocking_failure = False
        elif status == downloads_models.status_code.AUTHORIZATION_REQUIRED:
            self._finish_active_patrol_task("deferred", message)
            self._move_entry_to_queue_tail(entry)
            self.update_api_status("auth_required")
            full_message = self.tr("auth_required_message", error=message)
            ui_console.print(full_message)
            self._show_message_box(
                ui_qt.QMessageBox.Icon.Critical,
                self.tr("dialog_auth_required_title"),
                full_message,
            )
            blocking_failure = True
        elif status == downloads_models.status_code.RATE_LIMITED:
            self._finish_active_patrol_task("deferred", message)
            self._move_entry_to_queue_tail(entry)
            self.update_api_status("rate_limited")
            full_message = self.tr("rate_limited_message", error=message)
            ui_console.print(full_message)
            self._show_message_box(
                ui_qt.QMessageBox.Icon.Critical,
                self.tr("dialog_rate_limited_title"),
                full_message,
            )
            blocking_failure = True
        elif status == downloads_models.status_code.IPHONE_QUALITY_UNAVAILABLE:
            self._finish_active_patrol_task("deferred", message)
            self._move_entry_to_queue_tail(entry)
            self.update_api_status("iphone_quality_unavailable")
            full_message = self.tr("iphone_quality_unavailable_message", error=message)
            ui_console.print(full_message)
            self._show_message_box(
                ui_qt.QMessageBox.Icon.Critical,
                self.tr("dialog_iphone_quality_title"),
                full_message,
            )
            blocking_failure = True
        elif result.get("blocking_failure"):
            self._finish_active_patrol_task("pending", message)
            self.update_api_status("error")
            ui_console.print(f"Patrol blocked for this queue run: {message}")
            blocking_failure = True
        elif status == downloads_models.status_code.STOPPED:
            self._finish_active_patrol_task("pending", message)
            ui_console.print(message)
            if handoff_requested:
                self._start_queue_handoff_target()
                return
            if (
                was_queue_paused
                and not self._safe_exit_in_progress
                and self._first_runnable_index() is not None
            ):
                self.queue_paused = True
                self._restore_deferred_tasks()
                self.update_download_button_state()
                return
            self._finalize_queue_run()
            self.update_download_button_state()
            return
        else:
            self._finish_active_patrol_task(
                "pending" if handoff_requested else "deferred",
                message,
            )
            if not handoff_requested:
                self._move_entry_to_queue_tail(entry)
            self.update_api_status("error")
            ui_console.print(message)
            blocking_failure = False

        if handoff_requested:
            if blocking_failure:
                self._cancel_queue_handoff()
            else:
                self._start_queue_handoff_target()
                return

        if (
            was_queue_paused
            and not blocking_failure
            and not self._safe_exit_in_progress
        ):
            self.queue_paused = True
            self._restore_deferred_tasks()
            if self._first_runnable_index() is not None:
                self.update_download_button_state()
                return
            self.queue_paused = False

        next_index = (
            None
            if blocking_failure or not allow_handoff
            else self._first_runnable_index()
        )
        if next_index is not None:
            self.start_task_by_index(
                next_index,
                apply_user_switch_delay=True,
            )
            return

        self._finalize_queue_run()

        self.update_download_button_state()
        shutdown_hook = getattr(self, "_maybe_request_shutdown_after_queue", None)
        if not blocking_failure and next_index is None and callable(shutdown_hook):
            shutdown_hook()
