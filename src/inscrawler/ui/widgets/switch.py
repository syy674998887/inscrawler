"""Animated home-screen toggle with theme-aware rendering."""

from __future__ import annotations

import inscrawler.ui.qt as ui_qt


class HomeToggleSwitch(ui_qt.QCheckBox):
    """Compact animated switch used by task-creation controls."""

    TRACK_WIDTH = 40
    TRACK_HEIGHT = 22
    THUMB_DIAMETER = 18
    CONTROL_HEIGHT = 40
    HORIZONTAL_MARGIN = 2
    FOCUS_OUTSET = 1.0
    FOCUS_PEN_WIDTH = 1.5

    def __init__(self, color_provider=None, parent=None):
        super().__init__(parent)
        self._color_provider = color_provider
        self._control_height = self.CONTROL_HEIGHT
        self._thumb_position = 1.0 if self.isChecked() else 0.0
        self._hovered = False
        self._thumb_animation = ui_qt.QPropertyAnimation(
            self,
            b"thumbPosition",
            self,
        )
        self._thumb_animation.setDuration(140)
        self._thumb_animation.setEasingCurve(ui_qt.QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate_thumb)
        self.setCursor(ui_qt.Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(ui_qt.Qt.FocusPolicy.StrongFocus)
        self.setAttribute(ui_qt.Qt.WidgetAttribute.WA_Hover, True)
        self.setFixedHeight(self._control_height)
        self.setSizePolicy(
            ui_qt.QSizePolicy.Policy.Fixed,
            ui_qt.QSizePolicy.Policy.Fixed,
        )

    def _get_thumb_position(self):
        return self._thumb_position

    def _set_thumb_position(self, value):
        self._thumb_position = max(0.0, min(1.0, float(value)))
        self.update()

    thumbPosition = ui_qt.Property(
        float,
        _get_thumb_position,
        _set_thumb_position,
    )

    def _animate_thumb(self, checked):
        self._thumb_animation.stop()
        self._thumb_animation.setStartValue(self._thumb_position)
        self._thumb_animation.setEndValue(1.0 if checked else 0.0)
        self._thumb_animation.start()

    def setChecked(self, checked):
        checked = bool(checked)
        changed = self.isChecked() != checked
        signals_blocked = self.signalsBlocked()
        super().setChecked(checked)
        if not changed or signals_blocked:
            self._thumb_animation.stop()
            self._set_thumb_position(1.0 if checked else 0.0)

    def set_control_height(self, height):
        try:
            height = max(1, int(round(float(height))))
        except (TypeError, ValueError):
            height = self.CONTROL_HEIGHT
        if height == self._control_height:
            return
        self._control_height = height
        self.setFixedHeight(height)
        self.updateGeometry()
        self.update()

    def sizeHint(self):
        width = self.TRACK_WIDTH + (2 * self.HORIZONTAL_MARGIN)
        return ui_qt.QSize(width, self._control_height)

    def minimumSizeHint(self):
        return self.sizeHint()

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    @staticmethod
    def _blend_color(start, end, amount):
        amount = max(0.0, min(1.0, float(amount)))
        return ui_qt.QColor(
            round(start.red() + (end.red() - start.red()) * amount),
            round(start.green() + (end.green() - start.green()) * amount),
            round(start.blue() + (end.blue() - start.blue()) * amount),
            round(start.alpha() + (end.alpha() - start.alpha()) * amount),
        )

    def _colors(self):
        if callable(self._color_provider):
            try:
                colors = self._color_provider()
                if isinstance(colors, dict):
                    return colors
            except Exception:
                pass
        palette = self.palette()
        return {
            "surface": palette.base().color().name(),
            "button": palette.button().color().name(),
            "border": palette.mid().color().name(),
            "accent": palette.highlight().color().name(),
            "accent_hover": palette.highlight().color().lighter(112).name(),
        }

    def paintEvent(self, _event):
        painter = ui_qt.QPainter(self)
        painter.setRenderHint(ui_qt.QPainter.RenderHint.Antialiasing)
        colors = self._colors()
        progress = self._thumb_position
        track_height = min(
            float(self.TRACK_HEIGHT),
            max(12.0, float(self.height()) - 4.0),
        )
        thumb_diameter = min(
            float(self.THUMB_DIAMETER),
            max(8.0, track_height - 4.0),
        )

        track_rect = ui_qt.QRectF(
            float(self.HORIZONTAL_MARGIN),
            (self.height() - track_height) / 2.0,
            float(self.TRACK_WIDTH),
            track_height,
        )
        off_track = ui_qt.QColor(colors["button"])
        on_track = ui_qt.QColor(colors["accent"])
        if self._hovered and self.isEnabled():
            on_track = ui_qt.QColor(colors.get("accent_hover", colors["accent"]))
            off_track = off_track.lighter(105)
        track_color = self._blend_color(off_track, on_track, progress)
        border_color = self._blend_color(
            ui_qt.QColor(colors["border"]),
            on_track,
            progress,
        )
        if not self.isEnabled():
            track_color.setAlphaF(0.48)
            border_color.setAlphaF(0.48)

        painter.setPen(ui_qt.QPen(border_color, 1.0))
        painter.setBrush(track_color)
        radius = track_height / 2.0
        painter.drawRoundedRect(track_rect, radius, radius)

        thumb_travel = self.TRACK_WIDTH - thumb_diameter - 4
        thumb_left = track_rect.left() + 2 + thumb_travel * progress
        thumb_rect = ui_qt.QRectF(
            thumb_left,
            track_rect.center().y() - thumb_diameter / 2.0,
            thumb_diameter,
            thumb_diameter,
        )
        shadow_rect = thumb_rect.translated(0.0, 0.8)
        shadow = ui_qt.QColor("#000000")
        shadow.setAlpha(38 if self.isEnabled() else 20)
        painter.setPen(ui_qt.Qt.PenStyle.NoPen)
        painter.setBrush(shadow)
        painter.drawEllipse(shadow_rect)

        thumb_color = self._blend_color(
            ui_qt.QColor(colors["surface"]),
            ui_qt.QColor("#ffffff"),
            progress,
        )
        if not self.isEnabled():
            thumb_color.setAlphaF(0.65)
        painter.setBrush(thumb_color)
        painter.drawEllipse(thumb_rect)

        if self.hasFocus():
            focus_color = ui_qt.QColor(colors["accent"])
            focus_color.setAlpha(150)
            painter.setPen(ui_qt.QPen(focus_color, self.FOCUS_PEN_WIDTH))
            painter.setBrush(ui_qt.Qt.BrushStyle.NoBrush)
            focus_rect = track_rect.adjusted(
                -self.FOCUS_OUTSET,
                -self.FOCUS_OUTSET,
                self.FOCUS_OUTSET,
                self.FOCUS_OUTSET,
            )
            painter.drawRoundedRect(
                focus_rect,
                focus_rect.height() / 2.0,
                focus_rect.height() / 2.0,
            )
