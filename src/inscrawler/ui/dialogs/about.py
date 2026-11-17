"""Application identity and runtime dependency information."""

from __future__ import annotations

from instaloader import (
    __version__ as INSTALOADER_VERSION,
)

import inscrawler as inscrawler
import inscrawler.config as config
import inscrawler.ui.platform as ui_platform
import inscrawler.ui.qt as ui_qt


class AboutDialog(ui_qt.QDialog):
    def __init__(self, window):
        super().__init__(window)
        self._window = window
        self.setModal(True)
        self.setMinimumWidth(360)
        self.setWindowFlags(
            ui_qt.Qt.WindowType.Dialog
            | ui_qt.Qt.WindowType.CustomizeWindowHint
            | ui_qt.Qt.WindowType.WindowTitleHint
            | ui_qt.Qt.WindowType.WindowCloseButtonHint
            | ui_qt.Qt.WindowType.MSWindowsFixedSizeDialogHint
        )
        ui_platform._suppress_secondary_window_icon(self)

        layout = ui_qt.QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        form_layout = ui_qt.QGridLayout()
        form_layout.setHorizontalSpacing(12)
        form_layout.setVerticalSpacing(10)
        layout.addLayout(form_layout)

        self.version_caption = ui_qt.QLabel()
        self.version_value = ui_qt.QLabel()
        self.github_caption = ui_qt.QLabel()
        self.github_value = ui_qt.QLabel()
        self.instaloader_version_caption = ui_qt.QLabel()
        self.instaloader_version_value = ui_qt.QLabel()
        self.github_value.setTextFormat(ui_qt.Qt.TextFormat.RichText)
        self.github_value.setTextInteractionFlags(
            ui_qt.Qt.TextInteractionFlag.TextBrowserInteraction
        )
        self.github_value.setOpenExternalLinks(True)

        form_layout.addWidget(self.version_caption, 0, 0)
        form_layout.addWidget(self.version_value, 0, 1)
        form_layout.addWidget(self.github_caption, 1, 0)
        form_layout.addWidget(self.github_value, 1, 1)
        form_layout.addWidget(self.instaloader_version_caption, 2, 0)
        form_layout.addWidget(self.instaloader_version_value, 2, 1)
        form_layout.setColumnStretch(1, 1)

        self.retranslate_ui()

    def retranslate_ui(self):
        self.setWindowTitle(self._window.tr("about_title"))
        self.version_caption.setText(self._window.tr("about_version_label"))
        self.github_caption.setText(self._window.tr("about_github_label"))
        self.instaloader_version_caption.setText(
            self._window.tr("about_instaloader_version_label")
        )

        self.version_value.setText(f"V {inscrawler.__version__}")
        if config.APP_GITHUB_URL:
            self.github_value.setText(
                f'<a href="{config.APP_GITHUB_URL}">{config.APP_GITHUB_URL}</a>'
            )
        else:
            self.github_value.setText(self._window.tr("about_github_unset"))
        self.instaloader_version_value.setText(INSTALOADER_VERSION)
