"""Shared state for one desktop download session and its cooperative controls."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field

import inscrawler.downloads.models as downloads_models
import inscrawler.paths as app_paths


@dataclass
class DownloadRuntime:
    """Configured by the UI before dispatch; owned for the process lifetime.

    This is intentionally one shared runtime, matching the application's single
    active task and LoaderManager. It is not a multi-account worker container.
    """

    login_username: str | None = None
    session_cookie_text: str = ""
    webhook: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    notification_type: str = ""
    system_notification_callback: Callable[[str], None] | None = None
    image_download_callback: Callable[[str], None] | None = None
    profile_picture_callback: Callable[[str, str], None] | None = None
    file_download_callback: Callable[[dict], None] | None = None
    file_activity_callback: Callable[[dict], None] | None = None
    item_completed_callback: Callable[[dict], None] | None = None
    scope_completed_callback: Callable[[dict], None] | None = None
    api_status_callback: Callable[[str], None] | None = None
    download_status_callback: Callable[[str], None] | None = None
    private_client_status: str = downloads_models.status_code.SUCCESS
    download_base_dir: str = app_paths.DEFAULT_DATA_DIR
    iphone_support_enabled: bool = False
    profile_picture_sync_enabled: bool = False
    active_task_id: str | None = None
    api_lock: threading.Lock = field(default_factory=threading.Lock)
    pause_condition: threading.Condition = field(default_factory=threading.Condition)
    paused: bool = False
    stop_requested: bool = False


runtime = DownloadRuntime()
