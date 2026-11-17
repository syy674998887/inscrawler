"""Main preview history, cache management, and visibility controls."""

from __future__ import annotations

import json
import os

import inscrawler.paths as app_paths
import inscrawler.ui.console as ui_console
import inscrawler.ui.constants as ui_constants
import inscrawler.ui.qt as ui_qt
import inscrawler.ui.theme.icons as ui_theme_icons


class PreviewController:
    """Qt view concern composed explicitly by the main window."""

    def calculate_image_sizes(self):
        """计算每张图片的固定尺寸，确保3x5布局填满预览区域"""
        try:
            # 获取滚动区域的实际可用大小
            available_width = self.scroll.viewport().width()
            available_height = self.scroll.viewport().height()

            # 计算3x5布局中每个单元格的尺寸（无间隙）
            cell_width = available_width // 3
            cell_height = available_height // 5

            # 使用较小的尺寸保持正方形 - 并设置合理范围
            cell_size = min(cell_width, cell_height)
            cell_size = max(50, min(cell_size, 428))

            self.cell_size = ui_qt.QSize(cell_size, cell_size)

            # 更新所有现有媒体单元的尺寸
            for cell in self.cells:
                cell.set_fixed_size(self.cell_size)

            # 设置网格间距为0（无间隙）
            self.grid.setSpacing(0)
            self.grid.setContentsMargins(0, 0, 0, 0)

        except Exception as e:
            ui_console.print(f"Failed to calculate image sizes: {e}")
            # 使用默认尺寸
            self.cell_size = ui_qt.QSize(100, 100)

    def _normalize_media_path(self, path):
        return os.path.normcase(os.path.abspath(path))

    def _file_version_token(self, path):
        normalized_path = self._normalize_media_path(path)
        try:
            stat_result = os.stat(normalized_path)
            mtime_ns = getattr(
                stat_result, "st_mtime_ns", int(stat_result.st_mtime * 1_000_000_000)
            )
            return normalized_path, int(mtime_ns), int(stat_result.st_size)
        except OSError:
            return normalized_path, None, None

    def _image_cache_key(self, path, size):
        normalized_path, mtime_ns, file_size = self._file_version_token(path)
        return (
            "image",
            normalized_path,
            mtime_ns,
            file_size,
            int(size.width()),
            int(size.height()),
        )

    def _video_cache_key(self, path):
        normalized_path, mtime_ns, file_size = self._file_version_token(path)
        return ("video", normalized_path, mtime_ns, file_size)

    def _estimate_pixmap_bytes(self, pixmap):
        if pixmap is None or pixmap.isNull():
            return 0
        return max(1, pixmap.width()) * max(1, pixmap.height()) * 4

    def _discard_video_cached_pixmap(self, cache, cache_key):
        pixmap = cache.pop(cache_key, None)
        if pixmap is not None:
            self._video_cache_total_bytes = max(
                0, self._video_cache_total_bytes - self._estimate_pixmap_bytes(pixmap)
            )
        return pixmap

    def _promote_detail_video_cached_pixmap(self, cache_key):
        pixmap = self._video_home_pixmap_cache.get(cache_key)
        if pixmap is not None:
            return pixmap
        pixmap = self._video_detail_pixmap_cache.pop(cache_key, None)
        if pixmap is not None:
            self._video_home_pixmap_cache[cache_key] = pixmap
        return pixmap

    def _get_home_video_cached_pixmap(self, cache_key):
        return self._promote_detail_video_cached_pixmap(cache_key)

    def _get_detail_video_cached_pixmap(self, cache_key):
        pixmap = self._video_home_pixmap_cache.get(cache_key)
        if pixmap is not None:
            return pixmap
        pixmap = self._video_detail_pixmap_cache.pop(cache_key, None)
        if pixmap is not None:
            self._video_detail_pixmap_cache[cache_key] = pixmap
        return pixmap

    def _store_home_video_cached_pixmap(self, cache_key, pixmap):
        if pixmap is None or pixmap.isNull():
            return
        self._discard_video_cached_pixmap(self._video_home_pixmap_cache, cache_key)
        self._discard_video_cached_pixmap(self._video_detail_pixmap_cache, cache_key)
        self._video_home_pixmap_cache[cache_key] = pixmap
        self._video_cache_total_bytes += self._estimate_pixmap_bytes(pixmap)
        self._trim_video_cache()

    def _store_detail_video_cached_pixmap(self, cache_key, pixmap):
        if pixmap is None or pixmap.isNull():
            return
        self._discard_video_cached_pixmap(self._video_home_pixmap_cache, cache_key)
        self._discard_video_cached_pixmap(self._video_detail_pixmap_cache, cache_key)
        self._video_detail_pixmap_cache[cache_key] = pixmap
        self._video_cache_total_bytes += self._estimate_pixmap_bytes(pixmap)
        self._trim_video_cache()

    def _demote_home_video_cache_for_paths(self, normalized_paths):
        cache_keys = [
            cache_key
            for cache_key in self._video_home_pixmap_cache
            if cache_key[1] in normalized_paths
        ]
        for cache_key in cache_keys:
            pixmap = self._video_home_pixmap_cache.pop(cache_key)
            self._discard_video_cached_pixmap(
                self._video_detail_pixmap_cache, cache_key
            )
            self._video_detail_pixmap_cache[cache_key] = pixmap
            self._video_detail_pixmap_cache.move_to_end(cache_key, last=False)

    def _oldest_home_video_cache_key(self):
        if not self._video_home_pixmap_cache:
            return None

        home_paths = [
            self._normalize_media_path(path)
            for path in self.current_preview_images
            if path
        ]
        current_path_set = set(home_paths)
        for cache_key in self._video_home_pixmap_cache:
            if cache_key[1] not in current_path_set:
                return cache_key

        keys_by_path = {}
        for cache_key in self._video_home_pixmap_cache:
            keys_by_path.setdefault(cache_key[1], cache_key)
        for path in reversed(home_paths):
            cache_key = keys_by_path.get(path)
            if cache_key is not None:
                return cache_key
        return next(iter(self._video_home_pixmap_cache))

    def _trim_video_cache(self):
        cache_limit_bytes = self.video_preview_cache_mb * 1024 * 1024
        while (
            self._video_detail_pixmap_cache
            and self._video_cache_total_bytes > cache_limit_bytes
        ):
            _evicted_key, evicted_pixmap = self._video_detail_pixmap_cache.popitem(
                last=False
            )
            self._video_cache_total_bytes = max(
                0,
                self._video_cache_total_bytes
                - self._estimate_pixmap_bytes(evicted_pixmap),
            )
        while (
            self._video_home_pixmap_cache
            and self._video_cache_total_bytes > cache_limit_bytes
        ):
            cache_key = self._oldest_home_video_cache_key()
            self._discard_video_cached_pixmap(self._video_home_pixmap_cache, cache_key)

    def clear_detail_video_thumbnail_cache(self):
        freed_bytes = sum(
            self._estimate_pixmap_bytes(pixmap)
            for pixmap in self._video_detail_pixmap_cache.values()
        )
        self._video_detail_pixmap_cache.clear()
        self._video_cache_total_bytes = max(
            0, self._video_cache_total_bytes - freed_bytes
        )

    def detail_video_thumbnail_cache_bytes(self):
        return sum(
            self._estimate_pixmap_bytes(pixmap)
            for pixmap in self._video_detail_pixmap_cache.values()
        )

    def _drop_preview_cache_for_paths(self, paths):
        normalized_paths = {self._normalize_media_path(path) for path in paths if path}
        if not normalized_paths:
            return

        stale_image_keys = [
            cache_key
            for cache_key in self._image_pixmap_cache
            if cache_key[1] in normalized_paths
        ]
        for cache_key in stale_image_keys:
            self._image_pixmap_cache.pop(cache_key, None)

        self._image_thumb_inflight = {
            cache_key
            for cache_key in self._image_thumb_inflight
            if cache_key[1] not in normalized_paths
        }

        self._demote_home_video_cache_for_paths(normalized_paths)

    def _prune_image_preview_state(self, active_image_keys):
        active_image_keys = set(active_image_keys)
        stale_image_keys = [
            cache_key
            for cache_key in self._image_pixmap_cache
            if cache_key not in active_image_keys
        ]
        for cache_key in stale_image_keys:
            self._image_pixmap_cache.pop(cache_key, None)
        self._image_thumb_inflight.intersection_update(active_image_keys)

    def _normalize_preview_path(self, path):
        if not path:
            return None
        try:
            normalized = os.path.abspath(os.path.expanduser(str(path).strip()))
        except Exception:
            return None
        return normalized or None

    def _preview_path_key(self, path):
        normalized = self._normalize_preview_path(path)
        if not normalized:
            return None
        return os.path.normcase(normalized)

    def _is_supported_preview_path(self, path):
        normalized = self._normalize_preview_path(path)
        if not normalized:
            return False
        return (
            os.path.splitext(normalized)[1].lower()
            in ui_constants.PREVIEW_SUPPORTED_EXTENSIONS
        )

    def _is_video_preview_path(self, path):
        normalized = self._normalize_preview_path(path)
        if not normalized:
            return False
        return (
            os.path.splitext(normalized)[1].lower()
            in ui_constants.PREVIEW_VIDEO_EXTENSIONS
        )

    def _compact_preview_history_paths(self, paths):
        compacted = []
        seen = set()
        for raw_path in paths or []:
            normalized = self._normalize_preview_path(raw_path)
            if not normalized:
                continue
            key = os.path.normcase(normalized)
            if key in seen or not self._is_supported_preview_path(normalized):
                continue
            try:
                if not os.path.exists(normalized) or os.path.getsize(normalized) <= 0:
                    continue
            except OSError:
                continue
            seen.add(key)
            compacted.append(normalized)
            if len(compacted) >= self.max_preview_images:
                break
        return compacted

    def _write_preview_history(self, paths):
        os.makedirs(app_paths.CACHE_DIR, exist_ok=True)
        temp_path = app_paths.PREVIEW_HISTORY_FILE + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(paths, f, indent=2, ensure_ascii=False)
        os.replace(temp_path, app_paths.PREVIEW_HISTORY_FILE)

    def _schedule_preview_history_save(self):
        self._preview_history_save_timer.start()

    def _remember_preview_path(self, path):
        normalized = self._normalize_preview_path(path)
        if not normalized or not self._is_supported_preview_path(normalized):
            return False
        try:
            if not os.path.exists(normalized) or os.path.getsize(normalized) <= 0:
                return False
        except OSError:
            return False

        key = os.path.normcase(normalized)
        updated_paths = [
            existing_path
            for existing_path in self.current_preview_images
            if self._preview_path_key(existing_path) != key
        ]
        updated_paths.insert(0, normalized)

        removed_paths = updated_paths[self.max_preview_images :]
        if removed_paths:
            self._drop_preview_cache_for_paths(removed_paths)
        self.current_preview_images = updated_paths[: self.max_preview_images]
        if self._is_video_preview_path(normalized):
            self._promote_detail_video_cached_pixmap(self._video_cache_key(normalized))
        self._schedule_preview_history_save()
        return True

    def save_preview_history(self):
        compacted = self._compact_preview_history_paths(self.current_preview_images)
        self.current_preview_images = compacted
        try:
            self._write_preview_history(compacted)
        except Exception as e:
            ui_console.print(f"Failed to save preview history: {e}")

    def flush_preview_history(self):
        if self._preview_history_save_timer.isActive():
            self._preview_history_save_timer.stop()
        self.save_preview_history()

    def load_preview_history(self):
        try:
            os.makedirs(app_paths.CACHE_DIR, exist_ok=True)
        except Exception as e:
            ui_console.print(f"Failed to create cache directory: {e}")
            return

        if not os.path.exists(app_paths.PREVIEW_HISTORY_FILE):
            try:
                self._write_preview_history([])
            except Exception as e:
                ui_console.print(f"Failed to initialize preview history: {e}")
            return

        try:
            with open(app_paths.PREVIEW_HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            ui_console.print(f"Failed to load preview history: {e}")
            return

        if not isinstance(data, list):
            ui_console.print("Preview history file is invalid; resetting")
            data = []

        compacted = self._compact_preview_history_paths(data)
        self.current_preview_images = compacted
        if compacted:
            self.refresh_preview_images()
            ui_console.debug_print(f"Loaded {len(compacted)} preview history item(s)")

        if data != compacted:
            try:
                self._write_preview_history(compacted)
            except Exception as e:
                ui_console.print(f"Failed to normalize preview history: {e}")

    def _position_main_preview_visibility_button(self):
        group = getattr(self, "preview_group", None)
        button = getattr(self, "preview_visibility_button", None)
        if group is None or button is None:
            return

        option = ui_qt.QStyleOptionGroupBox()
        group.initStyleOption(option)
        title_rect = group.style().subControlRect(
            ui_qt.QStyle.ComplexControl.CC_GroupBox,
            option,
            ui_qt.QStyle.SubControl.SC_GroupBoxLabel,
            group,
        )
        if title_rect.isValid() and not title_rect.isEmpty():
            x = title_rect.right() + 5
            y = title_rect.center().y() - (button.height() // 2)
        else:
            title_width = group.fontMetrics().horizontalAdvance(group.title())
            x = 15 + title_width + 5
            y = 0
        x = max(0, min(x, max(0, group.width() - button.width())))
        button.move(x, max(0, y))
        button.raise_()

    def _sync_main_preview_visibility_button(self):
        button = getattr(self, "preview_visibility_button", None)
        if button is None:
            return

        visible = bool(self._main_preview_thumbnails_visible)
        if button.isChecked() != visible:
            button.blockSignals(True)
            button.setChecked(visible)
            button.blockSignals(False)

        colors = self._theme_tokens()
        icon_state = "visible" if visible else "hidden"
        svg = ui_theme_icons._PREVIEW_VISIBILITY_SVG[icon_state].format(
            color=colors["muted"]
        )
        icon = ui_theme_icons._make_svg_icon(svg, size=18)
        button.setIcon(icon if icon is not None else ui_qt.QIcon())
        action_key = "preview_hide_action" if visible else "preview_show_action"
        action_text = self.tr(action_key)
        button.setToolTip(action_text)
        button.setAccessibleName(action_text)
        self._position_main_preview_visibility_button()

    def _refresh_main_preview_after_show(self):
        if not self._main_preview_thumbnails_visible:
            return
        self.calculate_image_sizes()
        if self.current_preview_images:
            self.refresh_preview_images()

    @ui_qt.Slot(bool)
    def _set_main_preview_thumbnails_visible(self, visible, persist=True):
        self._main_preview_thumbnails_visible = bool(visible)
        self.scroll.setVisible(self._main_preview_thumbnails_visible)
        self._sync_main_preview_visibility_button()
        if self._main_preview_thumbnails_visible:
            ui_qt.QTimer.singleShot(0, self._refresh_main_preview_after_show)
        if persist and not getattr(self, "_loading_settings", False):
            try:
                self._write_env_settings(
                    {
                        "main_preview_visible": (
                            "true" if self._main_preview_thumbnails_visible else "false"
                        ),
                    }
                )
            except Exception as e:
                ui_console.print(f"Failed to save download preview visibility: {e}")
