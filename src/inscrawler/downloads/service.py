"""Configure the shared runtime and dispatch ordinary media tasks."""

from __future__ import annotations

import os

import inscrawler.diagnostics.console as diagnostics_console
import inscrawler.diagnostics.risk as diagnostics_risk
import inscrawler.downloads.control as downloads_control
import inscrawler.downloads.errors as downloads_errors
import inscrawler.downloads.files as downloads_files
import inscrawler.downloads.highlights as downloads_highlights
import inscrawler.downloads.models as downloads_models
import inscrawler.downloads.posts as downloads_posts
import inscrawler.downloads.runtime as downloads_runtime
import inscrawler.downloads.stories as downloads_stories
import inscrawler.instagram.session as instagram_session
import inscrawler.notifications.service as notifications_service
import inscrawler.paths as app_paths


def config_settings(
    username_input=None,
    session_cookie_input=None,
    notification_input=None,
    webhook_input=None,
    telegram_bot_token_input=None,
    telegram_chat_id_input=None,
    system_notification_callback_input=None,
    image_download_callback_input=None,
    profile_picture_callback_input=None,
    file_download_callback_input=None,
    item_completed_callback_input=None,
    scope_completed_callback_input=None,
    api_status_callback_input=None,
    download_status_callback_input=None,
    download_base_path_input=None,
    log_callback_input=None,
    iphone_support_input=None,
    profile_picture_sync_input=None,
    file_activity_callback_input=None,
):

    if username_input is not None:
        downloads_runtime.runtime.login_username = str(username_input).strip() or None
    if session_cookie_input is not None:
        downloads_runtime.runtime.session_cookie_text = str(session_cookie_input)
    if notification_input is not None:
        downloads_runtime.runtime.notification_type = (
            str(notification_input).strip().lower()
        )
    if webhook_input is not None:
        downloads_runtime.runtime.webhook = webhook_input
    if telegram_bot_token_input is not None:
        downloads_runtime.runtime.telegram_bot_token = telegram_bot_token_input
    if telegram_chat_id_input is not None:
        downloads_runtime.runtime.telegram_chat_id = telegram_chat_id_input
    if system_notification_callback_input is not None:
        downloads_runtime.runtime.system_notification_callback = (
            system_notification_callback_input
        )
    if image_download_callback_input is not None:
        downloads_runtime.runtime.image_download_callback = (
            image_download_callback_input
        )
    if profile_picture_callback_input is not None:
        downloads_runtime.runtime.profile_picture_callback = (
            profile_picture_callback_input
        )
    if file_download_callback_input is not None:
        downloads_runtime.runtime.file_download_callback = file_download_callback_input
    if file_activity_callback_input is not None:
        downloads_runtime.runtime.file_activity_callback = file_activity_callback_input
    if item_completed_callback_input is not None:
        downloads_runtime.runtime.item_completed_callback = (
            item_completed_callback_input
        )
    if scope_completed_callback_input is not None:
        downloads_runtime.runtime.scope_completed_callback = (
            scope_completed_callback_input
        )
    if api_status_callback_input is not None:
        downloads_runtime.runtime.api_status_callback = api_status_callback_input
    if download_status_callback_input is not None:
        downloads_runtime.runtime.download_status_callback = (
            download_status_callback_input
        )
    if download_base_path_input is not None:
        downloads_files.set_download_base_dir(download_base_path_input)
    if log_callback_input is not None:
        diagnostics_console.log_callback = log_callback_input
    if iphone_support_input is not None:
        downloads_runtime.runtime.iphone_support_enabled = bool(iphone_support_input)
    if profile_picture_sync_input is not None:
        downloads_runtime.runtime.profile_picture_sync_enabled = bool(
            profile_picture_sync_input
        )

    # 通知 LoaderManager 配置可能已变更；只有相关字段实际不同时
    # 才会在下次 get_loader() 触发重新初始化。
    instagram_session._loader_manager.invalidate()

    return {
        "has_username": bool(downloads_runtime.runtime.login_username),
        "has_session": bool(downloads_runtime.runtime.login_username)
        and os.path.exists(
            os.path.join(
                app_paths.SESSION_DIR, downloads_runtime.runtime.login_username.lower()
            )
        ),
        "has_webhook": bool(downloads_runtime.runtime.webhook),
        "has_telegram": bool(
            downloads_runtime.runtime.telegram_bot_token
            and downloads_runtime.runtime.telegram_chat_id
        ),
    }


def _download_user_content_impl(
    target_username,
    download_media_type,
    limit,
    task_id_override=None,
    scope_unit=None,
    media_request_cap=None,
):

    if task_id_override is not None:
        downloads_runtime.runtime.active_task_id = str(task_id_override).strip() or None

    if not target_username:
        diagnostics_console.print("Target username is required")
        downloads_runtime.runtime.active_task_id = None
        return {
            "status": downloads_models.status_code.FAILED,
            "message": "Target username is required",
        }

    if limit is None or limit <= 0:
        limit = 1

    try:
        downloads_control.wait_if_paused()

        normalized_scope = str(scope_unit or "").strip().lower()
        post_like = download_media_type in ("posts", "reels", "tagged")
        expected_scope = "page" if post_like else "full"
        if normalized_scope and normalized_scope != expected_scope:
            return {
                "status": downloads_models.status_code.FAILED,
                "message": (
                    f"{download_media_type} requires scope_unit='{expected_scope}'"
                ),
            }
        normalized_scope = expected_scope

        if download_media_type in ("posts", "reels", "tagged"):
            result = downloads_posts.download_post_pages(
                target_username,
                download_media_type,
                limit,
                media_request_cap=media_request_cap,
            )
        elif download_media_type == "stories":
            result = downloads_stories.download_stories(
                target_username,
                limit,
                scope_unit=normalized_scope,
                media_request_cap=media_request_cap,
            )
        elif download_media_type == "highlights":
            result = downloads_highlights.download_highlights(
                target_username,
                limit,
                scope_unit=normalized_scope,
                media_request_cap=media_request_cap,
            )
        else:
            diagnostics_console.print(f"Unknown download type: {download_media_type}")
            return {
                "status": downloads_models.status_code.FAILED,
                "message": f"Unknown download type: {download_media_type}",
            }

        if result is None:
            return {
                "status": downloads_models.status_code.FAILED,
                "message": f"Download function returned None for {target_username}",
            }
        if not isinstance(result, dict):
            return {
                "status": downloads_models.status_code.FAILED,
                "message": f"Download function returned unexpected type: {type(result)}",
            }
        if result.get("status") != downloads_models.status_code.SUCCESS:
            return result

        downloaded_count = result.get("downloaded_count", 0)
        diagnostics_console.print(
            f"Download completed for @{target_username} ({download_media_type.capitalize()})"
        )
        notifications_service.notify(
            f"@{target_username} download completed ({download_media_type.capitalize()})"
        )
        final_result = dict(result)
        final_result.update(
            {
                "status": downloads_models.status_code.SUCCESS,
                "message": result.get(
                    "message", f"Download completed for @{target_username}"
                ),
                "downloaded_count": downloaded_count,
            }
        )
        return final_result

    except downloads_models.DownloadStopRequested as e:
        diagnostics_console.print(
            f"Download stopped for @{target_username} ({download_media_type.capitalize()})"
        )
        stopped_result = {
            "status": downloads_models.status_code.STOPPED,
            "message": f"Download stopped for @{target_username}",
            "downloaded_count": max(0, int(getattr(e, "downloaded_count", 0))),
        }
        for field in ("scope_completed_count", "media_request_count"):
            if hasattr(e, field):
                stopped_result[field] = max(0, int(getattr(e, field, 0)))
        return stopped_result
    except downloads_models.IPhoneQualityUnavailable as e:
        status = downloads_errors.handle_error(e)
        if status == downloads_models.status_code.RATE_LIMITED:
            diagnostics_console.print(
                f"Instagram API rate limited while downloading @{target_username}: {e}"
            )
            return {
                "status": status,
                "message": f"Instagram temporarily limited API access: {e}",
                "downloaded_count": 0,
            }
        diagnostics_console.print(
            f"iPhone quality unavailable for @{target_username}: {e}"
        )
        return {
            "status": downloads_models.status_code.IPHONE_QUALITY_UNAVAILABLE,
            "message": f"iPhone quality unavailable: {e}",
            "downloaded_count": 0,
        }
    except Exception as e:
        diagnostics_console.print(f"Download failed for @{target_username}: {e}")
        return {
            "status": downloads_errors.handle_error(e),
            "message": f"Download failed: {e}",
        }
    finally:
        downloads_runtime.runtime.active_task_id = None


def download_user_content(
    target_username,
    download_media_type,
    limit,
    task_id_override=None,
    scope_unit=None,
    media_request_cap=None,
    risk_run_id=None,
    queue_completed_before=None,
):
    """Run one download and append one compact RC diagnostic in serial mode."""

    try:
        diagnostics_risk._begin_risk_attempt(
            download_media_type,
            run_id=risk_run_id,
            queue_completed_before=queue_completed_before,
        )
    except Exception:
        # Observability must never block the underlying download.
        pass
    result = None
    error = None
    try:
        result = _download_user_content_impl(
            target_username,
            download_media_type,
            limit,
            task_id_override=task_id_override,
            scope_unit=scope_unit,
            media_request_cap=media_request_cap,
        )
        return result
    except BaseException as exc:
        error = exc
        raise
    finally:
        diagnostics_risk._finish_risk_attempt(result, error)
