"""Patrol-task details and account result presentation."""

from __future__ import annotations

import os

import inscrawler.tasks.schema as tasks_schema
import inscrawler.ui.platform as ui_platform
import inscrawler.ui.preview.panel as ui_preview_panel
import inscrawler.ui.qt as ui_qt
import inscrawler.ui.widgets.patrol as ui_widgets_patrol


class AutoPatrolTaskDialog(ui_qt.QDialog):
    """Auto-patrol details with log, target status, and new-media columns."""

    def __init__(self, window, task_entry):
        super().__init__(window)
        self.setAttribute(ui_qt.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._window = window
        self._task_entry = task_entry
        self._target_rows = []
        self._selected_target_username = None
        self.setModal(True)
        self.setMinimumSize(1080, 480)
        self.resize(1200, 720)
        self.setWindowFlags(
            ui_qt.Qt.WindowType.Dialog
            | ui_qt.Qt.WindowType.CustomizeWindowHint
            | ui_qt.Qt.WindowType.WindowTitleHint
            | ui_qt.Qt.WindowType.WindowCloseButtonHint
        )
        ui_platform._suppress_secondary_window_icon(self)

        content = ui_qt.QHBoxLayout(self)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)

        self._left_panel = ui_qt.QWidget()
        self._left_panel.setObjectName("patrolLogPanel")
        self._left_panel.setMinimumWidth(390)
        left_layout = ui_qt.QVBoxLayout(self._left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        self._summary_frame = ui_qt.QFrame()
        self._summary_frame.setObjectName("patrolSummary")
        summary = ui_qt.QGridLayout(self._summary_frame)
        summary.setContentsMargins(14, 12, 14, 12)
        summary.setHorizontalSpacing(12)
        summary.setVerticalSpacing(8)

        self._type_caption = ui_qt.QLabel()
        self._type_value = ui_qt.QLabel()
        self._depth_caption = ui_qt.QLabel()
        self._depth_value = ui_qt.QLabel()
        self._targets_caption = ui_qt.QLabel()
        self._targets_value = ui_qt.QLabel()
        self._created_caption = ui_qt.QLabel()
        self._created_value = ui_qt.QLabel()

        summary.addWidget(self._type_caption, 0, 0)
        summary.addWidget(self._type_value, 0, 1)
        summary.addWidget(self._depth_caption, 0, 2)
        summary.addWidget(self._depth_value, 0, 3)
        summary.addWidget(self._targets_caption, 1, 0)
        summary.addWidget(self._targets_value, 1, 1)
        summary.addWidget(self._created_caption, 1, 2)
        summary.addWidget(self._created_value, 1, 3)
        summary.setColumnStretch(1, 1)
        summary.setColumnStretch(3, 1)
        left_layout.addWidget(self._summary_frame)

        self._log_header = ui_qt.QLabel()
        self._log_header.setAlignment(ui_qt.Qt.AlignmentFlag.AlignCenter)
        left_layout.addWidget(self._log_header)
        self._log_edit = ui_qt.QPlainTextEdit()
        self._log_edit.setReadOnly(True)
        self._log_edit.setLineWrapMode(ui_qt.QPlainTextEdit.LineWrapMode.NoWrap)
        self._log_edit.document().setDocumentMargin(0)
        self._log_edit.setViewportMargins(0, 0, 0, 0)
        self._log_edit.setPlainText("\n".join(str(line) for line in task_entry["logs"]))
        self._log_edit.verticalScrollBar().setValue(
            self._log_edit.verticalScrollBar().maximum()
        )
        left_layout.addWidget(self._log_edit, 1)
        content.addWidget(self._left_panel, 2)

        self._left_divider = ui_qt.QFrame()
        self._left_divider.setObjectName("patrolLeftDivider")
        self._left_divider.setFixedWidth(1)
        content.addWidget(self._left_divider)

        self._targets_panel = ui_qt.QWidget()
        self._targets_panel.setObjectName("patrolTargetsPanel")
        self._targets_panel.setMinimumWidth(195)
        targets_layout = ui_qt.QVBoxLayout(self._targets_panel)
        targets_layout.setContentsMargins(0, 0, 0, 0)
        targets_layout.setSpacing(0)
        self._targets_header = ui_qt.QLabel()
        self._targets_header.setAlignment(ui_qt.Qt.AlignmentFlag.AlignCenter)
        targets_layout.addWidget(self._targets_header)
        self._targets_list = ui_widgets_patrol.PatrolTargetStatusListWidget()
        self._targets_list.setObjectName("patrolTargetStatusList")
        self._targets_list.setSelectionMode(
            ui_qt.QAbstractItemView.SelectionMode.SingleSelection
        )
        self._targets_list.setVerticalScrollMode(
            ui_qt.QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self._targets_list.setHorizontalScrollBarPolicy(
            ui_qt.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._targets_list.setUniformItemSizes(True)
        self._targets_list.setContentsMargins(0, 0, 0, 0)
        self._targets_list.setViewportMargins(0, 0, 0, 0)
        self._targets_list.setSpacing(0)
        self._targets_list.setFocusPolicy(ui_qt.Qt.FocusPolicy.NoFocus)
        self._targets_list.itemClicked.connect(self._on_target_item_clicked)
        self._targets_list.blank_clicked.connect(self._clear_target_filter)
        targets_layout.addWidget(self._targets_list, 1)
        content.addWidget(self._targets_panel, 1)

        self._right_divider = ui_qt.QFrame()
        self._right_divider.setObjectName("patrolRightDivider")
        self._right_divider.setFixedWidth(1)
        content.addWidget(self._right_divider)

        self._media_panel = ui_qt.QWidget()
        self._media_panel.setObjectName("patrolMediaPanel")
        self._media_panel.setMinimumWidth(390)
        media_layout = ui_qt.QVBoxLayout(self._media_panel)
        media_layout.setContentsMargins(0, 0, 0, 0)
        media_layout.setSpacing(0)
        self._media_preview = ui_preview_panel.TaskMediaPreviewPanel(
            self._window,
            self._task_entry,
        )
        self._media_preview.setObjectName("patrolNewMediaPreview")
        self._preview_cells = self._media_preview._preview_cells
        media_layout.addWidget(self._media_preview, 1)
        content.addWidget(self._media_panel, 2)

        self._refresh_theme()
        self._retranslate()

    def done(self, result):
        media_preview = getattr(self, "_media_preview", None)
        if media_preview is not None:
            media_preview.release_video_thumbnail_requests()
        super().done(result)

    def _retranslate(self):
        self.setWindowTitle(self._window.tr("patrol_task_title"))
        self._type_caption.setText(self._window.tr("patrol_media_type_label"))
        self._targets_caption.setText(self._window.tr("patrol_target_count_label"))
        self._created_caption.setText(self._window.tr("log_created_at_label"))
        self._log_header.setText(self._window.tr("log_pane_title"))
        self._targets_header.setText(self._window.tr("patrol_target_list_title"))
        self.refresh_from_entry()

    def _state_text(self, state):
        key = f"patrol_state_{state}"
        translated = self._window.tr(key)
        return translated if translated != key else str(state or "")

    def refresh_from_entry(self):
        task = self._task_entry.get("task") or {}
        patrol_state = self._task_entry.get("patrol_state") or {}
        target_users = list(task.get("target_users") or [])
        media_type = str(task.get("download_media_type") or "posts").lower()
        self._type_value.setText(media_type.capitalize())
        if media_type in tasks_schema._PAGED_PATROL_MEDIA_TYPES:
            limit_label_key = "patrol_page_limit_label"
            limit_value = task["page_limit"]
        else:
            limit_label_key = None
            limit_value = ""
        show_limit = limit_label_key is not None
        self._depth_caption.setText(
            self._window.tr(limit_label_key) if show_limit else ""
        )
        self._depth_value.setText(str(limit_value) if show_limit else "")
        self._depth_caption.setVisible(show_limit)
        self._depth_value.setVisible(show_limit)
        self._targets_value.setText(str(len(target_users)))
        self._created_value.setText(tasks_schema._format_task_created_minute(task))

        accounts = patrol_state.get("accounts") or []
        account_by_name = {
            str(account.get("target_user") or "").casefold(): account
            for account in accounts
            if isinstance(account, dict)
        }
        scrollbar = self._targets_list.verticalScrollBar()
        scroll_value = scrollbar.value()
        self._targets_list.clear()
        self._target_rows = []
        for username in target_users:
            normalized_username = str(username or "").strip().lstrip("@")
            account = account_by_name.get(normalized_username.casefold(), {})
            state_text = self._state_text(account.get("state", "waiting"))
            row = ui_widgets_patrol.PatrolTargetStatusRow(
                self._window,
                task,
                normalized_username,
                account,
                state_text,
            )
            item = ui_qt.QListWidgetItem()
            item.setSizeHint(ui_qt.QSize(0, 60))
            item.setData(ui_qt.Qt.ItemDataRole.UserRole, normalized_username)
            message = str(account.get("message") or "").strip()
            if message:
                item.setToolTip(message)
            self._targets_list.addItem(item)
            self._targets_list.setItemWidget(item, row)
            self._target_rows.append(row)
            if (
                self._selected_target_username
                and normalized_username.casefold()
                == self._selected_target_username.casefold()
            ):
                item.setSelected(True)
                self._targets_list.setCurrentItem(item)
        scrollbar.setValue(min(scroll_value, scrollbar.maximum()))

    def refresh_target(self, username, account):
        target_key = self._normalize_target_username(username).casefold()
        for index, row in enumerate(self._target_rows):
            item = self._targets_list.item(index)
            item_key = self._normalize_target_username(
                item.data(ui_qt.Qt.ItemDataRole.UserRole)
            ).casefold()
            if item_key != target_key:
                continue
            row.update_account(
                account,
                self._state_text((account or {}).get("state", "waiting")),
            )
            item.setToolTip(str((account or {}).get("message") or "").strip())
            return

    def _apply_target_log_filter(self):
        logs = list(self._task_entry["logs"])
        selected = self._normalize_target_username(
            self._selected_target_username
        ).casefold()
        owners = self._task_entry["log_targets"]
        if selected:
            logs = [
                line
                for line, owner in zip(logs, owners)
                if self._normalize_target_username(owner).casefold() == selected
            ]
        self._log_edit.setPlainText("\n".join(str(line) for line in logs))
        scrollbar = self._log_edit.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def append_log(self, line, target_username=None):
        selected = self._normalize_target_username(
            self._selected_target_username
        ).casefold()
        owner = self._normalize_target_username(target_username).casefold()
        if selected and owner != selected:
            return
        self._log_edit.appendPlainText(str(line))

    @staticmethod
    def _normalize_target_username(username):
        return str(username or "").strip().lstrip("@").strip()

    def _media_path_belongs_to_target(self, image_path, username):
        username = self._normalize_target_username(username)
        path = str(image_path or "").strip()
        if not username or not path:
            return False

        normalized_path = os.path.normcase(os.path.abspath(os.path.expanduser(path)))
        target_key = username.casefold()

        # Match the stable <target>/<media type> part of the patrol layout.
        # Highlights may add another directory before the media file.
        task = self._task_entry.get("task") or {}
        media_directory = str(task.get("download_media_type") or "").casefold()
        path_parts = [part.casefold() for part in normalized_path.split(os.sep)]
        if any(
            owner == target_key and category == media_directory
            for owner, category in zip(path_parts, path_parts[1:])
        ):
            return True

        save_path = str(task.get("save_path") or "").strip()
        if not save_path:
            return False
        target_root = os.path.normcase(
            os.path.abspath(os.path.expanduser(os.path.join(save_path, username)))
        )
        try:
            return os.path.commonpath((normalized_path, target_root)) == target_root
        except ValueError:
            return False

    def _apply_target_filter(self):
        paths = list(self._task_entry.get("downloaded_files", []))
        if self._selected_target_username:
            paths = [
                path
                for path in paths
                if self._media_path_belongs_to_target(
                    path,
                    self._selected_target_username,
                )
            ]
        self._media_preview.set_preview_images(paths)
        self._apply_target_log_filter()

    def _on_target_item_clicked(self, item):
        username = self._normalize_target_username(
            item.data(ui_qt.Qt.ItemDataRole.UserRole) if item is not None else ""
        )
        if not username:
            return
        if (
            self._selected_target_username
            and self._selected_target_username.casefold() == username.casefold()
        ):
            self._targets_list.clearSelection()
            self._targets_list.setCurrentRow(-1)
            self._selected_target_username = None
        else:
            self._selected_target_username = username
            item.setSelected(True)
            self._targets_list.setCurrentItem(item)
        self._apply_target_filter()

    def _clear_target_filter(self):
        self._targets_list.clearSelection()
        self._targets_list.setCurrentRow(-1)
        if self._selected_target_username is None:
            return
        self._selected_target_username = None
        self._apply_target_filter()

    def add_preview_image(self, image_path, target_username=None):
        if self._selected_target_username:
            event_target = self._normalize_target_username(target_username)
            if event_target:
                if event_target.casefold() != self._selected_target_username.casefold():
                    return
            elif not self._media_path_belongs_to_target(
                image_path,
                self._selected_target_username,
            ):
                return
        self._media_preview.add_preview_image(image_path)

    def _refresh_theme(self):
        colors = self._window._theme_tokens()
        scrollbar_ss = self._window._scrollbar_stylesheet()
        self.setStyleSheet(f"QDialog {{ background-color: {colors['surface']}; }}")
        self._summary_frame.setStyleSheet(
            f"QFrame#patrolSummary {{ background-color: {colors['surface']}; "
            f"border-bottom: 1px solid {colors['border']}; }}"
        )
        caption_ss = f"color: {colors['muted']}; font-size: 12px;"
        value_ss = f"color: {colors['text']}; font-size: 12px; font-weight: 600;"
        for label in (
            self._type_caption,
            self._depth_caption,
            self._targets_caption,
            self._created_caption,
        ):
            label.setStyleSheet(caption_ss)
        for label in (
            self._type_value,
            self._depth_value,
            self._targets_value,
            self._created_value,
        ):
            label.setStyleSheet(value_ss)
        header_ss = (
            f"color: {colors['muted']}; font-size: 11px; font-weight: 600; padding: 6px 0; "
            f"background-color: {colors['surface']}; border-bottom: 1px solid {colors['border']};"
        )
        self._log_header.setStyleSheet(header_ss)
        self._targets_header.setStyleSheet(header_ss)
        divider_ss = f"background-color: {colors['border']};"
        self._left_divider.setStyleSheet(divider_ss)
        self._right_divider.setStyleSheet(divider_ss)
        self._log_edit.setStyleSheet(
            f"QPlainTextEdit {{ background-color: {colors['base']}; color: {colors['text']}; "
            f"border: none; font-family: Consolas, 'Courier New', monospace; font-size: 12px; }}"
            + scrollbar_ss
        )
        target_hover = colors.get("alternate", colors["highlight"])
        target_accent = colors.get("accent", colors["highlight"])
        self._targets_list.setStyleSheet(
            f"QListWidget#patrolTargetStatusList {{ background-color: {colors['base']}; "
            f"color: {colors['text']}; border: none; border-radius: 0; "
            f"margin: 0; padding: 0; outline: 0; }} "
            f"QListWidget#patrolTargetStatusList::item {{ background-color: transparent; "
            f"margin: 0; padding: 0; border: none; "
            f"border-bottom: 1px solid {colors['border']}; }} "
            f"QListWidget#patrolTargetStatusList::item:hover, "
            f"QListWidget#patrolTargetStatusList::item:selected {{ "
            f"background-color: {target_hover}; border-left: 2px solid {target_accent}; "
            f"border-bottom: 1px solid {colors['border']}; }} " + scrollbar_ss
        )
        for row in self._target_rows:
            row.refresh_theme()
        self._media_preview.refresh_theme()
