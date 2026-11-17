"""Headless contracts for the home-page download preview visibility toggle."""

import os
import unittest
from types import MethodType
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSize
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QGroupBox,
    QLabel,
    QScrollArea,
    QStyle,
    QStyleOptionGroupBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import inscrawler.ui.i18n.text as ui_i18n_text
import inscrawler.ui.main_window as ui_main_window
import inscrawler.ui.theme.manager as ui_theme_manager


class _HomePreviewHarness:
    def __init__(self):
        self.ui_language = "zh"
        self.muted_color = "#74695c"
        self._main_preview_thumbnails_visible = True
        self._loading_settings = False
        self.current_preview_images = ["first.jpg", "second.mp4"]
        self._write_env_settings = Mock()

        self.preview_group = QGroupBox("下载预览")
        self.preview_group.resize(600, 320)
        preview_layout = QVBoxLayout(self.preview_group)
        self.scroll = QScrollArea()
        self.container = QWidget()
        self.grid = QGridLayout(self.container)
        self.cells = [QLabel("first"), QLabel("second")]
        for index, cell in enumerate(self.cells):
            self.grid.addWidget(cell, 0, index)
        self.scroll.setWidget(self.container)
        self.scroll.setWidgetResizable(True)
        preview_layout.addWidget(self.scroll)

        self.preview_visibility_button = QToolButton(self.preview_group)
        self.preview_visibility_button.setObjectName("previewVisibilityButton")
        self.preview_visibility_button.setCheckable(True)
        self.preview_visibility_button.setChecked(True)
        self.preview_visibility_button.setFixedSize(QSize(24, 24))
        self.preview_visibility_button.setIconSize(QSize(18, 18))

        for method_name in (
            "_position_main_preview_visibility_button",
            "_sync_main_preview_visibility_button",
            "_refresh_main_preview_after_show",
            "_set_main_preview_thumbnails_visible",
        ):
            setattr(
                self,
                method_name,
                MethodType(getattr(ui_main_window.GUI, method_name), self),
            )

        self.calculate_image_sizes = Mock()
        self.refresh_preview_images = Mock()
        self.preview_visibility_button.toggled.connect(
            self._set_main_preview_thumbnails_visible
        )
        self._sync_main_preview_visibility_button()

    def _theme_tokens(self):
        return {"muted": self.muted_color}

    def tr(self, key):
        return ui_i18n_text.UI_TEXT[self.ui_language][key]


class HomePreviewVisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.harness = _HomePreviewHarness()
        self.harness.preview_group.show()
        self.app.processEvents()

    def tearDown(self):
        self.harness.preview_group.close()
        self.harness.preview_group.deleteLater()
        self.app.processEvents()

    def test_toggle_hides_only_home_scroll_and_restores_existing_content(self):
        harness = self.harness
        preview_paths = list(harness.current_preview_images)
        grid_count = harness.grid.count()
        visible_icon_key = harness.preview_visibility_button.icon().cacheKey()

        harness.preview_visibility_button.click()
        self.app.processEvents()

        self.assertFalse(harness._main_preview_thumbnails_visible)
        self.assertTrue(harness.scroll.isHidden())
        self.assertTrue(harness.preview_group.isVisible())
        self.assertTrue(harness.preview_visibility_button.isVisible())
        self.assertEqual(
            harness.preview_visibility_button.toolTip(),
            "显示下载预览",
        )
        self.assertEqual(
            harness.preview_visibility_button.accessibleName(),
            "显示下载预览",
        )
        self.assertNotEqual(
            harness.preview_visibility_button.icon().cacheKey(),
            visible_icon_key,
        )
        self.assertEqual(harness.current_preview_images, preview_paths)
        self.assertEqual(harness.grid.count(), grid_count)
        harness.calculate_image_sizes.assert_not_called()
        harness.refresh_preview_images.assert_not_called()

        harness.preview_visibility_button.click()
        self.app.processEvents()

        self.assertTrue(harness._main_preview_thumbnails_visible)
        self.assertFalse(harness.scroll.isHidden())
        self.assertEqual(
            harness.preview_visibility_button.toolTip(),
            "隐藏下载预览",
        )
        self.assertEqual(
            harness.preview_visibility_button.icon().cacheKey(),
            visible_icon_key,
        )
        self.assertEqual(harness.current_preview_images, preview_paths)
        self.assertEqual(harness.grid.count(), grid_count)
        harness.calculate_image_sizes.assert_called_once_with()
        harness.refresh_preview_images.assert_called_once_with()

    def test_toggle_persists_the_visibility_state(self):
        harness = self.harness

        harness.preview_visibility_button.click()
        self.assertEqual(
            harness._write_env_settings.call_args.args[0],
            {"main_preview_visible": "false"},
        )

        harness.preview_visibility_button.click()
        self.assertEqual(
            harness._write_env_settings.call_args.args[0],
            {"main_preview_visible": "true"},
        )

    def test_language_theme_and_title_position_sync_without_changing_state(self):
        harness = self.harness
        button = harness.preview_visibility_button

        option = QStyleOptionGroupBox()
        harness.preview_group.initStyleOption(option)
        title_rect = harness.preview_group.style().subControlRect(
            QStyle.ComplexControl.CC_GroupBox,
            option,
            QStyle.SubControl.SC_GroupBoxLabel,
            harness.preview_group,
        )
        harness._position_main_preview_visibility_button()
        self.assertGreater(button.x(), title_rect.right())
        self.assertTrue(
            harness.preview_group.rect().contains(button.geometry().bottomRight())
        )

        original_icon_key = button.icon().cacheKey()
        harness.ui_language = "en"
        harness.muted_color = "#b7afa4"
        harness._sync_main_preview_visibility_button()

        self.assertTrue(harness._main_preview_thumbnails_visible)
        self.assertTrue(button.isChecked())
        self.assertEqual(button.toolTip(), "Hide download previews")
        self.assertEqual(button.accessibleName(), "Hide download previews")
        self.assertNotEqual(button.icon().cacheKey(), original_icon_key)

    def test_visibility_button_reuses_group_title_background(self):
        class StyleHarness:
            ui_theme = "dark"
            _normalize_theme_key = staticmethod(lambda value: value)
            _section_title_background = staticmethod(
                ui_theme_manager.ThemeController._section_title_background
            )

        harness = StyleHarness()
        colors = ui_theme_manager.ThemeController._theme_tokens(harness)
        title_background = ui_theme_manager.ThemeController._section_title_background(
            colors
        )
        stylesheet = ui_theme_manager.ThemeController._build_application_stylesheet(
            harness, colors
        )

        self.assertEqual(
            stylesheet.count(f"background-color: {title_background};"),
            2,
        )


if __name__ == "__main__":
    unittest.main()
