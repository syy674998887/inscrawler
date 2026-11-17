"""Task log, details, and media-preview dialog."""

from __future__ import annotations

import inscrawler.tasks.schema as tasks_schema
import inscrawler.ui.platform as ui_platform
import inscrawler.ui.preview.panel as ui_preview_panel
import inscrawler.ui.qt as ui_qt


class TaskLogDialog(ui_qt.QDialog):
    """任务日志查看窗口 — 显示单个任务的下载日志"""

    def __init__(self, window, task_entry):
        super().__init__(window)
        self.setAttribute(ui_qt.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._window = window
        self._task_entry = task_entry
        self.setModal(True)
        self.setMinimumSize(900, 460)
        self.resize(960, 900)
        self.setWindowFlags(
            ui_qt.Qt.WindowType.Dialog
            | ui_qt.Qt.WindowType.CustomizeWindowHint
            | ui_qt.Qt.WindowType.WindowTitleHint
            | ui_qt.Qt.WindowType.WindowCloseButtonHint
        )
        ui_platform._suppress_secondary_window_icon(self)
        self.setWindowTitle(self._window.tr("task_log_title"))

        layout = ui_qt.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 顶部任务概况
        self._summary_frame = ui_qt.QFrame()
        self._summary_frame.setObjectName("logSummary")
        summary_layout = ui_qt.QGridLayout(self._summary_frame)
        summary_layout.setContentsMargins(14, 12, 14, 12)
        summary_layout.setHorizontalSpacing(12)
        summary_layout.setVerticalSpacing(8)

        self._target_caption = ui_qt.QLabel()
        self._target_value = ui_qt.QLabel()
        self._type_caption = ui_qt.QLabel()
        self._type_value = ui_qt.QLabel()
        self._limit_caption = ui_qt.QLabel()
        self._limit_value = ui_qt.QLabel()
        self._request_cap_caption = ui_qt.QLabel()
        self._request_cap_value = ui_qt.QLabel()
        self._created_caption = ui_qt.QLabel()
        self._created_value = ui_qt.QLabel()

        summary_layout.addWidget(self._target_caption, 0, 0)
        summary_layout.addWidget(self._target_value, 0, 1)
        summary_layout.addWidget(self._created_caption, 0, 2)
        summary_layout.addWidget(self._created_value, 0, 3)
        summary_layout.addWidget(self._type_caption, 1, 0)
        summary_layout.addWidget(self._type_value, 1, 1)
        summary_layout.addWidget(self._limit_caption, 1, 2)
        summary_layout.addWidget(self._limit_value, 1, 3)
        summary_layout.addWidget(self._request_cap_caption, 2, 0)
        summary_layout.addWidget(self._request_cap_value, 2, 1)
        summary_layout.setColumnStretch(1, 1)
        summary_layout.setColumnStretch(3, 1)

        content_layout = ui_qt.QHBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # 左侧面板：任务概况 + 日志
        log_panel = ui_qt.QWidget()
        log_panel.setFixedWidth(self.minimumWidth() // 2)
        log_panel.setSizePolicy(
            ui_qt.QSizePolicy.Policy.Fixed, ui_qt.QSizePolicy.Policy.Expanding
        )
        log_vbox = ui_qt.QVBoxLayout(log_panel)
        log_vbox.setContentsMargins(0, 0, 0, 0)
        log_vbox.setSpacing(0)

        log_vbox.addWidget(self._summary_frame)

        self._log_header = ui_qt.QLabel()
        self._log_header.setAlignment(ui_qt.Qt.AlignmentFlag.AlignCenter)
        log_vbox.addWidget(self._log_header)
        self._log_edit = ui_qt.QPlainTextEdit()
        self._log_edit.setReadOnly(True)
        self._log_edit.setLineWrapMode(ui_qt.QPlainTextEdit.LineWrapMode.NoWrap)
        self._log_edit.document().setDocumentMargin(0)
        self._log_edit.setViewportMargins(0, 0, 0, 0)
        log_vbox.addWidget(self._log_edit, 1)

        content_layout.addWidget(log_panel)

        self._detail_divider = ui_qt.QFrame()
        self._detail_divider.setFixedWidth(1)
        self._detail_divider.setFrameShape(ui_qt.QFrame.Shape.NoFrame)
        self._detail_divider.setSizePolicy(
            ui_qt.QSizePolicy.Policy.Fixed, ui_qt.QSizePolicy.Policy.Expanding
        )
        self._detail_divider.setAttribute(
            ui_qt.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
        )
        content_layout.addWidget(self._detail_divider)

        self._media_preview = ui_preview_panel.TaskMediaPreviewPanel(
            self._window, self._task_entry
        )
        self._preview_cells = self._media_preview._preview_cells
        content_layout.addWidget(self._media_preview, 1)

        layout.addLayout(content_layout, 1)

        self._load_existing_logs()
        self._refresh_theme()
        self._retranslate()

    def done(self, result):
        media_preview = getattr(self, "_media_preview", None)
        if media_preview is not None:
            media_preview.release_video_thumbnail_requests()
        super().done(result)

    def _retranslate(self):
        self.setWindowTitle(self._window.tr("task_log_title"))
        task = self._task_entry["task"]

        self._target_caption.setText(self._window.tr("target_label"))
        self._type_caption.setText(self._window.tr("download_type_label"))
        self._limit_caption.setText(self._window.tr("download_items_label"))
        self._limit_caption.setVisible(True)
        self._limit_value.setVisible(True)
        request_cap = task.get("media_request_cap")
        request_cap_visible = request_cap is not None
        self._request_cap_caption.setText(self._window.tr("task_download_limit_label"))
        self._request_cap_caption.setVisible(request_cap_visible)
        self._request_cap_value.setText(str(request_cap) if request_cap_visible else "")
        self._request_cap_value.setVisible(request_cap_visible)
        self._log_header.setText(self._window.tr("log_pane_title"))
        self._created_caption.setText(self._window.tr("log_created_at_label"))

        self._target_value.setText(str(task["target_user"]).strip().lstrip("@"))
        self._type_value.setText(task["download_media_type"].capitalize())
        self._limit_value.setText(str(task["downloaded_count"]))
        self._created_value.setText(tasks_schema._format_task_created_minute(task))

    def _load_existing_logs(self):
        logs = self._task_entry["logs"]
        if not logs:
            return
        self._log_edit.setPlainText("\n".join(logs))
        scrollbar = self._log_edit.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def append_log(self, line):
        """实时追加一行归属于当前任务的控制台日志。"""
        self._log_edit.appendPlainText(line)

    def _refresh_theme(self):
        colors = self._window._theme_tokens()
        font_family = "Consolas, 'Courier New', monospace"
        self.setStyleSheet(f"QDialog {{ background-color: {colors['surface']}; }}")
        self._summary_frame.setStyleSheet(
            f"QFrame#logSummary {{"
            f"  background-color: {colors['surface']};"
            f"  border-bottom: 1px solid {colors['border']};"
            f"}}"
        )
        caption_ss = f"color: {colors['muted']}; font-size: 12px;"
        value_ss = f"color: {colors['text']}; font-size: 12px; font-weight: 600;"
        for caption in (
            self._target_caption,
            self._type_caption,
            self._limit_caption,
            self._request_cap_caption,
            self._created_caption,
        ):
            caption.setStyleSheet(caption_ss)
        for value in (
            self._target_value,
            self._type_value,
            self._limit_value,
            self._request_cap_value,
            self._created_value,
        ):
            value.setStyleSheet(value_ss)
        scrollbar_ss = self._window._scrollbar_stylesheet()
        edit_ss = (
            f"QPlainTextEdit {{"
            f"  background-color: {colors['base']};"
            f"  color: {colors['text']};"
            f"  border: none;"
            f"  font-family: {font_family};"
            f"  font-size: 12px;"
            f"  padding: 0;"
            f"  selection-background-color: {colors['highlight']};"
            f"  selection-color: {colors['highlight_text']};"
            f"}}" + scrollbar_ss
        )
        self._log_edit.setStyleSheet(edit_ss)
        header_ss = (
            f"color: {colors['muted']}; "
            f"font-size: 11px; "
            f"font-weight: 600; "
            f"padding: 6px 0; "
            f"background-color: {colors['surface']}; "
            f"border-bottom: 1px solid {colors['border']};"
        )
        self._log_header.setStyleSheet(header_ss)
        self._detail_divider.setStyleSheet(f"background-color: {colors['border']};")
        self._media_preview.refresh_theme()

    def add_preview_image(self, image_path):
        self._media_preview.add_preview_image(image_path)
