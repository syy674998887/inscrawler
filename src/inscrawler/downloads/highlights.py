"""Serial Highlight collection enumeration and complete media scopes."""

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
import inscrawler.instagram.profile as instagram_profile
import inscrawler.instagram.session as instagram_session


def download_highlights(
    target_username,
    limit,
    *,
    patrol_mode=False,
    scope_unit="item",
    media_request_cap=None,
):
    patrol_mode = bool(patrol_mode)
    full_scope = not patrol_mode and scope_unit == "full"
    complete_scope = patrol_mode or full_scope
    budget = downloads_models._MediaRequestBudget(
        media_request_cap if full_scope else None
    )
    downloaded = found = checked = already_downloaded = updates_found = 0
    download_failed = full_item_count = 0
    L = instagram_session.get_loader()
    if L is None:
        result = downloads_errors._err_for_unavailable_client(
            target_username, "highlights"
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

        base_folder = downloads_files.build_save_path(target_username, "highlights")
        os.makedirs(base_folder, exist_ok=True)

        try:
            with downloads_runtime.runtime.api_lock:
                downloads_events.emit_content_phase_status("highlights", "fetch")
                downloads_control.delay("request")
                diagnostics_risk._risk_api("highlights_web")
                highlights = list(L.get_highlights(profile))
        except downloads_models.DownloadStopRequested:
            raise
        except Exception as e:
            s = downloads_errors.handle_error(e)
            return downloads_results._content_result(
                s,
                f"Error fetching highlights for {target_username}: {e}",
                patrol_mode=patrol_mode,
                scope_completed_count=0 if full_scope else None,
                media_request_count=budget.used if full_scope else None,
            )

        if not highlights:
            if full_scope:
                downloads_events._emit_scope_completed(
                    {
                        "scope_unit": "full",
                        "download_category": "highlights",
                        "item_count": 0,
                        "scope_completed_count": 1,
                    }
                )
            message = (
                downloads_results._patrol_content_message(
                    target_username, "highlights", 0, 0, 0, 0
                )
                if patrol_mode
                else f"No highlights found for {target_username}"
            )
            return downloads_results._content_result(
                downloads_models.status_code.SUCCESS,
                message,
                patrol_mode=patrol_mode,
                scope_completed_count=1 if full_scope else None,
                media_request_count=budget.used if full_scope else None,
            )

        fetch_error = None
        for highlight_index, h in enumerate(highlights):
            downloads_control.wait_if_paused()
            if not complete_scope and found >= limit:
                break
            title = (h.title or f"highlight_{h.unique_id}").strip()
            folder_name = downloads_files.clean_filename(title)
            # Windows limits a single directory name to 255 UTF-16 code units.
            if (
                not folder_name
                or len(folder_name.encode("utf-16-le")) > 510
                or (os.name == "nt" and os.path.isreserved(folder_name))
            ):
                folder_name = f"highlight_{h.unique_id}"
            folder = os.path.join(base_folder, folder_name)
            os.makedirs(folder, exist_ok=True)

            diagnostics_console.print(f"Highlight: {h.title} ({h.unique_id})")
            try:
                with downloads_runtime.runtime.api_lock:
                    downloads_events.emit_api_status("fetching_highlight_items")
                    downloads_control.delay("request")
                    try:
                        diagnostics_risk._risk_api("highlight_items")
                        items = list(h.get_items())
                    except downloads_models.DownloadStopRequested:
                        raise
                    except Exception as e:
                        # In 4.16, each fresh Highlight caches Web _items before
                        # fetching its iPhone reel. We consume it once here;
                        # failures before that boundary are still Web errors.
                        if (
                            downloads_runtime.runtime.iphone_support_enabled
                            and getattr(h, "_items", None) is not None
                        ):
                            raise downloads_models.IPhoneQualityUnavailable(
                                f"iPhone quality unavailable while fetching highlight '{h.title}': {e}"
                            ) from e
                        raise
            except downloads_models.DownloadStopRequested:
                raise
            except Exception as e:
                # End this target on a collection failure. Its classification
                # determines whether the queue may continue with other targets.
                s = downloads_errors.handle_error(e)
                fetch_error = (s, f"Error fetching items for '{h.title}': {e}")
                diagnostics_console.print(
                    f"Highlights aborted by {type(e).__name__}: {e}"
                )
                break

            for item_index, item in enumerate(items):
                downloads_control.wait_if_paused()
                if not complete_scope and found >= limit:
                    break
                if full_scope:
                    full_item_count += 1
                if patrol_mode:
                    checked += 1
                downloads_events.emit_content_phase_status("highlights", "prepare")
                paths = downloads_files.generate_filenames_storyitem(item, folder)
                if not paths:
                    if full_scope:
                        download_failed += 1
                        fetch_error = (
                            downloads_models.status_code.FAILED,
                            f"Unable to derive a filename for an item in '{h.title}'",
                        )
                        break
                    continue
                if downloads_files.is_already_downloaded(paths):
                    if patrol_mode:
                        already_downloaded += 1
                    continue
                if full_scope and not budget.can_fit(len(paths)):
                    message = (
                        f"Download limit cannot fit the next highlight item "
                        f"for @{target_username} "
                        f"({len(paths)} file request(s) needed)"
                    )
                    diagnostics_console.print(message)
                    return downloads_results._content_result(
                        downloads_models.status_code.SUCCESS,
                        message,
                        downloaded,
                        download_failed_count=download_failed,
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
                            download_category="highlights",
                            request_budget=budget if full_scope else None,
                        ):
                            downloaded += 1
                            downloads_events._emit_item_completed(
                                downloads_events._item_completed_detail(
                                    item, "highlights"
                                )
                            )
                        else:
                            download_failed += 1
                            if full_scope:
                                fetch_error = (
                                    downloads_models.status_code.FAILED,
                                    f"Failed downloading an item for '{h.title}'",
                                )
                                break
                    except downloads_models.DownloadStopRequested:
                        raise
                    except Exception as e:
                        download_failed += 1
                        s = downloads_errors.handle_error(e)
                        fetch_error = (
                            s,
                            f"Error downloading item for '{h.title}': {e}",
                        )
                        diagnostics_console.print(
                            f"Highlights download aborted by {type(e).__name__}: {e}"
                        )
                        break
                    finally:
                        is_final_full_item = (
                            full_scope
                            and highlight_index == len(highlights) - 1
                            and item_index == len(items) - 1
                        )
                        if (
                            fetch_error is None
                            or fetch_error[0]
                            != downloads_models.status_code.RATE_LIMITED
                        ) and not is_final_full_item:
                            downloads_control.delay("request")
                found += 1
            if fetch_error is not None:
                break

        if fetch_error is not None:
            return downloads_results._content_result(
                fetch_error[0],
                f"{fetch_error[1]} (saved {downloaded} highlight items before abort)",
                downloaded,
                patrol_mode=patrol_mode,
                checked_count=checked,
                already_downloaded_count=already_downloaded,
                updates_found_count=updates_found,
                download_failed_count=download_failed,
                scope_completed_count=0 if full_scope else None,
                media_request_count=budget.used if full_scope else None,
            )

        if full_scope and download_failed == 0:
            downloads_events._emit_scope_completed(
                {
                    "scope_unit": "full",
                    "download_category": "highlights",
                    "item_count": full_item_count,
                    "scope_completed_count": 1,
                }
            )
        message = (
            downloads_results._patrol_content_message(
                target_username,
                "highlights",
                checked,
                already_downloaded,
                updates_found,
                downloaded,
                download_failed,
            )
            if patrol_mode
            else f"Downloaded {downloaded} new highlight items for @{target_username}"
        )
        if download_failed and not patrol_mode:
            message += f" ({download_failed} failed)"
        return downloads_results._content_result(
            downloads_models.status_code.FAILED
            if download_failed
            else downloads_models.status_code.SUCCESS,
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
