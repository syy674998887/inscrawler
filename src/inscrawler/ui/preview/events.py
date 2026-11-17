"""Apply serial media and scope callbacks to the preview UI."""

from __future__ import annotations

import os

import inscrawler.tasks.schema as tasks_schema
import inscrawler.ui.console as ui_console
import inscrawler.ui.qt as ui_qt


class PreviewEvents:
    """Qt view concern composed explicitly by the main window."""

    def on_new_file_detail(self, detail):
        """记录新下载文件的结构化 JSON 详情。"""
        try:
            if not isinstance(detail, dict):
                return
            if self.current_queue_index is None or self.current_queue_index >= len(
                self.queue_items
            ):
                return

            entry = self.queue_items[self.current_queue_index]
            payload = dict(detail)
            normalized = self._normalize_preview_path(payload.get("path"))
            path_key = None
            if normalized:
                payload["path"] = normalized
                path_key = os.path.normcase(normalized)

            details = entry.setdefault("downloaded_file_details", [])
            if path_key:
                for idx, existing in enumerate(details):
                    existing_key = self._preview_path_key(
                        existing.get("path") if isinstance(existing, dict) else None
                    )
                    if existing_key == path_key:
                        details[idx] = payload
                        break
                else:
                    details.append(payload)
            else:
                details.append(payload)
        except Exception as e:
            ui_console.print(f"Failed to update file detail: {e}")

    def on_download_item_completed(self, detail):
        """Count one newly downloaded logical media object on the serial path."""
        try:
            if self.current_queue_index is None or self.current_queue_index >= len(
                self.queue_items
            ):
                return
            entry = self.queue_items[self.current_queue_index]
            expected = entry.get("_run_downloaded_expected")
            run_count = self._nonnegative_int(
                entry.get("_run_downloaded_count"),
                0,
            )
            if (
                entry.get("_run_finalized")
                and expected is not None
                and run_count >= self._nonnegative_int(expected, 0)
            ):
                return
            run_count += 1
            entry["_run_downloaded_count"] = run_count
            task = entry["task"]
            task["downloaded_count"] = entry["_run_downloaded_start"] + run_count
            entry["label"].setText(tasks_schema._format_task_label(task))
            self.save_task(entry)
            self._refresh_log_dialog_status(entry)
        except Exception as e:
            ui_console.print(f"Failed to update downloaded count: {e}")

    def on_download_scope_completed(self, detail):
        """Advance page/full progress only after the backend closes that scope."""
        try:
            if self.current_queue_index is None or self.current_queue_index >= len(
                self.queue_items
            ):
                return
            entry = self.queue_items[self.current_queue_index]
            task = entry.get("task") or {}
            if self._task_scope_unit(task) not in ("page", "full"):
                return
            self._advance_download_scope_progress(entry)
        except Exception as e:
            ui_console.print(f"Failed to update page/full scope progress: {e}")

    def _advance_download_scope_progress(self, entry):
        task = entry.get("task") or {}
        expected = entry.get("_run_scope_completed_expected")
        if (
            entry.get("_run_finalized")
            and expected is not None
            and self._nonnegative_int(entry.get("_run_scope_completed_count"), 0)
            >= self._nonnegative_int(expected, 0)
        ):
            return

        run_count = (
            self._nonnegative_int(entry.get("_run_scope_completed_count"), 0) + 1
        )
        entry["_run_scope_completed_count"] = run_count
        run_start = self._nonnegative_int(
            entry.get("_run_completed_start"),
            self._task_scope_completed(task),
        )
        target_count = min(self._task_scope_limit(task), run_start + run_count)
        self._set_task_scope_completed(task, target_count)
        self.save_task(entry)

    def _notify_log_dialog_new_image(self, entry, image_path, target_username=None):
        """实时通知日志窗口有新图片下载"""
        if not hasattr(self, "_log_dialog") or self._log_dialog is None:
            return
        try:
            if self._log_dialog.isVisible() and self._log_dialog._task_entry is entry:
                if target_username:
                    self._log_dialog.add_preview_image(
                        image_path,
                        target_username=target_username,
                    )
                else:
                    self._log_dialog.add_preview_image(image_path)
        except RuntimeError:
            self._log_dialog = None

    def refresh_preview_images(self):
        """刷新媒体预览（带缓存 + 跳过未变更的 cell）"""
        try:
            total = len(self.current_preview_images)

            if total == 0:
                self._prune_image_preview_state(set())
                for cell in self.cells:
                    cell.clear_content()
                self.scroll.setVerticalScrollBarPolicy(
                    ui_qt.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
                )
                return

            self.ensure_enough_cells(total)

            preview_items = []
            active_image_keys = set()
            for media_path in self.current_preview_images:
                is_video = self._is_video_preview_path(media_path)
                cache_key = (
                    self._video_cache_key(media_path)
                    if is_video
                    else self._image_cache_key(media_path, self.cell_size)
                )
                preview_items.append((media_path, is_video, cache_key))
                if not is_video:
                    active_image_keys.add(cache_key)

            self._prune_image_preview_state(active_image_keys)

            for i, (media_path, is_video, cache_key) in enumerate(preview_items):
                cell = self.cells[i]
                # 跳过已经显示正确内容的 cell
                if cell.matches_media(media_path, cache_key):
                    continue

                if is_video:
                    cached = self._get_home_video_cached_pixmap(cache_key)
                    if cached is not None:
                        cell.set_pixmap(
                            cached,
                            self._media_badge_label("video"),
                            media_path,
                            cache_key,
                        )
                        continue
                    # 先显示占位符，异步提取缩略图
                    cell.set_placeholder(
                        self._media_badge_label("video"),
                        path=media_path,
                        cache_key=cache_key,
                    )
                    self._thumb_extractor.request(
                        media_path,
                        cache_key,
                        owner=self._main_preview_thumbnail_owner,
                    )
                else:
                    cached = self._image_pixmap_cache.get(cache_key)
                    if cached is not None:
                        cell.set_pixmap(
                            cached,
                            self._media_badge_label("image"),
                            media_path,
                            cache_key,
                        )
                        continue
                    # 图片走后台 scaled decode，主线程先贴占位符
                    cell.set_placeholder(
                        self._media_badge_label("image"),
                        path=media_path,
                        cache_key=cache_key,
                    )
                    if cache_key not in self._image_thumb_inflight:
                        self._image_thumb_inflight.add(cache_key)
                        self._image_thumb_extractor.request(
                            media_path, self.cell_size, cache_key
                        )

            for i in range(total, len(self.cells)):
                self.cells[i].clear_content()

            rows_needed = (total + 2) // 3
            self.container.setMinimumHeight(rows_needed * self.cell_size.height())

            if total > 12:
                self.scroll.setVerticalScrollBarPolicy(
                    ui_qt.Qt.ScrollBarPolicy.ScrollBarAsNeeded
                )
            else:
                self.scroll.setVerticalScrollBarPolicy(
                    ui_qt.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
                )
        except Exception as e:
            ui_console.print(f"Failed to refresh media preview: {e}")

    def _on_thumbnail_ready(self, video_path, cache_key, pixmap):
        """视频缩略图提取完成回调"""
        video_path_key = self._preview_path_key(video_path)
        is_home_preview = any(
            self._preview_path_key(path) == video_path_key
            for path in self.current_preview_images
        )
        if pixmap and not pixmap.isNull():
            if is_home_preview:
                self._store_home_video_cached_pixmap(cache_key, pixmap)
            else:
                self._store_detail_video_cached_pixmap(cache_key, pixmap)
        for i, path in enumerate(self.current_preview_images):
            if self._preview_path_key(path) == video_path_key and i < len(self.cells):
                cell = self.cells[i]
                if not cell.matches_media(video_path, cache_key):
                    continue
                if pixmap and not pixmap.isNull():
                    cell.set_pixmap(
                        pixmap, self._media_badge_label("video"), video_path, cache_key
                    )
                else:
                    cell.set_placeholder(
                        self._media_badge_label("video"),
                        path=video_path,
                        cache_key=cache_key,
                    )
        # 同步更新任务详情弹窗的 cell
        self._update_log_dialog_cells(video_path, cache_key, pixmap, "video")

    def _on_image_thumbnail_ready(self, image_path, cache_key, image):
        """图片缩略图解码完成回调（后台线程把 QImage 传回主线程）"""
        self._image_thumb_inflight.discard(cache_key)
        if image is None or image.isNull():
            return

        pixmap = ui_qt.QPixmap.fromImage(image)
        if pixmap.isNull():
            return

        # 更新主界面匹配的 cell
        image_path_key = self._preview_path_key(image_path)
        for i, path in enumerate(self.current_preview_images):
            if self._preview_path_key(path) == image_path_key and i < len(self.cells):
                cell = self.cells[i]
                if cell.matches_media(image_path, cache_key):
                    # Detail cells own their images; only the current home size
                    # belongs in the shared cache after a detail closes/resizes.
                    self._image_pixmap_cache[cache_key] = pixmap
                    cell.set_pixmap(
                        pixmap, self._media_badge_label("image"), image_path, cache_key
                    )
        # 同步更新任务详情弹窗的 cell
        self._update_log_dialog_cells(image_path, cache_key, pixmap, "image")

    def _update_log_dialog_cells(self, media_path, cache_key, pixmap, media_type):
        """异步缩略图完成后，同步更新任务详情弹窗中匹配的 cell"""
        if not hasattr(self, "_log_dialog") or self._log_dialog is None:
            return
        try:
            label = self._media_badge_label(media_type)
            for cell in getattr(self._log_dialog, "_preview_cells", ()):
                if cell.matches_media(media_path, cache_key):
                    cell.set_pixmap(pixmap, label, media_path, cache_key)
        except RuntimeError:
            self._log_dialog = None

    def _thread_pipeline_is_running(self):
        return bool(
            self.queue_running
            or self._download_thread_is_running()
            or self._patrol_thread_is_running()
        )

    def _clear_thread_pipeline_current_file(self, *, refresh=True):
        changed = bool(getattr(self, "_thread_pipeline_current_file_name", ""))
        self._thread_pipeline_current_file_name = ""
        if changed and refresh:
            self._refresh_thread_pipeline()
