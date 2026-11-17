"""Form options, field visibility, and translated task labels."""

from __future__ import annotations

import inscrawler.tasks.schema as tasks_schema
import inscrawler.ui.i18n.options as ui_i18n_options
import inscrawler.ui.qt as ui_qt


class FormController:
    """Qt view concern composed explicitly by the main window."""

    def _current_download_scope_unit(self):
        media_type = ""
        if hasattr(self, "type"):
            media_type = str(self.type.currentText() or "").strip().lower()
        if media_type in tasks_schema._FULL_DOWNLOAD_MEDIA_TYPES:
            return "full"
        return "page"

    def _update_download_scope_controls(self, *_args):
        if not all(
            hasattr(self, name) for name in ("download_limit_label", "scope_limit")
        ):
            return
        scope_unit = self._current_download_scope_unit()
        is_full = scope_unit == "full"
        if not is_full:
            self.download_limit_label.setText(self.tr("download_pages_label"))
        else:
            self.download_limit_label.clear()
        self.download_limit_label.setVisible(not is_full)
        self.scope_limit.setEnabled(not is_full)
        self.scope_limit.setVisible(not is_full)
        FormController._update_media_request_cap_controls(self, update_height=False)
        if hasattr(self, "task_group") and not getattr(
            self, "_syncing_minimum_window_height", False
        ):
            self._set_fixed_height_from_hint(self.task_group)
            self._sync_minimum_window_height()

    def _update_media_request_cap_controls(self, *_args, update_height=True):
        if not all(
            hasattr(self, name)
            for name in (
                "media_request_cap_enabled_label",
                "media_request_cap_control",
                "media_request_cap_enabled_check",
                "media_request_cap_label",
                "media_request_cap_spin",
            )
        ):
            return
        quantity_enabled = bool(self.media_request_cap_enabled)
        self.media_request_cap_spin.ensurePolished()
        input_height = max(
            self.media_request_cap_spin.sizeHint().height(),
            self.media_request_cap_spin.minimumSizeHint().height(),
        )
        self.media_request_cap_enabled_check.set_control_height(input_height)
        if hasattr(self, "task_group") and all(
            hasattr(self, name)
            for name in (
                "target_label",
                "download_type_label",
                "download_limit_label",
            )
        ):
            task_layout = self.task_group.layout()
            scope_label_width = (
                self.download_limit_label.fontMetrics().horizontalAdvance(
                    self.tr("download_pages_label")
                )
            )
            task_layout.setColumnMinimumWidth(
                0,
                max(
                    self.target_label.sizeHint().width(),
                    self.download_type_label.sizeHint().width(),
                    scope_label_width,
                    self.media_request_cap_enabled_label.sizeHint().width(),
                ),
            )
        self.media_request_cap_enabled_label.setVisible(True)
        self.media_request_cap_control.setVisible(True)
        self.media_request_cap_enabled_check.setVisible(True)
        self.media_request_cap_enabled_check.setEnabled(True)
        self.media_request_cap_label.setVisible(quantity_enabled)
        self.media_request_cap_label.setEnabled(quantity_enabled)
        self.media_request_cap_spin.setVisible(quantity_enabled)
        self.media_request_cap_spin.setEnabled(quantity_enabled)
        control_layout = self.media_request_cap_control.layout()
        if control_layout is not None:
            control_layout.invalidate()
            control_layout.activate()
        self.media_request_cap_control.updateGeometry()
        if hasattr(self, "task_group"):
            task_layout = self.task_group.layout()
            if task_layout is not None:
                task_layout.invalidate()
                task_layout.activate()
        if (
            update_height
            and hasattr(self, "task_group")
            and not getattr(self, "_syncing_minimum_window_height", False)
        ):
            self._set_fixed_height_from_hint(self.task_group)
            self._sync_minimum_window_height()

    def _on_media_request_cap_enabled_changed(self, _state):
        self.set_media_request_cap_enabled(
            self.media_request_cap_enabled_check.isChecked(),
            persist=True,
        )

    def _update_patrol_page_controls(self, *_args):
        if not all(
            hasattr(self, name)
            for name in (
                "patrol_media_type",
                "patrol_page_limit_label",
                "patrol_page_limit",
            )
        ):
            return
        media_type = (
            str(self.patrol_media_type.currentData() or "posts").strip().lower()
        )
        show_page_limit = media_type in tasks_schema._PAGED_PATROL_MEDIA_TYPES
        page_limit_text = self.tr("patrol_page_limit_label")
        self.patrol_page_limit_label.setText(page_limit_text)
        if hasattr(self, "patrol_group"):
            self.patrol_group.layout().setColumnMinimumWidth(
                0,
                max(
                    self.patrol_media_type_label.sizeHint().width(),
                    self.patrol_page_limit_label.sizeHint().width(),
                ),
            )
        if not show_page_limit:
            self.patrol_page_limit_label.clear()
        self.patrol_page_limit_label.setVisible(show_page_limit)
        self.patrol_page_limit.setVisible(show_page_limit)
        controls_enabled = not (
            getattr(self, "_safe_exit_in_progress", False)
            or getattr(self, "_force_exit", False)
        )
        self.patrol_page_limit_label.setEnabled(show_page_limit and controls_enabled)
        self.patrol_page_limit.setEnabled(show_page_limit and controls_enabled)
        if hasattr(self, "patrol_group") and not getattr(
            self, "_syncing_minimum_window_height", False
        ):
            self._set_fixed_height_from_hint(self.patrol_group)
            self._sync_minimum_window_height()

    def _theme_label(self, theme_key, language=None):
        target_language = language or self.ui_language
        for key, labels in ui_i18n_options.THEME_OPTIONS:
            if key == theme_key:
                return labels.get(target_language, labels["en"])
        return theme_key

    def _media_badge_label(self, media_kind):
        labels = {
            "image": {"en": "Image", "zh": "图片"},
            "video": {"en": "Video", "zh": "视频"},
        }
        return labels.get(media_kind, labels["image"]).get(
            self.ui_language, labels["image"]["en"]
        )

    def _retranslate_media_badges(self):
        """切换语言后，更新所有已显示媒体单元上的 图片/视频 徽章文字"""
        if not hasattr(self, "cells"):
            return
        for cell in self.cells:
            path = getattr(cell, "_current_path", None)
            if not path:
                continue
            kind = "video" if self._is_video_preview_path(path) else "image"
            cell.update_badge_text(self._media_badge_label(kind))

    def choose_save_path(self):
        """打开目录选择对话框"""
        current = self.save_path.text().strip() or self.default_save_path
        directory = ui_qt.QFileDialog.getExistingDirectory(
            self,
            self.tr("dialog_select_save_path"),
            current,
            ui_qt.QFileDialog.Option.ShowDirsOnly,
        )
        if directory:
            self.save_path.setText(directory)

    def _update_notification_fields_visibility(self):
        """根据通知类型显示/隐藏对应的输入框"""
        current = self.notification.currentData()
        is_discord = current == "discord"
        is_telegram = current == "telegram"
        self.webhook_label.setVisible(is_discord)
        self.webhook.setVisible(is_discord)
        self.telegram_bot_token_label.setVisible(is_telegram)
        self.telegram_bot_token.setVisible(is_telegram)
        self.telegram_chat_id_label.setVisible(is_telegram)
        self.telegram_chat_id.setVisible(is_telegram)
        if hasattr(self, "download_group") and not getattr(
            self, "_syncing_minimum_window_height", False
        ):
            self._set_fixed_height_from_hint(self.download_group)
            self._sync_minimum_window_height()
