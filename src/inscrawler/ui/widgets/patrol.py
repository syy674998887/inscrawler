"""Patrol target status rows and account list widgets."""

from __future__ import annotations

import inscrawler.ui.qt as ui_qt


class PatrolTargetStatusListWidget(ui_qt.QListWidget):
    """Single-select target list that can also clear its filter on blank space."""

    blank_clicked = ui_qt.Signal()

    def mousePressEvent(self, event):
        position = (
            event.position().toPoint() if hasattr(event, "position") else event.pos()
        )
        clicked_blank = self.itemAt(position) is None
        super().mousePressEvent(event)
        if clicked_blank:
            self.clearSelection()
            self.setCurrentRow(-1)
            self.blank_clicked.emit()


class PatrolTargetStatusRow(ui_qt.QWidget):
    """Two-line patrol target summary with an optional local avatar."""

    def __init__(self, window, task, username, account, state_text, parent=None):
        super().__init__(parent)
        self._window = window
        self._username = str(username or "").strip().lstrip("@")
        self.setObjectName("patrolTargetStatusRow")
        self.setAttribute(ui_qt.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setMinimumHeight(60)
        self.setSizePolicy(
            ui_qt.QSizePolicy.Policy.Expanding, ui_qt.QSizePolicy.Policy.Fixed
        )

        layout = ui_qt.QVBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(2)

        self._top_line_layout = ui_qt.QHBoxLayout()
        self._top_line_layout.setContentsMargins(0, 0, 0, 0)
        self._top_line_layout.setSpacing(0)

        # Give the label a parent before setVisible() below.  A visible,
        # parentless QWidget is briefly shown by Qt as its own native window.
        self._avatar_label = ui_qt.QLabel(self)
        self._avatar_label.setObjectName("patrolTargetAvatar")
        self._avatar_label.setFixedSize(ui_qt.QSize(36, 28))
        self._avatar_label.setAlignment(
            ui_qt.Qt.AlignmentFlag.AlignLeft | ui_qt.Qt.AlignmentFlag.AlignVCenter
        )
        avatar = self._window._target_profile_picture_icon(
            self._username,
            (task or {}).get("save_path"),
        )
        has_avatar = not avatar.isNull()
        if has_avatar:
            self._avatar_label.setPixmap(avatar.pixmap(ui_qt.QSize(28, 28)))
        self._avatar_label.setVisible(has_avatar)
        self._top_line_layout.addWidget(self._avatar_label)

        self._username_label = ui_qt.QLabel(self._username)
        self._username_label.setObjectName("patrolTargetUsername")
        self._username_label.setAlignment(
            ui_qt.Qt.AlignmentFlag.AlignLeft | ui_qt.Qt.AlignmentFlag.AlignVCenter
        )
        self._username_label.setMinimumWidth(0)
        self._username_label.setSizePolicy(
            ui_qt.QSizePolicy.Policy.Ignored,
            ui_qt.QSizePolicy.Policy.Preferred,
        )
        self._top_line_layout.addWidget(self._username_label, 1)

        self._status_label = ui_qt.QLabel()
        self._status_label.setObjectName("patrolTargetStatus")
        self._status_label.setAlignment(
            ui_qt.Qt.AlignmentFlag.AlignRight | ui_qt.Qt.AlignmentFlag.AlignVCenter
        )
        self._status_label.setSizePolicy(
            ui_qt.QSizePolicy.Policy.Minimum,
            ui_qt.QSizePolicy.Policy.Preferred,
        )
        self._top_line_layout.addWidget(self._status_label)
        layout.addLayout(self._top_line_layout)

        self._metrics_label = ui_qt.QLabel()
        self._metrics_label.setObjectName("patrolTargetMetrics")
        self._metrics_label.setAlignment(
            ui_qt.Qt.AlignmentFlag.AlignLeft | ui_qt.Qt.AlignmentFlag.AlignVCenter
        )
        layout.addWidget(self._metrics_label)

        self.update_account(account, state_text)
        self.refresh_theme()

    @staticmethod
    def _nonnegative_int(value):
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    def update_account(self, account, state_text):
        account = account or {}
        self._status_label.setText(str(state_text or ""))
        self._metrics_label.setText(
            self._window.tr(
                "patrol_target_metrics",
                checked=self._nonnegative_int(account.get("checked_count")),
                downloaded=self._nonnegative_int(account.get("total_downloaded_count")),
            )
        )
        message = str(account.get("message") or "").strip()
        self.setToolTip(message)
        for child in (
            self._avatar_label,
            self._username_label,
            self._status_label,
            self._metrics_label,
        ):
            child.setToolTip(message)

    def refresh_theme(self):
        colors = self._window._theme_tokens()
        self.setStyleSheet(
            "QWidget#patrolTargetStatusRow { background-color: transparent; }"
        )
        self._username_label.setStyleSheet(
            f"color: {colors['text']}; font-size: 12px; font-weight: 600;"
        )
        self._status_label.setStyleSheet(f"color: {colors['text']}; font-size: 12px;")
        self._metrics_label.setStyleSheet(f"color: {colors['muted']}; font-size: 11px;")
