"""Checks for the target account local-folder button."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import inscrawler.ui.controllers.targets as ui_controllers_targets
import inscrawler.ui.i18n.text as ui_i18n_text
import inscrawler.ui.qt as ui_qt


class _TextField:
    def __init__(self, value):
        self._value = value

    def text(self):
        return self._value


class _LocalFolderHarness:
    open_target_local_folder = (
        ui_controllers_targets.TargetController.open_target_local_folder
    )

    def __init__(self, base_path, target_user="alice"):
        self.save_path = _TextField(str(base_path))
        self.default_save_path = str(base_path)
        self.target_user = target_user
        self.messages = []

    def _current_target_user(self):
        return self.target_user

    def tr(self, key, **kwargs):
        return ui_i18n_text.UI_TEXT["zh"].get(key, key).format(**kwargs)

    def _show_message_box(self, *args):
        self.messages.append(args)


class LocalFolderButtonTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_missing_target_folder_warns_without_creating_or_opening(self):
        harness = _LocalFolderHarness(self.base_path)
        target_path = self.base_path / "alice"

        with (
            patch.object(os, "makedirs") as makedirs_mock,
            patch.object(ui_qt.QDesktopServices, "openUrl") as open_mock,
        ):
            harness.open_target_local_folder()

        self.assertFalse(target_path.exists())
        makedirs_mock.assert_not_called()
        open_mock.assert_not_called()
        self.assertEqual(len(harness.messages), 1)
        self.assertEqual(harness.messages[0][2], "目标账户 alice 的本地文件夹不存在")

    def test_existing_target_folder_opens_without_creating(self):
        harness = _LocalFolderHarness(self.base_path)
        target_path = self.base_path / "alice"
        target_path.mkdir()

        with (
            patch.object(os, "makedirs") as makedirs_mock,
            patch.object(
                ui_qt.QDesktopServices, "openUrl", return_value=True
            ) as open_mock,
        ):
            harness.open_target_local_folder()

        makedirs_mock.assert_not_called()
        open_mock.assert_called_once()
        opened_url = open_mock.call_args.args[0]
        self.assertEqual(
            os.path.normcase(os.path.abspath(opened_url.toLocalFile())),
            os.path.normcase(os.path.abspath(target_path)),
        )
        self.assertEqual(harness.messages, [])


if __name__ == "__main__":
    unittest.main()
