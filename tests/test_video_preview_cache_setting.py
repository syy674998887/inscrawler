"""Contracts for the configurable in-memory video thumbnail cache limit."""

from __future__ import annotations

import os
import unittest
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

import inscrawler.ui.constants as ui_constants
import inscrawler.ui.dialogs.settings as ui_dialogs_settings
import inscrawler.ui.i18n.text as ui_i18n_text
import inscrawler.ui.preferences as ui_preferences
import inscrawler.ui.preview.controller as ui_preview_controller


class _FakePixmap:
    def __init__(self, byte_size):
        self.byte_size = byte_size

    @staticmethod
    def isNull():
        return False


class _VideoCacheHarness:
    _discard_video_cached_pixmap = (
        ui_preview_controller.PreviewController._discard_video_cached_pixmap
    )
    _promote_detail_video_cached_pixmap = (
        ui_preview_controller.PreviewController._promote_detail_video_cached_pixmap
    )
    _get_home_video_cached_pixmap = (
        ui_preview_controller.PreviewController._get_home_video_cached_pixmap
    )
    _get_detail_video_cached_pixmap = (
        ui_preview_controller.PreviewController._get_detail_video_cached_pixmap
    )
    _store_home_video_cached_pixmap = (
        ui_preview_controller.PreviewController._store_home_video_cached_pixmap
    )
    _store_detail_video_cached_pixmap = (
        ui_preview_controller.PreviewController._store_detail_video_cached_pixmap
    )
    _demote_home_video_cache_for_paths = (
        ui_preview_controller.PreviewController._demote_home_video_cache_for_paths
    )
    _drop_preview_cache_for_paths = (
        ui_preview_controller.PreviewController._drop_preview_cache_for_paths
    )
    _oldest_home_video_cache_key = (
        ui_preview_controller.PreviewController._oldest_home_video_cache_key
    )
    _trim_video_cache = ui_preview_controller.PreviewController._trim_video_cache
    clear_detail_video_thumbnail_cache = (
        ui_preview_controller.PreviewController.clear_detail_video_thumbnail_cache
    )
    detail_video_thumbnail_cache_bytes = (
        ui_preview_controller.PreviewController.detail_video_thumbnail_cache_bytes
    )

    def __init__(self, limit_mb=64):
        self.video_preview_cache_mb = limit_mb
        self.current_preview_images = []
        self._image_pixmap_cache = {}
        self._image_thumb_inflight = set()
        self._video_home_pixmap_cache = {}
        self._video_detail_pixmap_cache = OrderedDict()
        self._video_cache_total_bytes = 0

    @staticmethod
    def _normalize_media_path(path):
        return os.path.normcase(os.path.abspath(path))

    @staticmethod
    def _estimate_pixmap_bytes(pixmap):
        return pixmap.byte_size

    def key(self, path):
        return ("video", self._normalize_media_path(path), 1, 1)


class _SettingsWindow(QWidget):
    def __init__(self, language="en"):
        super().__init__()
        self.ui_theme = "light"
        self.ui_language = language
        self.close_behavior = "tray"
        self.iphone_support = False
        self.profile_picture_sync_enabled = False
        self.max_preview_images = 45
        self.media_request_cap_enabled = False
        self.debug_logging_enabled = False
        self.video_preview_cache_mb = ui_constants.DEFAULT_VIDEO_PREVIEW_CACHE_MB
        self.apply_ui_preferences = Mock()
        self.set_close_behavior = Mock()
        self.set_iphone_support = Mock()
        self.set_profile_picture_sync_enabled = Mock()
        self.set_max_preview_images = Mock()
        self.set_media_request_cap_enabled = Mock()
        self.set_debug_logging_enabled = Mock()
        self.set_video_preview_cache_mb = Mock()
        self._detail_video_cache_bytes = int(12.5 * 1024 * 1024)
        self.detail_video_thumbnail_cache_bytes = Mock(
            side_effect=lambda: self._detail_video_cache_bytes
        )

        def clear_detail_cache():
            self._detail_video_cache_bytes = 0

        self.clear_detail_video_thumbnail_cache = Mock(side_effect=clear_detail_cache)

    def tr(self, key, **kwargs):
        text = ui_i18n_text.UI_TEXT[self.ui_language].get(key, key)
        return text.format(**kwargs) if kwargs else text

    @staticmethod
    def _configure_combo_box(_combo):
        return None

    @staticmethod
    def is_downloading():
        return False


class VideoPreviewCacheSettingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.widgets = []

    def tearDown(self):
        for widget in self.widgets:
            widget.close()
            widget.deleteLater()
        self.app.processEvents()

    def _dialog(self, language="en"):
        window = _SettingsWindow(language)
        dialog = ui_dialogs_settings.SettingsDialog(window)
        self.widgets.extend((dialog, window))
        return window, dialog

    def test_options_cover_64_to_512_mb_in_32_mb_steps(self):
        self.assertEqual(ui_constants.VIDEO_PREVIEW_CACHE_MB_OPTIONS[0], 64)
        self.assertEqual(ui_constants.VIDEO_PREVIEW_CACHE_MB_OPTIONS[-1], 512)
        self.assertTrue(
            all(
                right - left == 32
                for left, right in zip(
                    ui_constants.VIDEO_PREVIEW_CACHE_MB_OPTIONS,
                    ui_constants.VIDEO_PREVIEW_CACHE_MB_OPTIONS[1:],
                )
            )
        )
        self.assertEqual(ui_constants.DEFAULT_VIDEO_PREVIEW_CACHE_MB, 192)

    def test_slider_previews_while_dragging_and_applies_on_value_commit(self):
        window, dialog = self._dialog()
        slider = dialog.video_preview_cache_slider
        target_position = ui_constants.VIDEO_PREVIEW_CACHE_MB_OPTIONS.index(384)

        self.assertFalse(slider.hasTracking())
        self.assertEqual(
            ui_constants.VIDEO_PREVIEW_CACHE_MB_OPTIONS[slider.value()],
            ui_constants.DEFAULT_VIDEO_PREVIEW_CACHE_MB,
        )
        self.assertEqual(dialog.video_preview_cache_value_label.text(), "192 MB")
        dialog.show()
        self.app.processEvents()
        self.assertGreaterEqual(slider.width(), 160)

        dialog._on_video_preview_cache_slider_moved(target_position)

        self.assertEqual(dialog.video_preview_cache_value_label.text(), "384 MB")
        window.set_video_preview_cache_mb.assert_not_called()

        dialog._on_video_preview_cache_changed(target_position)

        window.set_video_preview_cache_mb.assert_called_once_with(
            384,
            persist=True,
        )

    def test_setting_has_english_and_chinese_labels(self):
        self.assertEqual(
            ui_i18n_text.UI_TEXT["en"]["video_preview_cache_label"],
            "Video Thumbnail Cache:",
        )
        self.assertEqual(
            ui_i18n_text.UI_TEXT["zh"]["video_preview_cache_label"],
            "视频缩略图缓存:",
        )
        _window, dialog = self._dialog("zh")
        self.assertEqual(dialog.video_preview_cache_label.text(), "视频缩略图缓存:")
        self.assertEqual(
            dialog.clear_detail_video_cache_button.text(),
            "清空详情页缓存",
        )
        self.assertEqual(
            dialog.detail_video_cache_usage_label.text(),
            "详情页缓存：12.50 MB",
        )

    def test_current_detail_cache_size_is_left_of_clear_button(self):
        _window, dialog = self._dialog("en")
        form_layout = dialog.layout().itemAt(0).layout()
        usage_position = form_layout.getItemPosition(
            form_layout.indexOf(dialog.detail_video_cache_usage_label)
        )
        button_position = form_layout.getItemPosition(
            form_layout.indexOf(dialog.clear_detail_video_cache_button)
        )

        self.assertEqual(
            dialog.detail_video_cache_usage_label.text(),
            "Detail cache: 12.50 MB",
        )
        self.assertEqual(usage_position[0], button_position[0])
        self.assertEqual(usage_position[1], 0)
        self.assertEqual(button_position[1], 1)

    def test_profile_picture_updates_are_off_by_default_and_toggle_immediately(self):
        window, dialog = self._dialog("zh")

        self.assertFalse(dialog.profile_picture_sync_check.isChecked())
        self.assertEqual(dialog.profile_picture_sync_label.text(), "头像更新:")
        self.assertIn("默认关闭", dialog.profile_picture_sync_check.toolTip())

        dialog.profile_picture_sync_check.click()

        window.set_profile_picture_sync_enabled.assert_called_once_with(
            True,
            persist=True,
        )

    def test_download_limit_switch_is_not_in_settings_dialog(self):
        _window, dialog = self._dialog("zh")

        self.assertFalse(hasattr(dialog, "media_request_cap_enabled_label"))
        self.assertFalse(hasattr(dialog, "media_request_cap_enabled_check"))

    def test_clear_detail_cache_button_calls_the_window(self):
        window, dialog = self._dialog()

        dialog.clear_detail_video_cache_button.click()

        window.clear_detail_video_thumbnail_cache.assert_called_once_with()
        self.assertEqual(
            dialog.detail_video_cache_usage_label.text(),
            "Detail cache: 0.00 MB",
        )

    def test_value_is_clamped_to_the_nearest_supported_step(self):
        normalize = (
            ui_preferences.PreferencesController._normalize_video_preview_cache_mb
        )

        self.assertEqual(normalize("384"), 384)
        self.assertEqual(normalize(95), 96)
        self.assertEqual(normalize(0), 64)
        self.assertEqual(normalize(999), 512)
        self.assertEqual(normalize("invalid"), 192)

    def test_setter_trims_immediately_and_persists_committed_value(self):
        target = SimpleNamespace(
            video_preview_cache_mb=384,
            _loading_settings=False,
            _normalize_video_preview_cache_mb=(
                ui_preferences.PreferencesController._normalize_video_preview_cache_mb
            ),
            _trim_video_cache=Mock(),
            save_video_preview_cache_setting=Mock(),
        )

        ui_preferences.PreferencesController.set_video_preview_cache_mb(
            target, 380, persist=True
        )

        self.assertEqual(target.video_preview_cache_mb, 384)
        target._trim_video_cache.assert_called_once_with()
        target.save_video_preview_cache_setting.assert_called_once_with()

    def test_detail_lookup_reuses_home_and_updates_only_detail_lru(self):
        target = _VideoCacheHarness()
        home_key = target.key("home.mp4")
        detail_old_key = target.key("detail-old.mp4")
        detail_new_key = target.key("detail-new.mp4")
        home_pixmap = _FakePixmap(1)
        detail_old_pixmap = _FakePixmap(2)
        detail_new_pixmap = _FakePixmap(3)
        target._video_home_pixmap_cache[home_key] = home_pixmap
        target._video_detail_pixmap_cache.update(
            (
                (detail_old_key, detail_old_pixmap),
                (detail_new_key, detail_new_pixmap),
            )
        )
        target._video_cache_total_bytes = 6

        self.assertIs(target._get_detail_video_cached_pixmap(home_key), home_pixmap)
        self.assertEqual(
            list(target._video_detail_pixmap_cache),
            [detail_old_key, detail_new_key],
        )

        self.assertIs(
            target._get_detail_video_cached_pixmap(detail_old_key),
            detail_old_pixmap,
        )
        self.assertEqual(
            list(target._video_detail_pixmap_cache),
            [detail_new_key, detail_old_key],
        )
        self.assertEqual(target._video_cache_total_bytes, 6)

    def test_home_lookup_promotes_detail_without_copying_or_recounting(self):
        target = _VideoCacheHarness()
        cache_key = target.key("promoted.mp4")
        pixmap = _FakePixmap(12)
        target._video_detail_pixmap_cache[cache_key] = pixmap
        target._video_cache_total_bytes = 12

        self.assertIs(target._get_home_video_cached_pixmap(cache_key), pixmap)

        self.assertIs(target._video_home_pixmap_cache[cache_key], pixmap)
        self.assertNotIn(cache_key, target._video_detail_pixmap_cache)
        self.assertEqual(target._video_cache_total_bytes, 12)

    def test_home_history_eviction_demotes_thumbnail_as_oldest_detail_entry(self):
        target = _VideoCacheHarness()
        demoted_key = target.key("demoted.mp4")
        viewed_key = target.key("viewed.mp4")
        demoted_pixmap = _FakePixmap(4)
        viewed_pixmap = _FakePixmap(5)
        target._video_home_pixmap_cache[demoted_key] = demoted_pixmap
        target._video_detail_pixmap_cache[viewed_key] = viewed_pixmap
        target._video_cache_total_bytes = 9

        target._drop_preview_cache_for_paths(["demoted.mp4"])

        self.assertNotIn(demoted_key, target._video_home_pixmap_cache)
        self.assertEqual(
            list(target._video_detail_pixmap_cache),
            [demoted_key, viewed_key],
        )
        self.assertEqual(target._video_cache_total_bytes, 9)

    def test_lower_limit_evicts_detail_then_oldest_home_preview(self):
        mib = 1024 * 1024
        target = _VideoCacheHarness(limit_mb=64)
        newest_key = target.key("newest.mp4")
        oldest_key = target.key("oldest.mp4")
        detail_key = target.key("detail.mp4")
        target.current_preview_images = ["newest.mp4", "oldest.mp4"]
        target._video_home_pixmap_cache.update(
            (
                (oldest_key, _FakePixmap(32 * mib)),
                (newest_key, _FakePixmap(32 * mib)),
            )
        )
        target._video_detail_pixmap_cache[detail_key] = _FakePixmap(32 * mib)
        target._video_cache_total_bytes = 96 * mib

        target._trim_video_cache()

        self.assertEqual(target._video_detail_pixmap_cache, OrderedDict())
        self.assertEqual(set(target._video_home_pixmap_cache), {oldest_key, newest_key})
        self.assertEqual(target._video_cache_total_bytes, 64 * mib)

        target.video_preview_cache_mb = 32
        target._trim_video_cache()

        self.assertEqual(list(target._video_home_pixmap_cache), [newest_key])
        self.assertEqual(target._video_cache_total_bytes, 32 * mib)

    def test_detail_insert_cannot_displace_home_cache(self):
        mib = 1024 * 1024
        target = _VideoCacheHarness(limit_mb=64)
        newest_key = target.key("newest.mp4")
        oldest_key = target.key("oldest.mp4")
        detail_key = target.key("detail.mp4")
        target.current_preview_images = ["newest.mp4", "oldest.mp4"]
        target._video_home_pixmap_cache.update(
            (
                (oldest_key, _FakePixmap(32 * mib)),
                (newest_key, _FakePixmap(32 * mib)),
            )
        )
        target._video_cache_total_bytes = 64 * mib

        target._store_detail_video_cached_pixmap(
            detail_key,
            _FakePixmap(32 * mib),
        )

        self.assertEqual(target._video_detail_pixmap_cache, OrderedDict())
        self.assertEqual(set(target._video_home_pixmap_cache), {oldest_key, newest_key})
        self.assertEqual(target._video_cache_total_bytes, 64 * mib)

    def test_clear_detail_cache_never_removes_home_cache(self):
        target = _VideoCacheHarness()
        home_key = target.key("home.mp4")
        detail_one_key = target.key("detail-one.mp4")
        detail_two_key = target.key("detail-two.mp4")
        home_pixmap = _FakePixmap(7)
        target._video_home_pixmap_cache[home_key] = home_pixmap
        target._video_detail_pixmap_cache.update(
            (
                (detail_one_key, _FakePixmap(11)),
                (detail_two_key, _FakePixmap(13)),
            )
        )
        target._video_cache_total_bytes = 31

        self.assertEqual(target.detail_video_thumbnail_cache_bytes(), 24)

        target.clear_detail_video_thumbnail_cache()

        self.assertEqual(target._video_detail_pixmap_cache, OrderedDict())
        self.assertEqual(target._video_home_pixmap_cache, {home_key: home_pixmap})
        self.assertEqual(target._video_cache_total_bytes, 7)
        self.assertEqual(target.detail_video_thumbnail_cache_bytes(), 0)

    def test_persistence_uses_the_mb_value(self):
        target = SimpleNamespace(
            video_preview_cache_mb=384,
            _write_env_settings=Mock(),
            _show_message_box=Mock(),
            tr=lambda key, **_kwargs: key,
        )

        ui_preferences.PreferencesController.save_video_preview_cache_setting(target)

        target._write_env_settings.assert_called_once_with(
            {
                "video_preview_cache_mb": "384",
            }
        )


if __name__ == "__main__":
    unittest.main()
