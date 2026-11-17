"""Reusable media thumbnail cell with badges and loading states."""

from __future__ import annotations

import os

import inscrawler.ui.qt as ui_qt


class MediaCell(ui_qt.QWidget):
    """媒体单元 — 纯展示组件，不持有播放器"""

    clicked = ui_qt.Signal(str)

    def __init__(self):
        super().__init__()
        self.setMinimumSize(170, 170)
        self.setSizePolicy(
            ui_qt.QSizePolicy.Policy.Fixed, ui_qt.QSizePolicy.Policy.Fixed
        )

        layout = ui_qt.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._image_label = ui_qt.QLabel()
        self._image_label.setAlignment(ui_qt.Qt.AlignmentFlag.AlignCenter)
        self._image_label.setScaledContents(False)
        layout.addWidget(self._image_label)

        self._type_label = ui_qt.QLabel(self)
        self._type_label.setAlignment(ui_qt.Qt.AlignmentFlag.AlignCenter)
        self._type_label.setStyleSheet(
            "background-color: rgba(0,0,0,0.6); color: white; "
            "font-size: 9px; font-weight: bold; padding: 2px; border-radius: 3px;"
        )
        self._type_label.hide()

        self.hide()
        self._original_pixmap = None
        self._fixed_size = ui_qt.QSize(100, 100)
        self._media_type = None
        self._current_path = None  # 当前显示的文件路径（跳过重复刷新）
        self._current_cache_key = None
        self._is_placeholder = False
        self._theme_bg_color = None
        self._apply_image_background()

    def _theme_background_color(self):
        if self._theme_bg_color:
            return self._theme_bg_color
        app = ui_qt.QApplication.instance()
        palette = app.palette() if app else self.palette()
        return palette.color(ui_qt.QPalette.ColorRole.Base).name()

    def set_theme_background(self, color=None):
        self._theme_bg_color = color
        self.refresh_theme()

    def _apply_image_background(self):
        bg_color = self._theme_background_color()
        self._image_label.setStyleSheet(f"border: none; background-color: {bg_color};")

    def set_pixmap(self, pixmap, media_type, path=None, cache_key=None):
        """设置预加载的 QPixmap"""
        self._current_path = path
        self._current_cache_key = cache_key
        if pixmap and not pixmap.isNull():
            self._show_pixmap(pixmap, media_type)
        else:
            self.set_placeholder(media_type, path=path, cache_key=cache_key)
        self._update_cursor()

    def set_placeholder(self, media_type, path=None, cache_key=None):
        """显示占位符"""
        if path is not None:
            self._current_path = path
        self._current_cache_key = cache_key
        self._image_label.clear()
        self._original_pixmap = None
        self._is_placeholder = True
        self._apply_image_background()
        self._set_type_label(media_type)
        self.show()
        self._update_cursor()

    def prepare_pixmap_update(self, media_type, path=None, cache_key=None):
        """等待新缩略图时保留当前画面，只更新匹配键。"""
        if path is not None:
            self._current_path = path
        self._current_cache_key = cache_key
        self._set_type_label(media_type)
        self.show()
        self._update_cursor()

    def clear_content(self):
        """清除内容"""
        self._current_path = None
        self._current_cache_key = None
        self._image_label.clear()
        self._is_placeholder = False
        self._apply_image_background()
        self._original_pixmap = None
        self._type_label.hide()
        self.hide()
        self._update_cursor()

    def matches_media(self, path, cache_key):
        if self._current_cache_key != cache_key:
            return False
        if self._current_path == path:
            return True
        if not self._current_path or not path:
            return False
        try:
            current = os.path.normcase(os.path.abspath(self._current_path))
            other = os.path.normcase(os.path.abspath(path))
        except Exception:
            return False
        return current == other

    def _update_cursor(self):
        if self._current_path and not self._is_placeholder:
            self.setCursor(ui_qt.Qt.CursorShape.PointingHandCursor)
        else:
            self.unsetCursor()

    def mousePressEvent(self, event):
        if (
            event.button() == ui_qt.Qt.MouseButton.LeftButton
            and self._current_path
            and not self._is_placeholder
        ):
            self.clicked.emit(self._current_path)
            event.accept()
            return
        super().mousePressEvent(event)

    def set_fixed_size(self, size):
        """设置固定尺寸"""
        self._fixed_size = size
        self.setFixedSize(size)
        if self._original_pixmap and not self._original_pixmap.isNull():
            scaled = self._original_pixmap.scaled(
                size,
                ui_qt.Qt.AspectRatioMode.KeepAspectRatio,
                ui_qt.Qt.TransformationMode.SmoothTransformation,
            )
            self._image_label.setPixmap(scaled)
        self._update_label_position()

    def _show_pixmap(self, pixmap, media_type):
        self._original_pixmap = pixmap
        self._is_placeholder = False
        scaled = pixmap.scaled(
            self._fixed_size,
            ui_qt.Qt.AspectRatioMode.KeepAspectRatio,
            ui_qt.Qt.TransformationMode.SmoothTransformation,
        )
        self._image_label.setPixmap(scaled)
        self._apply_image_background()
        self._set_type_label(media_type)
        self.show()

    def _set_type_label(self, media_type):
        self._media_type = media_type
        self._type_label.setText(media_type)
        self._type_label.adjustSize()
        self._type_label.show()
        self._update_label_position()

    def update_badge_text(self, media_type):
        """只刷新徽章文字（供切换语言时复用，不重绘图片）"""
        if self._type_label.isHidden():
            return
        self._media_type = media_type
        self._type_label.setText(media_type)
        self._type_label.adjustSize()
        self._update_label_position()

    def _update_label_position(self):
        # During filter rebuilds the badge is shown before its parent cell.
        # isVisible() is false in that interval even though the badge itself
        # is no longer hidden, which used to leave new badges at (0, 0).
        if not self._type_label.isHidden():
            self._type_label.move(5, 5)

    def refresh_theme(self):
        self._apply_image_background()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_label_position()
