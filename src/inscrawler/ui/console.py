"""Capture GUI, Qt, and worker console messages for task diagnostics."""

from __future__ import annotations

import builtins
import os
import re
import sys
import threading
from collections import deque

import inscrawler.ui.qt as ui_qt

os.environ.setdefault("IMAGEIO_FFMPEG_LOGLEVEL", "error")


os.environ.setdefault("FFMPEG_HIDE_BANNER", "1")


os.environ.setdefault("AV_LOG_FORCE_NOCOLOR", "1")


_module_print = builtins.print


_gui_log_callback = None


_console_log_callback = None


_debug_logging_enabled = False


_console_capture_hub = None


_LOG_TARGET_UNSET = object()


def _console_capture_is_active():
    return _console_capture_hub is not None


def print(*args, **kwargs):
    msg = " ".join(str(a) for a in args)
    line = f"[gui] {msg}"
    _module_print("[gui]", msg, **kwargs)
    # When stdout/stderr mirroring is active, the stream capture is the single
    # source for both the process console and task logs.  Calling the legacy
    # callbacks as well would duplicate every GUI line.
    if _console_capture_is_active():
        return
    cb = _gui_log_callback
    delivered = False
    if cb is not None:
        try:
            cb(line)
            delivered = True
        except Exception:
            pass
    if not delivered:
        console_cb = _console_log_callback
        if console_cb is not None:
            try:
                console_cb(line)
            except Exception:
                pass


def debug_print(*args, **kwargs):
    if not _debug_logging_enabled:
        return
    msg = " ".join(str(a) for a in args)
    line = f"[gui] {msg}"
    _module_print("[gui]", msg, **kwargs)
    if _console_capture_is_active():
        return
    cb = _console_log_callback
    if cb is not None:
        try:
            cb(line)
        except Exception:
            pass


class _ConsoleCaptureBridge(ui_qt.QObject):
    """Drain complete stdout/stderr records on the GUI thread in write order."""

    records_pending = ui_qt.Signal()
    record_ready = ui_qt.Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._gui_thread_id = threading.get_ident()
        self._pending = deque()
        self._pending_lock = threading.Lock()
        self._draining = False
        self.records_pending.connect(
            self._drain,
            ui_qt.Qt.ConnectionType.QueuedConnection,
        )

    def submit(self, record):
        with self._pending_lock:
            self._pending.append(record)
        if threading.get_ident() == self._gui_thread_id:
            # A main-thread write may overtake a queued worker signal.  Drain
            # both here so GUI history still follows the real console order.
            self._drain()
        else:
            self.records_pending.emit()

    def flush(self):
        if threading.get_ident() == self._gui_thread_id:
            self._drain()

    @ui_qt.Slot()
    def _drain(self):
        if self._draining:
            return
        self._draining = True
        try:
            while True:
                with self._pending_lock:
                    if not self._pending:
                        break
                    record = self._pending.popleft()
                self.record_ready.emit(record)
        finally:
            self._draining = False


class _MirroredTextStream:
    """Text stream that preserves the real console while mirroring its lines."""

    def __init__(self, hub, stream_name, original):
        self._hub = hub
        self._stream_name = stream_name
        self._original = original

    def write(self, text):
        return self._hub.write(self._stream_name, self._original, text)

    def writelines(self, lines):
        for line in lines:
            self.write(line)

    def flush(self):
        if self._original is not None:
            return self._original.flush()
        return None

    def isatty(self):
        return bool(self._original is not None and self._original.isatty())

    @property
    def encoding(self):
        return getattr(self._original, "encoding", "utf-8")

    @property
    def errors(self):
        return getattr(self._original, "errors", "replace")

    def __getattr__(self, name):
        if self._original is None:
            raise AttributeError(name)
        return getattr(self._original, name)


class _ConsoleCaptureHub:
    """Single ordered stdout/stderr source for console and task log views."""

    _LINE_ENDING_RE = re.compile(r"\r\n|\r|\n")

    def __init__(self, emit_record, stdout, stderr):
        self._emit_record = emit_record
        self._lock = threading.RLock()
        self._buffers = {}
        self._task_id = None
        self._target_username = None
        self.original_stdout = stdout
        self.original_stderr = stderr
        self.stdout = _MirroredTextStream(self, "stdout", stdout)
        self.stderr = _MirroredTextStream(self, "stderr", stderr)

    @staticmethod
    def _normalize_task_id(task_id):
        return str(task_id or "").strip() or None

    @staticmethod
    def _normalize_target(target_username):
        return str(target_username or "").strip().lstrip("@").strip() or None

    @staticmethod
    def _record(line, stream_name, task_id, target_username):
        return {
            "line": str(line),
            "stream": stream_name,
            "task_id": task_id,
            "target_username": target_username,
        }

    def _emit(self, records):
        for record in records:
            try:
                self._emit_record(record)
            except Exception:
                # Mirroring must never make the real stdout/stderr write fail.
                pass

    def write(self, stream_name, original, text):
        if not isinstance(text, str):
            raise TypeError(f"write() argument must be str, not {type(text).__name__}")
        if not text:
            return 0

        records = []
        with self._lock:
            written = original.write(text) if original is not None else len(text)
            key = (stream_name, threading.get_ident())
            pending = self._buffers.get(key)
            if pending is None:
                pending_text = ""
                task_id = self._task_id
                target_username = self._target_username
            else:
                pending_text, task_id, target_username = pending

            combined = pending_text + text
            start = 0
            for match in self._LINE_ENDING_RE.finditer(combined):
                records.append(
                    self._record(
                        combined[start : match.start()],
                        stream_name,
                        task_id,
                        target_username,
                    )
                )
                start = match.end()

            remainder = combined[start:]
            if remainder:
                # A new physical line starts after every completed newline, so
                # snapshot ownership again for that pending line.
                if start:
                    task_id = self._task_id
                    target_username = self._target_username
                self._buffers[key] = (remainder, task_id, target_username)
            else:
                self._buffers.pop(key, None)

            # Emit while the write lock is still held so records reach the GUI
            # bridge in exactly the same order as stdout/stderr writes reached
            # the real process console.
            self._emit(records)
        return written if isinstance(written, int) else len(text)

    def _take_pending(self, *, task_id=None):
        records = []
        with self._lock:
            for key, pending in list(self._buffers.items()):
                text, owner_task_id, target_username = pending
                if task_id is not None and owner_task_id != task_id:
                    continue
                stream_name, _thread_id = key
                records.append(
                    self._record(
                        text,
                        stream_name,
                        owner_task_id,
                        target_username,
                    )
                )
                self._buffers.pop(key, None)
        return records

    def flush_pending(self, *, task_id=None):
        with self._lock:
            self._emit(self._take_pending(task_id=task_id))

    def begin_task(self, task_id):
        task_id = self._normalize_task_id(task_id)
        with self._lock:
            self.flush_pending()
            self._task_id = task_id
            self._target_username = None

    def set_target(self, task_id, target_username):
        task_id = self._normalize_task_id(task_id)
        with self._lock:
            if self._task_id != task_id:
                return False
            self.flush_pending(task_id=task_id)
            self._target_username = self._normalize_target(target_username)
            return True

    def end_task(self, task_id):
        task_id = self._normalize_task_id(task_id)
        with self._lock:
            if self._task_id != task_id:
                return False
            self.flush_pending(task_id=task_id)
            self._task_id = None
            self._target_username = None
            return True

    @property
    def task_id(self):
        with self._lock:
            return self._task_id


def _install_console_capture(emit_record):
    global _console_capture_hub
    if _console_capture_hub is not None:
        return _console_capture_hub
    hub = _ConsoleCaptureHub(emit_record, sys.stdout, sys.stderr)
    _console_capture_hub = hub
    sys.stdout = hub.stdout
    sys.stderr = hub.stderr
    return hub


def _uninstall_console_capture(hub):
    global _console_capture_hub
    if hub is None or _console_capture_hub is not hub:
        return
    hub.flush_pending()
    if sys.stdout is hub.stdout:
        sys.stdout = hub.original_stdout
    if sys.stderr is hub.stderr:
        sys.stderr = hub.original_stderr
    _console_capture_hub = None


def _begin_console_task_scope(task_id):
    hub = _console_capture_hub
    if hub is not None:
        hub.begin_task(task_id)


def _set_console_target_scope(task_id, target_username):
    hub = _console_capture_hub
    if hub is not None:
        hub.set_target(task_id, target_username)


def _end_console_task_scope(task_id):
    hub = _console_capture_hub
    return bool(hub is not None and hub.end_task(task_id))
