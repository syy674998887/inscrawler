"""Apply the design system to windows, palettes, and live widgets."""

from __future__ import annotations

import ctypes
import sys

import inscrawler.ui.qt as ui_qt
import inscrawler.ui.theme.styles as ui_theme_styles
import inscrawler.ui.theme.tokens as ui_theme_tokens
import inscrawler.ui.widgets.queue as ui_widgets_queue


class ThemeController:
    """Qt view concern composed explicitly by the main window."""

    def _apply_combo_popup_style(self, combo):
        colors = self._theme_tokens()
        item_height = combo.POPUP_ITEM_HEIGHT
        combo.set_arrow_colors(colors["accent"], colors["muted"])
        popup_view = combo.popup_view()
        popup_container = combo.popup_container()
        popup_view.set_item_action_danger_color(colors["danger"])

        popup_view.verticalScrollBar().setStyleSheet(self._scrollbar_stylesheet())

        popup_view.setStyleSheet(
            f"""
            QAbstractItemView#comboPopupView {{
                background-color: {colors["base"]};
                color: {colors["text"]};
                border: none;
                outline: 0;
                padding: 0;
            }}
            QAbstractItemView#comboPopupView::item {{
                min-height: {item_height}px;
                max-height: {item_height}px;
                margin: 0;
                padding: 0 10px;
                border: none;
                background-color: {colors["base"]};
                color: {colors["text"]};
            }}
            QAbstractItemView#comboPopupView::item:hover,
            QAbstractItemView#comboPopupView::item:selected {{
                background-color: {colors["highlight"]};
                color: {colors["highlight_text"]};
            }}
            """
        )

        popup_container.setStyleSheet(
            f"background-color: {colors['base']}; border: 1px solid {colors['accent']};"
        )

    def _refresh_combo_popup_styles(self):
        for combo in getattr(self, "_combo_boxes", []):
            self._apply_combo_popup_style(combo)

    def _theme_tokens(self, theme_name=None):
        return ui_theme_tokens.theme_tokens(
            self._normalize_theme_key(theme_name or self.ui_theme)
        )

    @staticmethod
    def _section_title_background(colors):
        return ui_theme_styles.section_title_background(colors)

    def _build_application_stylesheet(self, colors):
        return ui_theme_styles.application_stylesheet(colors)

    def _set_dwm_window_attribute(self, hwnd, attribute, value, value_type):
        try:
            result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd),
                ctypes.c_uint(attribute),
                ctypes.byref(value),
                ctypes.sizeof(value_type),
            )
            return result == 0
        except Exception:
            return False

    def _sync_system_title_bar(self):
        if sys.platform != "win32":
            return
        hwnd = int(self.winId())
        if not hwnd:
            return

        colors = self._theme_tokens()
        dark_mode = self.ui_theme == "dark"
        dark_mode_value = ctypes.c_int(1 if dark_mode else 0)
        color = ui_qt.QColor(colors["window"])
        colorref = color.red() | (color.green() << 8) | (color.blue() << 16)
        caption_color = ctypes.c_uint(colorref)
        text_color = ui_qt.QColor(colors["titlebar_text"])
        text_colorref = (
            text_color.red() | (text_color.green() << 8) | (text_color.blue() << 16)
        )
        caption_text = ctypes.c_uint(text_colorref)

        # Windows 11 / recent Windows 10 builds support 20. Older builds may use 19.
        if not self._set_dwm_window_attribute(hwnd, 20, dark_mode_value, ctypes.c_int):
            self._set_dwm_window_attribute(hwnd, 19, dark_mode_value, ctypes.c_int)
        self._set_dwm_window_attribute(hwnd, 35, caption_color, ctypes.c_uint)
        self._set_dwm_window_attribute(hwnd, 36, caption_text, ctypes.c_uint)

    def apply_theme(self, theme_name):
        app = ui_qt.QApplication.instance()
        if not app:
            return
        self.ui_theme = self._normalize_theme_key(theme_name or self.ui_theme)
        colors = self._theme_tokens(self.ui_theme)
        palette = ui_qt.QPalette()
        if self.ui_theme == "dark":
            palette.setColor(
                ui_qt.QPalette.ColorRole.ToolTipBase, ui_qt.QColor(230, 230, 230)
            )
            palette.setColor(
                ui_qt.QPalette.ColorRole.ToolTipText, ui_qt.QColor(0, 0, 0)
            )
        else:
            palette.setColor(
                ui_qt.QPalette.ColorRole.ToolTipBase, ui_qt.QColor(255, 255, 255)
            )
            palette.setColor(
                ui_qt.QPalette.ColorRole.ToolTipText, ui_qt.QColor(0, 0, 0)
            )
        palette.setColor(
            ui_qt.QPalette.ColorRole.Window, ui_qt.QColor(colors["window"])
        )
        palette.setColor(
            ui_qt.QPalette.ColorRole.WindowText, ui_qt.QColor(colors["text"])
        )
        palette.setColor(ui_qt.QPalette.ColorRole.Base, ui_qt.QColor(colors["base"]))
        palette.setColor(
            ui_qt.QPalette.ColorRole.AlternateBase, ui_qt.QColor(colors["alternate"])
        )
        palette.setColor(ui_qt.QPalette.ColorRole.Text, ui_qt.QColor(colors["text"]))
        palette.setColor(
            ui_qt.QPalette.ColorRole.Button, ui_qt.QColor(colors["button"])
        )
        palette.setColor(
            ui_qt.QPalette.ColorRole.ButtonText, ui_qt.QColor(colors["text"])
        )
        palette.setColor(ui_qt.QPalette.ColorRole.Link, ui_qt.QColor(colors["link"]))
        palette.setColor(
            ui_qt.QPalette.ColorRole.LinkVisited, ui_qt.QColor(colors["link"])
        )
        palette.setColor(
            ui_qt.QPalette.ColorRole.Highlight, ui_qt.QColor(colors["highlight"])
        )
        palette.setColor(
            ui_qt.QPalette.ColorRole.HighlightedText,
            ui_qt.QColor(colors["highlight_text"]),
        )
        app.setPalette(palette)
        app.setStyleSheet(self._build_application_stylesheet(colors))
        if hasattr(self, "media_request_cap_spin"):
            self._update_media_request_cap_controls(update_height=False)
        self._sync_main_preview_visibility_button()
        self._refresh_combo_popup_styles()
        self._refresh_scrollbar_theme()
        self._refresh_queue_label_styles()
        self._refresh_preview_theme()
        pipeline = getattr(self, "thread_pipeline", None)
        if pipeline is not None:
            pipeline.apply_theme_colors(colors)
        self._refresh_thread_pipeline()
        if self._console_log_dialog is not None:
            try:
                self._console_log_dialog.refresh_theme()
            except RuntimeError:
                self._console_log_dialog = None
        if hasattr(self, "title_bar"):
            self.title_bar.sync_ui()
        ui_qt.QTimer.singleShot(0, self._sync_system_title_bar)

    def _queue_label_style(self):
        text_color = self._theme_tokens()["text"]
        return f"font-size: 15px; color: {text_color};"

    def _queue_item_style(self):
        colors = self._theme_tokens()
        return f"background-color: {colors['surface']}; border: none;"

    def _refresh_queue_item_styles(self):
        divider_color = self._theme_tokens()["border"]
        last_index = len(getattr(self, "queue_items", [])) - 1
        for index, entry in enumerate(getattr(self, "queue_items", [])):
            widget = entry["widget"]
            widget.setStyleSheet(self._queue_item_style())
            if isinstance(widget, ui_widgets_queue.QueueItemWidget):
                widget.set_divider_color(divider_color)
                widget.set_divider_visible(index < last_index)
            self._set_queue_status_icon(entry)

    def _refresh_queue_dividers(self):
        last_index = len(self.queue_items) - 1
        for index, entry in enumerate(self.queue_items):
            widget = entry.get("widget")
            if isinstance(widget, ui_widgets_queue.QueueItemWidget):
                widget.set_divider_visible(index < last_index)

    def _refresh_queue_label_styles(self):
        for entry in getattr(self, "queue_items", []):
            entry["label"].setStyleSheet(self._queue_label_style())
            date_label = entry.get("date_label")
            if date_label is not None:
                date_label.setStyleSheet(self._queue_label_style())

    def _scrollbar_stylesheet(self):
        return ui_theme_styles.scrollbar_stylesheet(self._theme_tokens())

    def _refresh_scrollbar_theme(self):
        scrollbar_stylesheet = self._scrollbar_stylesheet()
        colors = self._theme_tokens()
        self.queue_list.setStyleSheet(
            f"QListWidget#taskQueueList {{ border: none; border-radius: 0px; padding: 0px; background-color: {colors['surface']}; }} "
            f"QListWidget#taskQueueList::viewport {{ background-color: {colors['surface']}; }} "
            "QListWidget::item { margin: 0px; padding: 0px; } "
            f"{scrollbar_stylesheet}"
        )
        self.scroll.setStyleSheet(
            f"QScrollArea {{ border: none; }} {scrollbar_stylesheet}"
        )
        self._refresh_queue_item_styles()
        self._refresh_queue_reorder_state()

    def _preview_background_color(self):
        return self._theme_tokens()["preview"]

    def _refresh_preview_theme(self):
        bg_color = self._preview_background_color()
        self.scroll.viewport().setStyleSheet(f"background-color: {bg_color};")
        self.container.setStyleSheet(f"background-color: {bg_color};")
        for cell in self.cells:
            cell.set_theme_background(bg_color)
