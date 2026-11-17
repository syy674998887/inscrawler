"""Serial per-account patrol dispatch and aggregate results."""

from __future__ import annotations

import inscrawler.diagnostics.console as diagnostics_console
import inscrawler.downloads.checkpoints as downloads_checkpoints
import inscrawler.downloads.control as downloads_control
import inscrawler.downloads.errors as downloads_errors
import inscrawler.downloads.highlights as downloads_highlights
import inscrawler.downloads.models as downloads_models
import inscrawler.downloads.posts as downloads_posts
import inscrawler.downloads.results as downloads_results
import inscrawler.downloads.stories as downloads_stories


def _normalize_patrol_targets(target_usernames):
    if isinstance(target_usernames, str):
        target_usernames = [target_usernames]

    normalized = []
    seen = set()
    for value in target_usernames or []:
        username = str(value or "").strip().lstrip("@").strip()
        key = username.casefold()
        if not username or key in seen:
            continue
        seen.add(key)
        normalized.append(username)
    return normalized


def _count_value(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def patrol_user_content(
    target_username, download_media_type, page_limit=None, resume_task_id=None
):
    """Detect updates in complete newest pages, then continue the same scan.

    Patrol always uses the stable serial path. Post-like media can resume the
    formal scan after an update was detected. Stories and Highlights consume
    their complete current response sets and remain non-resumable.
    """
    username = str(target_username or "").strip().lstrip("@").strip()
    kind = str(download_media_type or "").strip().lower()

    if not username:
        return downloads_results._content_result(
            downloads_models.status_code.FAILED,
            "Target username is required",
            patrol_mode=True,
        )
    if kind not in downloads_models._PATROL_MEDIA_TYPES:
        return downloads_results._content_result(
            downloads_models.status_code.FAILED,
            f"Unsupported patrol media type: {kind or '(empty)'}",
            patrol_mode=True,
        )

    try:
        downloads_control.wait_if_paused()
        if kind == "stories":
            result = downloads_stories.download_stories(
                username,
                1,
                patrol_mode=True,
            )
        elif kind == "highlights":
            result = downloads_highlights.download_highlights(
                username,
                1,
                patrol_mode=True,
            )
        else:
            page_limit = downloads_results._positive_limit(page_limit)
            post_kwargs = {"patrol_page_limit": page_limit}
            if resume_task_id:
                post_kwargs["patrol_resume_task_id"] = resume_task_id
            result = downloads_posts.download_posts(username, kind, **post_kwargs)

        if not isinstance(result, dict):
            return downloads_results._content_result(
                downloads_models.status_code.FAILED,
                f"Patrol function returned unexpected type: {type(result)}",
                patrol_mode=True,
            )
        diagnostics_console.print(
            result.get("message", f"Patrol finished for @{username}")
        )
        return result
    except downloads_models.DownloadStopRequested:
        diagnostics_console.print(
            f"Patrol stopped while checking @{username} ({kind.capitalize()})"
        )
        return downloads_results._content_result(
            downloads_models.status_code.STOPPED,
            f"Patrol stopped while checking @{username}",
            patrol_mode=True,
        )
    except downloads_models.IPhoneQualityUnavailable as e:
        diagnostics_console.print(
            f"iPhone quality unavailable during patrol for @{username}: {e}"
        )
        return downloads_results._content_result(
            downloads_models.status_code.IPHONE_QUALITY_UNAVAILABLE,
            f"iPhone quality unavailable: {e}",
            patrol_mode=True,
        )
    except Exception as e:
        diagnostics_console.print(f"Patrol failed for @{username}: {e}")
        return downloads_results._content_result(
            downloads_errors.handle_error(e),
            f"Patrol failed: {e}",
            patrol_mode=True,
        )


def patrol_targets_once(
    target_usernames,
    download_media_type,
    page_limit=None,
    target_event_callback=None,
    patrol_task_id=None,
    target_indexes=None,
):
    """Run one serial pass and synchronously report each target boundary."""
    targets = _normalize_patrol_targets(target_usernames)
    kind = str(download_media_type or "").strip().lower()
    page_limit = (
        downloads_results._positive_limit(page_limit)
        if kind in downloads_models._RESUMABLE_MEDIA_TYPES
        else None
    )
    processed = failed = unresolved = 0
    updates_found = downloaded = download_failed = 0

    def finish(status, message, *, blocking_failure=False):
        result = {
            "status": status,
            "message": message,
            "downloaded_count": downloaded,
        }
        if blocking_failure:
            result["blocking_failure"] = True
        return result

    if not targets:
        return finish(
            downloads_models.status_code.FAILED, "No patrol targets are configured"
        )
    if target_indexes is None:
        normalized_target_indexes = list(range(len(targets)))
    else:
        try:
            normalized_target_indexes = list(target_indexes)
        except TypeError:
            return finish(
                downloads_models.status_code.FAILED,
                "Patrol target indexes must be an iterable",
            )
        if len(normalized_target_indexes) != len(targets):
            return finish(
                downloads_models.status_code.FAILED,
                "Patrol target indexes must match the target count",
            )
        if any(
            type(value) is not int or value < 0 for value in normalized_target_indexes
        ):
            return finish(
                downloads_models.status_code.FAILED,
                "Patrol target indexes must be non-negative integers",
            )
        if len(set(normalized_target_indexes)) != len(normalized_target_indexes):
            return finish(
                downloads_models.status_code.FAILED,
                "Patrol target indexes must be unique",
            )
    if kind not in downloads_models._PATROL_MEDIA_TYPES:
        return finish(
            downloads_models.status_code.FAILED,
            f"Unsupported patrol media type: {kind or '(empty)'}",
        )

    blocking_statuses = {
        downloads_models.status_code.AUTHORIZATION_REQUIRED,
        downloads_models.status_code.RATE_LIMITED,
        downloads_models.status_code.IPHONE_QUALITY_UNAVAILABLE,
    }

    def emit_target_event(event, **payload):
        if target_event_callback is None:
            return
        event_payload = {
            "event": event,
            "media_type": kind,
        }
        event_payload.update(payload)
        try:
            target_event_callback(dict(event_payload))
        except Exception as e:
            diagnostics_console.print(f"Patrol target event callback failed: {e}")

    try:
        for position, (username, target_index) in enumerate(
            zip(targets, normalized_target_indexes),
            start=1,
        ):
            downloads_control.wait_if_paused()
            downloads_control._raise_if_stop_requested()
            emit_target_event(
                "target_started",
                target_username=username,
                target_index=target_index,
            )
            diagnostics_console.print(
                f"Patrol target {position}/{len(targets)}: @{username} ({kind.capitalize()})"
            )
            child_task_id = (
                downloads_checkpoints._patrol_resume_task_id(
                    patrol_task_id, target_index
                )
                if kind in downloads_models._RESUMABLE_MEDIA_TYPES
                else None
            )
            child_kwargs = {}
            if child_task_id:
                child_kwargs["resume_task_id"] = child_task_id
            item_result = patrol_user_content(
                username,
                kind,
                page_limit,
                **child_kwargs,
            )
            item_result = dict(item_result or {})
            processed += 1

            item_unresolved = _count_value(item_result.get("unresolved_count"))
            item_download_failed = _count_value(
                item_result.get("download_failed_count")
            )
            updates_found += _count_value(item_result.get("updates_found_count"))
            unresolved += item_unresolved
            downloaded += _count_value(item_result.get("downloaded_count"))
            download_failed += item_download_failed

            item_status = item_result.get("status", downloads_models.status_code.FAILED)
            item_incomplete = bool(item_unresolved or item_download_failed)
            target_failed = item_status not in (
                downloads_models.status_code.SUCCESS,
                downloads_models.status_code.STOPPED,
            ) or (
                item_status == downloads_models.status_code.SUCCESS and item_incomplete
            )
            if target_failed:
                failed += 1
                failure_message = item_result.get("message", item_status)
                if (
                    item_status == downloads_models.status_code.SUCCESS
                    and item_incomplete
                ):
                    failure_message = (
                        "incomplete result "
                        f"(download failures {item_download_failed}, "
                        f"unresolved {item_unresolved})"
                    )
                diagnostics_console.print(
                    f"Patrol could not check @{username}; continuing: {failure_message}"
                )
            emit_target_event(
                "target_finished",
                target_username=username,
                target_index=target_index,
                result=dict(item_result),
            )

            if item_status == downloads_models.status_code.STOPPED:
                return finish(
                    downloads_models.status_code.STOPPED,
                    f"Patrol stopped after {processed} target(s)",
                )
            if item_result.get("blocking_failure"):
                return finish(
                    item_status,
                    item_result.get("message", item_status),
                    blocking_failure=True,
                )
            if item_status in blocking_statuses:
                return finish(item_status, item_result.get("message", item_status))

            if position < len(targets):
                downloads_control.delay("user_switch")
    except downloads_models.DownloadStopRequested:
        return finish(
            downloads_models.status_code.STOPPED,
            f"Patrol stopped after {processed} target(s)",
        )

    message = (
        f"Patrol completed: checked {processed}/{len(targets)} target(s), "
        f"found {updates_found} update(s), downloaded {downloaded}, "
        f"download failures {download_failed}, unresolved {unresolved}, "
        f"failed targets {failed}"
    )
    final_status = (
        downloads_models.status_code.FAILED
        if failed or download_failed or unresolved
        else downloads_models.status_code.SUCCESS
    )
    return finish(final_status, message)
