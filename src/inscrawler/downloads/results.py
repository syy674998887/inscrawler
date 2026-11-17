"""Download and patrol result payloads and messages."""

from __future__ import annotations

import inscrawler.downloads.models as downloads_models


def _content_result(
    status,
    message,
    downloaded_count=0,
    *,
    patrol_mode=False,
    checked_count=0,
    already_downloaded_count=0,
    updates_found_count=0,
    download_failed_count=0,
    scope_completed_count=None,
    media_request_count=None,
    stop_reason=None,
    completion_reason=None,
    blocking_failure=False,
):
    result = {
        "status": status,
        "message": message,
        "downloaded_count": downloaded_count,
    }
    if patrol_mode:
        unresolved_count = max(
            0,
            checked_count - already_downloaded_count - updates_found_count,
        )
        result.update(
            {
                "checked_count": checked_count,
                "already_downloaded_count": already_downloaded_count,
                "updates_found_count": updates_found_count,
                "unresolved_count": unresolved_count,
                "download_failed_count": download_failed_count,
                "no_updates_confirmed": (
                    status == downloads_models.status_code.SUCCESS
                    and updates_found_count == 0
                    and unresolved_count == 0
                ),
            }
        )
    if scope_completed_count is not None:
        result["scope_completed_count"] = max(0, int(scope_completed_count))
    if not patrol_mode and (scope_completed_count is not None or download_failed_count):
        result["download_failed_count"] = max(0, int(download_failed_count))
    if media_request_count is not None:
        result["media_request_count"] = max(0, int(media_request_count))
    if stop_reason:
        result["stop_reason"] = str(stop_reason)
    if completion_reason:
        result["completion_reason"] = str(completion_reason)
    if blocking_failure:
        result["blocking_failure"] = True
    return result


def _patrol_content_message(
    target_username,
    kind,
    checked_count,
    already_downloaded_count,
    updates_found_count,
    downloaded_count,
    download_failed_count=0,
):
    if checked_count <= 0:
        return f"Patrol found no available {kind} for @{target_username}"
    if updates_found_count <= 0:
        unresolved_count = max(0, checked_count - already_downloaded_count)
        if unresolved_count:
            return (
                f"Patrol checked {checked_count} latest {kind} for @{target_username}: "
                f"{already_downloaded_count} already downloaded, "
                f"{unresolved_count} could not be classified"
            )
        return (
            f"Patrol found no {kind} updates for @{target_username} "
            f"({already_downloaded_count}/{checked_count} checked items already downloaded)"
        )
    return (
        f"Patrol detected an update and scanned {checked_count} {kind} for @{target_username}: "
        f"found {updates_found_count} update(s), downloaded {downloaded_count}, "
        f"failed {download_failed_count}"
    )


def _positive_limit(value, default=1):
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return max(1, int(default))


def _post_page_result(
    status,
    message,
    downloaded,
    completed_pages,
    budget,
    *,
    stop_reason=None,
    completion_reason=None,
    download_failed_count=0,
    blocking_failure=False,
):
    result = {
        "status": status,
        "message": message,
        "downloaded_count": downloaded,
        "scope_completed_count": completed_pages,
        "media_request_count": budget.used,
        "download_failed_count": download_failed_count,
    }
    if stop_reason:
        result["stop_reason"] = stop_reason
    if completion_reason:
        result["completion_reason"] = completion_reason
    if blocking_failure:
        result["blocking_failure"] = True
    return result
