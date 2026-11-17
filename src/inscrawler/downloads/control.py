"""Cooperative Pause/Stop and the shared request-delay policy."""

from __future__ import annotations

import random
import time

import inscrawler.diagnostics.console as diagnostics_console
import inscrawler.downloads.models as downloads_models
import inscrawler.downloads.runtime as downloads_runtime

REQUEST_DELAY = (20, 40)  # 单次网络请求间隔


DOWNLOAD_DELAY = REQUEST_DELAY  # CDN 文件下载节奏；默认沿用请求延迟


USER_SWITCH_DELAY = (40, 80)  # 队列任务间冷却（GUI 调用）


DELAY_JITTER_RANGE = (1.0, 1.5)  # 所有延迟类型共用的随机抖动系数


def pause_downloads():
    with downloads_runtime.runtime.pause_condition:
        downloads_runtime.runtime.paused = True


def resume_downloads():
    with downloads_runtime.runtime.pause_condition:
        if downloads_runtime.runtime.paused:
            downloads_runtime.runtime.paused = False
            downloads_runtime.runtime.pause_condition.notify_all()


def request_stop_downloads():
    """Ask the active download flow to stop at the next safe point."""
    with downloads_runtime.runtime.pause_condition:
        downloads_runtime.runtime.stop_requested = True
        downloads_runtime.runtime.paused = False
        downloads_runtime.runtime.pause_condition.notify_all()


def clear_stop_request():
    with downloads_runtime.runtime.pause_condition:
        downloads_runtime.runtime.stop_requested = False


def is_stop_requested():
    with downloads_runtime.runtime.pause_condition:
        return downloads_runtime.runtime.stop_requested


def _raise_if_stop_requested():
    if is_stop_requested():
        raise downloads_models.DownloadStopRequested()


def wait_if_paused():
    logged = False
    with downloads_runtime.runtime.pause_condition:
        while (
            downloads_runtime.runtime.paused
            and not downloads_runtime.runtime.stop_requested
        ):
            if not logged:
                diagnostics_console.print("Download paused")
                logged = True
            downloads_runtime.runtime.pause_condition.wait(timeout=0.25)
        if downloads_runtime.runtime.stop_requested:
            raise downloads_models.DownloadStopRequested()
    if logged:
        diagnostics_console.print("Download resumed")


def delay(kind: str = "request"):
    ranges = {
        "request": REQUEST_DELAY,
        "download": DOWNLOAD_DELAY,
        "user_switch": USER_SWITCH_DELAY,
    }
    min_s, max_s = ranges.get(kind, REQUEST_DELAY)
    base = random.uniform(min_s, max_s)
    jitter = random.uniform(*DELAY_JITTER_RANGE)
    wait = max(1, base * jitter)
    diagnostics_console.print(f"Delay ({kind}): {wait:.2f} seconds")
    end = time.monotonic() + wait
    while True:
        wait_if_paused()
        _raise_if_stop_requested()
        remaining = end - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.25, remaining))
