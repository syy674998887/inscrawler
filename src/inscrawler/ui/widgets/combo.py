"""Aligned combo boxes, popup views, and item delegates."""

from __future__ import annotations

import inscrawler.ui.qt as ui_qt
import inscrawler.ui.theme.icons as ui_theme_icons


class FixedHeightItemDelegate(ui_qt.QStyledItemDelegate):
    def __init__(self, height, parent=None):
        super().__init__(parent)
        self._height = height

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        size.setHeight(self._height)
        return size


class ComboPopupListWidget(ui_qt.QListWidget):
    item_action_requested = ui_qt.Signal(int)
    ACTION_BUTTON_SIZE = 30
    ACTION_RIGHT_MARGIN = 7

    def __init__(self, parent=None):
        super().__init__(parent)
        self._item_action_enabled = False
        self._item_action_icon = None
        self._item_action_tooltip = ""
        self._action_danger_color = ui_qt.QColor("#e74c3c")
        self._pressed_action_row = None
        self._hover_action_row = -1
        self.viewport().setMouseTracking(True)

    def set_item_action(self, enabled, icon=None, tooltip=""):
        self._item_action_enabled = bool(enabled)
        self._item_action_icon = icon
        self._item_action_tooltip = str(tooltip or "")
        self.viewport().update()

    def set_item_action_tooltip(self, tooltip):
        self._item_action_tooltip = str(tooltip or "")

    def set_item_action_danger_color(self, danger):
        self._action_danger_color = ui_qt.QColor(danger)
        self.viewport().update()

    def item_action_rect(self, item_rect):
        size = self.ACTION_BUTTON_SIZE
        right = item_rect.right() - self.ACTION_RIGHT_MARGIN
        return ui_qt.QRect(
            right - size + 1,
            item_rect.center().y() - size // 2,
            size,
            size,
        )

    def _action_row_at(self, position):
        if not self._item_action_enabled:
            return -1
        index = self.indexAt(position)
        if not index.isValid():
            return -1
        action_rect = self.item_action_rect(self.visualRect(index))
        return index.row() if action_rect.contains(position) else -1

    def mousePressEvent(self, event):
        if event.button() == ui_qt.Qt.MouseButton.LeftButton:
            row = self._action_row_at(event.position().toPoint())
            if row >= 0:
                self._pressed_action_row = row
                event.accept()
                return
        self._pressed_action_row = None
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._pressed_action_row is not None:
            pressed_row = self._pressed_action_row
            self._pressed_action_row = None
            row = self._action_row_at(event.position().toPoint())
            if event.button() == ui_qt.Qt.MouseButton.LeftButton and row == pressed_row:
                self.item_action_requested.emit(row)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        row = self._action_row_at(event.position().toPoint())
        if row != self._hover_action_row:
            self._hover_action_row = row
            self.viewport().setCursor(
                ui_qt.Qt.CursorShape.PointingHandCursor
                if row >= 0
                else ui_qt.Qt.CursorShape.ArrowCursor
            )
            self.viewport().setToolTip(self._item_action_tooltip if row >= 0 else "")
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._hover_action_row = -1
        self.viewport().setCursor(ui_qt.Qt.CursorShape.ArrowCursor)
        self.viewport().setToolTip("")
        super().leaveEvent(event)


class ComboPopupItemDelegate(FixedHeightItemDelegate):
    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        view = self.parent()
        if not isinstance(view, ComboPopupListWidget) or not view._item_action_enabled:
            return

        action_rect = view.item_action_rect(option.rect)
        painter.save()
        painter.setRenderHint(ui_qt.QPainter.RenderHint.Antialiasing)

        icon = view._item_action_icon
        if icon is not None and not icon.isNull():
            icon_size = ui_qt.QSize(
                ui_theme_icons.DEFAULT_ICON_SIZE, ui_theme_icons.DEFAULT_ICON_SIZE
            )
            pixmap = icon.pixmap(icon_size)
            icon_x = action_rect.center().x() - pixmap.width() // 2
            icon_y = option.rect.top() + (option.rect.height() - pixmap.height()) // 2
            painter.drawPixmap(icon_x, icon_y, pixmap)
        else:
            center_x = action_rect.center().x()
            center_y = option.rect.top() + option.rect.height() // 2
            half_size = 6
            painter.setPen(
                ui_qt.QPen(
                    view._action_danger_color,
                    2.0,
                    ui_qt.Qt.PenStyle.SolidLine,
                    ui_qt.Qt.PenCapStyle.RoundCap,
                )
            )
            painter.drawLine(
                center_x - half_size,
                center_y - half_size,
                center_x + half_size,
                center_y + half_size,
            )
            painter.drawLine(
                center_x + half_size,
                center_y - half_size,
                center_x - half_size,
                center_y + half_size,
            )
        painter.restore()


class ComboPopupFrame(ui_qt.QFrame):
    def __init__(self, combo):
        super().__init__(
            combo, ui_qt.Qt.WindowType.Popup | ui_qt.Qt.WindowType.FramelessWindowHint
        )
        self._combo = combo
        self.setObjectName("comboPopupContainer")
        self.setAttribute(ui_qt.Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFrameShape(ui_qt.QFrame.Shape.NoFrame)
        self.setLineWidth(0)

        layout = ui_qt.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._view = ComboPopupListWidget(self)
        self._view.setObjectName("comboPopupView")
        self._view.setFrameShape(ui_qt.QFrame.Shape.NoFrame)
        self._view.setSpacing(0)
        self._view.setUniformItemSizes(True)
        self._view.setSelectionMode(
            ui_qt.QAbstractItemView.SelectionMode.SingleSelection
        )
        self._view.setVerticalScrollMode(
            ui_qt.QAbstractItemView.ScrollMode.ScrollPerItem
        )
        self._view.setVerticalScrollBarPolicy(
            ui_qt.Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._view.setHorizontalScrollBarPolicy(
            ui_qt.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._view.setItemDelegate(
            ComboPopupItemDelegate(combo.POPUP_ITEM_HEIGHT, self._view)
        )
        self._view.itemClicked.connect(self._on_item_activated)
        self._view.itemActivated.connect(self._on_item_activated)
        self._view.item_action_requested.connect(self._combo._request_popup_item_action)
        layout.addWidget(self._view)
        self._animation = ui_qt.QParallelAnimationGroup(self)
        self._pos_animation = ui_qt.QPropertyAnimation(self, b"pos", self)
        self._pos_animation.setDuration(100)
        self._pos_animation.setEasingCurve(ui_qt.QEasingCurve.Type.OutCubic)
        self._opacity_animation = ui_qt.QPropertyAnimation(self, b"windowOpacity", self)
        self._opacity_animation.setDuration(100)
        self._opacity_animation.setEasingCurve(ui_qt.QEasingCurve.Type.OutCubic)
        self._animation.addAnimation(self._pos_animation)
        self._animation.addAnimation(self._opacity_animation)

    def view(self):
        return self._view

    def _on_item_activated(self, item):
        self._combo._select_popup_item(item)

    def show_animated(self, final_geometry, scroll_value=0):
        start_pos = ui_qt.QPoint(final_geometry.x(), final_geometry.y() - 6)
        end_pos = final_geometry.topLeft()
        self._animation.stop()
        self.setWindowOpacity(0.0)
        self.setGeometry(final_geometry)
        self.move(start_pos)
        # Reset scroll after geometry is applied to prevent auto-scroll artifacts
        self._view.verticalScrollBar().setValue(scroll_value)
        self.show()
        self.raise_()
        self._pos_animation.setStartValue(start_pos)
        self._pos_animation.setEndValue(end_pos)
        self._opacity_animation.setStartValue(0.0)
        self._opacity_animation.setEndValue(1.0)
        self._animation.start()

    def hideEvent(self, event):
        self._animation.stop()
        self.setWindowOpacity(1.0)
        super().hideEvent(event)
        self._combo._on_custom_popup_hidden()


class AlignedComboBox(ui_qt.QComboBox):
    item_action_requested = ui_qt.Signal(str)
    POPUP_ITEM_HEIGHT = 44

    def __init__(self, parent=None):
        super().__init__(parent)
        self._arrow_color = ui_qt.QColor("#547a86")
        self._arrow_disabled_color = ui_qt.QColor("#74695c")
        self._popup_open = False
        self._popup_hiding = False
        self._popup_frame = ComboPopupFrame(self)
        app = ui_qt.QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def set_arrow_colors(self, color, disabled_color=None):
        self._arrow_color = ui_qt.QColor(color)
        self._arrow_disabled_color = ui_qt.QColor(disabled_color or color)
        self.update()

    def mousePressEvent(self, event):
        if (
            event.button() == ui_qt.Qt.MouseButton.LeftButton
            and self._popup_open
            and self._popup_frame.isVisible()
        ):
            self.hidePopup()
            event.accept()
            return
        super().mousePressEvent(event)

    def eventFilter(self, watched, event):
        if (
            self._popup_open
            and self._popup_frame.isVisible()
            and event.type() == ui_qt.QEvent.Type.MouseButtonPress
            and hasattr(event, "globalPosition")
        ):
            global_pos = event.globalPosition().toPoint()
            combo_rect = ui_qt.QRect(self.mapToGlobal(ui_qt.QPoint(0, 0)), self.size())
            popup_rect = self._popup_frame.geometry()
            if combo_rect.contains(global_pos):
                self.hidePopup()
                return True
            if popup_rect.contains(global_pos):
                return False
        return super().eventFilter(watched, event)

    def popup_view(self):
        return self._popup_frame.view()

    def popup_container(self):
        return self._popup_frame

    def set_item_action(self, icon, tooltip=""):
        self._popup_frame.view().set_item_action(True, icon, tooltip)

    def set_item_action_tooltip(self, tooltip):
        self._popup_frame.view().set_item_action_tooltip(tooltip)

    def showPopup(self):
        if self._popup_open and self._popup_frame.isVisible():
            self.hidePopup()
            return
        if self.count() <= 0:
            return
        popup_geometry, scroll_value = self._prepare_popup()
        self._popup_open = True
        self.update()
        self._popup_frame.show_animated(popup_geometry, scroll_value)
        self._popup_frame.view().setFocus()

    def hidePopup(self):
        if self._popup_hiding:
            return
        try:
            self._popup_hiding = True
            if self._popup_frame.isVisible():
                self._popup_frame.hide()
            self._popup_open = False
            self.update()
            super().hidePopup()
        finally:
            self._popup_hiding = False

    def _on_custom_popup_hidden(self):
        if self._popup_hiding:
            return
        try:
            self._popup_hiding = True
            self._popup_open = False
            self.update()
            super().hidePopup()
        finally:
            self._popup_hiding = False

    def _select_popup_index(self, index):
        if not index.isValid():
            return
        if index.row() != self.currentIndex():
            self.setCurrentIndex(index.row())
        self.hidePopup()

    def _select_popup_item(self, item):
        if item is None:
            return
        row = item.data(ui_qt.Qt.ItemDataRole.UserRole)
        if row is None:
            return
        if row != self.currentIndex():
            self.setCurrentIndex(row)
        self.hidePopup()

    def _request_popup_item_action(self, row):
        if row < 0 or row >= self.count():
            return
        text = self.itemText(row)
        self.hidePopup()
        self.item_action_requested.emit(text)

    def _prepare_popup(self):
        popup = self._popup_frame
        popup_view = popup.view()
        popup_view.clear()
        popup_view.setIconSize(self.iconSize())

        for row in range(self.count()):
            item = ui_qt.QListWidgetItem(self.itemIcon(row), self.itemText(row))
            item.setData(ui_qt.Qt.ItemDataRole.UserRole, row)
            item.setData(ui_qt.Qt.ItemDataRole.AccessibleTextRole, self.itemText(row))
            item.setSizeHint(ui_qt.QSize(self.width(), self.POPUP_ITEM_HEIGHT))
            popup_view.addItem(item)

        visible_count = min(self.count(), max(1, self.maxVisibleItems()))
        popup_view.setVerticalScrollBarPolicy(
            ui_qt.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
            if self.count() <= visible_count
            else ui_qt.Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )

        current_row = self.currentIndex()
        popup_view.verticalScrollBar().setValue(0)
        if current_row >= 0:
            popup_view.setCurrentRow(current_row)
        else:
            popup_view.clearSelection()

        popup_view.doItemsLayout()
        popup_view.updateGeometries()

        if self.count() > visible_count:
            max_first_row = max(0, self.count() - visible_count)
            first_visible_row = (
                0 if current_row < 0 else min(max(current_row, 0), max_first_row)
            )
            scroll_value = first_visible_row * self.POPUP_ITEM_HEIGHT
        else:
            scroll_value = 0
        popup_view.verticalScrollBar().setValue(scroll_value)

        popup_width = self.width()
        popup_height = self._popup_height()
        screen_geometry = self.screen().availableGeometry() if self.screen() else None
        target_pos = self.mapToGlobal(ui_qt.QPoint(0, self.height() - 1))

        x = target_pos.x()
        y = target_pos.y()

        if screen_geometry is not None:
            if x + popup_width > screen_geometry.right():
                x = max(
                    screen_geometry.left(), screen_geometry.right() - popup_width + 1
                )
            if y + popup_height > screen_geometry.bottom():
                y = self.mapToGlobal(ui_qt.QPoint(0, 1 - popup_height)).y()

        return ui_qt.QRect(x, y, popup_width, popup_height), scroll_value

    def _popup_height(self):
        visible_count = min(self.count(), max(1, self.maxVisibleItems()))
        if visible_count <= 0:
            return 0
        row_height = self.POPUP_ITEM_HEIGHT
        content_height = row_height * visible_count
        # Account for the popup frame's border (from stylesheet) so the
        # QListWidget viewport is large enough to display all items without
        # Qt auto-scrolling when the last item is selected.
        popup = self._popup_frame
        frame_rect = popup.rect()
        content_rect = popup.contentsRect()
        border_overhead = (
            (frame_rect.height() - content_rect.height())
            if frame_rect.height() > 0
            else 2
        )
        return content_height + border_overhead

    def paintEvent(self, event):
        super().paintEvent(event)

        option = ui_qt.QStyleOptionComboBox()
        self.initStyleOption(option)
        arrow_rect = self.style().subControlRect(
            ui_qt.QStyle.ComplexControl.CC_ComboBox,
            option,
            ui_qt.QStyle.SubControl.SC_ComboBoxArrow,
            self,
        )

        painter = ui_qt.QPainter(self)
        painter.setRenderHint(ui_qt.QPainter.RenderHint.Antialiasing)
        arrow_direction = "open" if self._popup_open else "closed"
        arrow_color = (
            self._arrow_color if self.isEnabled() else self._arrow_disabled_color
        ).name()
        if ui_qt._HAS_SVG:
            svg = ui_theme_icons._COMBO_ARROW_SVG[arrow_direction].format(
                color=arrow_color
            )
            icon_size = ui_qt.QSize(12, 12)
            pixmap = ui_theme_icons._make_svg_pixmap(svg, icon_size)
            icon_x = arrow_rect.center().x() - (pixmap.width() // 2)
            icon_y = arrow_rect.center().y() - (pixmap.height() // 2)
            painter.drawPixmap(icon_x, icon_y, pixmap)
        else:
            painter.setPen(ui_qt.QColor(arrow_color))
            painter.drawLine(
                arrow_rect.left() + 7,
                arrow_rect.top() + 7,
                arrow_rect.center().x(),
                arrow_rect.top() + 3,
            )
            painter.drawLine(
                arrow_rect.center().x(),
                arrow_rect.top() + 3,
                arrow_rect.right() - 7,
                arrow_rect.top() + 7,
            )
            painter.drawLine(
                arrow_rect.left() + 7,
                arrow_rect.bottom() - 7,
                arrow_rect.center().x(),
                arrow_rect.bottom() - 3,
            )
            painter.drawLine(
                arrow_rect.center().x(),
                arrow_rect.bottom() - 3,
                arrow_rect.right() - 7,
                arrow_rect.bottom() - 7,
            )
        painter.end()
