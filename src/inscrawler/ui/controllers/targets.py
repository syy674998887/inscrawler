"""Target-account form state and persisted target-list interactions."""

from __future__ import annotations

import json
import os
from datetime import datetime

import inscrawler.paths as app_paths
import inscrawler.tasks.schema as tasks_schema
import inscrawler.ui.console as ui_console
import inscrawler.ui.constants as ui_constants
import inscrawler.ui.dialogs.targets as ui_dialogs_targets
import inscrawler.ui.qt as ui_qt


class TargetController:
    """Qt view concern composed explicitly by the main window."""

    def _read_target_values(self):
        if not os.path.exists(app_paths.TARGET_FILE):
            return []
        with open(app_paths.TARGET_FILE, "r", encoding="utf-8") as f:
            targets = json.load(f)
        if not isinstance(targets, list):
            raise ValueError("target.json must contain a JSON array")
        return targets

    def _write_target_values(self, values):
        targets = list(values or [])
        temp_path = app_paths.TARGET_FILE + ".tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(targets, f, indent=2, ensure_ascii=False)
            os.replace(temp_path, app_paths.TARGET_FILE)
        except Exception:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError:
                pass
            raise
        return targets

    def load_target(self):
        """加载target.json中的账号列表"""
        try:
            target = self._read_target_values()
            self.target.clear()
            self.target.setIconSize(ui_qt.QSize(28, 28))
            for username in sorted(target, key=lambda x: x.lower()):
                self._add_target_item(username)
            self._set_patrol_target_options(target, preserve_selection=True)
            self._update_download_scope_controls()
        except Exception as e:
            ui_console.print(f"Failed to load target.json: {e}")

    def _normalize_target_values(self, values):
        if not isinstance(values, list):
            raise ValueError("target.json must contain a JSON array")
        targets = []
        seen = set()
        for value in values:
            username = str(value or "").strip().lstrip("@").strip()
            key = username.casefold()
            if not username or key in seen:
                continue
            seen.add(key)
            targets.append(username)
        return targets

    def _set_patrol_target_options(self, targets, preserve_selection=False):
        normalized = self._normalize_target_values(list(targets or []))
        previous_option_keys = {
            str(username).casefold() for username in self._patrol_target_options
        }
        previous_selected_keys = set(self._patrol_selected_target_keys)
        new_option_keys = {str(username).casefold() for username in normalized}

        self._patrol_target_options = normalized
        if preserve_selection:
            self._patrol_selected_target_keys = {
                key for key in previous_selected_keys if key in new_option_keys
            }
            # Accounts that appear for the first time default to selected.
            self._patrol_selected_target_keys.update(
                key for key in new_option_keys if key not in previous_option_keys
            )
        else:
            self._patrol_selected_target_keys = set(new_option_keys)
        self._update_patrol_target_button_text()

    def _add_patrol_target_option(self, username, selected=True):
        username = str(username or "").strip().lstrip("@").strip()
        if not username:
            return
        key = username.casefold()
        if any(str(value).casefold() == key for value in self._patrol_target_options):
            return
        self._patrol_target_options.append(username)
        if selected:
            self._patrol_selected_target_keys.add(key)
        self._update_patrol_target_button_text()

    def request_remove_target(self, username):
        normalized = self._normalize_target_values([username])
        if not normalized:
            return False
        target_username = normalized[0]
        target_key = target_username.casefold()

        msg = self._create_standard_message_box(
            ui_qt.QMessageBox.Icon.Question,
            self.tr("confirm_remove_target_title"),
            self.tr("confirm_remove_target_message", username=target_username),
            ui_qt.QMessageBox.StandardButton.Yes | ui_qt.QMessageBox.StandardButton.No,
            default_button=ui_qt.QMessageBox.StandardButton.No,
        )
        if self._exec_centered_dialog(msg) != ui_qt.QMessageBox.StandardButton.Yes:
            return False

        try:
            targets = self._read_target_values()
            remaining = [
                value
                for value in targets
                if str(value or "").strip().lstrip("@").strip().casefold() != target_key
            ]
            if len(remaining) != len(targets):
                remaining = self._write_target_values(remaining)
        except Exception as e:
            ui_console.print(f"Failed to remove target account @{target_username}: {e}")
            self._show_message_box(
                ui_qt.QMessageBox.Icon.Critical,
                self.tr("dialog_error_title"),
                self.tr("remove_target_failed", error=e),
            )
            return False

        current_text = self.target.currentText()
        current_values = self._normalize_target_values([current_text])
        current_key = current_values[0].casefold() if current_values else ""
        current_index = self.target.currentIndex()

        for row in range(self.target.count() - 1, -1, -1):
            item_key = (
                str(self.target.itemText(row) or "").strip().lstrip("@").casefold()
            )
            if item_key == target_key:
                self.target.removeItem(row)

        if current_key == target_key:
            self.target.setCurrentIndex(-1)
            self.target.setEditText("")
        elif current_index < 0:
            self.target.setCurrentIndex(-1)
            self.target.setEditText(current_text)
        elif current_key:
            for row in range(self.target.count()):
                item_key = (
                    str(self.target.itemText(row) or "").strip().lstrip("@").casefold()
                )
                if item_key == current_key:
                    self.target.setCurrentIndex(row)
                    break

        self._set_patrol_target_options(remaining, preserve_selection=True)
        ui_console.print(f"Target account @{target_username} removed from saved list")
        return True

    def _selected_patrol_targets(self):
        return [
            username
            for username in self._patrol_target_options
            if str(username).casefold() in self._patrol_selected_target_keys
        ]

    def _update_patrol_target_button_text(self):
        if not hasattr(self, "patrol_targets_btn"):
            return
        self.patrol_targets_btn.setText(
            self.tr(
                "patrol_choose_targets",
                selected=len(self._selected_patrol_targets()),
                total=len(self._patrol_target_options),
            )
        )

    def open_patrol_target_selector(self):
        dialog = ui_dialogs_targets.PatrolTargetSelectionDialog(
            self,
            self._patrol_target_options,
            self._selected_patrol_targets(),
        )
        if self._exec_centered_dialog(dialog) != ui_qt.QDialog.DialogCode.Accepted:
            return
        self._patrol_selected_target_keys = {
            str(username).casefold() for username in dialog.selected_targets()
        }
        self._update_patrol_target_button_text()

    def _target_profile_dir(self, username, base_path=None):
        username = str(username or "").strip().lstrip("@")
        if not username:
            return None
        if base_path is None:
            base_path = self.save_path.text().strip()
        base_path = str(base_path or "").strip() or self.default_save_path
        return os.path.join(
            os.path.abspath(os.path.expanduser(base_path)), username, "profile"
        )

    def _latest_target_profile_picture(self, username, base_path=None):
        profile_dir = self._target_profile_dir(username, base_path)
        if not profile_dir or not os.path.isdir(profile_dir):
            return None
        try:
            candidates = [
                os.path.join(profile_dir, name)
                for name in os.listdir(profile_dir)
                if self._is_target_profile_picture_filename(name)
                and os.path.isfile(os.path.join(profile_dir, name))
            ]
        except OSError:
            return None
        if not candidates:
            return None
        return max(candidates, key=lambda path: os.path.basename(path).lower())

    def _is_target_profile_picture_filename(self, filename):
        stem, ext = os.path.splitext(str(filename or ""))
        if ext.lower() not in ui_constants.PROFILE_PICTURE_EXTENSIONS:
            return False
        try:
            datetime.strptime(stem, "%Y-%m-%d_%H-%M-%S")
        except ValueError:
            return False
        return True

    def _target_profile_picture_icon(self, username, base_path=None):
        path = self._latest_target_profile_picture(username, base_path)
        if not path:
            return ui_qt.QIcon()
        try:
            stat = os.stat(path)
            cache_key = (path, stat.st_mtime_ns, stat.st_size)
        except OSError:
            return ui_qt.QIcon()
        cached = self._target_avatar_icon_cache.get(cache_key)
        if cached is not None:
            return cached

        size = ui_qt.QSize(28, 28)
        reader = ui_qt.QImageReader(path)
        reader.setAutoTransform(True)
        original_size = reader.size()
        if (
            original_size.isValid()
            and original_size.width() > 0
            and original_size.height() > 0
        ):
            if (
                original_size.width() * size.height()
                > original_size.height() * size.width()
            ):
                scaled_width = max(
                    size.width(),
                    (original_size.width() * size.height()) // original_size.height(),
                )
                reader.setScaledSize(ui_qt.QSize(scaled_width, size.height()))
            else:
                scaled_height = max(
                    size.height(),
                    (original_size.height() * size.width()) // original_size.width(),
                )
                reader.setScaledSize(ui_qt.QSize(size.width(), scaled_height))
        else:
            reader.setScaledSize(size)

        image = reader.read()
        if image.isNull():
            return ui_qt.QIcon()
        scaled = ui_qt.QPixmap.fromImage(image)
        if scaled.isNull():
            return ui_qt.QIcon()
        x = max(0, (scaled.width() - size.width()) // 2)
        y = max(0, (scaled.height() - size.height()) // 2)
        icon = ui_qt.QIcon(scaled.copy(x, y, size.width(), size.height()))
        self._target_avatar_icon_cache[cache_key] = icon
        return icon

    def _add_target_item(self, username):
        self.target.addItem(self._target_profile_picture_icon(username), username)

    def _refresh_target_account_icon(self, username, base_path=None):
        username_value = str(username or "").strip().lstrip("@")
        username_key = username_value.lower()
        if not username_key:
            return
        icon = self._target_profile_picture_icon(username_value, base_path)
        for row in range(self.target.count()):
            if self.target.itemText(row).strip().lstrip("@").lower() == username_key:
                self.target.setItemIcon(row, icon)
                break
        self._refresh_queue_avatars_for_username(username_value)

    def _refresh_all_target_account_icons(self):
        for row in range(self.target.count()):
            self.target.setItemIcon(
                row, self._target_profile_picture_icon(self.target.itemText(row))
            )
        for entry in getattr(self, "queue_items", []):
            self._refresh_queue_avatar(entry)

    def _refresh_queue_avatar(self, entry):
        avatar_label = entry.get("avatar_label")
        if avatar_label is None:
            return

        task = entry.get("task") or {}
        if tasks_schema._is_auto_patrol_task(task):
            avatar_label.clear()
            avatar_label.setVisible(False)
            return
        icon = self._target_profile_picture_icon(
            task.get("target_user", ""), task.get("save_path")
        )
        has_avatar = not icon.isNull()
        if has_avatar:
            avatar_label.setPixmap(icon.pixmap(ui_qt.QSize(28, 28)))
        else:
            avatar_label.clear()
        avatar_label.setVisible(has_avatar)

        label = entry.get("label")
        if label is not None:
            label.setContentsMargins(0, 0, 0, 0)

    def _refresh_queue_avatars_for_username(self, username):
        username_key = str(username or "").strip().lstrip("@").lower()
        if not username_key:
            return
        for entry in getattr(self, "queue_items", []):
            task = entry.get("task") or {}
            target_user = str(task.get("target_user", "")).strip().lstrip("@").lower()
            if target_user == username_key:
                self._refresh_queue_avatar(entry)

    def _describe_config_changes(self, existing_config, new_config):
        labels = {
            "username": "Username",
            "session_cookie": "Cookie",
            "user_agent": "User-Agent",
            "notification": "Notification",
            "notification_webhook": "Discord webhook",
            "telegram_bot_token": "Telegram bot token",
            "telegram_chat_id": "Telegram chat id",
            "save_path": "Save path",
            "iphone_support": "iPhone support",
            "profile_picture_sync": "Profile picture updates",
            "media_request_cap_enabled": "Download limit",
            "debug_logging": "Debug logging",
            "video_preview_cache_mb": "Video thumbnail cache",
            "max_preview_images": "Preview count",
            "patrol_media_type": "Patrol media type",
            "patrol_page_limit": "Patrol page limit",
            "ui_theme": "Theme",
            "ui_language": "Language",
            "close_behavior": "Close behavior",
        }
        changes = []
        for key, new_value in new_config.items():
            if new_value is None:
                continue
            old_value = str(existing_config.get(key, "")).strip()
            new_value = str(new_value or "").strip()
            if old_value == new_value:
                continue
            action = "cleared" if old_value and not new_value else "updated"
            changes.append(f"{labels.get(key, key)} {action}")
        return changes

    def save_new_target(self, username):
        """保存全新目标账号至target.json"""
        try:
            normalized = self._normalize_target_values([username])
            if not normalized:
                return False
            username = normalized[0]
            username_key = username.casefold()
            target = self._read_target_values()
            if any(
                str(value or "").strip().lstrip("@").strip().casefold() == username_key
                for value in target
            ):
                return False

            target.append(username)
            self._write_target_values(target)
            self._add_target_item(username)
            self._add_patrol_target_option(username, selected=True)
            ui_console.print(f"New account @{username} added")
            return True
        except Exception as e:
            ui_console.print(f"Failed to save new account: {e}")
            return False

    def _current_target_user(self, warn=True):
        target_user = self.target.currentText().strip().lstrip("@").strip()
        if not target_user and warn:
            self._show_message_box(
                ui_qt.QMessageBox.Icon.Warning,
                self.tr("dialog_warning_title"),
                self.tr("warning_target_required"),
            )
        return target_user

    def open_target_profile(self):
        target_user = self._current_target_user()
        if not target_user:
            return
        ui_qt.QDesktopServices.openUrl(
            ui_qt.QUrl(f"https://www.instagram.com/{target_user}/")
        )

    def open_target_local_folder(self):
        target_user = self._current_target_user()
        if not target_user:
            return
        base_path = self.save_path.text().strip() or self.default_save_path
        folder = os.path.join(
            os.path.abspath(os.path.expanduser(base_path)), target_user
        )
        if not os.path.isdir(folder):
            self._show_message_box(
                ui_qt.QMessageBox.Icon.Warning,
                self.tr("dialog_warning_title"),
                self.tr(
                    "warning_local_folder_missing",
                    username=target_user,
                ),
            )
            return
        ui_qt.QDesktopServices.openUrl(ui_qt.QUrl.fromLocalFile(folder))

    def _clear_target_selection_on_edit(self, text):
        row = self.target.currentIndex()
        if row < 0:
            return
        if str(text or "").strip() == self.target.itemText(row).strip():
            return
        self.target.setCurrentIndex(-1)
        self.target.setEditText(text)
        line_edit = self.target.lineEdit()
        if line_edit is not None:
            line_edit.setCursorPosition(len(text))
