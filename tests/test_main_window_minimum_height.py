"""Headless contracts for the main window's vertical resize boundary."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import inscrawler.paths as app_paths

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont, QPalette
from PySide6.QtWidgets import QApplication, QLineEdit, QStyle, QStyleOptionGroupBox
from shiboken6 import isValid

import inscrawler.config as config
import inscrawler.ui.console as ui_console
import inscrawler.ui.constants as ui_constants
import inscrawler.ui.controllers.desktop as ui_controllers_desktop
import inscrawler.ui.controllers.targets as ui_controllers_targets
import inscrawler.ui.dialogs.targets as ui_dialogs_targets
import inscrawler.ui.main_window as ui_main_window
import inscrawler.ui.preferences as ui_preferences
import inscrawler.ui.preview.controller as ui_preview_controller
import inscrawler.ui.widgets.combo as ui_widgets_combo

LOAD_ENV_SETTINGS = ui_preferences.PreferencesController.load_env_settings


class EnvSettingsWriteTests(unittest.TestCase):
    def test_appending_settings_preserves_unterminated_values_and_comments(self):
        for existing in (
            "username=alice",
            "# retained comment",
            "username=alice\n",
            "",
        ):
            with (
                self.subTest(existing=existing),
                tempfile.TemporaryDirectory() as folder,
            ):
                path = Path(folder) / ".env"
                path.write_text(existing, encoding="utf-8")
                with patch.object(app_paths, "ENV_FILE", str(path)):
                    ui_preferences.PreferencesController._write_env_settings(
                        None, {"ui_theme": "dark", "ui_language": "zh"}
                    )
                expected = {"ui_theme": "dark", "ui_language": "zh"}
                if existing.startswith("username="):
                    expected["username"] = "alice"
                self.assertEqual(config._read_dotenv_file(str(path)), expected)
                if existing.startswith("#"):
                    self.assertTrue(
                        path.read_text(encoding="utf-8").startswith(existing + "\n")
                    )


class MainWindowMinimumHeightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.original_app_stylesheet = self.app.styleSheet()
        self.original_app_palette = QPalette(self.app.palette())
        self.original_app_font = QFont(self.app.font())
        self.window = None
        self.temp_tasks = tempfile.TemporaryDirectory()
        patches = (
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
        )
        self.patchers = list(patches)
        for patcher in self.patchers:
            patcher.start()

        self.window = ui_main_window.GUI(capture_console=False)
        self.window.apply_ui_preferences("dark", "zh", persist=False)
        self.window.notification.setCurrentText("Discord")
        self.window.patrol_media_type.setCurrentIndex(
            self.window.patrol_media_type.findData("highlights")
        )
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
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
        self.app.setStyleSheet(self.original_app_stylesheet)
        self.app.setPalette(self.original_app_palette)
        self.app.setFont(self.original_app_font)
        self.app.processEvents()

    def _visible_section_padding(self, group):
        layout = group.layout()
        group.ensurePolished()
        layout.activate()
        self.app.processEvents()

        option = QStyleOptionGroupBox()
        group.initStyleOption(option)
        frame_rect = group.style().subControlRect(
            QStyle.ComplexControl.CC_GroupBox,
            option,
            QStyle.SubControl.SC_GroupBoxFrame,
            group,
        )
        visible_rects = []
        for index in range(layout.count()):
            widget = layout.itemAt(index).widget()
            if widget is not None and widget.isVisible():
                visible_rects.append(widget.geometry())

        self.assertTrue(visible_rects)
        content_left = min(rect.left() for rect in visible_rects)
        content_top = min(rect.top() for rect in visible_rects)
        content_right = max(rect.right() for rect in visible_rects)
        content_bottom = max(rect.bottom() for rect in visible_rects)
        return {
            "left": content_left - frame_rect.left(),
            "top": content_top - frame_rect.top(),
            "right": frame_rect.right() - content_right,
            "bottom": frame_rect.bottom() - content_bottom,
        }

    def test_minimum_resize_preserves_sections_buttons_gaps_and_queue_title(self):
        window = self.window
        window.resize(window.width(), 1)
        self.app.processEvents()

        self.assertEqual(window.height(), window.minimumHeight())
        self.assertGreaterEqual(
            window.minimumHeight(),
            window._calculated_minimum_window_height(),
        )

        for section in (
            window.login_group,
            window.task_group,
            window.patrol_group,
            window.download_group,
        ):
            self.assertEqual(section.height(), section.minimumHeight())
            self.assertEqual(section.height(), section.maximumHeight())

        self.assertGreater(window.queue_group.minimumHeight(), 0)
        self.assertEqual(
            window.queue_group.height(),
            window.queue_group.minimumHeight(),
        )

        expected_spacing = window.left_layout.spacing()
        adjacent_widgets = (
            (window.login_group, window.task_group),
            (window.task_group, window.add_task_btn),
            (window.add_task_btn, window.patrol_group),
            (window.patrol_group, window.patrol_targets_btn),
            (window.patrol_targets_btn, window.download_group),
            (window.download_group, window.queue_group),
            (window.queue_group, window.download_btn),
        )
        for upper, lower in adjacent_widgets:
            gap = lower.geometry().top() - (upper.geometry().bottom() + 1)
            self.assertGreaterEqual(gap, expected_spacing)

    def test_cookie_input_is_a_standard_single_line_field(self):
        self.assertIsInstance(self.window.session_cookie, QLineEdit)

    def test_patrol_target_rows_use_home_target_account_font(self):
        dialog = ui_dialogs_targets.PatrolTargetSelectionDialog(
            self.window,
            ["alice"],
            ["alice"],
        )
        try:
            expected_font = self.window.target.font()
            actual_font = dialog._target_list.font()
            self.assertEqual(actual_font.family(), expected_font.family())
            self.assertEqual(actual_font.pointSizeF(), expected_font.pointSizeF())
            self.assertEqual(actual_font.weight(), expected_font.weight())
            self.assertEqual(
                dialog._target_list.item(0).sizeHint().height(),
                self.window.target.POPUP_ITEM_HEIGHT,
            )
        finally:
            dialog.close()
            dialog.deleteLater()

    def test_system_completion_notification_option_is_saved(self):
        self.assertGreaterEqual(self.window.notification.findText("系统通知"), 0)
        self.window.notification.setCurrentText("系统通知")
        with (
            patch.object(self.window, "_write_env_settings") as writer,
            patch.object(self.window, "_refresh_all_target_account_icons"),
            patch.object(os.path, "exists", return_value=False),
        ):
            self.window.save_settings()

        self.assertEqual(
            writer.call_args.args[0]["notification"],
            "system",
        )

    def test_system_completion_notification_option_is_loaded(self):
        with (
            patch.object(os.path, "exists", return_value=True),
            patch.object(
                config,
                "_read_dotenv_file",
                return_value={"notification": "system", "ui_language": "zh"},
            ),
        ):
            LOAD_ENV_SETTINGS(self.window)

        self.assertEqual(self.window.notification.currentText(), "系统通知")
        self.assertEqual(self.window.notification.currentData(), "system")

    def test_download_preview_visibility_is_loaded(self):
        with (
            patch.object(os.path, "exists", return_value=True),
            patch.object(
                config,
                "_read_dotenv_file",
                return_value={"main_preview_visible": "false"},
            ),
        ):
            LOAD_ENV_SETTINGS(self.window)

        self.assertFalse(self.window._main_preview_thumbnails_visible)
        self.assertFalse(self.window.preview_visibility_button.isChecked())
        self.assertTrue(self.window.scroll.isHidden())

    def test_settings_file_is_created_in_a_new_data_home(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "new-home" / ".env"
            with patch.object(app_paths, "ENV_FILE", str(path)):
                LOAD_ENV_SETTINGS(self.window)

            self.assertTrue(path.is_file())

    def test_closed_settings_release_combo_popups_and_registry_entries(self):
        original_combos = list(self.window._combo_boxes)
        popups = []

        def close_dialog(dialog):
            popups.extend(
                combo.popup_container()
                for combo in dialog.findChildren(ui_widgets_combo.AlignedComboBox)
            )
            QTimer.singleShot(0, dialog.reject)
            return dialog.exec()

        with patch.object(self.window, "_exec_centered_dialog", new=close_dialog):
            for _ in range(3):
                self.window.open_settings_dialog()

        self.assertTrue(popups)
        self.assertTrue(all(not isValid(popup) for popup in popups))
        self.assertEqual(self.window._combo_boxes, original_combos)

    def test_cookie_save_normalizes_pasted_line_breaks(self):
        self.window.session_cookie.setText(
            "sessionid=sid;\r\nds_user_id=42;\ncsrftoken=csrf"
        )
        with (
            patch.object(self.window, "_write_env_settings") as writer,
            patch.object(self.window, "_refresh_all_target_account_icons"),
            patch.object(os.path, "exists", return_value=False),
        ):
            self.window.save_settings()

        saved_cookie = writer.call_args.args[0]["session_cookie"]
        self.assertEqual(
            saved_cookie,
            "sessionid=sid; ds_user_id=42; csrftoken=csrf",
        )
        self.assertNotIn("\r", saved_cookie)
        self.assertNotIn("\n", saved_cookie)

    def test_home_sections_have_the_same_equal_four_side_padding(self):
        sections = (
            ("login", self.window.login_group),
            ("task", self.window.task_group),
            ("patrol", self.window.patrol_group),
            ("download", self.window.download_group),
            ("queue", self.window.queue_group),
            ("thread_status", self.window.thread_status_group),
            ("preview", self.window.preview_group),
        )
        expected_padding = ui_constants.HOME_SECTION_CONTENT_PADDING + 1

        for theme in ("dark", "light"):
            for language in ("zh", "en"):
                self.window.apply_ui_preferences(
                    theme,
                    language,
                    persist=False,
                )
                self.app.processEvents()
                for name, group in sections:
                    with self.subTest(
                        theme=theme,
                        language=language,
                        section=name,
                    ):
                        margins = group.layout().contentsMargins()
                        self.assertEqual(
                            (
                                margins.left(),
                                margins.top(),
                                margins.right(),
                                margins.bottom(),
                            ),
                            ui_constants.HOME_SECTION_LAYOUT_MARGINS,
                        )
                        padding = self._visible_section_padding(group)
                        self.assertEqual(
                            len(set(padding.values())),
                            1,
                            f"{name} visible padding differs: {padding}",
                        )
                        side_padding = padding["top"]
                        self.assertEqual(
                            side_padding,
                            expected_padding,
                            f"{name} visible padding differs: {padding}",
                        )

    def test_create_task_section_keeps_equal_padding_when_controls_change(self):
        group = self.window.task_group
        expected_padding = ui_constants.HOME_SECTION_CONTENT_PADDING + 1

        for theme in ("dark", "light"):
            for language in ("zh", "en"):
                self.window.apply_ui_preferences(
                    theme,
                    language,
                    persist=False,
                )
                for media_type in ("Posts", "Stories"):
                    self.window.type.setCurrentText(media_type)
                    for limit_enabled in (False, True):
                        with self.subTest(
                            theme=theme,
                            language=language,
                            media_type=media_type,
                            limit_enabled=limit_enabled,
                        ):
                            self.window.set_media_request_cap_enabled(
                                limit_enabled,
                                persist=False,
                            )
                            padding = self._visible_section_padding(group)
                            self.assertEqual(
                                len(set(padding.values())),
                                1,
                                f"Create Task visible padding differs: {padding}",
                            )
                            side_padding = padding["top"]
                            self.assertEqual(side_padding, expected_padding)


if __name__ == "__main__":
    unittest.main()
