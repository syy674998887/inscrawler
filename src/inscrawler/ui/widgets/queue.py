"""Task rows, status presentation, and queue drag-and-drop widgets."""

from __future__ import annotations

import inscrawler.ui.qt as ui_qt


class QueueItemWidget(ui_qt.QWidget):
    hovered_changed = ui_qt.Signal(bool)
    clicked = ui_qt.Signal()
    drag_started = ui_qt.Signal()
    drag_moved = ui_qt.Signal(ui_qt.QPoint)
    drag_finished = ui_qt.Signal(ui_qt.QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(ui_qt.Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMouseTracking(True)
        self._divider_visible = True
        self._divider_color = ui_qt.QColor("#cccccc")
        self._divider_height = 1
        self._hovered = False
        self._drag_enabled = False
        self._press_pos = None
        self._moved_beyond_threshold = False
        self._dragging = False

    def is_hovered(self):
        return self._hovered

    def _set_hovered(self, hovered):
        hovered = bool(hovered)
        if self._hovered == hovered:
            return
        self._hovered = hovered
        self.hovered_changed.emit(hovered)

    def enterEvent(self, event):
        super().enterEvent(event)
        self._set_hovered(True)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._set_hovered(False)

    def set_drag_enabled(self, enabled):
        self._drag_enabled = bool(enabled)
        if not self._dragging:
            self.setCursor(ui_qt.Qt.CursorShape.ArrowCursor)

    def mousePressEvent(self, event):
        if event.button() == ui_qt.Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
            self._moved_beyond_threshold = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press_pos is not None and bool(
            event.buttons() & ui_qt.Qt.MouseButton.LeftButton
        ):
            if self._dragging or (
                (event.position().toPoint() - self._press_pos).manhattanLength()
                >= ui_qt.QApplication.startDragDistance()
            ):
                self._moved_beyond_threshold = True
                if self._drag_enabled:
                    if not self._dragging:
                        self._dragging = True
                        self.drag_started.emit()
                    self.setCursor(ui_qt.Qt.CursorShape.ClosedHandCursor)
                    self.drag_moved.emit(event.globalPosition().toPoint())
                else:
                    self.setCursor(ui_qt.Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if (
            event.button() == ui_qt.Qt.MouseButton.LeftButton
            and self._press_pos is not None
        ):
            # Finish a gesture that started as a drag even if queue state made
            # the row ineligible while the mouse was held.  The list validator
            # will reject a final move, but it still needs the finish signal to
            # stop edge scrolling and clear pointer-drag state.
            should_drag = self._dragging and self._moved_beyond_threshold
            should_click = not self._moved_beyond_threshold
            self._press_pos = None
            self._moved_beyond_threshold = False
            self._dragging = False
            self.set_drag_enabled(self._drag_enabled)
            if should_drag:
                self.drag_finished.emit(event.globalPosition().toPoint())
            elif should_click:
                self.clicked.emit()
            event.accept()
            return
        self._press_pos = None
        self._moved_beyond_threshold = False
        self._dragging = False
        super().mouseReleaseEvent(event)

    def set_divider_visible(self, visible):
        visible = bool(visible)
        if self._divider_visible != visible:
            self._divider_visible = visible
            self.update()

    def set_divider_color(self, color):
        next_color = ui_qt.QColor(color)
        if next_color != self._divider_color:
            self._divider_color = next_color
            self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._divider_visible or self.width() <= 8:
            return

        painter = ui_qt.QPainter(self)
        line_y = self.height() - self._divider_height
        gradient = ui_qt.QLinearGradient(0, 0, self.width(), 0)

        solid = ui_qt.QColor(self._divider_color)
        solid.setAlpha(min(255, max(solid.alpha(), 150)))
        transparent = ui_qt.QColor(solid)
        transparent.setAlpha(0)

        gradient.setColorAt(0.0, transparent)
        gradient.setColorAt(0.12, solid)
        gradient.setColorAt(0.88, solid)
        gradient.setColorAt(1.0, transparent)

        painter.fillRect(
            0, line_y, self.width(), self._divider_height, ui_qt.QBrush(gradient)
        )


class TaskQueueListWidget(ui_qt.QListWidget):
    """Pointer-driven queue reordering with GUI-owned validation."""

    order_preview_changed = ui_qt.Signal()
    order_changed = ui_qt.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drop_validator = None
        self._pointer_drag_item = None
        self._pointer_drag_global_position = None
        self._pointer_drag_changed = False
        self._pointer_drag_timer = ui_qt.QTimer(self)
        self._pointer_drag_timer.setInterval(50)
        self._pointer_drag_timer.timeout.connect(self._continue_pointer_drag)

    def set_drop_validator(self, validator):
        self._drop_validator = validator

    def _destination_row_for_global_position(self, item, global_position):
        source_row = self.row(item)
        if source_row < 0:
            return None

        position = self.viewport().mapFromGlobal(global_position)
        if position.y() < 0:
            destination_row = 0
        elif position.y() >= self.viewport().height():
            destination_row = self.count()
        else:
            # Reordering is vertical.  Keep using the pointer's y position if
            # it strays slightly beyond the viewport's left/right edge.
            clamped_x = max(0, min(position.x(), self.viewport().width() - 1))
            index = self.indexAt(ui_qt.QPoint(clamped_x, position.y()))
            if not index.isValid():
                destination_row = self.count()
            else:
                target_row = index.row()
                if target_row < source_row:
                    # Dropping anywhere on an earlier row means "before it".
                    destination_row = target_row
                elif target_row > source_row:
                    # Dropping anywhere on a later row means "after it".
                    destination_row = target_row + 1
                else:
                    return None
        return destination_row

    def _auto_scroll_for_global_position(self, global_position):
        position = self.viewport().mapFromGlobal(global_position)
        margin = min(32, max(18, self.viewport().height() // 6))
        scrollbar = self.verticalScrollBar()
        step = max(12, scrollbar.singleStep() * 2)
        old_value = scrollbar.value()
        if position.y() < margin:
            scrollbar.setValue(old_value - step)
        elif position.y() > self.viewport().height() - margin:
            scrollbar.setValue(old_value + step)
        return scrollbar.value() != old_value

    def begin_pointer_drag(self, item):
        if item is None or not bool(item.flags() & ui_qt.Qt.ItemFlag.ItemIsDragEnabled):
            return False
        if self.row(item) < 0:
            return False
        self.setCurrentItem(item)
        self._pointer_drag_item = item
        self._pointer_drag_global_position = None
        self._pointer_drag_changed = False
        self._pointer_drag_timer.start()
        return True

    def update_pointer_drag(self, item, global_position):
        if item is not self._pointer_drag_item:
            return False
        self._pointer_drag_global_position = ui_qt.QPoint(global_position)
        self._auto_scroll_for_global_position(global_position)

        source_row = self.row(item)
        destination_row = self._destination_row_for_global_position(
            item, global_position
        )
        if source_row < 0 or destination_row is None:
            return False

        validator = self._drop_validator
        if validator is not None and not validator(source_row, destination_row):
            return False
        if destination_row in (source_row, source_row + 1):
            return False

        moved = self.model().moveRow(
            ui_qt.QModelIndex(),
            source_row,
            ui_qt.QModelIndex(),
            destination_row,
        )
        if moved:
            self._pointer_drag_changed = True
            self.order_preview_changed.emit()
        return bool(moved)

    def _continue_pointer_drag(self):
        if (
            self._pointer_drag_item is None
            or self._pointer_drag_global_position is None
        ):
            return
        self.update_pointer_drag(
            self._pointer_drag_item,
            self._pointer_drag_global_position,
        )

    def finish_pointer_drag(self, item, global_position):
        if item is not self._pointer_drag_item:
            return False
        self.update_pointer_drag(item, global_position)
        changed = self._pointer_drag_changed
        self._pointer_drag_timer.stop()
        self._pointer_drag_item = None
        self._pointer_drag_global_position = None
        self._pointer_drag_changed = False
        if changed:
            self.order_changed.emit()
        return changed
