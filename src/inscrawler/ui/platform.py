"""Windows shutdown execution and secondary-window icon handling."""

from __future__ import annotations

import subprocess

import inscrawler.ui.qt as ui_qt


def _system_shutdown_command():
    # The app provides the cancellable 60-second countdown before this request.
    return [
        "shutdown",
        "/s",
        "/f",
        "/t",
        "0",
    ]


def _run_system_shutdown_command(command):
    kwargs = {
        "capture_output": True,
        "text": True,
        "timeout": 10,
    }
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if creationflags:
        kwargs["creationflags"] = creationflags
    return subprocess.run(command, **kwargs)


def _suppress_secondary_window_icon(window):
    # Omitting setWindowIcon() would inherit the owner/application icon. An
    # explicit null icon removes both the native title-bar icon and its slot.
    window.setWindowIcon(ui_qt.QIcon())
