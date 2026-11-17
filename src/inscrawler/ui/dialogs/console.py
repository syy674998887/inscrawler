"""Read-only application console dialog."""

from __future__ import annotations

import inscrawler.ui.platform as ui_platform
import inscrawler.ui.qt as ui_qt


class ConsoleLogDialog(ui_qt.QDialog):
    """显示当前进程内收集到的控制台日志。"""

    def __init__(self, window):
        super().__init__(window)
        self._window = window
        self.setMinimumSize(900, 460)
        self.resize(960, 700)
        self.setWindowFlags(
            ui_qt.Qt.WindowType.Dialog
            | ui_qt.Qt.WindowType.CustomizeWindowHint
            | ui_qt.Qt.WindowType.WindowTitleHint
            | ui_qt.Qt.WindowType.WindowCloseButtonHint
        )
        ui_platform._suppress_secondary_window_icon(self)

        layout = ui_qt.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._log_edit = ui_qt.QPlainTextEdit()
        self._log_edit.setReadOnly(True)
        self._log_edit.setLineWrapMode(ui_qt.QPlainTextEdit.LineWrapMode.NoWrap)
        self._log_edit.document().setDocumentMargin(0)
        self._log_edit.setViewportMargins(0, 0, 0, 0)
        layout.addWidget(self._log_edit, 1)

        self.refresh_theme()
        self.retranslate()
        self.set_logs(self._window.console_logs)

    def retranslate(self):
        self.setWindowTitle(self._window.tr("console_log_title"))

    def refresh_theme(self):
        colors = self._window._theme_tokens()
        font_family = "Consolas, 'Courier New', monospace"
        self.setStyleSheet(f"QDialog {{ background-color: {colors['surface']}; }}")
        self._log_edit.setStyleSheet(
            f"QPlainTextEdit {{"
            f"  background-color: {colors['base']};"
            f"  color: {colors['text']};"
            f"  border: none;"
            f"  font-family: {font_family};"
            f"  font-size: 12px;"
            f"  padding: 0;"
            f"  selection-background-color: {colors['highlight']};"
            f"  selection-color: {colors['highlight_text']};"
            f"}}" + self._window._scrollbar_stylesheet()
        )

    def set_logs(self, lines):
        self._log_edit.setPlainText("\n".join(str(line) for line in lines))
        scrollbar = self._log_edit.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def append_log(self, line):
        self._log_edit.appendPlainText(str(line))
