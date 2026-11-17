"""Task-detail preview panel and thumbnail lifecycle."""

from __future__ import annotations

import os

import inscrawler.ui.constants as ui_constants
import inscrawler.ui.preview.cell as ui_preview_cell
import inscrawler.ui.qt as ui_qt


class TaskMediaPreviewPanel(ui_qt.QWidget):
    """Shared three-column media preview used by task detail dialogs."""

    def __init__(self, window, task_entry):
        super().__init__()
        self._window = window
        self._task_entry = task_entry
        self._preview_size_recalc_pending = False
        self.setSizePolicy(
            ui_qt.QSizePolicy.Policy.Expanding, ui_qt.QSizePolicy.Policy.Expanding
        )

        layout = ui_qt.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._preview_scroll = ui_qt.QScrollArea()
        self._preview_scroll.setWidgetResizable(True)
        self._preview_scroll.setVerticalScrollBarPolicy(
            ui_qt.Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._preview_scroll.setHorizontalScrollBarPolicy(
            ui_qt.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._preview_scroll.verticalScrollBar().rangeChanged.connect(
            self._schedule_preview_cell_size_recalc
        )
        self._preview_container = ui_qt.QWidget()
        self._preview_grid = ui_qt.QGridLayout(self._preview_container)
        self._preview_grid.setSpacing(0)
        self._preview_grid.setContentsMargins(0, 0, 0, 0)
        self._preview_grid.setAlignment(
            ui_qt.Qt.AlignmentFlag.AlignTop | ui_qt.Qt.AlignmentFlag.AlignLeft
        )
        for col in range(3):
            self._preview_grid.setColumnStretch(col, 1)
        self._preview_scroll.setWidget(self._preview_container)
        layout.addWidget(self._preview_scroll, 1)

        self._preview_cells = []
        self._preview_image_paths = set()
        self._preview_cell_size = ui_qt.QSize(120, 120)
        self._video_thumbnail_owner = object()
        self._initial_preview_load_pending = True
        self.refresh_theme()
        ui_qt.QTimer.singleShot(0, self, self._load_task_preview_images)

    def refresh_theme(self):
        preview_bg = self._window._preview_background_color()
        scrollbar_ss = self._window._scrollbar_stylesheet()
        self._preview_scroll.setStyleSheet(
            f"QScrollArea {{ border: none; background-color: {preview_bg}; }}"
            + scrollbar_ss
        )
        self._preview_scroll.viewport().setStyleSheet(
            f"background-color: {preview_bg};"
        )
        self._preview_container.setStyleSheet(f"background-color: {preview_bg};")
        for cell in self._preview_cells:
            cell.set_theme_background(preview_bg)

    def _load_task_preview_images(self):
        if (
            not self._initial_preview_load_pending
            or self._video_thumbnail_owner is None
        ):
            return
        paths = list(self._task_entry.get("downloaded_files", []))
        self.set_preview_images(paths)

    def set_preview_images(self, image_paths):
        """Replace the visible dataset, displaying the newest saved path first."""
        if self._video_thumbnail_owner is None:
            return
        self._initial_preview_load_pending = False
        for cell in self._preview_cells:
            self._preview_grid.removeWidget(cell)
            cell.hide()
            cell.deleteLater()
        self._preview_cells.clear()
        self._preview_image_paths.clear()

        self._recalc_preview_cell_size()
        paths = list(image_paths or [])
        for path in reversed(paths):
            if path:
                self._add_preview_cell(path)
        self._preview_scroll.verticalScrollBar().setValue(0)
        self._schedule_preview_cell_size_recalc()
        ui_qt.QTimer.singleShot(50, self, self._schedule_preview_cell_size_recalc)

    def _reflow_preview_cells(self):
        for cell in self._preview_cells:
            self._preview_grid.removeWidget(cell)
        for index, cell in enumerate(self._preview_cells):
            self._preview_grid.addWidget(cell, index // 3, index % 3)

    def _add_preview_cell(self, image_path, *, prepend=False):
        if (
            self._video_thumbnail_owner is None
            or not image_path
            or not os.path.exists(image_path)
        ):
            return
        norm_path = os.path.normcase(os.path.abspath(image_path))
        if norm_path in self._preview_image_paths:
            return
        self._preview_image_paths.add(norm_path)

        cell = ui_preview_cell.MediaCell()
        cell.set_theme_background(self._window._preview_background_color())
        cell.set_fixed_size(self._preview_cell_size)
        cell.clicked.connect(self._window._open_media_file)
        if prepend:
            self._preview_cells.insert(0, cell)
            self._reflow_preview_cells()
        else:
            index = len(self._preview_cells)
            self._preview_grid.addWidget(cell, index // 3, index % 3)
            self._preview_cells.append(cell)
        self._schedule_preview_cell_size_recalc()

        is_video = (
            os.path.splitext(norm_path)[1].lower()
            in ui_constants.PREVIEW_VIDEO_EXTENSIONS
        )
        media_label = self._window._media_badge_label("video" if is_video else "image")
        if is_video:
            cache_key = self._window._video_cache_key(norm_path)
            cached = self._window._get_detail_video_cached_pixmap(cache_key)
            if cached:
                cell.set_pixmap(cached, media_label, norm_path, cache_key)
            else:
                cell.set_placeholder(media_label, path=norm_path, cache_key=cache_key)
                self._window._thumb_extractor.request(
                    norm_path,
                    cache_key,
                    owner=self._video_thumbnail_owner,
                )
            return

        cache_key = self._window._image_cache_key(norm_path, self._preview_cell_size)
        cached = self._window._image_pixmap_cache.get(cache_key)
        if cached:
            cell.set_pixmap(cached, media_label, norm_path, cache_key)
        else:
            cell.set_placeholder(media_label, path=norm_path, cache_key=cache_key)
            if cache_key not in self._window._image_thumb_inflight:
                self._window._image_thumb_inflight.add(cache_key)
                self._window._image_thumb_extractor.request(
                    norm_path, self._preview_cell_size, cache_key
                )

    def add_preview_image(self, image_path):
        if self._initial_preview_load_pending:
            self._load_task_preview_images()
        self._add_preview_cell(image_path, prepend=True)

    def release_video_thumbnail_requests(self):
        owner = self._video_thumbnail_owner
        if owner is None:
            return 0
        self._video_thumbnail_owner = None
        return self._window._thumb_extractor.release_owner(owner)

    def _recalc_preview_cell_size(self):
        available_width = self._preview_scroll.viewport().width()
        if available_width <= 0:
            return
        new_size = ui_qt.QSize(
            max(50, available_width // 3), max(50, available_width // 3)
        )
        if new_size == self._preview_cell_size:
            return
        self._preview_cell_size = new_size
        for cell in self._preview_cells:
            cell.set_fixed_size(new_size)
        self._preview_container.updateGeometry()
        self._refresh_image_cells_for_current_size()

    def _refresh_image_cells_for_current_size(self):
        media_label = self._window._media_badge_label("image")
        for cell in self._preview_cells:
            path = getattr(cell, "_current_path", None)
            if not path:
                continue
            norm_path = os.path.normcase(os.path.abspath(path))
            if (
                os.path.splitext(norm_path)[1].lower()
                not in ui_constants.PREVIEW_IMAGE_EXTENSIONS
            ):
                continue
            cache_key = self._window._image_cache_key(
                norm_path, self._preview_cell_size
            )
            if cell.matches_media(norm_path, cache_key):
                continue
            cached = self._window._image_pixmap_cache.get(cache_key)
            if cached:
                cell.set_pixmap(cached, media_label, norm_path, cache_key)
                continue
            current_pixmap = getattr(cell, "_original_pixmap", None)
            if (
                current_pixmap
                and not current_pixmap.isNull()
                and not getattr(cell, "_is_placeholder", False)
            ):
                cell.prepare_pixmap_update(
                    media_label, path=norm_path, cache_key=cache_key
                )
            else:
                cell.set_placeholder(media_label, path=norm_path, cache_key=cache_key)
            if cache_key not in self._window._image_thumb_inflight:
                self._window._image_thumb_inflight.add(cache_key)
                self._window._image_thumb_extractor.request(
                    norm_path, self._preview_cell_size, cache_key
                )

    def _schedule_preview_cell_size_recalc(self, *args):
        if self._preview_size_recalc_pending:
            return
        self._preview_size_recalc_pending = True
        ui_qt.QTimer.singleShot(0, self, self._apply_scheduled_preview_cell_size_recalc)

    def _apply_scheduled_preview_cell_size_recalc(self):
        self._preview_size_recalc_pending = False
        self._recalc_preview_cell_size()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._schedule_preview_cell_size_recalc()

    def showEvent(self, event):
        super().showEvent(event)
        self._schedule_preview_cell_size_recalc()
        ui_qt.QTimer.singleShot(50, self, self._schedule_preview_cell_size_recalc)
