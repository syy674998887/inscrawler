"""Small shared Qt controls."""

from __future__ import annotations

import inscrawler.ui.qt as ui_qt


class ClickableGroupBox(ui_qt.QGroupBox):
    clicked = ui_qt.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(ui_qt.Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event):
        if event.button() == ui_qt.Qt.MouseButton.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)
