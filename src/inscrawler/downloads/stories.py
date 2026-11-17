"""Serial Story scopes, strict quality, and resume-free patrol checks."""

from __future__ import annotations

import os

import inscrawler.diagnostics.console as diagnostics_console
import inscrawler.diagnostics.risk as diagnostics_risk
import inscrawler.downloads.avatar as downloads_avatar
import inscrawler.downloads.control as downloads_control
import inscrawler.downloads.errors as downloads_errors
import inscrawler.downloads.events as downloads_events
import inscrawler.downloads.files as downloads_files
import inscrawler.downloads.models as downloads_models
import inscrawler.downloads.results as downloads_results
import inscrawler.downloads.runtime as downloads_runtime
import inscrawler.downloads.transfer as downloads_transfer
import inscrawler.instagram.hotfixes as instagram_hotfixes
import inscrawler.instagram.media as instagram_media
import inscrawler.instagram.profile as instagram_profile
import inscrawler.instagram.session as instagram_session


def download_stories(
    target_username,
    limit,
    *,
    patrol_mode=False,
    scope_unit="item",
    media_request_cap=None,
):
    patrol_mode = bool(patrol_mode)
    full_scope = not patrol_mode and scope_unit == "full"
    downloaded = found = checked = already_downloaded = updates_found = 0
    download_failed = 0
    full_item_count = 0
    expired_web_only_count = 0
    iphone_media_ids = None
    budget = downloads_models._MediaRequestBudget(
        media_request_cap if full_scope else None
    )
    L = instagram_session.get_loader()
    if L is None:
        result = downloads_errors._err_for_unavailable_client(
            target_username, "stories"
        )
        return downloads_results._content_result(
            result["status"],
            result["message"],
            patrol_mode=patrol_mode,
            scope_completed_count=0 if full_scope else None,
            media_request_count=budget.used if full_scope else None,
            blocking_failure=result.get("blocking_failure", False),
        )

    try:
        profile = instagram_profile.resolve_profile(L, target_username)
        if profile is None:
            return downloads_results._content_result(
                downloads_models.status_code.USER_NOT_FOUND,
                f"User @{target_username} does not exist",
                patrol_mode=patrol_mode,
                scope_completed_count=0 if full_scope else None,
                media_request_count=budget.used if full_scope else None,
            )

        accessible = instagram_profile._ensure_accessible(L, profile)
        if accessible:
            return downloads_results._content_result(
                accessible,
                f"Cannot access @{target_username} ({accessible})",
                patrol_mode=patrol_mode,
                scope_completed_count=0 if full_scope else None,
                media_request_count=budget.used if full_scope else None,
            )

        downloads_avatar.sync_profile_picture_best_effort(L, profile, target_username)

        save_folder = downloads_files.build_save_path(target_username, "stories")
        os.makedirs(save_folder, exist_ok=True)

        try:
            with downloads_runtime.runtime.api_lock:
                downloads_events.emit_content_phase_status("stories", "fetch")
                downloads_control.delay("request")
                diagnostics_risk._risk_api("story_web")
                stories = instagram_hotfixes.get_target_stories(L, profile)
                try:
                    if not stories:
                        items = iter(()) if patrol_mode else []
                    elif patrol_mode:
                        # Stories have one current reel rather than a pageable
                        # detection window.  Patrol consumes this iterator to
                        # exhaustion and applies no item/page depth.
                        items = iter(stories[0].get_items())
                    else:
                        # Preserve the ordinary serial path's existing eager
                        # fetch behavior. Story.get_items() triggers
                        # Instaloader's iPhone reel request on first iteration.
                        if downloads_runtime.runtime.iphone_support_enabled:
                            diagnostics_risk._risk_api("story_iphone")
                        # Full scopes send that request first. Without an iPhone
                        # reel, only a Web reel whose items have all expired is
                        # skipped; anything else fails without another request.
                        iphone_reel_absent = (
                            full_scope
                            and downloads_runtime.runtime.iphone_support_enabled
                            and instagram_media._load_story_iphone_reel(stories[0])
                        )
                        items = list(stories[0].get_items())
                        if (
                            full_scope
                            and downloads_runtime.runtime.iphone_support_enabled
                        ):
                            iphone_media_ids = instagram_media._story_iphone_media_ids(
                                stories[0]
                            )
                        if iphone_reel_absent:
                            unexpired = (
                                instagram_media._story_items_not_confirmed_expired(
                                    items
                                )
                            )
                            if unexpired:
                                raise downloads_models.IPhoneQualityUnavailable(
                                    f"iPhone reels_media returned no Story reel; "
                                    f"{unexpired} of {len(items)} Web item(s) are "
                                    f"not confirmed expired"
                                )
                            with diagnostics_console._log_as("fetch"):
                                diagnostics_console.print(
                                    f"No current iPhone Story reel for "
                                    f"@{target_username}; all {len(items)} Web "
                                    f"item(s) have expired"
                                )
                except downloads_models.DownloadStopRequested:
                    raise
                except Exception as e:
                    if downloads_runtime.runtime.iphone_support_enabled:
                        raise downloads_models.IPhoneQualityUnavailable(
                            f"iPhone quality unavailable while fetching stories for {target_username}: {e}"
                        ) from e
                    raise
        except downloads_models.DownloadStopRequested:
            raise
        except Exception as e:
            s = downloads_errors.handle_error(e)
            return downloads_results._content_result(
                s,
                f"Error fetching stories for {target_username}: {e}",
                patrol_mode=patrol_mode,
                scope_completed_count=0 if full_scope else None,
                media_request_count=budget.used if full_scope else None,
            )

        if not stories:
            if full_scope:
                downloads_events._emit_scope_completed(
                    {
                        "scope_unit": "full",
                        "download_category": "stories",
                        "item_count": 0,
                        "scope_completed_count": 1,
                    }
                )
            message = (
                downloads_results._patrol_content_message(
                    target_username, "stories", 0, 0, 0, 0
                )
                if patrol_mode
                else f"No stories found for {target_username}"
            )
            return downloads_results._content_result(
                downloads_models.status_code.SUCCESS,
                message,
                patrol_mode=patrol_mode,
                scope_completed_count=1 if full_scope else None,
                media_request_count=budget.used if full_scope else None,
            )

        download_error = None
        item_index = 0
        while patrol_mode or full_scope or found < limit:
            if not patrol_mode and item_index >= len(items):
                break
            downloads_control.wait_if_paused()
            try:
                if patrol_mode:
                    with downloads_runtime.runtime.api_lock:
                        item = next(items)
                else:
                    item = items[item_index]
                    item_index += 1
            except StopIteration:
                break
            except downloads_models.DownloadStopRequested:
                raise
            except Exception as e:
                if downloads_runtime.runtime.iphone_support_enabled:
                    e = downloads_models.IPhoneQualityUnavailable(
                        f"iPhone quality unavailable while fetching stories for {target_username}: {e}"
                    )
                s = downloads_errors.handle_error(e)
                download_error = (
                    s,
                    f"Error fetching stories for {target_username}: {e}",
                )
                diagnostics_console.print(
                    f"Stories fetch aborted by {type(e).__name__}: {e}"
                )
                break
            if full_scope and downloads_runtime.runtime.iphone_support_enabled:
                web_only = instagram_media._storyitem_web_only_info(
                    item, iphone_media_ids
                )
                if web_only is not None:
                    desc = instagram_media._media_desc(item, "story item")
                    source_counts = (
                        f"Web items={len(items)}, iPhone items={len(iphone_media_ids)}"
                    )
                    expires_at = instagram_media._utc_log_timestamp(
                        web_only["expires_at"]
                    )
                    observed_at = instagram_media._utc_log_timestamp(
                        web_only["observed_at"]
                    )
                    if web_only["is_expired"]:
                        expired_web_only_count += 1
                        with diagnostics_console._log_as("fetch"):
                            diagnostics_console.print(
                                f"Skipping expired Web-only story {desc} for "
                                f"@{target_username}: media_id={web_only['media_id']}; "
                                f"expired_at={expires_at}; observed_at={observed_at}; "
                                f"no matching iPhone media ({source_counts})"
                            )
                        continue
                    with diagnostics_console._log_as("fetch"):
                        diagnostics_console.print(
                            f"Story Web/iPhone mismatch for {desc} at "
                            f"@{target_username}: media_id={web_only['media_id']}; "
                            f"expired_at={expires_at}; observed_at={observed_at}; "
                            f"item is not confirmed expired, so strict iPhone "
                            f"quality handling remains enabled ({source_counts})"
                        )
            if full_scope:
                full_item_count += 1
            if patrol_mode:
                checked += 1
            downloads_events.emit_content_phase_status("stories", "prepare")
            paths = downloads_files.generate_filenames_storyitem(item, save_folder)
            if not paths:
                if full_scope:
                    download_failed += 1
                    download_error = (
                        downloads_models.status_code.FAILED,
                        f"Unable to derive a filename for a story item from "
                        f"@{target_username}",
                    )
                    break
                continue
            if downloads_files.is_already_downloaded(paths):
                if patrol_mode:
                    already_downloaded += 1
                continue
            if full_scope and not budget.can_fit(len(paths)):
                message = (
                    f"Download limit cannot fit the next story item for "
                    f"@{target_username} ({len(paths)} file request(s) needed)"
                )
                diagnostics_console.print(message)
                return downloads_results._content_result(
                    downloads_models.status_code.SUCCESS,
                    message,
                    downloaded,
                    scope_completed_count=0,
                    media_request_count=budget.used,
                    completion_reason="media_request_cap",
                )
            if patrol_mode:
                updates_found += 1
            downloads_control.wait_if_paused()
            with (
                downloads_runtime.runtime.api_lock,
                diagnostics_console._log_as("download"),
            ):
                try:
                    if downloads_transfer.download_storyitem_to_paths(
                        L,
                        item,
                        paths,
                        download_category="stories",
                        request_budget=budget if full_scope else None,
                    ):
                        downloaded += 1
                        downloads_events._emit_item_completed(
                            downloads_events._item_completed_detail(item, "stories")
                        )
                    else:
                        download_failed += 1
                        if full_scope:
                            download_error = (
                                downloads_models.status_code.FAILED,
                                f"Failed downloading a story item for @{target_username}",
                            )
                except downloads_models.DownloadStopRequested:
                    raise
                except Exception as e:
                    download_failed += 1
                    s = downloads_errors.handle_error(e)
                    download_error = (
                        s,
                        f"Error downloading stories for {target_username}: {e}",
                    )
                    diagnostics_console.print(
                        f"Stories download aborted by {type(e).__name__}: {e}"
                    )
                    break
                finally:
                    if (
                        download_error is None
                        or download_error[0]
                        != downloads_models.status_code.RATE_LIMITED
                    ) and (not full_scope or item_index < len(items)):
                        downloads_control.delay("request")
            found += 1
            if download_error is not None:
                break

        if expired_web_only_count:
            with diagnostics_console._log_as("fetch"):
                diagnostics_console.print(
                    f"Skipped {expired_web_only_count} expired Web-only "
                    f"story item(s) for @{target_username}; these items were "
                    f"not counted as failures"
                )
        if download_error is not None:
            return downloads_results._content_result(
                download_error[0],
                download_error[1],
                downloaded,
                patrol_mode=patrol_mode,
                checked_count=checked,
                already_downloaded_count=already_downloaded,
                updates_found_count=updates_found,
                download_failed_count=download_failed,
                scope_completed_count=0 if full_scope else None,
                media_request_count=budget.used if full_scope else None,
            )

        message = (
            downloads_results._patrol_content_message(
                target_username,
                "stories",
                checked,
                already_downloaded,
                updates_found,
                downloaded,
                download_failed,
            )
            if patrol_mode
            else (
                f"Downloaded {downloaded} new stories for @{target_username} "
                f"({download_failed} failed)"
                if download_failed
                else f"Downloaded {downloaded} new stories for @{target_username}"
            )
        )
        result_status = (
            downloads_models.status_code.FAILED
            if download_failed > 0
            else downloads_models.status_code.SUCCESS
        )
        if full_scope and download_failed == 0:
            downloads_events._emit_scope_completed(
                {
                    "scope_unit": "full",
                    "download_category": "stories",
                    "item_count": full_item_count,
                    "scope_completed_count": 1,
                }
            )
        return downloads_results._content_result(
            result_status,
            message,
            downloaded,
            patrol_mode=patrol_mode,
            checked_count=checked,
            already_downloaded_count=already_downloaded,
            updates_found_count=updates_found,
            download_failed_count=download_failed,
            scope_completed_count=(
                1
                if full_scope and download_failed == 0
                else (0 if full_scope else None)
            ),
            media_request_count=budget.used if full_scope else None,
        )
    except downloads_models.DownloadStopRequested as e:
        if patrol_mode:
            return downloads_results._content_result(
                downloads_models.status_code.STOPPED,
                f"Patrol stopped while checking @{target_username}",
                downloaded,
                patrol_mode=True,
                checked_count=checked,
                already_downloaded_count=already_downloaded,
                updates_found_count=updates_found,
                download_failed_count=download_failed,
            )
        e.downloaded_count = downloaded
        if full_scope:
            e.scope_completed_count = 0
            e.media_request_count = budget.used
        raise
    finally:
        downloads_events.emit_download_status("idle")
