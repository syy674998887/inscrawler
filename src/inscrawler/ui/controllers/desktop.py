"""Desktop dialogs, tray notifications, and shutdown UI."""

from __future__ import annotations

import os

import inscrawler.downloads.service as downloads_service
import inscrawler.notifications.service as notifications_service
import inscrawler.paths as app_paths
import inscrawler.ui.console as ui_console
import inscrawler.ui.constants as ui_constants
import inscrawler.ui.dialogs.shutdown as ui_dialogs_shutdown
import inscrawler.ui.platform as ui_platform
import inscrawler.ui.qt as ui_qt


class DesktopController:
    """Qt view concern composed explicitly by the main window."""

    def _init_system_tray(self):
        if not ui_qt.QSystemTrayIcon.isSystemTrayAvailable():
            return

        self.tray_icon = ui_qt.QSystemTrayIcon(self)
        if os.path.exists(app_paths.ICON_FILE):
            self.tray_icon.setIcon(ui_qt.QIcon(app_paths.ICON_FILE))
        elif not self.windowIcon().isNull():
            self.tray_icon.setIcon(self.windowIcon())

        self.tray_menu = ui_qt.QMenu(self)
        self.tray_open_action = ui_qt.QAction(self)
        self.tray_open_action.triggered.connect(self.show_from_tray)
        self.tray_menu.addAction(self.tray_open_action)

        self.tray_menu.addSeparator()

        self.tray_exit_action = ui_qt.QAction(self)
        self.tray_exit_action.triggered.connect(self.request_exit_application)
        self.tray_menu.addAction(self.tray_exit_action)

        self.tray_icon.setContextMenu(self.tray_menu)
        self.tray_icon.activated.connect(self.on_tray_activated)
        self.tray_icon.show()
        self._refresh_tray_ui()

    def _refresh_tray_ui(self):
        if self.tray_icon is None:
            return
        self.tray_icon.setToolTip(self.tr("tray_tooltip"))
        if self.tray_open_action is not None:
            self.tray_open_action.setText(self.tr("tray_open"))
        if self.tray_exit_action is not None:
            self.tray_exit_action.setText(self.tr("tray_exit"))

    @ui_qt.Slot(str)
    def _show_system_notification(self, message):
        tray_icon = self.tray_icon
        if tray_icon is None or not ui_qt.QSystemTrayIcon.supportsMessages():
            ui_console.print("System notifications are unavailable")
            return
        tray_icon.showMessage(
            "inscrawler",
            str(message),
            ui_qt.QSystemTrayIcon.MessageIcon.Information,
            10000,
        )

    def on_tray_activated(self, reason):
        if reason == ui_qt.QSystemTrayIcon.ActivationReason.DoubleClick:
            if self.isVisible() and not self.isMinimized():
                self.hide()
            else:
                self.show_from_tray()

    def show_from_tray(self):
        if self.isHidden():
            self.show()
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def hide_to_tray(self):
        if self.tray_icon is None:
            return False
        self.hide()
        return True

    def set_close_behavior(self, behavior_key=None, persist=False):
        self.close_behavior = self._normalize_close_behavior_key(
            behavior_key or self.close_behavior
        )
        if persist and not getattr(self, "_loading_settings", False):
            self.save_ui_settings()

    def set_iphone_support(self, value=None, persist=False):
        self.iphone_support = self._normalize_iphone_support_value(
            value if value is not None else self.iphone_support
        )
        downloads_service.config_settings(iphone_support_input=self.iphone_support)
        if persist and not getattr(self, "_loading_settings", False):
            self.save_iphone_support_setting()

    def set_profile_picture_sync_enabled(self, value=None, persist=False):
        self.profile_picture_sync_enabled = self._normalize_iphone_support_value(
            value if value is not None else self.profile_picture_sync_enabled
        )
        downloads_service.config_settings(
            profile_picture_sync_input=self.profile_picture_sync_enabled
        )
        if persist and not getattr(self, "_loading_settings", False):
            self.save_profile_picture_sync_setting()

    def set_shutdown_after_queue(self, enabled):
        self.shutdown_after_queue = bool(enabled)
        checkbox = getattr(self, "shutdown_after_queue_check", None)
        if checkbox is None:
            return
        if checkbox.isChecked() != self.shutdown_after_queue:
            checkbox.blockSignals(True)
            checkbox.setChecked(self.shutdown_after_queue)
            checkbox.blockSignals(False)

    def _on_shutdown_after_queue_changed(self, _state):
        checkbox = getattr(self, "shutdown_after_queue_check", None)
        self.shutdown_after_queue = bool(
            checkbox.isChecked() if checkbox is not None else False
        )

    def _maybe_request_shutdown_after_queue(self):
        if not getattr(self, "shutdown_after_queue", False):
            return False
        if getattr(self, "_safe_exit_in_progress", False):
            return False
        if getattr(self, "_force_exit", False):
            return False

        if not self._ensure_system_shutdown_supported():
            self.set_shutdown_after_queue(False)
            return False

        seconds = ui_constants.SHUTDOWN_COUNTDOWN_SECONDS
        try:
            self._open_shutdown_countdown_dialog(seconds)
        except Exception as e:
            ui_console.print(f"Failed to start shutdown countdown: {e}")
            self._show_message_box(
                ui_qt.QMessageBox.Icon.Critical,
                self.tr("dialog_error_title"),
                self.tr("shutdown_countdown_start_failed", error=e),
            )
            return False
        self.set_shutdown_after_queue(False)
        ui_console.print(f"Queue finished; forced shutdown in {seconds} seconds")
        notify_message = self.tr("notify_shutdown_countdown", seconds=seconds)
        self._send_shutdown_countdown_notify(notify_message)
        return True

    def _send_shutdown_countdown_notify(self, message):
        return notifications_service.notify(message, background=True)

    def _ensure_system_shutdown_supported(self):
        if os.name != "nt":
            ui_console.print("Shutdown after completion is only supported on Windows")
            self._show_message_box(
                ui_qt.QMessageBox.Icon.Warning,
                self.tr("dialog_warning_title"),
                self.tr("shutdown_unsupported"),
            )
            return False
        return True

    def _request_system_shutdown_now(self):
        if not self._ensure_system_shutdown_supported():
            return False
        try:
            # Forced shutdown may end this process without running closeEvent.
            self.flush_preview_history()
            if self._console_capture_bridge is not None:
                self._console_capture_bridge.flush()
            self.save_tasks()
            completed = ui_platform._run_system_shutdown_command(
                ui_platform._system_shutdown_command()
            )
        except Exception as e:
            ui_console.print(f"Failed to request system shutdown: {e}")
            self._show_message_box(
                ui_qt.QMessageBox.Icon.Critical,
                self.tr("dialog_error_title"),
                self.tr("shutdown_request_failed", error=e),
            )
            return False
        if completed.returncode != 0:
            error = (
                completed.stderr or completed.stdout or ""
            ).strip() or f"exit {completed.returncode}"
            ui_console.print(f"Failed to request system shutdown: {error}")
            self._show_message_box(
                ui_qt.QMessageBox.Icon.Critical,
                self.tr("dialog_error_title"),
                self.tr("shutdown_request_failed", error=error),
            )
            return False
        return True

    def _cancel_local_shutdown_countdown(self):
        ui_console.print("Shutdown after completion cancelled")

    def _open_shutdown_countdown_dialog(self, seconds):
        self.show_from_tray()
        dialog = ui_dialogs_shutdown.ShutdownCountdownDialog(
            self,
            seconds,
            self._request_system_shutdown_now,
            self._cancel_local_shutdown_countdown,
        )
        self._shutdown_countdown_dialog = dialog

        def _clear_dialog(result=None, dialog=dialog):
            if self._shutdown_countdown_dialog is dialog:
                self._shutdown_countdown_dialog = None

        dialog.finished.connect(_clear_dialog)
        try:
            self._show_centered_dialog(dialog)
            dialog.start_countdown()
        except Exception:
            if self._shutdown_countdown_dialog is dialog:
                self._shutdown_countdown_dialog = None
            try:
                dialog.close()
            except RuntimeError:
                pass
            raise
