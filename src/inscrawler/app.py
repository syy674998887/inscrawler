"""Create the Qt application, configure its identity, and open the main window."""

from __future__ import annotations

import os
import sys

import inscrawler.paths as app_paths
import inscrawler.ui.main_window as ui_main_window
import inscrawler.ui.qt as ui_qt


def main():
    app = ui_qt.QApplication(sys.argv)
    app_font = app.font()
    app_font.setPointSize(11)
    app.setFont(app_font)
    app.setApplicationName("inscrawler")
    app.setOrganizationName("inscrawler")
    if os.path.exists(app_paths.ICON_FILE):
        app.setWindowIcon(ui_qt.QIcon(app_paths.ICON_FILE))

    # 设置应用图标和样式
    app.setStyle("Fusion")

    window = ui_main_window.GUI(capture_console=True)
    window.show()

    sys.exit(app.exec())
