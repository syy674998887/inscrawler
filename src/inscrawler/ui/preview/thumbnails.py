"""Bounded image decoding and reusable video thumbnail workers."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading

import inscrawler.ui.console as ui_console
import inscrawler.ui.constants as ui_constants
import inscrawler.ui.qt as ui_qt


class VideoThumbnailTaskSignals(ui_qt.QObject):
    thumbnail_ready = ui_qt.Signal(object, str, object, ui_qt.QImage, str)


class VideoThumbnailTask(ui_qt.QRunnable):
    def __init__(self, video_path, cache_key, ffmpeg_path):
        super().__init__()
        self.video_path = video_path
        self.cache_key = cache_key
        self.ffmpeg_path = ffmpeg_path
        self.signals = VideoThumbnailTaskSignals()
        self._cancel_requested = threading.Event()
        self._process_lock = threading.Lock()
        self._process = None
        self.setAutoDelete(False)

    @staticmethod
    def _kill_process(process):
        try:
            if process.poll() is None:
                process.kill()
        except OSError:
            pass

    def cancel(self):
        self._cancel_requested.set()
        with self._process_lock:
            process = self._process
        if process is not None:
            self._kill_process(process)

    def run(self):
        error = ""
        image = ui_qt.QImage()
        scale_filter = (
            f"scale='min({ui_constants.VIDEO_THUMBNAIL_MAX_EDGE},iw)':"
            f"'min({ui_constants.VIDEO_THUMBNAIL_MAX_EDGE},ih)':"
            "force_original_aspect_ratio=decrease"
        )
        command = [
            self.ffmpeg_path,
            "-v",
            "error",
            "-i",
            self.video_path,
            "-an",
            "-vf",
            scale_filter,
            "-frames:v",
            "1",
            "-f",
            "image2pipe",
            "-vcodec",
            "png",
            "-",
        ]
        process = None
        try:
            if self._cancel_requested.is_set():
                return
            creationflags = 0
            if hasattr(subprocess, "CREATE_NO_WINDOW"):
                creationflags = subprocess.CREATE_NO_WINDOW
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=creationflags,
            )
            with self._process_lock:
                self._process = process
                cancel_now = self._cancel_requested.is_set()
            if cancel_now:
                self._kill_process(process)
            try:
                stdout, stderr_bytes = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                self._kill_process(process)
                stdout, stderr_bytes = process.communicate()
                error = "generation timed out"
            stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
            if not error and process.returncode != 0:
                error = (
                    stderr or f"video processor exited with code {process.returncode}"
                )
            elif not error and not stdout:
                error = stderr or "no image data was produced"
            elif not error:
                image = ui_qt.QImage.fromData(stdout, "PNG")
                if image.isNull():
                    error = stderr or "generated image data could not be decoded"
        except Exception as exc:
            error = str(exc)
        finally:
            with self._process_lock:
                if self._process is process:
                    self._process = None
        if self._cancel_requested.is_set():
            return
        self.signals.thumbnail_ready.emit(
            self, self.video_path, self.cache_key, image, error
        )


class VideoThumbnailExtractor(ui_qt.QObject):
    thumbnail_ready = ui_qt.Signal(str, object, ui_qt.QPixmap)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ffmpeg_path = shutil.which("ffmpeg")
        self._pool = ui_qt.QThreadPool(self)
        self._pool.setMaxThreadCount(2)
        self._tasks = {}
        self._task_owners = {}

    def request(self, video_path, cache_key, owner):
        label = self._video_label(video_path)
        if not self._ffmpeg_path:
            ui_console.print(
                f"Video thumbnail failed: {label}; video processor not found"
            )
            self.thumbnail_ready.emit(video_path, cache_key, ui_qt.QPixmap())
            return False
        if cache_key in self._tasks:
            self._task_owners.setdefault(cache_key, set()).add(owner)
            return True
        task = VideoThumbnailTask(video_path, cache_key, self._ffmpeg_path)
        task.signals.thumbnail_ready.connect(
            self._on_task_ready, ui_qt.Qt.ConnectionType.QueuedConnection
        )
        self._tasks[cache_key] = task
        self._task_owners[cache_key] = {owner}
        ui_console.debug_print(f"Video thumbnail started: {label}")
        self._pool.start(task)
        return True

    def release_owner(self, owner):
        """Cancel unfinished work that is no longer needed by any request owner."""
        cancelled = 0
        for cache_key, owners in list(self._task_owners.items()):
            if owner not in owners:
                continue
            owners.discard(owner)
            if owners:
                continue
            task = self._tasks.get(cache_key)
            if task is None:
                self._task_owners.pop(cache_key, None)
                continue
            if not self._pool.tryTake(task):
                task.cancel()
            self._tasks.pop(cache_key, None)
            self._task_owners.pop(cache_key, None)
            cancelled += 1
        return cancelled

    @ui_qt.Slot(object, str, object, ui_qt.QImage, str)
    def _on_task_ready(self, task, video_path, cache_key, image, error):
        if self._tasks.get(cache_key) is not task:
            return
        self._tasks.pop(cache_key, None)
        self._task_owners.pop(cache_key, None)
        label = self._video_label(video_path)
        if image is None or image.isNull():
            ui_console.print(
                f"Video thumbnail failed: {label}; {error or 'no valid image was produced'}"
            )
            self.thumbnail_ready.emit(video_path, cache_key, ui_qt.QPixmap())
            return

        pixmap = ui_qt.QPixmap.fromImage(image)
        if pixmap.isNull():
            ui_console.print(
                f"Video thumbnail failed: {label}; generated image could not be decoded"
            )
            self.thumbnail_ready.emit(video_path, cache_key, ui_qt.QPixmap())
            return

        ui_console.debug_print(f"Video thumbnail completed: {label}")
        self.thumbnail_ready.emit(video_path, cache_key, pixmap)

    def _video_label(self, video_path):
        return os.path.basename(video_path) or str(video_path)

    def shutdown(self):
        for task in self._tasks.values():
            task.cancel()
        self._pool.clear()
        self._pool.waitForDone(5000)
        self._tasks.clear()
        self._task_owners.clear()


class ImageThumbnailDecodeTaskSignals(ui_qt.QObject):
    thumbnail_ready = ui_qt.Signal(str, object, ui_qt.QImage)


class ImageThumbnailDecodeTask(ui_qt.QRunnable):
    def __init__(self, path, w, h, cache_key):
        super().__init__()
        self.path = path
        self.w = int(w)
        self.h = int(h)
        self.cache_key = cache_key
        self.signals = ImageThumbnailDecodeTaskSignals()
        self.setAutoDelete(False)

    @ui_qt.Slot()
    def run(self):
        img = ui_qt.QImage()
        try:
            reader = ui_qt.QImageReader(self.path)
            reader.setAutoTransform(True)  # 尊重 EXIF 方向
            src = reader.size()
            target = ui_qt.QSize(self.w, self.h)
            if src.isValid() and src.width() > 0 and src.height() > 0:
                scaled = src.scaled(target, ui_qt.Qt.AspectRatioMode.KeepAspectRatio)
                reader.setScaledSize(scaled)
            else:
                reader.setScaledSize(target)
            img = reader.read()
        except Exception:
            img = ui_qt.QImage()
        self.signals.thumbnail_ready.emit(self.path, self.cache_key, img)


class ImageThumbnailExtractor(ui_qt.QObject):
    """后台线程池图片缩略图解码器。
    - 用 QImageReader.setScaledSize 在 decode 阶段就缩到目标尺寸，比 QPixmap(path) 快一个数量级
    - 用受限并发的 QThreadPool，避免主线程阻塞又不会无限抢占 CPU
    - QPixmap 非线程安全，所以跨线程传 QImage，主线程再转 QPixmap
    """

    thumbnail_ready = ui_qt.Signal(str, object, ui_qt.QImage)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pool = ui_qt.QThreadPool(self)
        ideal_workers = ui_qt.QThread.idealThreadCount()
        if ideal_workers <= 0:
            ideal_workers = 2
        self._pool.setMaxThreadCount(
            max(2, min(ui_constants.MAX_IMAGE_PREVIEW_DECODE_WORKERS, ideal_workers))
        )
        self._tasks = {}

    def request(self, path, size, cache_key):
        """主线程调用；交给受限线程池并发解码。"""
        if cache_key in self._tasks:
            return
        task = ImageThumbnailDecodeTask(path, size.width(), size.height(), cache_key)
        task.signals.thumbnail_ready.connect(
            self._on_task_ready, ui_qt.Qt.ConnectionType.QueuedConnection
        )
        self._tasks[cache_key] = task
        self._pool.start(task)

    @ui_qt.Slot(str, object, ui_qt.QImage)
    def _on_task_ready(self, path, cache_key, image):
        self._tasks.pop(cache_key, None)
        self.thumbnail_ready.emit(path, cache_key, image)

    def shutdown(self):
        """主线程调用：等后台线程池尽量干净退出，避免 Qt 析构告警。"""
        self._pool.clear()
        self._pool.waitForDone(5000)
        self._tasks.clear()
