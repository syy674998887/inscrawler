"""Headless contracts for applying the app icon to native windows."""

from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import inscrawler.paths as app_paths

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFont, QIcon, QPalette
from PySide6.QtWidgets import QApplication, QMessageBox

import inscrawler.instagram.reels as instagram_reels
import inscrawler.ui.console as ui_console
import inscrawler.ui.controllers.desktop as ui_controllers_desktop
import inscrawler.ui.controllers.targets as ui_controllers_targets
import inscrawler.ui.dialogs.about as ui_dialogs_about
import inscrawler.ui.dialogs.console as ui_dialogs_console
import inscrawler.ui.dialogs.settings as ui_dialogs_settings
import inscrawler.ui.dialogs.shutdown as ui_dialogs_shutdown
import inscrawler.ui.dialogs.targets as ui_dialogs_targets
import inscrawler.ui.main_window as ui_main_window
import inscrawler.ui.platform as ui_platform
import inscrawler.ui.preferences as ui_preferences
import inscrawler.ui.preview.controller as ui_preview_controller
import inscrawler.ui.qt as ui_qt


class WindowIconLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_app_icon_is_applied_to_widget_and_native_window(self):
        native_window = Mock()
        window = SimpleNamespace(
            setWindowIcon=Mock(),
            windowHandle=Mock(return_value=native_window),
        )

        self.assertTrue(ui_main_window.GUI._apply_window_icon(window))

        widget_icon = window.setWindowIcon.call_args.args[0]
        native_icon = native_window.setIcon.call_args.args[0]
        self.assertFalse(widget_icon.isNull())
        self.assertIs(native_icon, widget_icon)

    def test_missing_icon_does_not_overwrite_window_icon(self):
        window = SimpleNamespace(
            setWindowIcon=Mock(),
            windowHandle=Mock(),
        )

        with patch.object(os.path, "exists", return_value=False):
            self.assertFalse(ui_main_window.GUI._apply_window_icon(window))

        window.setWindowIcon.assert_not_called()
        window.windowHandle.assert_not_called()

    def test_secondary_window_helper_sets_an_explicit_null_icon(self):
        window = SimpleNamespace(setWindowIcon=Mock())

        ui_platform._suppress_secondary_window_icon(window)

        icon = window.setWindowIcon.call_args.args[0]
        self.assertTrue(icon.isNull())


class SecondaryWindowIconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.original_app_icon = QIcon(self.app.windowIcon())
        self.original_app_stylesheet = self.app.styleSheet()
        self.original_app_palette = QPalette(self.app.palette())
        self.original_app_font = QFont(self.app.font())
        self.window = None
        self.dialogs = []
        self.temp_tasks = tempfile.TemporaryDirectory()
        self.patchers = [
            patch.object(app_paths, "TASKS_DIR", self.temp_tasks.name),
            patch.object(
                ui_controllers_desktop.DesktopController,
                "_init_system_tray",
                lambda _self: None,
            ),
            patch.object(
                ui_preferences.PreferencesController,
                "load_env_settings",
                lambda _self: None,
            ),
            patch.object(
                ui_controllers_targets.TargetController,
                "load_target",
                lambda _self: None,
            ),
            patch.object(
                ui_preview_controller.PreviewController,
                "load_preview_history",
                lambda _self: None,
            ),
        ]
        for patcher in self.patchers:
            patcher.start()

        self.window = ui_main_window.GUI(capture_console=False)
        self.app.setWindowIcon(self.window.windowIcon())

    def tearDown(self):
        for dialog in reversed(self.dialogs):
            dialog.close()
            dialog.deleteLater()
        if self.window is not None:
            self.window._thumb_extractor.shutdown()
            self.window._image_thumb_extractor.shutdown()
            self.window.hide()
            self.window.deleteLater()
        self.app.processEvents()
        ui_console._console_log_callback = None
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp_tasks.cleanup()
        self.app.setWindowIcon(self.original_app_icon)
        self.app.setStyleSheet(self.original_app_stylesheet)
        self.app.setPalette(self.original_app_palette)
        self.app.setFont(self.original_app_font)
        self.app.processEvents()

    def test_secondary_titlebar_windows_do_not_inherit_the_app_icon(self):
        window = self.window
        self.assertFalse(window.windowIcon().isNull())
        dialogs = [
            ui_dialogs_shutdown.ShutdownCountdownDialog(window, 60, lambda: None),
            ui_dialogs_console.ConsoleLogDialog(window),
            ui_dialogs_targets.PatrolTargetSelectionDialog(window, [], []),
            ui_dialogs_settings.SettingsDialog(window),
            ui_dialogs_about.AboutDialog(window),
            window._create_standard_message_box(
                QMessageBox.Icon.Information,
                "Title",
                "Text",
                QMessageBox.StandardButton.Ok,
            ),
        ]
        self.dialogs.extend(dialogs)

        for dialog in dialogs:
            with self.subTest(dialog=type(dialog).__name__):
                self.assertTrue(dialog.windowIcon().isNull())

    def test_about_dialog_shows_release_version_and_repository(self):
        dialog = ui_dialogs_about.AboutDialog(self.window)
        self.dialogs.append(dialog)

        self.assertEqual(dialog.version_value.text(), "V 1.0")
        self.assertIn(
            'href="https://github.com/syy674998887/inscrawler"',
            dialog.github_value.text(),
        )
        self.assertEqual(
            dialog.instaloader_version_value.text(),
            instagram_reels.INSTALOADER_VERSION,
        )

    def test_about_repository_link_uses_each_theme_link_color(self):
        dialog = ui_dialogs_about.AboutDialog(self.window)
        self.dialogs.append(dialog)

        for theme in ("light", "dark"):
            with self.subTest(theme=theme):
                self.window.apply_theme(theme)
                expected = ui_qt.QColor(self.window._theme_tokens()["link"])
                self.assertEqual(
                    dialog.github_value.palette().color(QPalette.ColorRole.Link),
                    expected,
                )


if __name__ == "__main__":
    unittest.main()
