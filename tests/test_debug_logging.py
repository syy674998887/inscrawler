"""Contracts for the opt-in GUI debug logging setting."""

import ast
import os
import unittest
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

from tests.source_contracts import ui_source

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import inscrawler.ui.console as ui_console
import inscrawler.ui.dialogs.settings as ui_dialogs_settings
import inscrawler.ui.i18n.text as ui_i18n_text
import inscrawler.ui.preferences as ui_preferences


class DebugLoggingTests(unittest.TestCase):
    def setUp(self):
        self._original_debug_logging = ui_console._debug_logging_enabled
        self._original_gui_log_callback = ui_console._gui_log_callback
        self._original_console_log_callback = ui_console._console_log_callback

    def tearDown(self):
        ui_console._debug_logging_enabled = self._original_debug_logging
        ui_console._gui_log_callback = self._original_gui_log_callback
        ui_console._console_log_callback = self._original_console_log_callback

    def test_debug_print_is_console_only_when_enabled(self):
        task_log = Mock()
        console_log = Mock()
        ui_console._gui_log_callback = task_log
        ui_console._console_log_callback = console_log
        with patch.object(ui_console, "_module_print") as terminal_output:
            ui_console._debug_logging_enabled = False
            ui_console.debug_print("hidden")
            terminal_output.assert_not_called()
            console_log.assert_not_called()
            task_log.assert_not_called()

            ui_console._debug_logging_enabled = True
            ui_console.debug_print("visible")
            terminal_output.assert_called_once_with("[gui]", "visible")
            console_log.assert_called_once_with("[gui] visible")
            task_log.assert_not_called()

    def test_normal_gui_print_still_reaches_the_task_log(self):
        task_log = Mock()
        console_log = Mock()
        ui_console._gui_log_callback = task_log
        ui_console._console_log_callback = console_log

        with patch.object(ui_console, "_module_print"):
            ui_console.print("normal")

        task_log.assert_called_once_with("[gui] normal")
        console_log.assert_not_called()

    def test_setting_updates_runtime_state_and_persists(self):
        target = SimpleNamespace(
            debug_logging_enabled=False,
            _loading_settings=False,
            _normalize_iphone_support_value=MethodType(
                ui_preferences.PreferencesController._normalize_iphone_support_value,
                SimpleNamespace(),
            ),
            save_debug_logging_setting=Mock(),
        )

        ui_preferences.PreferencesController.set_debug_logging_enabled(
            target, "enabled", persist=True
        )

        self.assertTrue(target.debug_logging_enabled)
        self.assertTrue(ui_console._debug_logging_enabled)
        target.save_debug_logging_setting.assert_called_once_with()

        persistence_target = SimpleNamespace(
            debug_logging_enabled=True,
            _write_env_settings=Mock(),
            _show_message_box=Mock(),
            tr=lambda key, **_kwargs: key,
        )
        ui_preferences.PreferencesController.save_debug_logging_setting(
            persistence_target
        )
        persistence_target._write_env_settings.assert_called_once_with(
            {
                "debug_logging": "true",
            }
        )

    def test_settings_checkbox_applies_immediately(self):
        dialog = SimpleNamespace(
            _syncing=False,
            _window=SimpleNamespace(set_debug_logging_enabled=Mock()),
            debug_logging_check=SimpleNamespace(isChecked=lambda: True),
        )

        ui_dialogs_settings.SettingsDialog._on_debug_logging_changed(dialog, None)

        dialog._window.set_debug_logging_enabled.assert_called_once_with(
            True,
            persist=True,
        )

    def test_only_the_selected_six_messages_use_debug_print(self):
        source = ui_source()
        tree = ast.parse(source)
        debug_calls = [
            ast.get_source_segment(source, node) or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and (getattr(node.func, "id", None) or getattr(node.func, "attr", None))
            == "debug_print"
        ]

        self.assertEqual(len(debug_calls), 6)
        for expected in (
            "Video thumbnail started",
            "Video thumbnail completed",
            "Preview added",
            "Created media cell",
            "[layout] locked min window height",
            "preview history item(s)",
        ):
            self.assertEqual(
                sum(expected in call for call in debug_calls),
                1,
                expected,
            )

        normal_print_calls = [
            ast.get_source_segment(source, node) or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and (getattr(node.func, "id", None) or getattr(node.func, "attr", None))
            == "print"
        ]
        self.assertTrue(
            any("Video thumbnail failed" in call for call in normal_print_calls)
        )

    def test_setting_has_english_and_chinese_labels(self):
        self.assertEqual(
            ui_i18n_text.UI_TEXT["en"]["debug_logging_label"], "Debug Logging:"
        )
        self.assertEqual(ui_i18n_text.UI_TEXT["zh"]["debug_logging_label"], "调试日志:")


if __name__ == "__main__":
    unittest.main()
