"""Serial post-like media scopes, real pages, and patrol continuation."""

from __future__ import annotations

import os

from instaloader import (
    Post,
)
from instaloader.exceptions import (
    BadResponseException,
    InvalidArgumentException,
    PostChangedException,
)

import inscrawler.diagnostics.console as diagnostics_console
import inscrawler.diagnostics.risk as diagnostics_risk
import inscrawler.downloads.avatar as downloads_avatar
import inscrawler.downloads.checkpoints as downloads_checkpoints
import inscrawler.downloads.control as downloads_control
import inscrawler.downloads.errors as downloads_errors
import inscrawler.downloads.events as downloads_events
import inscrawler.downloads.files as downloads_files
import inscrawler.downloads.models as downloads_models
import inscrawler.downloads.results as downloads_results
import inscrawler.downloads.runtime as downloads_runtime
import inscrawler.downloads.transfer as downloads_transfer
import inscrawler.instagram.profile as instagram_profile
import inscrawler.instagram.reels as instagram_reels
import inscrawler.instagram.session as instagram_session


def download_post_pages(target_username, kind, page_limit, media_request_cap=None):
    """Download ordinary post-like content in complete remote response pages."""
    page_limit = downloads_results._positive_limit(page_limit)
    budget = downloads_models._MediaRequestBudget(media_request_cap)
    downloaded = completed_pages = 0
    iterator = None
    replay_current = False
    L = instagram_session.get_loader()
    if L is None:
        result = downloads_errors._err_for_unavailable_client(target_username, kind)
        return downloads_results._post_page_result(
            result["status"],
            result["message"],
            0,
            0,
            budget,
            blocking_failure=result.get("blocking_failure", False),
        )

    def save_cursor(replay):
        try:
            instagram_reels._node_page_state(iterator)
        except InvalidArgumentException as e:
            diagnostics_console.print(f"Not saving invalid {kind} page cursor: {e}")
            return False
        return downloads_checkpoints._save_resume_checkpoint(
            iterator,
            kind,
            replay_current=bool(replay),
        )

    def open_fresh_iterator():
        downloads_events.emit_content_phase_status(kind, "fetch")
        downloads_control.delay("request")
        diagnostics_risk._risk_api(f"{kind}_web")
        return instagram_reels._ordinary_post_iterator(profile, kind)

    def cap_completed(message):
        diagnostics_console.print(message)
        downloads_checkpoints._delete_resume_checkpoint(kind, log=True)
        return downloads_results._post_page_result(
            downloads_models.status_code.SUCCESS,
            message,
            downloaded,
            completed_pages,
            budget,
            completion_reason="media_request_cap",
        )

    try:
        profile = instagram_profile.resolve_profile(L, target_username)
        if profile is None:
            return downloads_results._post_page_result(
                downloads_models.status_code.USER_NOT_FOUND,
                f"User @{target_username} does not exist",
                downloaded,
                completed_pages,
                budget,
            )
        accessible = instagram_profile._ensure_accessible(L, profile)
        if accessible:
            return downloads_results._post_page_result(
                accessible,
                f"Cannot access @{target_username} ({accessible})",
                downloaded,
                completed_pages,
                budget,
            )

        downloads_avatar.sync_profile_picture_best_effort(L, profile, target_username)
        save_folder = downloads_files.build_save_path(target_username, kind)
        os.makedirs(save_folder, exist_ok=True)

        try:
            frozen_checkpoint = downloads_checkpoints._read_resume_checkpoint(
                L.context, kind
            )
            with downloads_runtime.runtime.api_lock:
                if frozen_checkpoint is not None:
                    iterator = instagram_reels._ordinary_post_iterator(
                        profile,
                        kind,
                        defer_initial_query=True,
                    )
                    resumed = downloads_checkpoints._thaw_resume_checkpoint(
                        iterator,
                        frozen_checkpoint,
                        kind,
                    )
                    if resumed:
                        try:
                            instagram_reels._node_page_state(iterator)
                        except InvalidArgumentException as e:
                            diagnostics_console.print(
                                f"Discarding invalid saved {kind} cursor: {e}"
                            )
                            downloads_checkpoints._delete_resume_checkpoint(kind)
                            resumed = False
                    if not resumed:
                        # A mismatching cursor was deleted by the thaw helper;
                        # now obtain a genuine fresh first page.
                        iterator = open_fresh_iterator()
                else:
                    iterator = open_fresh_iterator()
                instagram_reels._node_page_state(iterator)
        except downloads_models.DownloadStopRequested:
            raise
        except Exception as e:
            s = downloads_errors.handle_error(e)
            return downloads_results._post_page_result(
                s,
                f"Error opening {kind} page source for {target_username}: {e}",
                downloaded,
                completed_pages,
                budget,
            )

        while completed_pages < page_limit:
            downloads_control.wait_if_paused()
            if budget.exhausted:
                if instagram_reels._node_page_has_unconsumed_item(
                    iterator
                ) or instagram_reels._node_page_has_next_response(iterator):
                    return cap_completed(
                        f"Download limit reached for @{target_username} ({kind})",
                    )
                break

            previous_total_index = None
            page_data_before_next = getattr(iterator, "_data", None)
            try:
                previous_total_index = iterator.total_index
                with downloads_runtime.runtime.api_lock:
                    _data, edges, page_index, page_info = (
                        instagram_reels._node_page_state(iterator)
                    )
                    if page_index >= len(edges) and page_info.get("has_next_page"):
                        downloads_events.emit_content_phase_status(kind, "fetch")
                        downloads_control.delay("request")
                        diagnostics_risk._risk_api(f"{kind}_web")
                    first_item = instagram_reels._next_real_page_first_item(iterator)
            except downloads_models.DownloadStopRequested:
                raise
            except PostChangedException as e:
                replay_current = True
                if getattr(iterator, "_data", None) is page_data_before_next:
                    save_cursor(True)
                return downloads_results._post_page_result(
                    downloads_models.status_code.FAILED,
                    f"Post changed while opening a {kind} page for {target_username}: {e}",
                    downloaded,
                    completed_pages,
                    budget,
                )
            except Exception as e:
                s = downloads_errors.handle_error(e)
                if getattr(iterator, "_data", None) is page_data_before_next:
                    save_cursor(
                        instagram_reels._node_advanced_during_next(
                            iterator, previous_total_index
                        )
                    )
                return downloads_results._post_page_result(
                    s,
                    f"Error fetching {kind} page for {target_username}: {e}",
                    downloaded,
                    completed_pages,
                    budget,
                )

            if first_item is instagram_reels._NO_PAGE_ITEM:
                break
            page_data = getattr(iterator, "_data", None)
            page_item_count = 0
            pending_item = first_item
            # The first edge has already advanced NodeIterator.  Until it is
            # classified, a stop must replay it from the checkpoint.
            replay_current = True

            while True:
                if pending_item is None:
                    if getattr(iterator, "_data", None) is not page_data:
                        raise InvalidArgumentException(
                            "NodeIterator crossed a page boundary unexpectedly."
                        )
                    if not instagram_reels._node_page_has_unconsumed_item(iterator):
                        break
                downloads_control.wait_if_paused()
                if pending_item is None:
                    previous_total_index = None
                    try:
                        previous_total_index = iterator.total_index
                        with downloads_runtime.runtime.api_lock:
                            pending_item = next(iterator)
                    except PostChangedException as e:
                        replay_current = True
                        save_cursor(True)
                        return downloads_results._post_page_result(
                            downloads_models.status_code.FAILED,
                            f"Post changed while consuming {kind} page for {target_username}: {e}",
                            downloaded,
                            completed_pages,
                            budget,
                        )
                    except Exception as e:
                        s = downloads_errors.handle_error(e)
                        save_cursor(
                            instagram_reels._node_advanced_during_next(
                                iterator,
                                previous_total_index,
                            )
                        )
                        return downloads_results._post_page_result(
                            s,
                            f"Error consuming {kind} page for {target_username}: {e}",
                            downloaded,
                            completed_pages,
                            budget,
                        )

                candidate = pending_item
                pending_item = None
                replay_current = True
                page_item_count += 1

                try:
                    downloads_events.emit_content_phase_status(kind, "prepare")
                    if kind == "reels":
                        predicted_path = instagram_reels._raw_reel_existing_path(
                            candidate, save_folder
                        )
                        if (
                            predicted_path
                            and downloads_files._is_reel_already_downloaded(
                                target_username,
                                [predicted_path],
                            )
                        ):
                            replay_current = False
                            continue
                        if budget.exhausted:
                            return cap_completed(
                                f"Download limit reached before Reel detail for "
                                f"@{target_username}",
                            )
                        shortcode = instagram_reels._raw_reel_shortcode(candidate)
                        if shortcode is None:
                            raise BadResponseException(
                                "Reels page item has no shortcode."
                            )
                        summary_reason = (
                            "local file missing"
                            if predicted_path
                            else "summary cannot safely derive canonical path"
                        )
                        diagnostics_console.print(
                            f"Reel {shortcode}: {summary_reason}; fetching one full detail response"
                        )
                        with downloads_runtime.runtime.api_lock:
                            downloads_events.emit_content_phase_status(kind, "fetch")
                            downloads_control.delay("request")
                            diagnostics_risk._risk_api("post_detail")
                            post = Post.from_shortcode(L.context, shortcode)
                        downloads_events.emit_content_phase_status(kind, "prepare")
                    else:
                        post = candidate

                    paths = downloads_files.generate_filenames_post(post, save_folder)
                    if not paths:
                        raise BadResponseException(
                            f"Unable to derive local filenames for {kind} item."
                        )
                    already_downloaded = (
                        downloads_files._is_reel_already_downloaded(
                            target_username, paths
                        )
                        if kind == "reels"
                        else downloads_files.is_already_downloaded(paths)
                    )
                    if already_downloaded:
                        replay_current = False
                        continue
                    if not budget.can_fit(len(paths)):
                        return cap_completed(
                            f"Download limit cannot fit the next {kind} item "
                            f"for @{target_username} ({len(paths)} file request(s) needed)",
                        )

                    downloads_control.wait_if_paused()
                    with downloads_runtime.runtime.api_lock:
                        if (
                            kind == "reels"
                            and downloads_runtime.runtime.iphone_support_enabled
                        ):
                            downloads_control.delay("request")
                        with diagnostics_console._log_as("download"):
                            ok = downloads_transfer.download_post_to_paths(
                                L,
                                post,
                                paths,
                                download_category=kind,
                                request_budget=budget,
                            )
                            if ok:
                                downloaded += 1
                                downloads_events._emit_item_completed(
                                    downloads_events._item_completed_detail(post, kind)
                                )
                                replay_current = False
                            if ok and instagram_reels._node_page_has_unconsumed_item(
                                iterator
                            ):
                                downloads_control.delay("request")
                    if not ok:
                        save_cursor(True)
                        return downloads_results._post_page_result(
                            downloads_models.status_code.FAILED,
                            f"Failed downloading a {kind} item for @{target_username}",
                            downloaded,
                            completed_pages,
                            budget,
                            download_failed_count=1,
                        )
                except downloads_models.DownloadStopRequested:
                    raise
                except Exception as e:
                    s = downloads_errors.handle_error(e)
                    save_cursor(True)
                    return downloads_results._post_page_result(
                        s,
                        f"Error processing {kind} item for {target_username}: {e}",
                        downloaded,
                        completed_pages,
                        budget,
                        download_failed_count=1,
                    )

            # Persist the safe boundary before GUI progress is allowed to hit
            # disk.  If the process dies after this save but before the
            # callback, a retry may cover one extra page; it cannot silently
            # under-cover the requested depth.
            if completed_pages + 1 < page_limit:
                checkpoint_available = bool(
                    downloads_checkpoints._resume_checkpoint_path(kind)
                )
                if checkpoint_available and not save_cursor(False):
                    return downloads_results._post_page_result(
                        downloads_models.status_code.FAILED,
                        f"Completed a {kind} page for @{target_username}, but "
                        "failed to save its page-boundary cursor",
                        downloaded,
                        completed_pages,
                        budget,
                    )

            completed_pages += 1
            downloads_events._emit_scope_completed(
                {
                    "scope_unit": "page",
                    "download_category": kind,
                    "item_count": page_item_count,
                    "scope_completed_count": completed_pages,
                }
            )
            replay_current = False

            if budget.exhausted and completed_pages < page_limit:
                if instagram_reels._node_page_has_next_response(iterator):
                    return cap_completed(
                        f"Download limit reached for @{target_username} ({kind})",
                    )
                break

        downloads_checkpoints._delete_resume_checkpoint(kind, log=True)
        return downloads_results._post_page_result(
            downloads_models.status_code.SUCCESS,
            f"Downloaded {downloaded} new {kind} for @{target_username} "
            f"across {completed_pages} complete page(s)",
            downloaded,
            completed_pages,
            budget,
        )
    except downloads_models.DownloadStopRequested as e:
        checkpoint_available = iterator is not None and bool(
            downloads_checkpoints._resume_checkpoint_path(kind)
        )
        if checkpoint_available and not save_cursor(replay_current):
            return downloads_results._post_page_result(
                downloads_models.status_code.FAILED,
                f"Download stopped for @{target_username} ({kind}), but failed "
                "to save its resumable cursor",
                downloaded,
                completed_pages,
                budget,
            )
        e.downloaded_count = downloaded
        e.scope_completed_count = completed_pages
        e.media_request_count = budget.used
        raise
    finally:
        downloads_events.emit_download_status("idle")


def download_posts(
    target_username, kind, *, patrol_page_limit, patrol_resume_task_id=None
):
    """Patrol scan of posts-like media; ordinary tasks use download_post_pages()."""
    page_limit = downloads_results._positive_limit(patrol_page_limit)
    promoted = False
    checked_pages = 0
    replay_current = False
    downloaded = checked = already_downloaded = updates_found = 0
    download_failed = 0
    L = instagram_session.get_loader()
    if L is None:
        result = downloads_errors._err_for_unavailable_client(target_username, kind)
        return downloads_results._content_result(
            result["status"],
            result["message"],
            patrol_mode=True,
            blocking_failure=result.get("blocking_failure", False),
        )

    def save_patrol_cursor(replay):
        try:
            instagram_reels._node_page_state(iterator)
        except InvalidArgumentException as e:
            diagnostics_console.print(f"Not saving invalid {kind} patrol cursor: {e}")
            return False
        return downloads_checkpoints._save_resume_checkpoint(
            iterator,
            kind,
            task_id=patrol_resume_task_id,
            replay_current=bool(replay),
        )

    iterator = None
    try:
        profile = instagram_profile.resolve_profile(L, target_username)
        if profile is None:
            return downloads_results._content_result(
                downloads_models.status_code.USER_NOT_FOUND,
                f"User @{target_username} does not exist",
                patrol_mode=True,
            )

        accessible = instagram_profile._ensure_accessible(L, profile)
        if accessible:
            return downloads_results._content_result(
                accessible,
                f"Cannot access @{target_username} ({accessible})",
                patrol_mode=True,
            )

        downloads_avatar.sync_profile_picture_best_effort(L, profile, target_username)

        save_folder = downloads_files.build_save_path(target_username, kind)
        os.makedirs(save_folder, exist_ok=True)

        # Patrol page detection uses the same strict NodeIterator page state as
        # ordinary page downloads.  A formal-download cursor is only loaded
        # for a previously promoted patrol child; detection itself is never
        # checkpointed.
        try:
            frozen_checkpoint = (
                downloads_checkpoints._read_resume_checkpoint(
                    L.context,
                    kind,
                    task_id=patrol_resume_task_id,
                )
                if patrol_resume_task_id
                else None
            )
            with downloads_runtime.runtime.api_lock:
                if frozen_checkpoint is not None:
                    iterator = instagram_reels._ordinary_post_iterator(
                        profile,
                        kind,
                        defer_initial_query=True,
                    )
                    promoted = downloads_checkpoints._thaw_resume_checkpoint(
                        iterator,
                        frozen_checkpoint,
                        kind,
                        task_id=patrol_resume_task_id,
                    )
                    if promoted:
                        try:
                            instagram_reels._node_page_state(iterator)
                        except InvalidArgumentException as e:
                            diagnostics_console.print(
                                f"Discarding invalid saved {kind} patrol cursor: {e}"
                            )
                            downloads_checkpoints._delete_resume_checkpoint(
                                kind,
                                task_id=patrol_resume_task_id,
                            )
                            promoted = False
                if not promoted:
                    downloads_events.emit_content_phase_status(kind, "fetch")
                    downloads_control.delay("request")
                    iterator = instagram_reels._ordinary_post_iterator(profile, kind)
                instagram_reels._node_page_state(iterator)
        except downloads_models.DownloadStopRequested:
            raise
        except Exception as e:
            s = downloads_errors.handle_error(e)
            return downloads_results._content_result(
                s,
                f"Error opening {kind} iterator for {target_username}: {e}",
                patrol_mode=True,
            )

        fetch_error = None  # (status, message); 设置后表示循环以失败方式终止
        while True:
            # The detection page limit counts complete response pages. As soon
            # as an update promotes the scan, the same iterator runs to
            # exhaustion and the page limit no longer applies.
            if not promoted and checked_pages >= page_limit:
                break

            downloads_control.wait_if_paused()
            previous_total_index = None
            try:
                with downloads_runtime.runtime.api_lock:
                    previous_total_index = iterator.total_index
                    _data, edges, page_index, page_info = (
                        instagram_reels._node_page_state(iterator)
                    )
                    if page_index >= len(edges) and page_info.get("has_next_page"):
                        downloads_events.emit_content_phase_status(kind, "fetch")
                        downloads_control.delay("request")
                    post = instagram_reels._next_real_page_first_item(iterator)
            except downloads_models.DownloadStopRequested:
                raise
            except PostChangedException as e:
                # NodeIterator advances before invoking its wrapper.  Count
                # the unclassifiable item and, when it was the last edge,
                # finish the detection page without crossing into the next.
                if instagram_reels._node_advanced_during_next(
                    iterator,
                    previous_total_index,
                ):
                    checked += 1
                    try:
                        _data, edges, page_index, _page_info = (
                            instagram_reels._node_page_state(iterator)
                        )
                    except Exception as state_error:
                        s = downloads_errors.handle_error(state_error)
                        fetch_error = (
                            s,
                            f"Error fetching {kind} for {target_username}: {state_error}",
                        )
                        diagnostics_console.print(
                            f"Fetch aborted by {type(state_error).__name__}: "
                            f"{state_error}"
                        )
                        break
                    if page_index >= len(edges):
                        checked_pages += 1
                diagnostics_console.print(
                    f"Post changed during iteration, skipping: {e}"
                )
                replay_current = False
                continue
            except Exception as e:
                if promoted and instagram_reels._node_advanced_during_next(
                    iterator, previous_total_index
                ):
                    replay_current = True
                s = downloads_errors.handle_error(e)
                fetch_error = (s, f"Error fetching {kind} for {target_username}: {e}")
                diagnostics_console.print(f"Fetch aborted by {type(e).__name__}: {e}")
                break

            if post is instagram_reels._NO_PAGE_ITEM:
                break

            # next() has advanced, but this item is not safe to skip until its
            # local state has been classified or its download finishes.
            replay_current = True

            checked += 1
            try:
                _data, edges, page_index, _page_info = instagram_reels._node_page_state(
                    iterator
                )
            except InvalidArgumentException as state_error:
                s = downloads_errors.handle_error(state_error)
                fetch_error = (
                    s,
                    f"Error fetching {kind} for {target_username}: {state_error}",
                )
                diagnostics_console.print(
                    f"Fetch aborted by {type(state_error).__name__}: {state_error}"
                )
                break
            if page_index >= len(edges):
                checked_pages += 1

            downloads_events.emit_content_phase_status(kind, "prepare")
            if kind == "reels":
                try:
                    predicted_path = instagram_reels._raw_reel_existing_path(
                        post, save_folder
                    )
                    if predicted_path and downloads_files._is_reel_already_downloaded(
                        target_username,
                        [predicted_path],
                    ):
                        already_downloaded += 1
                        replay_current = False
                        continue
                    shortcode = instagram_reels._raw_reel_shortcode(post)
                    if shortcode is None:
                        raise BadResponseException("Reels page item has no shortcode.")
                    with downloads_runtime.runtime.api_lock:
                        downloads_events.emit_content_phase_status(kind, "fetch")
                        downloads_control.delay("request")
                        post = Post.from_shortcode(L.context, shortcode)
                    downloads_events.emit_content_phase_status(kind, "prepare")
                except downloads_models.DownloadStopRequested:
                    raise
                except Exception as e:
                    s = downloads_errors.handle_error(e)
                    fetch_error = (
                        s,
                        f"Error preparing {kind} for {target_username}: {e}",
                    )
                    diagnostics_console.print(
                        f"Fetch aborted by {type(e).__name__}: {e}"
                    )
                    break

            paths = downloads_files.generate_filenames_post(post, save_folder)
            if not paths:
                replay_current = False
                continue
            already_on_disk = (
                downloads_files._is_reel_already_downloaded(target_username, paths)
                if kind == "reels"
                else downloads_files.is_already_downloaded(paths)
            )
            if already_on_disk:
                already_downloaded += 1
                replay_current = False
                continue

            first_promotion = not promoted
            updates_found += 1
            if (
                first_promotion
                and patrol_resume_task_id
                and not save_patrol_cursor(True)
            ):
                return downloads_results._content_result(
                    downloads_models.status_code.STOPPED,
                    f"Patrol stopped before downloading @{target_username}: "
                    f"failed to save {kind} resume cursor",
                    downloaded,
                    patrol_mode=True,
                    checked_count=checked,
                    already_downloaded_count=already_downloaded,
                    updates_found_count=updates_found,
                    download_failed_count=download_failed,
                )
            promoted = True

            downloads_control.wait_if_paused()
            with (
                downloads_runtime.runtime.api_lock,
                diagnostics_console._log_as("download"),
            ):
                if kind == "reels" and downloads_runtime.runtime.iphone_support_enabled:
                    downloads_control.delay("request")
                try:
                    if downloads_transfer.download_post_to_paths(
                        L, post, paths, download_category=kind
                    ):
                        downloaded += 1
                        downloads_events._emit_item_completed(
                            downloads_events._item_completed_detail(post, kind)
                        )
                        replay_current = False
                    else:
                        download_failed += 1
                        fetch_error = (
                            downloads_models.status_code.FAILED,
                            f"Failed downloading {kind} for {target_username}",
                        )
                        break
                except PostChangedException:
                    updates_found = max(0, updates_found - 1)
                    replay_current = False
                    continue
                except downloads_models.DownloadStopRequested:
                    raise
                except Exception as e:
                    download_failed += 1
                    s = downloads_errors.handle_error(e)
                    fetch_error = (
                        s,
                        f"Error downloading {kind} for {target_username}: {e}",
                    )
                    diagnostics_console.print(
                        f"Download aborted by {type(e).__name__}: {e}"
                    )
                    break
                finally:
                    if (
                        fetch_error is None
                        or fetch_error[0] != downloads_models.status_code.RATE_LIMITED
                    ):
                        downloads_control.delay("request")

        if fetch_error is not None:
            if promoted and patrol_resume_task_id:
                save_patrol_cursor(replay_current)
            return downloads_results._content_result(
                fetch_error[0],
                fetch_error[1],
                downloaded,
                patrol_mode=True,
                checked_count=checked,
                already_downloaded_count=already_downloaded,
                updates_found_count=updates_found,
                download_failed_count=download_failed,
            )

        if patrol_resume_task_id:
            downloads_checkpoints._delete_resume_checkpoint(
                kind,
                task_id=patrol_resume_task_id,
                log=True,
            )
        message = downloads_results._patrol_content_message(
            target_username,
            kind,
            checked,
            already_downloaded,
            updates_found,
            downloaded,
            download_failed,
        )
        result_status = (
            downloads_models.status_code.FAILED
            if download_failed > 0
            else downloads_models.status_code.SUCCESS
        )
        return downloads_results._content_result(
            result_status,
            message,
            downloaded,
            patrol_mode=True,
            checked_count=checked,
            already_downloaded_count=already_downloaded,
            updates_found_count=updates_found,
            download_failed_count=download_failed,
        )
    except downloads_models.DownloadStopRequested:
        if promoted and patrol_resume_task_id:
            if not save_patrol_cursor(replay_current):
                return downloads_results._content_result(
                    downloads_models.status_code.STOPPED,
                    f"Patrol stopped for @{target_username}; the latest "
                    f"{kind} cursor could not be saved, so the earlier "
                    "checkpoint was retained",
                    downloaded,
                    patrol_mode=True,
                    checked_count=checked,
                    already_downloaded_count=already_downloaded,
                    updates_found_count=updates_found,
                    download_failed_count=download_failed,
                )
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
    finally:
        downloads_events.emit_download_status("idle")
