from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import inscrawler.downloads.runtime as downloads_runtime
import inscrawler.notifications.service as notifications_service
import inscrawler.ui.console as ui_console
import inscrawler.ui.controllers.desktop as ui_controllers_desktop
import inscrawler.ui.qt as ui_qt


class SystemNotificationTests(unittest.TestCase):
    def test_download_notifier_routes_system_messages_to_callback(self):
        callback = Mock()
        with (
            patch.object(downloads_runtime.runtime, "notification_type", "system"),
            patch.object(
                downloads_runtime.runtime, "system_notification_callback", callback
            ),
        ):
            notifications_service.notify("Queue completed")

        callback.assert_called_once_with("Queue completed")

    def test_gui_uses_existing_tray_icon_for_system_message(self):
        tray_icon = Mock()
        target = SimpleNamespace(tray_icon=tray_icon)

        with patch.object(
            ui_qt.QSystemTrayIcon,
            "supportsMessages",
            return_value=True,
        ):
            ui_controllers_desktop.DesktopController._show_system_notification(
                target, "Queue completed"
            )

        tray_icon.showMessage.assert_called_once_with(
            "inscrawler",
            "Queue completed",
            ui_qt.QSystemTrayIcon.MessageIcon.Information,
            10000,
        )

    def test_gui_skips_message_when_system_notifications_are_unavailable(self):
        tray_icon = Mock()
        target = SimpleNamespace(tray_icon=tray_icon)

        with (
            patch.object(
                ui_qt.QSystemTrayIcon,
                "supportsMessages",
                return_value=False,
            ),
            patch.object(ui_console, "print"),
        ):
            ui_controllers_desktop.DesktopController._show_system_notification(
                target, "Queue completed"
            )

        tray_icon.showMessage.assert_not_called()


if __name__ == "__main__":
    unittest.main()
