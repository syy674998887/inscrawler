"""Task records and queue-controller targets shared by offline tests."""

from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import inscrawler.ui.main_window as ui_main_window


def task_entry(
    task_id,
    status="pending",
    *,
    kind="download",
    created_at="2026-01-01 00:00:00",
    scope_unit="page",
    scope_limit=3,
    scope_completed=0,
    downloaded_count=0,
):
    if kind == "auto_patrol":
        task = {
            "task_id": task_id,
            "task_kind": kind,
            "target_users": [task_id.lower()],
            "download_media_type": "posts",
            "page_limit": 3,
            "notification": "",
            "created_at": created_at,
        }
    else:
        task = {
            "task_id": task_id,
            "task_kind": kind,
            "target_user": task_id.lower(),
            "download_media_type": "posts",
            "scope_unit": scope_unit,
            "scope_limit": scope_limit,
            "scope_completed": scope_completed,
            "downloaded_count": downloaded_count,
            "media_request_cap": None,
            "notification": "",
            "created_at": created_at,
        }
    entry = {
        "schema_version": 7,
        "task": task,
        "status": status,
        "logs": [],
        "downloaded_files": [],
        "downloaded_file_details": [],
    }
    if kind == "auto_patrol":
        entry["log_targets"] = []
        entry["patrol_state"] = {
            "total_downloaded": 0,
            "accounts": [
                {
                    "target_user": task_id.lower(),
                    "state": "waiting",
                    "checked_count": 0,
                    "updates_found_count": 0,
                    "total_downloaded_count": 0,
                    "message": "",
                }
            ],
        }
    if status in ("pending", "running", "deferred", "stopped"):
        entry["queue_order"] = 0
    elif status == "completed":
        entry["completed_at"] = created_at
        if kind == "auto_patrol":
            entry["patrol_state"]["accounts"][0]["state"] = "no_updates"
    return entry


def make_queue_target():
    pending = task_entry("B")
    active = task_entry("A", "running")
    target = SimpleNamespace(
        queue_items=[active, pending],
        current_queue_index=0,
        queue_running=True,
        queue_paused=True,
        queue_started=2,
        queue_completed=0,
        _download_stop_reason=None,
        _queue_handoff_target_id=None,
        _thread_pipeline_stop_requested=False,
        _safe_exit_in_progress=False,
        _force_exit=False,
        _thread_pipeline_current_file_name="active-file.jpg",
        _download_thread_is_running=lambda: True,
        active_patrol_entry=None,
        patrol_enabled=False,
        _refresh_thread_pipeline=Mock(),
        update_download_button_state=Mock(),
        _refresh_queue_reorder_state=Mock(),
        update_api_status=Mock(),
        update_download_status=Mock(),
        start_task_by_index=Mock(),
        _refresh_target_account_icon=Mock(),
        apply_download_result_to_task=Mock(),
        _refresh_log_dialog_status=Mock(),
        save_task=Mock(),
        save_tasks=Mock(),
        _finalize_queue_run=Mock(),
        set_queue_item_status=Mock(
            side_effect=lambda entry, status, **kwargs: entry.__setitem__(
                "status", status
            )
        ),
    )
    for name in (
        "_current_queue_entry",
        "_first_runnable_index",
        "_first_paused_queue_candidate",
        "_queue_index_for_entry",
        "_queue_index_for_task_id",
        "_nonnegative_int",
        "_task_scope_unit",
        "_task_scope_limit",
        "_task_scope_completed",
        "_set_task_scope_completed",
        "_task_remaining_scope",
        "_clear_thread_pipeline_current_file",
        "_begin_queue_handoff",
        "_cancel_queue_handoff",
        "_start_queue_handoff_target",
        "_restore_deferred_tasks",
    ):
        setattr(target, name, MethodType(getattr(ui_main_window.GUI, name), target))
    return target, active, pending
