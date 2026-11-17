"""Contracts for the opt-in profile-picture update setting."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import inscrawler.downloads.avatar as downloads_avatar
import inscrawler.downloads.runtime as downloads_runtime
import inscrawler.downloads.service as downloads_service
import inscrawler.ui.controllers.desktop as ui_controllers_desktop
import inscrawler.ui.preferences as ui_preferences


class ProfilePictureSyncSettingTests(unittest.TestCase):
    def test_disabled_setting_skips_profile_picture_sync_entirely(self):
        with (
            patch.object(
                downloads_runtime.runtime, "profile_picture_sync_enabled", False
            ),
            patch.object(downloads_avatar, "_sync_profile_picture") as sync_mock,
        ):
            result = downloads_avatar.sync_profile_picture_best_effort(
                object(),
                object(),
                "alice",
            )

        self.assertIsNone(result)
        sync_mock.assert_not_called()

    def test_enabled_setting_keeps_existing_best_effort_sync(self):
        expected = object()
        with (
            patch.object(
                downloads_runtime.runtime, "profile_picture_sync_enabled", True
            ),
            patch.object(
                downloads_avatar,
                "_sync_profile_picture",
                return_value=expected,
            ) as sync_mock,
        ):
            result = downloads_avatar.sync_profile_picture_best_effort(
                "loader",
                "profile",
                "alice",
            )

        self.assertIs(result, expected)
        sync_mock.assert_called_once_with("loader", "profile", "alice")

    def test_gui_setter_updates_download_setting_and_persists(self):
        target = SimpleNamespace(
            profile_picture_sync_enabled=False,
            _loading_settings=False,
            _normalize_iphone_support_value=lambda value: (
                str(value).strip().lower() in ("1", "true", "yes", "on", "enabled")
            ),
            save_profile_picture_sync_setting=Mock(),
        )

        with patch.object(downloads_service, "config_settings") as config_mock:
            ui_controllers_desktop.DesktopController.set_profile_picture_sync_enabled(
                target,
                "true",
                persist=True,
            )

        self.assertTrue(target.profile_picture_sync_enabled)
        config_mock.assert_called_once_with(profile_picture_sync_input=True)
        target.save_profile_picture_sync_setting.assert_called_once_with()

    def test_profile_picture_setting_is_saved_as_boolean_env_value(self):
        target = SimpleNamespace(
            profile_picture_sync_enabled=False,
            _write_env_settings=Mock(),
            _show_message_box=Mock(),
            tr=lambda key, **kwargs: key,
        )

        ui_preferences.PreferencesController.save_profile_picture_sync_setting(target)

        target._write_env_settings.assert_called_once_with(
            {"profile_picture_sync": "false"}
        )
        target._show_message_box.assert_not_called()


if __name__ == "__main__":
    unittest.main()
