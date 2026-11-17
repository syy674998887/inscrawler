"""Frameless-window title bar and window-control buttons."""

from __future__ import annotations

import os

import inscrawler.paths as app_paths
import inscrawler.ui.constants as ui_constants
import inscrawler.ui.qt as ui_qt
import inscrawler.ui.theme.icons as ui_theme_icons


class TitleBar(ui_qt.QWidget):
    def __init__(self, window):
        super().__init__(window)
        self._window = window
        self.setObjectName("customTitleBar")
        self.setFixedHeight(ui_constants.TITLEBAR_HEIGHT)

        layout = ui_qt.QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 0, 0)
        layout.setSpacing(0)

        self.icon_label = ui_qt.QLabel()
        self.icon_label.setObjectName("titleBarIcon")
        self.icon_label.setFixedSize(20, 20)
        self.icon_label.setAlignment(ui_qt.Qt.AlignmentFlag.AlignCenter)
        if os.path.exists(app_paths.ICON_FILE):
            self.icon_label.setPixmap(
                ui_qt.QIcon(app_paths.ICON_FILE).pixmap(ui_qt.QSize(18, 18))
            )
        layout.addWidget(self.icon_label)

        self.title_label = ui_qt.QLabel()
        self.title_label.setObjectName("titleBarTitle")
        layout.addSpacing(10)
        layout.addWidget(self.title_label)
        layout.addStretch()

        self.help_button = self._create_button("help")
        self.help_button.clicked.connect(self._window.open_about_dialog)
        layout.addWidget(self.help_button)

        self.settings_button = self._create_button("settings")
        self.settings_button.clicked.connect(self._window.open_settings_dialog)
        layout.addWidget(self.settings_button)

        self.minimize_button = self._create_button("minimize")
        self.minimize_button.clicked.connect(self._window.showMinimized)
        layout.addWidget(self.minimize_button)

        self.maximize_button = self._create_button("maximize")
        self.maximize_button.clicked.connect(self.toggle_maximize)
        layout.addWidget(self.maximize_button)

        self.close_button = self._create_button("close", role="close")
        self.close_button.clicked.connect(self._window.close)
        layout.addWidget(self.close_button)

    def _create_button(self, icon_name, role="normal"):
        button = ui_qt.QToolButton(self)
        button.setAutoRaise(True)
        button.setFixedSize(46, ui_constants.TITLEBAR_HEIGHT)
        button.setProperty("titlebarButton", True)
        button.setProperty("titlebarRole", role)
        button.setFocusPolicy(ui_qt.Qt.FocusPolicy.NoFocus)
        button.setCursor(ui_qt.Qt.CursorShape.ArrowCursor)
        button.setProperty("icon_name", icon_name)
        return button

    def _set_button_icon(self, button, icon_name, color):
        svg = ui_theme_icons._TITLEBAR_SVG[icon_name].format(color=color)
        icon = ui_theme_icons._make_svg_icon(svg, size=14)
        if icon is not None:
            button.setIcon(icon)
            button.setIconSize(ui_qt.QSize(14, 14))

    def sync_ui(self):
        colors = self._window._theme_tokens()
        icon_color = colors["titlebar_text"]
        self.title_label.setText(self._window.tr("app_title"))
        self._set_button_icon(self.help_button, "help", icon_color)
        self._set_button_icon(self.settings_button, "settings", icon_color)
        self._set_button_icon(self.minimize_button, "minimize", icon_color)
        max_icon = "restore" if self._window.isMaximized() else "maximize"
        self._set_button_icon(self.maximize_button, max_icon, icon_color)
        self._set_button_icon(self.close_button, "close", icon_color)
        self.help_button.setToolTip(self._window.tr("titlebar_help_tooltip"))
        self.settings_button.setToolTip(self._window.tr("titlebar_settings_tooltip"))
        self.minimize_button.setToolTip(self._window.tr("titlebar_minimize_tooltip"))
        self.maximize_button.setToolTip(
            self._window.tr("titlebar_restore_tooltip")
            if self._window.isMaximized()
            else self._window.tr("titlebar_maximize_tooltip")
        )
        self.close_button.setToolTip(self._window.tr("titlebar_close_tooltip"))
        self.style().unpolish(self)
        self.style().polish(self)

    def _is_drag_region(self, pos):
        target = self.childAt(pos)
        while target is not None and target is not self:
            if isinstance(target, ui_qt.QToolButton):
                return False
            target = target.parentWidget()
        return True

    def mousePressEvent(self, event):
        if event.button() == ui_qt.Qt.MouseButton.LeftButton and self._is_drag_region(
            event.position().toPoint()
        ):
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemMove()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == ui_qt.Qt.MouseButton.LeftButton and self._is_drag_region(
            event.position().toPoint()
        ):
            self.toggle_maximize()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def toggle_maximize(self):
        if self._window.isMaximized():
            self._window.showNormal()
        else:
            self._window.showMaximized()
        self.sync_ui()
