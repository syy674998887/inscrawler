"""Cancellable shutdown countdown and result notifications."""

from __future__ import annotations

import inscrawler.ui.platform as ui_platform
import inscrawler.ui.qt as ui_qt


class ShutdownCountdownDialog(ui_qt.QDialog):
    def __init__(self, window, seconds, on_elapsed, on_cancel=None):
        super().__init__(None)
        self._window = window
        self._remaining = max(1, int(seconds))
        self._on_elapsed = on_elapsed
        self._on_cancel = on_cancel
        self._cancelled = False
        self._finished_naturally = False
        self.setModal(True)
        self.setWindowModality(ui_qt.Qt.WindowModality.ApplicationModal)
        self.setMinimumWidth(420)
        self.setWindowFlags(
            ui_qt.Qt.WindowType.Dialog
            | ui_qt.Qt.WindowType.CustomizeWindowHint
            | ui_qt.Qt.WindowType.WindowTitleHint
            | ui_qt.Qt.WindowType.WindowCloseButtonHint
            | ui_qt.Qt.WindowType.WindowStaysOnTopHint
            | ui_qt.Qt.WindowType.MSWindowsFixedSizeDialogHint
        )
        ui_platform._suppress_secondary_window_icon(self)

        layout = ui_qt.QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        self._label = ui_qt.QLabel()
        self._label.setWordWrap(True)
        layout.addWidget(self._label)

        button_row = ui_qt.QHBoxLayout()
        button_row.addStretch()
        self._cancel_button = ui_qt.QPushButton()
        self._cancel_button.setMinimumHeight(40)
        self._cancel_button.clicked.connect(self.cancel_shutdown)
        button_row.addWidget(self._cancel_button)
        layout.addLayout(button_row)

        self._timer = ui_qt.QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self.retranslate()

    def retranslate(self):
        window = self._window
        if window is None:
            return
        self.setWindowTitle(window.tr("shutdown_countdown_title"))
        self._cancel_button.setText(window.tr("shutdown_countdown_cancel"))
        self._sync_message()

    def _sync_message(self):
        window = self._window
        if window is None:
            return
        self._label.setText(
            window.tr("shutdown_countdown_message", seconds=self._remaining)
        )

    def start_countdown(self):
        if (
            not self._cancelled
            and not self._finished_naturally
            and not self._timer.isActive()
        ):
            self._timer.start()

    def _tick(self):
        if self._cancelled or self._finished_naturally:
            return
        self._remaining -= 1
        if self._remaining <= 0:
            self._timer.stop()
            self._finished_naturally = True
            callback = self._on_elapsed
            # Close the always-on-top countdown before requesting shutdown so
            # a command failure can show its error dialog in front.
            self.accept()
            if callable(callback):
                callback()
            return
        self._sync_message()

    def cancel_shutdown(self):
        if self._cancelled or self._finished_naturally:
            return
        self._cancelled = True
        self._timer.stop()
        callback = self._on_cancel
        if callable(callback):
            callback()
        super().reject()

    def reject(self):
        # Esc uses reject(), so it must stop the countdown too.
        self.cancel_shutdown()

    def closeEvent(self, event):
        if not self._cancelled and not self._finished_naturally:
            self.cancel_shutdown()
        event.accept()
