"""Dispatch task-result notifications to configured channels."""

from __future__ import annotations

import threading
from functools import partial

import requests

import inscrawler.diagnostics.console as diagnostics_console
import inscrawler.downloads.runtime as downloads_runtime


def notify(message, *, background=False):
    # Capture recipients before dispatch: the GUI may start another task while
    # a background notification is still waiting to run.
    runtime = downloads_runtime.runtime
    if runtime.notification_type == "system":
        send = partial(_notify_system, message, runtime.system_notification_callback)
    elif runtime.notification_type == "discord":
        send = partial(_notify_discord, message, runtime.webhook)
    elif runtime.notification_type == "telegram":
        send = partial(
            _notify_telegram,
            message,
            runtime.telegram_bot_token,
            runtime.telegram_chat_id,
        )
    else:
        return None

    if not background:
        return send()
    try:
        worker = threading.Thread(target=send, name="inscrawler-notify", daemon=True)
        worker.start()
        return worker
    except RuntimeError as e:
        diagnostics_console.print(f"Failed to start notification: {type(e).__name__}")
        return None


def _notify_system(message, callback):
    if not callable(callback):
        diagnostics_console.print("System notification callback not configured")
        return
    try:
        callback(str(message))
    except Exception as e:
        diagnostics_console.print(f"Failed to request system notification: {e}")


def _notify_discord(message, webhook):
    if not webhook:
        diagnostics_console.print("Discord webhook not configured")
        return
    try:
        response = requests.post(
            webhook,
            json={"username": "inscrawler", "content": message},
            timeout=10,
        )
        response.raise_for_status()
        diagnostics_console.print("Discord notification sent successfully")
    except Exception as e:
        error = type(e).__name__
        if isinstance(e, requests.HTTPError) and e.response is not None:
            error += f" (HTTP {e.response.status_code})"
        diagnostics_console.print(f"Failed to send Discord notification: {error}")


def _notify_telegram(message, bot_token, chat_id):
    if not bot_token or not chat_id:
        diagnostics_console.print("Telegram bot token or chat id not configured")
        return
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        response = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": f"[inscrawler] {message}",
            },
            timeout=10,
        )
        response.raise_for_status()
        diagnostics_console.print("Telegram notification sent successfully")
    except Exception as e:
        error = type(e).__name__
        if isinstance(e, requests.HTTPError) and e.response is not None:
            error += f" (HTTP {e.response.status_code})"
        diagnostics_console.print(f"Failed to send Telegram notification: {error}")
