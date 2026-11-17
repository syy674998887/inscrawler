"""Stable option keys, translated labels, and environment-value mappings."""

from __future__ import annotations

LANGUAGE_OPTIONS = (
    ("en", "English"),
    ("zh", "中文"),
)


THEME_OPTIONS = (
    ("light", {"en": "Light", "zh": "浅色"}),
    ("dark", {"en": "Dark", "zh": "深色"}),
)


CLOSE_BEHAVIOR_OPTIONS = (
    ("tray", {"en": "System Tray", "zh": "系统托盘"}),
    ("exit", {"en": "Exit App", "zh": "退出程序"}),
)


NOTIFICATION_OPTIONS = (
    ("", {"en": "None", "zh": "无"}),
    ("system", {"en": "System", "zh": "系统通知"}),
    ("discord", {"en": "Discord", "zh": "Discord"}),
    ("telegram", {"en": "Telegram", "zh": "Telegram"}),
)


THEME_ENV_MAP = {
    "light": "light",
    "dark": "dark",
    "浅色": "light",
    "深色": "dark",
}


LANGUAGE_ENV_MAP = {
    "en": "en",
    "english": "en",
    "zh": "zh",
    "zh_cn": "zh",
    "chinese": "zh",
    "中文": "zh",
}


CLOSE_BEHAVIOR_ENV_MAP = {
    "tray": "tray",
    "system_tray": "tray",
    "system tray": "tray",
    "系统托盘": "tray",
    "exit": "exit",
    "close": "exit",
    "direct_close": "exit",
    "direct close": "exit",
    "退出程序": "exit",
}
