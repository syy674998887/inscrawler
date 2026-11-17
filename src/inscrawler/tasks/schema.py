"""Strict schema-v7 task validation and pure task state helpers."""

from __future__ import annotations

import os
from datetime import datetime

TASK_SCHEMA_VERSION = 7


DOWNLOAD_MEDIA_TYPES = (
    "Posts",
    "Reels",
    "Tagged",
    "Stories",
    "Highlights",
)


PATROL_MEDIA_TYPES = (
    ("Posts", "posts"),
    ("Reels", "reels"),
    ("Tagged", "tagged"),
    ("Stories", "stories"),
    ("Highlights", "highlights"),
)


_DOWNLOAD_MEDIA_TYPES = frozenset(label.lower() for label in DOWNLOAD_MEDIA_TYPES)


_PATROL_MEDIA_TYPES = frozenset(value for _label, value in PATROL_MEDIA_TYPES)


_NOTIFICATION_TYPES = frozenset({"", "system", "discord", "telegram"})


_DOWNLOAD_TASK_STATUSES = frozenset(
    {
        "pending",
        "running",
        "deferred",
        "completed",
        "stopped",
    }
)


_PATROL_TASK_STATUSES = frozenset(
    {
        "pending",
        "running",
        "deferred",
        "completed",
        "stopped",
    }
)


_PATROL_ACCOUNT_STATES = frozenset(
    {
        "waiting",
        "checking",
        "no_updates",
        "updated",
        "failed",
        "stopped",
    }
)


_PATROL_COMPLETED_ACCOUNT_STATES = frozenset(
    {
        "no_updates",
        "updated",
    }
)


_SAVED_TASK_ROOT_FIELDS = frozenset(
    {
        "schema_version",
        "task",
        "status",
        "logs",
        "downloaded_files",
        "downloaded_file_details",
    }
)


_DOWNLOAD_TASK_FIELDS = frozenset(
    {
        "task_id",
        "task_kind",
        "target_user",
        "download_media_type",
        "scope_unit",
        "scope_limit",
        "scope_completed",
        "media_request_cap",
        "notification",
        "created_at",
        "downloaded_count",
    }
)


_DOWNLOAD_SCOPE_UNITS = frozenset({"page", "full"})


_PAGED_DOWNLOAD_MEDIA_TYPES = frozenset({"posts", "reels", "tagged"})


_FULL_DOWNLOAD_MEDIA_TYPES = frozenset({"stories", "highlights"})


_PAGED_PATROL_MEDIA_TYPES = frozenset({"posts", "reels", "tagged"})


_PATROL_TASK_FIELDS = frozenset(
    {
        "task_id",
        "task_kind",
        "target_users",
        "download_media_type",
        "notification",
        "created_at",
    }
)


_PAGED_PATROL_TASK_FIELDS = _PATROL_TASK_FIELDS | {"page_limit"}


_PATROL_STATE_FIELDS = frozenset({"total_downloaded", "accounts"})


_PATROL_ACCOUNT_FIELDS = frozenset(
    {
        "target_user",
        "state",
        "checked_count",
        "updates_found_count",
        "total_downloaded_count",
        "message",
    }
)


_DOWNLOAD_FILE_DETAIL_FIELDS = frozenset(
    {
        "path",
        "filename",
        "extension",
        "media_kind",
        "download_category",
        "segment_index",
        "segment_total",
        "source",
        "source_url_host",
        "shortcode",
        "media_id",
        "typename",
        "media_date_utc",
        "media_date_local",
        "downloaded_at",
        "size_bytes",
        "response_content_type",
        "response_content_length",
        "response_status_code",
    }
)


def _schema_error(path, message):
    raise ValueError(f"{path}: {message}")


def _require_exact_fields(value, expected, path):
    if not isinstance(value, dict):
        _schema_error(path, "must be an object")
    actual = set(value)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing:
        _schema_error(path, f"missing field(s): {', '.join(missing)}")
    if extra:
        _schema_error(path, f"unexpected field(s): {', '.join(extra)}")


def _require_text(value, path, allow_empty=False):
    if not isinstance(value, str):
        _schema_error(path, "must be a string")
    if not allow_empty and not value:
        _schema_error(path, "must not be empty")
    return value


def _require_nonnegative_int(value, path, minimum=0):
    if type(value) is not int or value < minimum:
        _schema_error(path, f"must be an integer >= {minimum}")
    return value


def _require_timestamp(value, path):
    _require_text(value, path)
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        _schema_error(path, "must use YYYY-MM-DD HH:MM:SS")
    if parsed.strftime("%Y-%m-%d %H:%M:%S") != value:
        _schema_error(path, "must use YYYY-MM-DD HH:MM:SS")


def _require_optional_text(value, path):
    if value is not None and not isinstance(value, str):
        _schema_error(path, "must be a string or null")


def _validate_download_file_detail(detail, index, downloaded_paths):
    path = f"downloaded_file_details[{index}]"
    _require_exact_fields(detail, _DOWNLOAD_FILE_DETAIL_FIELDS, path)

    file_path = _require_text(detail["path"], f"{path}.path")
    filename = _require_text(detail["filename"], f"{path}.filename")
    extension = _require_text(detail["extension"], f"{path}.extension")
    if filename != os.path.basename(file_path):
        _schema_error(f"{path}.filename", "must match path basename")
    if extension != os.path.splitext(filename)[1].lower():
        _schema_error(f"{path}.extension", "must match filename extension")

    media_kind = _require_text(detail["media_kind"], f"{path}.media_kind")
    if media_kind not in ("image", "video"):
        _schema_error(f"{path}.media_kind", "must be image or video")
    category = _require_text(detail["download_category"], f"{path}.download_category")
    if category not in _DOWNLOAD_MEDIA_TYPES:
        _schema_error(f"{path}.download_category", "is not supported")

    segment_index = _require_nonnegative_int(
        detail["segment_index"],
        f"{path}.segment_index",
        minimum=1,
    )
    segment_total = _require_nonnegative_int(
        detail["segment_total"],
        f"{path}.segment_total",
        minimum=1,
    )
    if segment_index > segment_total:
        _schema_error(f"{path}.segment_index", "must not exceed segment_total")

    _require_text(detail["source"], f"{path}.source")
    _require_text(
        detail["source_url_host"], f"{path}.source_url_host", allow_empty=True
    )
    for field in ("shortcode", "typename"):
        _require_optional_text(detail[field], f"{path}.{field}")
    media_id = detail["media_id"]
    if media_id is not None and type(media_id) not in (int, str):
        _schema_error(f"{path}.media_id", "must be an integer, string, or null")
    for field in ("media_date_utc", "media_date_local"):
        value = detail[field]
        _require_optional_text(value, f"{path}.{field}")
        if value:
            try:
                datetime.fromisoformat(value)
            except ValueError:
                _schema_error(f"{path}.{field}", "must be an ISO 8601 datetime or null")
    _require_timestamp(detail["downloaded_at"], f"{path}.downloaded_at")

    size_bytes = detail["size_bytes"]
    if size_bytes is not None:
        _require_nonnegative_int(size_bytes, f"{path}.size_bytes")
    for field in ("response_content_type", "response_content_length"):
        _require_optional_text(detail[field], f"{path}.{field}")
    status_code = detail["response_status_code"]
    if status_code is not None:
        _require_nonnegative_int(
            status_code, f"{path}.response_status_code", minimum=100
        )
        if status_code > 599:
            _schema_error(f"{path}.response_status_code", "must be <= 599")

    normalized_path = os.path.normcase(os.path.normpath(file_path))
    if normalized_path not in downloaded_paths:
        _schema_error(f"{path}.path", "must also appear in downloaded_files")
    return normalized_path


def _validate_saved_task_data(data, expected_task_id=None):
    """Validate the only supported on-disk task format (schema v7)."""
    if not isinstance(data, dict):
        _schema_error("root", "must be an object")
    if (
        data.get("schema_version") != TASK_SCHEMA_VERSION
        or type(data.get("schema_version")) is not int
    ):
        _schema_error("schema_version", f"must be {TASK_SCHEMA_VERSION}")

    task = data.get("task")
    if not isinstance(task, dict):
        _schema_error("task", "must be an object")
    task_kind = task.get("task_kind")
    status = data.get("status")
    if task_kind == "download":
        allowed_statuses = _DOWNLOAD_TASK_STATUSES
    elif task_kind == "auto_patrol":
        allowed_statuses = _PATROL_TASK_STATUSES
    else:
        _schema_error("task.task_kind", "must be download or auto_patrol")
    _require_text(status, "status")
    if status not in allowed_statuses:
        _schema_error("status", f"is invalid for {task_kind}")

    media_type = _require_text(
        task.get("download_media_type"),
        "task.download_media_type",
    )
    supported_media = (
        _PATROL_MEDIA_TYPES if task_kind == "auto_patrol" else _DOWNLOAD_MEDIA_TYPES
    )
    if media_type not in supported_media:
        _schema_error("task.download_media_type", "is not supported")

    if task_kind == "download":
        task_fields = _DOWNLOAD_TASK_FIELDS
    elif media_type in _PAGED_PATROL_MEDIA_TYPES:
        task_fields = _PAGED_PATROL_TASK_FIELDS
    else:
        task_fields = _PATROL_TASK_FIELDS
    _require_exact_fields(task, task_fields, "task")

    root_fields = set(_SAVED_TASK_ROOT_FIELDS)
    if _uses_queue_order(task, status):
        root_fields.add("queue_order")
    if status == "completed":
        root_fields.add("completed_at")
    if task_kind == "auto_patrol":
        root_fields.add("patrol_state")
        root_fields.add("log_targets")
    _require_exact_fields(data, root_fields, "root")

    task_id = _require_text(task["task_id"], "task.task_id")
    if len(task_id) != 12 or any(
        character not in "0123456789abcdef" for character in task_id
    ):
        _schema_error("task.task_id", "must be 12 lowercase hexadecimal characters")
    if expected_task_id is not None and task_id != expected_task_id:
        _schema_error("task.task_id", "must match the JSON filename")

    notification = _require_text(
        task["notification"], "task.notification", allow_empty=True
    )
    if notification not in _NOTIFICATION_TYPES:
        _schema_error(
            "task.notification",
            "must be empty, system, discord, or telegram",
        )
    _require_timestamp(task["created_at"], "task.created_at")

    if task_kind == "download":
        target_user = _require_text(task["target_user"], "task.target_user")
        if target_user != target_user.strip().lstrip("@"):
            _schema_error("task.target_user", "must be a normalized username")
        scope_unit = _require_text(task["scope_unit"], "task.scope_unit")
        if scope_unit not in _DOWNLOAD_SCOPE_UNITS:
            _schema_error("task.scope_unit", "must be page or full")
        scope_limit = _require_nonnegative_int(
            task["scope_limit"],
            "task.scope_limit",
            minimum=1,
        )
        if scope_limit > 1000:
            _schema_error("task.scope_limit", "must be <= 1000")
        scope_completed = _require_nonnegative_int(
            task["scope_completed"],
            "task.scope_completed",
        )
        if scope_completed > scope_limit:
            _schema_error("task.scope_completed", "must not exceed scope_limit")
        _require_nonnegative_int(
            task["downloaded_count"],
            "task.downloaded_count",
        )
        media_request_cap = task["media_request_cap"]
        if media_request_cap is not None:
            _require_nonnegative_int(
                media_request_cap,
                "task.media_request_cap",
                minimum=1,
            )
            if media_request_cap > 10000:
                _schema_error("task.media_request_cap", "must be <= 10000")
        if scope_unit == "page" and media_type not in _PAGED_DOWNLOAD_MEDIA_TYPES:
            _schema_error("task.scope_unit", "page is only valid for paged media")
        if scope_unit == "full":
            if media_type not in _FULL_DOWNLOAD_MEDIA_TYPES:
                _schema_error(
                    "task.scope_unit", "full is only valid for Stories/Highlights"
                )
            if scope_limit != 1:
                _schema_error("task.scope_limit", "must be 1 for full scope")
    else:
        targets = task["target_users"]
        if not isinstance(targets, list) or not targets:
            _schema_error("task.target_users", "must be a non-empty array")
        normalized_targets = []
        for index, target in enumerate(targets):
            target = _require_text(target, f"task.target_users[{index}]")
            if target != target.strip().lstrip("@"):
                _schema_error(
                    f"task.target_users[{index}]", "must be a normalized username"
                )
            normalized_targets.append(target.casefold())
        if len(set(normalized_targets)) != len(normalized_targets):
            _schema_error("task.target_users", "must not contain duplicates")
        if media_type in _PAGED_PATROL_MEDIA_TYPES:
            page_limit = _require_nonnegative_int(
                task["page_limit"],
                "task.page_limit",
                minimum=1,
            )
            if page_limit > 100:
                _schema_error("task.page_limit", "must be <= 100")

        patrol_state = data["patrol_state"]
        _require_exact_fields(patrol_state, _PATROL_STATE_FIELDS, "patrol_state")
        total_downloaded = _require_nonnegative_int(
            patrol_state["total_downloaded"],
            "patrol_state.total_downloaded",
        )
        accounts = patrol_state["accounts"]
        if not isinstance(accounts, list) or len(accounts) != len(targets):
            _schema_error("patrol_state.accounts", "must match task.target_users")
        account_total = 0
        for index, (account, target) in enumerate(zip(accounts, targets)):
            path = f"patrol_state.accounts[{index}]"
            _require_exact_fields(account, _PATROL_ACCOUNT_FIELDS, path)
            if account["target_user"] != target:
                _schema_error(
                    f"{path}.target_user", "must match task.target_users order"
                )
            account_state = _require_text(account["state"], f"{path}.state")
            if account_state not in _PATROL_ACCOUNT_STATES:
                _schema_error(f"{path}.state", "is invalid")
            if (
                status == "completed"
                and account_state not in _PATROL_COMPLETED_ACCOUNT_STATES
            ):
                _schema_error(f"{path}.state", "is unfinished for a completed patrol")
            for field in (
                "checked_count",
                "updates_found_count",
                "total_downloaded_count",
            ):
                _require_nonnegative_int(account[field], f"{path}.{field}")
            _require_text(account["message"], f"{path}.message", allow_empty=True)
            account_total += account["total_downloaded_count"]
        if account_total != total_downloaded:
            _schema_error(
                "patrol_state.total_downloaded", "must equal the account total"
            )

    if "queue_order" in data:
        _require_nonnegative_int(data["queue_order"], "queue_order")

    if "completed_at" in data:
        _require_timestamp(data["completed_at"], "completed_at")

    logs = data["logs"]
    if not isinstance(logs, list) or any(not isinstance(line, str) for line in logs):
        _schema_error("logs", "must be an array of strings")
    if task_kind == "auto_patrol":
        log_targets = data["log_targets"]
        if not isinstance(log_targets, list) or len(log_targets) != len(logs):
            _schema_error("log_targets", "must align with logs")
        target_users = set(task["target_users"])
        for index, target in enumerate(log_targets):
            if target is None:
                continue
            target = _require_text(target, f"log_targets[{index}]")
            if target not in target_users:
                _schema_error(
                    f"log_targets[{index}]",
                    "must be null or an exact task target username",
                )
    downloaded_files = data["downloaded_files"]
    if not isinstance(downloaded_files, list):
        _schema_error("downloaded_files", "must be an array")
    normalized_paths = []
    for index, file_path in enumerate(downloaded_files):
        file_path = _require_text(file_path, f"downloaded_files[{index}]")
        normalized_paths.append(os.path.normcase(os.path.normpath(file_path)))
    if len(set(normalized_paths)) != len(normalized_paths):
        _schema_error("downloaded_files", "must not contain duplicate paths")

    details = data["downloaded_file_details"]
    if not isinstance(details, list):
        _schema_error("downloaded_file_details", "must be an array")
    downloaded_path_set = set(normalized_paths)
    detail_paths = [
        _validate_download_file_detail(detail, index, downloaded_path_set)
        for index, detail in enumerate(details)
    ]
    if len(set(detail_paths)) != len(detail_paths):
        _schema_error("downloaded_file_details", "must not contain duplicate paths")
    return data


def _is_auto_patrol_task(task):
    return isinstance(task, dict) and task.get("task_kind") == "auto_patrol"


def _patrol_target_progress(entry):
    """Return completed target checks and the immutable patrol target total."""
    entry = entry if isinstance(entry, dict) else {}
    task = entry.get("task") if isinstance(entry.get("task"), dict) else {}
    target_users = list(task.get("target_users") or [])
    total = len(target_users)
    accounts = (entry.get("patrol_state") or {}).get("accounts") or []
    completed = sum(
        1
        for account in accounts
        if isinstance(account, dict)
        and account.get("state") in _PATROL_COMPLETED_ACCOUNT_STATES
    )
    return min(completed, total), total


def _is_runnable_task(task, status):
    return (
        status in ("pending", "stopped")
        and isinstance(task, dict)
        and task.get("task_kind") in ("download", "auto_patrol")
    )


def _uses_queue_order(task, status):
    return (
        isinstance(task, dict)
        and task.get("task_kind") in ("download", "auto_patrol")
        and (status in ("running", "deferred") or _is_runnable_task(task, status))
    )


def _format_task_label(task):
    if _is_auto_patrol_task(task):
        media_type = task["download_media_type"].capitalize()
        target_count = len(task["target_users"])
        return f"Auto Patrol - {media_type}: {target_count} targets"
    media_type = task["download_media_type"].capitalize()
    target_user = task["target_user"]
    return f"{target_user} - {media_type}: {task['downloaded_count']}"


def _task_display_name(task):
    if _is_auto_patrol_task(task):
        return _format_task_label(task)
    target_user = str((task or {}).get("target_user") or "").strip().lstrip("@")
    media_type = str((task or {}).get("download_media_type") or "").capitalize()
    return f"@{target_user} {media_type}".strip()


def _format_task_date(task):
    return str(task.get("created_at", ""))[:10]


def _format_task_created_minute(task):
    return str(task.get("created_at", ""))[:16]
