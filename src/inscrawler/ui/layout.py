"""Main-window geometry and minimum-height synchronization."""

from __future__ import annotations

import inscrawler.ui.console as ui_console
import inscrawler.ui.qt as ui_qt


class WindowLayout:
    """Qt view concern composed explicitly by the main window."""

    def _set_fixed_height_from_hint(self, widget):
        widget.ensurePolished()
        layout = widget.layout()
        if layout is not None:
            layout.activate()
        widget.setFixedHeight(widget.sizeHint().height())
        widget.setSizePolicy(
            ui_qt.QSizePolicy.Policy.Preferred, ui_qt.QSizePolicy.Policy.Fixed
        )

    def _sync_queue_header_minimum_height(self):
        """Keep the resizable task list large enough to show its section title."""
        group = self.queue_group
        group.ensurePolished()
        layout = group.layout()
        if layout is not None:
            layout.activate()

        option = ui_qt.QStyleOptionGroupBox()
        group.initStyleOption(option)
        title_rect = group.style().subControlRect(
            ui_qt.QStyle.ComplexControl.CC_GroupBox,
            option,
            ui_qt.QStyle.SubControl.SC_GroupBoxLabel,
            group,
        )
        margins = group.contentsMargins()
        title_bottom = max(0, title_rect.bottom() + 1)
        header_height = max(
            margins.top() + margins.bottom(),
            title_bottom + margins.bottom(),
        )
        group.setMinimumHeight(header_height)
        return header_height

    @staticmethod
    def _layout_minimum_height(layout):
        margins = layout.contentsMargins()
        heights = [
            layout.itemAt(index).minimumSize().height()
            for index in range(layout.count())
        ]
        spacing = max(0, layout.spacing())
        return (
            margins.top()
            + margins.bottom()
            + sum(heights)
            + spacing * max(0, len(heights) - 1)
        )

    def _calculated_minimum_window_height(self):
        left_height = self._layout_minimum_height(self.left_layout)
        right_height = self._layout_minimum_height(self.right_layout)
        self.left_widget.setMinimumHeight(left_height)

        content_margins = self.content_layout.contentsMargins()
        content_height = (
            content_margins.top()
            + content_margins.bottom()
            + max(left_height, right_height)
        )
        self.content_widget.setMinimumHeight(content_height)

        root_margins = self.root_layout.contentsMargins()
        title_height = max(
            self.title_bar.minimumHeight(),
            self.title_bar.minimumSizeHint().height(),
            self.title_bar.sizeHint().height(),
        )
        return (
            root_margins.top()
            + root_margins.bottom()
            + title_height
            + max(0, self.root_layout.spacing())
            + content_height
        )

    def _lock_static_section_heights(self):
        """Keep non-queue form sections from being compressed by window resize."""
        self._set_fixed_height_from_hint(self.login_group)
        self._set_fixed_height_from_hint(self.task_group)
        self._set_fixed_height_from_hint(self.patrol_group)
        self._sync_queue_header_minimum_height()

    def _sync_minimum_window_height(self):
        if getattr(self, "_syncing_minimum_window_height", False):
            return
        self._syncing_minimum_window_height = True
        try:
            self.ensurePolished()
            self._sync_queue_header_minimum_height()
            self.central_widget.layout().activate()
            min_h = max(
                self._calculated_minimum_window_height(),
                self.minimumSizeHint().height(),
            )
            ui_console.debug_print(f"[layout] locked min window height = {min_h}px")
            self.setMinimumHeight(min_h)
            if self.height() < min_h:
                self.resize(self.width(), min_h)
        finally:
            self._syncing_minimum_window_height = False
