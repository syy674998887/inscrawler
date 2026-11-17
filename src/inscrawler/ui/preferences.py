"""Apply and persist settings, theme, language, and preview preferences."""

from __future__ import annotations

import os

import inscrawler.config as config
import inscrawler.paths as app_paths
import inscrawler.ui.console as ui_console
import inscrawler.ui.constants as ui_constants
import inscrawler.ui.dialogs.about as ui_dialogs_about
import inscrawler.ui.dialogs.settings as ui_dialogs_settings
import inscrawler.ui.i18n.options as ui_i18n_options
import inscrawler.ui.i18n.text as ui_i18n_text
import inscrawler.ui.qt as ui_qt


class PreferencesController:
    """Qt view concern composed explicitly by the main window."""

    def tr(self, key, **kwargs):
        text = ui_i18n_text.UI_TEXT.get(
            self.ui_language, ui_i18n_text.UI_TEXT["en"]
        ).get(key, key)
        return text.format(**kwargs) if kwargs else text

    def _normalize_theme_key(self, value):
        normalized = str(value or "").strip().lower()
        return ui_i18n_options.THEME_ENV_MAP.get(normalized, "light")

    def _normalize_language_key(self, value):
        normalized = str(value or "").strip().lower()
        return ui_i18n_options.LANGUAGE_ENV_MAP.get(normalized, "en")

    def _normalize_close_behavior_key(self, value):
        normalized = str(value or "").strip().lower()
        return ui_i18n_options.CLOSE_BEHAVIOR_ENV_MAP.get(normalized, "tray")

    def _normalize_iphone_support_value(self, value):
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        normalized = str(value).strip().lower()
        return normalized in ("1", "true", "yes", "on", "enabled", "启用", "已启用")

    @staticmethod
    def _normalize_video_preview_cache_mb(value):
        try:
            value = int(str(value).strip())
        except (TypeError, ValueError):
            return ui_constants.DEFAULT_VIDEO_PREVIEW_CACHE_MB
        return min(
            ui_constants.VIDEO_PREVIEW_CACHE_MB_OPTIONS,
            key=lambda option: (abs(option - value), option),
        )

    def set_media_request_cap_enabled(self, value=None, persist=False):
        self.media_request_cap_enabled = self._normalize_iphone_support_value(
            value if value is not None else self.media_request_cap_enabled
        )
        checkbox = getattr(self, "media_request_cap_enabled_check", None)
        if (
            checkbox is not None
            and checkbox.isChecked() != self.media_request_cap_enabled
        ):
            checkbox.blockSignals(True)
            checkbox.setChecked(self.media_request_cap_enabled)
            checkbox.blockSignals(False)
        self._update_media_request_cap_controls()
        if persist and not getattr(self, "_loading_settings", False):
            self.save_media_request_cap_enabled_setting()

    def set_debug_logging_enabled(self, value=None, persist=False):
        self.debug_logging_enabled = self._normalize_iphone_support_value(
            value if value is not None else self.debug_logging_enabled
        )
        ui_console._debug_logging_enabled = self.debug_logging_enabled
        if persist and not getattr(self, "_loading_settings", False):
            self.save_debug_logging_setting()

    def set_video_preview_cache_mb(self, value=None, persist=False):
        self.video_preview_cache_mb = self._normalize_video_preview_cache_mb(
            value if value is not None else self.video_preview_cache_mb
        )
        self._trim_video_cache()
        if persist and not getattr(self, "_loading_settings", False):
            self.save_video_preview_cache_setting()

    def apply_ui_preferences(self, theme_key=None, language_key=None, persist=False):
        if language_key is not None:
            self.ui_language = self._normalize_language_key(language_key)
        if theme_key is not None:
            self.ui_theme = self._normalize_theme_key(theme_key)
        self.apply_theme(self.ui_theme)
        self.retranslate_ui()
        if hasattr(self, "login_group"):
            # Re-polishing can change the form controls' size hints. Keep the
            # fixed section height in sync so stale extra height does not turn
            # into unequal visible top and bottom padding.
            self._set_fixed_height_from_hint(self.login_group)
            self._sync_minimum_window_height()
        if persist and not getattr(self, "_loading_settings", False):
            self.save_ui_settings()

    def save_ui_settings(self):
        try:
            self._write_env_settings(
                {
                    "ui_theme": self.ui_theme,
                    "ui_language": self.ui_language,
                    "close_behavior": self.close_behavior,
                }
            )
            ui_console.print("UI settings saved")
        except Exception as e:
            ui_console.print(f"Failed to save UI settings: {e}")
            self._show_message_box(
                ui_qt.QMessageBox.Icon.Critical,
                self.tr("dialog_error_title"),
                self.tr("save_settings_failed", error=e),
            )

    def save_iphone_support_setting(self):
        try:
            self._write_env_settings(
                {
                    "iphone_support": "true" if self.iphone_support else "false",
                }
            )
            ui_console.print("iPhone API setting saved")
        except Exception as e:
            ui_console.print(f"Failed to save iPhone API setting: {e}")
            self._show_message_box(
                ui_qt.QMessageBox.Icon.Critical,
                self.tr("dialog_error_title"),
                self.tr("save_settings_failed", error=e),
            )

    def save_profile_picture_sync_setting(self):
        try:
            self._write_env_settings(
                {
                    "profile_picture_sync": (
                        "true" if self.profile_picture_sync_enabled else "false"
                    ),
                }
            )
            ui_console.print("Profile picture update setting saved")
        except Exception as e:
            ui_console.print(f"Failed to save profile picture update setting: {e}")
            self._show_message_box(
                ui_qt.QMessageBox.Icon.Critical,
                self.tr("dialog_error_title"),
                self.tr("save_settings_failed", error=e),
            )

    def save_media_request_cap_enabled_setting(self):
        try:
            self._write_env_settings(
                {
                    "media_request_cap_enabled": (
                        "true" if self.media_request_cap_enabled else "false"
                    ),
                }
            )
            ui_console.print("Download limit setting saved")
        except Exception as e:
            ui_console.print(f"Failed to save download limit setting: {e}")
            self._show_message_box(
                ui_qt.QMessageBox.Icon.Critical,
                self.tr("dialog_error_title"),
                self.tr("save_settings_failed", error=e),
            )

    def save_debug_logging_setting(self):
        try:
            self._write_env_settings(
                {
                    "debug_logging": (
                        "true" if self.debug_logging_enabled else "false"
                    ),
                }
            )
        except Exception as e:
            ui_console.print(f"Failed to save debug logging setting: {e}")
            self._show_message_box(
                ui_qt.QMessageBox.Icon.Critical,
                self.tr("dialog_error_title"),
                self.tr("save_settings_failed", error=e),
            )

    def save_video_preview_cache_setting(self):
        try:
            self._write_env_settings(
                {
                    "video_preview_cache_mb": str(self.video_preview_cache_mb),
                }
            )
        except Exception as e:
            ui_console.print(f"Failed to save video thumbnail cache setting: {e}")
            self._show_message_box(
                ui_qt.QMessageBox.Icon.Critical,
                self.tr("dialog_error_title"),
                self.tr("save_settings_failed", error=e),
            )

    def set_max_preview_images(self, value, persist=False):
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = ui_constants.MAX_PREVIEW_IMAGES
        self.max_preview_images = max(15, min(99, value))
        # 如果当前预览数量超出新上限，则裁剪
        if len(self.current_preview_images) > self.max_preview_images:
            removed = self.current_preview_images[self.max_preview_images :]
            self._drop_preview_cache_for_paths(removed)
            self.current_preview_images = self.current_preview_images[
                : self.max_preview_images
            ]
            self._schedule_preview_history_save()
            self.refresh_preview_images()
        if persist and not getattr(self, "_loading_settings", False):
            self.save_preview_count_setting()

    def save_preview_count_setting(self):
        try:
            self._write_env_settings(
                {
                    "max_preview_images": str(self.max_preview_images),
                }
            )
            ui_console.print(f"Preview count setting saved: {self.max_preview_images}")
        except Exception as e:
            ui_console.print(f"Failed to save preview count setting: {e}")
            self._show_message_box(
                ui_qt.QMessageBox.Icon.Critical,
                self.tr("dialog_error_title"),
                self.tr("save_settings_failed", error=e),
            )

    def open_settings_dialog(self):
        dialog = ui_dialogs_settings.SettingsDialog(self)
        self._exec_centered_dialog(dialog)

    def open_about_dialog(self):
        dialog = ui_dialogs_about.AboutDialog(self)
        self._exec_centered_dialog(dialog)

    def retranslate_ui(self):
        self.setWindowTitle(self.tr("app_title"))
        if hasattr(self, "title_bar"):
            self.title_bar.sync_ui()
        self._refresh_tray_ui()
        if hasattr(self, "login_group"):
            self.login_group.setTitle(self.tr("login_group"))
            self.username_label.setText(self.tr("username_label"))
            self.agent_label.setText(self.tr("agent_label"))
            self.cookie_label.setText(self.tr("cookie_label"))

            self.task_group.setTitle(self.tr("task_group"))
            self.target_label.setText(self.tr("target_label"))
            self.target.set_item_action_tooltip(self.tr("remove_target_tooltip"))
            self.download_type_label.setText(self.tr("download_type_label"))
            self.media_request_cap_enabled_label.setText(
                self.tr("media_request_cap_enabled_label")
            )
            download_limit_accessible_name = self.tr(
                "media_request_cap_enabled_label"
            ).rstrip(":：")
            download_limit_tooltip = self.tr("media_request_cap_enabled_tooltip")
            self.media_request_cap_enabled_check.setAccessibleName(
                download_limit_accessible_name
            )
            self.media_request_cap_enabled_check.setAccessibleDescription(
                download_limit_tooltip
            )
            self.media_request_cap_enabled_label.setToolTip(download_limit_tooltip)
            self.media_request_cap_enabled_check.setToolTip(download_limit_tooltip)
            self.media_request_cap_label.setText(self.tr("media_request_cap_label"))
            self._update_download_scope_controls()
            self.add_task_btn.setText(self.tr("add_task"))
            self.open_profile_btn.setText(self.tr("open_profile"))
            self.open_local_folder_btn.setText(self.tr("open_local_folder"))
            self.patrol_group.setTitle(self.tr("patrol_group"))
            self.patrol_media_type_label.setText(self.tr("patrol_media_type_label"))
            self._update_patrol_page_controls()
            self._update_patrol_target_button_text()
            self.update_patrol_button_state()
            self.download_group.setTitle(self.tr("download_group"))
            self.save_path_label.setText(self.tr("save_path_label"))
            self.save_path.setPlaceholderText(self.tr("save_path_placeholder"))
            self.browse_button.setText(self.tr("browse"))
            self.notification_label.setText(self.tr("notification_label"))
            for notification_key, labels in ui_i18n_options.NOTIFICATION_OPTIONS:
                self.notification.setItemText(
                    self.notification.findData(notification_key),
                    labels.get(self.ui_language, labels["en"]),
                )
            self.webhook_label.setText(self.tr("webhook_label"))
            self.telegram_bot_token_label.setText(self.tr("telegram_bot_token_label"))
            self.telegram_chat_id_label.setText(self.tr("telegram_chat_id_label"))
            self.shutdown_after_queue_label.setText(
                self.tr("shutdown_after_queue_label")
            )
            shutdown_tooltip = self.tr("shutdown_after_queue_tooltip")
            self.shutdown_after_queue_label.setToolTip(shutdown_tooltip)
            self.shutdown_after_queue_check.setToolTip(shutdown_tooltip)
            self.shutdown_after_queue_check.setAccessibleName(
                self.tr("shutdown_after_queue_label").rstrip(":：")
            )
            if self._shutdown_countdown_dialog is not None:
                try:
                    self._shutdown_countdown_dialog.retranslate()
                except RuntimeError:
                    self._shutdown_countdown_dialog = None
            self.update_download_button_state()
            self.save.setText(self.tr("save_config"))
            self.clear_queue_btn.setText(self.tr("clear_queue"))
            self.queue_group.setTitle(self.tr("queue_group"))
            self.preview_group.setTitle(self.tr("preview_group"))
            self._sync_main_preview_visibility_button()
            self.thread_status_group.setTitle(self.tr("thread_status_group"))
            self._refresh_thread_pipeline()
            if self._console_log_dialog is not None:
                try:
                    self._console_log_dialog.retranslate()
                except RuntimeError:
                    self._console_log_dialog = None
            self._retranslate_media_badges()
            if self.current_preview_images:
                self.refresh_preview_images()
            self._refresh_open_task_dialog()
            self._refresh_queue_reorder_state()

    def load_env_settings(self):
        """从本地.env文件加载设置；缺键/损坏时回退到默认值，不阻塞启动"""
        env_file = app_paths.ENV_FILE
        if os.path.exists(env_file):
            env_vars = config._read_dotenv_file(env_file)

            self.username.setText(env_vars.get("username", ""))
            self.user_agent.setText(env_vars.get("user_agent", ""))
            self.session_cookie.setText(env_vars.get("session_cookie", ""))
            self.webhook.setText(env_vars.get("notification_webhook", ""))
            self.telegram_bot_token.setText(env_vars.get("telegram_bot_token", ""))
            self.telegram_chat_id.setText(env_vars.get("telegram_chat_id", ""))
            self.save_path.setText(
                env_vars.get("save_path", "").strip() or self.default_save_path
            )

            notification = env_vars.get("notification", "").lower()
            notification_index = self.notification.findData(notification)
            self.notification.setCurrentIndex(max(0, notification_index))
            self._update_notification_fields_visibility()

            self.set_iphone_support(
                env_vars.get("iphone_support", "false"), persist=False
            )
            self.set_profile_picture_sync_enabled(
                env_vars.get("profile_picture_sync", "false"),
                persist=False,
            )
            self.set_media_request_cap_enabled(
                env_vars.get("media_request_cap_enabled", "false"),
                persist=False,
            )
            self.set_debug_logging_enabled(
                env_vars.get("debug_logging", "false"),
                persist=False,
            )
            self.set_video_preview_cache_mb(
                env_vars.get(
                    "video_preview_cache_mb",
                    str(ui_constants.DEFAULT_VIDEO_PREVIEW_CACHE_MB),
                ),
                persist=False,
            )
            self.set_max_preview_images(
                env_vars.get(
                    "max_preview_images", str(ui_constants.MAX_PREVIEW_IMAGES)
                ),
                persist=False,
            )
            self._set_main_preview_thumbnails_visible(
                self._normalize_iphone_support_value(
                    env_vars.get("main_preview_visible", "true")
                ),
                persist=False,
            )
            patrol_media_type = (
                env_vars.get("patrol_media_type", "posts").strip().lower()
            )
            patrol_media_index = self.patrol_media_type.findData(patrol_media_type)
            if patrol_media_index >= 0:
                self.patrol_media_type.setCurrentIndex(patrol_media_index)
            try:
                self.patrol_page_limit.setValue(
                    int(
                        env_vars.get(
                            "patrol_page_limit",
                            str(ui_constants.DEFAULT_PATROL_PAGE_LIMIT),
                        )
                    )
                )
            except (TypeError, ValueError):
                self.patrol_page_limit.setValue(ui_constants.DEFAULT_PATROL_PAGE_LIMIT)

            self.ui_theme = self._normalize_theme_key(env_vars.get("ui_theme"))
            self.ui_language = self._normalize_language_key(env_vars.get("ui_language"))
            self.close_behavior = self._normalize_close_behavior_key(
                env_vars.get("close_behavior")
            )
            self.apply_ui_preferences(self.ui_theme, self.ui_language, persist=False)
            self.set_close_behavior(self.close_behavior, persist=False)
        else:
            os.makedirs(os.path.dirname(env_file), exist_ok=True)
            with open(env_file, "w", encoding="utf-8") as f:
                f.write("# Instagram login configuration\n")
                f.write("username=\n")
                f.write("# Custom User-Agent\n")
                f.write("user_agent=\n")
                f.write("# Notification configuration\n")
                f.write("notification=\n")
                f.write("notification_webhook=\n")
                f.write("telegram_bot_token=\n")
                f.write("telegram_chat_id=\n")
                f.write(f"save_path={self.default_save_path}\n")
                f.write("# Download behavior\n")
                f.write(
                    f"iphone_support={'true' if self.iphone_support else 'false'}\n"
                )
                f.write("profile_picture_sync=false\n")
                f.write("media_request_cap_enabled=false\n")
                f.write("debug_logging=false\n")
                f.write(
                    f"video_preview_cache_mb={ui_constants.DEFAULT_VIDEO_PREVIEW_CACHE_MB}\n"
                )
                f.write(f"max_preview_images={self.max_preview_images}\n")
                f.write("main_preview_visible=true\n")
                f.write("patrol_media_type=posts\n")
                f.write(f"patrol_page_limit={ui_constants.DEFAULT_PATROL_PAGE_LIMIT}\n")
                f.write("# UI preferences\n")
                f.write(f"ui_theme={self.ui_theme}\n")
                f.write(f"ui_language={self.ui_language}\n")
                f.write(f"close_behavior={self.close_behavior}\n")
            ui_console.print(
                "Created .env file. Fill in your Instagram login information."
            )
            self.save_path.setText(self.default_save_path)
            self.apply_ui_preferences(self.ui_theme, self.ui_language, persist=False)
            self.set_close_behavior(self.close_behavior, persist=False)
            self._update_notification_fields_visibility()

    def _write_env_settings(self, config_updates):
        """更新.env中的指定配置项，并保留未改动内容与注释"""
        env_file = app_paths.ENV_FILE
        existing_lines = []
        if os.path.exists(env_file):
            with open(env_file, "r", encoding="utf-8") as f:
                existing_lines = f.readlines()

        updated_lines = []
        updated_keys = set()

        for line in existing_lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                if key in config_updates:
                    if config_updates[key] is not None:
                        updated_lines.append(f"{key}={config_updates[key]}\n")
                    updated_keys.add(key)
                else:
                    updated_lines.append(line)
            else:
                updated_lines.append(line)

        for key, value in config_updates.items():
            if key not in updated_keys and value is not None:
                if updated_lines and not updated_lines[-1].endswith("\n"):
                    updated_lines[-1] += "\n"
                updated_lines.append(f"{key}={value}\n")

        with open(env_file, "w", encoding="utf-8") as f:
            f.writelines(updated_lines)

    def save_settings(self):
        """保存设置到本地.env文件"""
        try:
            save_path_value = self.save_path.text().strip() or self.default_save_path

            new_config = {
                "username": self.username.text(),
                "session_cookie": " ".join(
                    self.session_cookie.text().splitlines()
                ).strip(),
                "user_agent": self.user_agent.text().strip(),
                "notification": self.notification.currentData() or "",
                "notification_webhook": self.webhook.text(),
                "telegram_bot_token": self.telegram_bot_token.text(),
                "telegram_chat_id": self.telegram_chat_id.text(),
                "save_path": save_path_value,
                "iphone_support": "true" if self.iphone_support else "false",
                "profile_picture_sync": (
                    "true" if self.profile_picture_sync_enabled else "false"
                ),
                "media_request_cap_enabled": (
                    "true" if self.media_request_cap_enabled else "false"
                ),
                "debug_logging": ("true" if self.debug_logging_enabled else "false"),
                "video_preview_cache_mb": str(self.video_preview_cache_mb),
                "max_preview_images": str(self.max_preview_images),
                "patrol_media_type": str(
                    self.patrol_media_type.currentData() or "posts"
                ),
                "patrol_page_limit": str(self.patrol_page_limit.value()),
                "ui_theme": self.ui_theme,
                "ui_language": self.ui_language,
                "close_behavior": self.close_behavior,
            }
            existing_config = (
                config._read_dotenv_file(app_paths.ENV_FILE)
                if os.path.exists(app_paths.ENV_FILE)
                else {}
            )
            config_changes = self._describe_config_changes(existing_config, new_config)
            self._write_env_settings(new_config)

            # 如果输入了全新目标账号 - 保存到target.json
            target_values = self._normalize_target_values([self.target.currentText()])
            if target_values:
                target_user = target_values[0]
                target_key = target_user.casefold()
                existing_keys = {
                    str(self.target.itemText(i) or "").strip().lstrip("@").casefold()
                    for i in range(self.target.count())
                }
                if target_key not in existing_keys:
                    self.save_new_target(target_user)
            self._refresh_all_target_account_icons()

            if config_changes:
                ui_console.print("Settings updated:")
                for change in config_changes:
                    ui_console.print(f"- {change}")

        except Exception as e:
            ui_console.print(f"Failed to save settings: {e}")
            self._show_message_box(
                ui_qt.QMessageBox.Icon.Critical,
                self.tr("dialog_error_title"),
                self.tr("save_settings_failed", error=e),
            )
