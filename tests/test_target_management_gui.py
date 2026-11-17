"""Focused headless tests for saved target-account management."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import inscrawler.paths as app_paths

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

import inscrawler.ui.controllers.targets as ui_controllers_targets
import inscrawler.ui.widgets.combo as ui_widgets_combo


class _TargetManagementHarness:
    """Bind the production methods without constructing the full main window."""

    _normalize_target_values = (
        ui_controllers_targets.TargetController._normalize_target_values
    )
    _read_target_values = ui_controllers_targets.TargetController._read_target_values
    _write_target_values = ui_controllers_targets.TargetController._write_target_values
    _set_patrol_target_options = (
        ui_controllers_targets.TargetController._set_patrol_target_options
    )
    _add_patrol_target_option = (
        ui_controllers_targets.TargetController._add_patrol_target_option
    )
    _selected_patrol_targets = (
        ui_controllers_targets.TargetController._selected_patrol_targets
    )
    request_remove_target = (
        ui_controllers_targets.TargetController.request_remove_target
    )
    save_new_target = ui_controllers_targets.TargetController.save_new_target

    def __init__(self):
        self.target = ui_widgets_combo.AlignedComboBox()
        self.target.setEditable(True)
        self._patrol_target_options = []
        self._patrol_selected_target_keys = set()
        self.dialog_result = QMessageBox.StandardButton.No
        self.created_messages = []
        self.error_messages = []
        self.patrol_counts = (0, 0)

    def tr(self, key, **kwargs):
        if kwargs:
            return f"{key}: {kwargs}"
        return key

    def _create_standard_message_box(self, *args, **kwargs):
        message = (args, kwargs)
        self.created_messages.append(message)
        return message

    def _exec_centered_dialog(self, _message):
        return self.dialog_result

    def _show_message_box(self, *args, **kwargs):
        self.error_messages.append((args, kwargs))

    def _update_patrol_target_button_text(self):
        self.patrol_counts = (
            len(self._selected_patrol_targets()),
            len(self._patrol_target_options),
        )

    def _add_target_item(self, username):
        self.target.addItem(username)

    def dispose(self):
        popup = self.target.popup_container()
        popup.close()
        popup.deleteLater()
        self.target.close()
        self.target.deleteLater()


class TargetManagementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.target_path = Path(self.temp_dir.name) / "target.json"
        self.target_file_patch = patch.object(
            app_paths, "TARGET_FILE", str(self.target_path)
        )
        self.target_file_patch.start()
        self.harness = _TargetManagementHarness()

    def tearDown(self):
        self.harness.dispose()
        self.app.processEvents()
        self.target_file_patch.stop()
        self.temp_dir.cleanup()

    def _seed(self, values, current=None, selected=None):
        self.target_path.write_text(
            json.dumps(values, ensure_ascii=False),
            encoding="utf-8",
        )
        for value in values:
            self.harness.target.addItem(value)
        self.harness._set_patrol_target_options(values)
        if selected is not None:
            self.harness._patrol_selected_target_keys = {
                value.casefold() for value in selected
            }
            self.harness._update_patrol_target_button_text()
        if current is None:
            self.harness.target.setCurrentIndex(-1)
            self.harness.target.setEditText("")
        else:
            wanted = current.casefold()
            for row in range(self.harness.target.count()):
                if self.harness.target.itemText(row).casefold() == wanted:
                    self.harness.target.setCurrentIndex(row)
                    break

    def _combo_values(self):
        return [
            self.harness.target.itemText(row)
            for row in range(self.harness.target.count())
        ]

    def test_write_is_atomic_and_preserves_values(self):
        real_replace = os.replace
        values = ["  @Alice  ", "alice", " @Bob", "BOB ", "\u5f20\u4e09"]

        with patch.object(os, "replace", wraps=real_replace) as replace_mock:
            written = self.harness._write_target_values(values)

        self.assertEqual(written, values)
        self.assertEqual(self.harness._read_target_values(), written)
        self.assertEqual(
            json.loads(self.target_path.read_text(encoding="utf-8")),
            written,
        )
        replace_mock.assert_called_once_with(
            str(self.target_path) + ".tmp",
            str(self.target_path),
        )
        self.assertFalse(Path(str(self.target_path) + ".tmp").exists())

    def test_save_new_target_normalizes_and_rejects_casefold_duplicate(self):
        self._seed([" @Alice "], current=" @Alice ")

        self.assertFalse(self.harness.save_new_target(" @aLiCe "))
        self.assertTrue(self.harness.save_new_target("  @Bob  "))

        self.assertEqual(
            json.loads(self.target_path.read_text(encoding="utf-8")),
            [" @Alice ", "Bob"],
        )
        self.assertEqual(self._combo_values(), [" @Alice ", "Bob"])
        self.assertEqual(self.harness._patrol_target_options, ["Alice", "Bob"])
        self.assertEqual(self.harness._patrol_selected_target_keys, {"alice", "bob"})

    def test_cancelled_removal_leaves_file_and_ui_unchanged(self):
        self._seed(["Alpha", "Bravo", "Charlie"], current="Bravo")
        original_bytes = self.target_path.read_bytes()
        original_options = list(self.harness._patrol_target_options)
        original_selected = set(self.harness._patrol_selected_target_keys)

        self.harness.dialog_result = QMessageBox.StandardButton.No
        result = self.harness.request_remove_target(" @bRAVO ")

        self.assertFalse(result)
        self.assertEqual(self.target_path.read_bytes(), original_bytes)
        self.assertEqual(self._combo_values(), ["Alpha", "Bravo", "Charlie"])
        self.assertEqual(self.harness.target.currentText(), "Bravo")
        self.assertEqual(self.harness._patrol_target_options, original_options)
        self.assertEqual(self.harness._patrol_selected_target_keys, original_selected)
        self.assertFalse(Path(str(self.target_path) + ".tmp").exists())

    def test_confirm_removing_current_target_clears_edit_and_syncs_patrol(self):
        self._seed(
            ["Alpha", " @Bravo ", "Charlie"],
            current=" @Bravo ",
            selected=["Alpha", "Bravo"],
        )
        self.harness.dialog_result = QMessageBox.StandardButton.Yes

        result = self.harness.request_remove_target(" @bRAVO ")

        self.assertTrue(result)
        self.assertEqual(
            json.loads(self.target_path.read_text(encoding="utf-8")),
            ["Alpha", "Charlie"],
        )
        self.assertEqual(self._combo_values(), ["Alpha", "Charlie"])
        self.assertEqual(self.harness.target.currentIndex(), -1)
        self.assertEqual(self.harness.target.currentText(), "")
        self.assertEqual(self.harness._patrol_target_options, ["Alpha", "Charlie"])
        self.assertEqual(self.harness._patrol_selected_target_keys, {"alpha"})
        self.assertEqual(self.harness.patrol_counts, (1, 2))

    def test_confirm_removing_other_target_preserves_current_selection(self):
        self._seed(["Alpha", "Bravo", "Charlie"], current="Charlie")
        self.harness.dialog_result = QMessageBox.StandardButton.Yes

        result = self.harness.request_remove_target("alpha")

        self.assertTrue(result)
        self.assertEqual(self._combo_values(), ["Bravo", "Charlie"])
        self.assertEqual(self.harness.target.currentText(), "Charlie")
        self.assertGreaterEqual(self.harness.target.currentIndex(), 0)
        self.assertEqual(self.harness._patrol_target_options, ["Bravo", "Charlie"])
        self.assertEqual(
            self.harness._patrol_selected_target_keys, {"bravo", "charlie"}
        )

    def test_replace_failure_keeps_file_and_ui_unchanged(self):
        self._seed(["Alpha", "Bravo", "Charlie"], current="Bravo")
        self.harness.dialog_result = QMessageBox.StandardButton.Yes
        original_bytes = self.target_path.read_bytes()
        original_options = list(self.harness._patrol_target_options)
        original_selected = set(self.harness._patrol_selected_target_keys)

        with patch.object(os, "replace", side_effect=OSError("replace failed")):
            result = self.harness.request_remove_target("Bravo")

        self.assertFalse(result)
        self.assertEqual(self.target_path.read_bytes(), original_bytes)
        self.assertEqual(self._combo_values(), ["Alpha", "Bravo", "Charlie"])
        self.assertEqual(self.harness.target.currentText(), "Bravo")
        self.assertEqual(self.harness._patrol_target_options, original_options)
        self.assertEqual(self.harness._patrol_selected_target_keys, original_selected)
        self.assertEqual(len(self.harness.error_messages), 1)
        self.assertFalse(Path(str(self.target_path) + ".tmp").exists())


class TargetComboActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.combos = []

    def tearDown(self):
        for combo in self.combos:
            popup = combo.popup_container()
            popup.close()
            popup.deleteLater()
            combo.close()
            combo.deleteLater()
        self.app.processEvents()

    def _combo(self):
        combo = ui_widgets_combo.AlignedComboBox()
        combo.addItems(["Alpha", "Bravo", "Charlie"])
        combo.setCurrentIndex(0)
        combo.resize(240, 32)
        self.combos.append(combo)
        return combo

    def test_popup_animations_use_100_ms(self):
        combo = self._combo()
        popup = combo.popup_container()

        self.assertEqual(popup._pos_animation.duration(), 100)
        self.assertEqual(popup._opacity_animation.duration(), 100)

    def test_popup_action_emits_target_without_changing_current_index(self):
        combo = self._combo()
        requested = []
        combo.item_action_requested.connect(requested.append)
        combo.set_item_action(QIcon(), "Remove target")
        combo.show()
        combo.showPopup()
        self.app.processEvents()

        second_item = combo.popup_view().item(1)
        self.assertIsNone(combo.popup_view().itemWidget(second_item))
        self.assertEqual(second_item.text(), "Bravo")
        action_rect = combo.popup_view().item_action_rect(
            combo.popup_view().visualItemRect(second_item)
        )
        QTest.mouseClick(
            combo.popup_view().viewport(),
            Qt.MouseButton.LeftButton,
            pos=action_rect.center(),
        )
        self.app.processEvents()

        self.assertEqual(requested, ["Bravo"])
        self.assertEqual(combo.currentIndex(), 0)
        self.assertEqual(combo.currentText(), "Alpha")

    def test_combo_without_configured_action_selects_row_normally(self):
        combo = self._combo()
        requested = []
        combo.item_action_requested.connect(requested.append)
        combo.show()
        combo.showPopup()
        self.app.processEvents()

        second_item = combo.popup_view().item(1)
        action_rect = combo.popup_view().item_action_rect(
            combo.popup_view().visualItemRect(second_item)
        )
        QTest.mouseClick(
            combo.popup_view().viewport(),
            Qt.MouseButton.LeftButton,
            pos=action_rect.center(),
        )
        self.app.processEvents()

        self.assertEqual(requested, [])
        self.assertEqual(combo.currentIndex(), 1)
        self.assertEqual(combo.currentText(), "Bravo")

    def test_action_popup_uses_native_items_and_preserves_icons(self):
        combo = ui_widgets_combo.AlignedComboBox()
        combo.addItem(QIcon(), "NoAvatar")
        avatar = QPixmap(8, 8)
        avatar.fill(QColor("#c2584d"))
        combo.addItem(QIcon(avatar), "HasAvatar")
        combo.set_item_action(QIcon(), "Remove target")
        combo._prepare_popup()
        self.combos.append(combo)

        no_avatar_item = combo.popup_view().item(0)
        avatar_item = combo.popup_view().item(1)
        self.assertIsNone(combo.popup_view().itemWidget(no_avatar_item))
        self.assertIsNone(combo.popup_view().itemWidget(avatar_item))
        self.assertTrue(no_avatar_item.icon().isNull())
        self.assertFalse(avatar_item.icon().isNull())
        self.assertEqual(no_avatar_item.text(), "NoAvatar")
        self.assertEqual(avatar_item.text(), "HasAvatar")


if __name__ == "__main__":
    unittest.main()
