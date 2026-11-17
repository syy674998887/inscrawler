"""Complete-file CDN transfer, retry handling, and media writes."""

from __future__ import annotations

import os
import re
from datetime import datetime
from http.client import IncompleteRead

import requests
from instaloader.exceptions import (
    BadResponseException,
    ConnectionException,
    InstaloaderException,
    LoginException,
    PostChangedException,
    QueryReturnedBadRequestException,
    QueryReturnedForbiddenException,
    QueryReturnedNotFoundException,
    TooManyRequestsException,
)
from urllib3.exceptions import (
    ProtocolError as Urllib3ProtocolError,
)
from urllib3.exceptions import (
    ReadTimeoutError as Urllib3ReadTimeoutError,
)

import inscrawler.diagnostics.classification as diagnostics_classification
import inscrawler.diagnostics.console as diagnostics_console
import inscrawler.diagnostics.risk as diagnostics_risk
import inscrawler.downloads.control as downloads_control
import inscrawler.downloads.events as downloads_events
import inscrawler.downloads.models as downloads_models
import inscrawler.downloads.runtime as downloads_runtime
import inscrawler.instagram.media as instagram_media

_SERIAL_CDN_RETRY_COUNT = 1


_RETRYABLE_CDN_HTTP_STATUSES = frozenset({408, 500, 502, 503, 504})


def _transfer_prepared_file(
    client, prepared_file, *, before_get=None, after_write=None
):
    """Transfer one fully prepared file without owning mode-specific policy.

    The caller supplies the transport and optional serial-only hooks.  There is
    deliberately no Stop check between ``get_raw`` and ``write_raw`` so a file
    that has started keeps the existing complete-current-file semantics.
    """
    downloads_control.wait_if_paused()
    os.makedirs(os.path.dirname(prepared_file.path), exist_ok=True)
    if before_get is not None:
        before_get()
    resp = client.get_raw(prepared_file.url)
    try:
        client.write_raw(resp, prepared_file.path)
        try:
            os.utime(
                prepared_file.path,
                (datetime.now().timestamp(), prepared_file.mtime.timestamp()),
            )
        except Exception as e:
            diagnostics_console.print(
                f"Failed to set mtime on {prepared_file.path}: {e}"
            )
        if after_write is not None:
            after_write(resp)
    finally:
        resp.close()


def _is_retryable_cdn_transfer_error(error):
    """Return True only for transient failures in a serial CDN file GET."""
    errors = tuple(diagnostics_classification._exception_tree(error))
    non_retryable = (
        downloads_models.DownloadStopRequested,
        BadResponseException,
        LoginException,
        QueryReturnedBadRequestException,
        QueryReturnedForbiddenException,
        QueryReturnedNotFoundException,
        TooManyRequestsException,
    )
    if any(isinstance(current, non_retryable) for current in errors):
        return False

    transport_errors = (
        IncompleteRead,
        Urllib3ProtocolError,
        Urllib3ReadTimeoutError,
        requests.exceptions.ChunkedEncodingError,
        requests.exceptions.ConnectionError,
        requests.exceptions.Timeout,
        ConnectionError,
        TimeoutError,
    )
    if any(isinstance(current, transport_errors) for current in errors):
        return True

    for current in errors:
        if not isinstance(current, ConnectionException):
            continue
        match = re.match(
            r"\s*(?:HTTP\s+)?(\d{3})\b",
            str(current),
            re.IGNORECASE,
        )
        if match and int(match.group(1)) in _RETRYABLE_CDN_HTTP_STATUSES:
            return True
    return False


def _discard_partial_transfer(target_path):
    temp_path = os.fspath(target_path) + ".temp"
    try:
        os.remove(temp_path)
    except FileNotFoundError:
        pass
    except OSError as e:
        diagnostics_console.print(f"Failed to remove partial CDN file {temp_path}: {e}")


def _write_url_to_path(L, url, target_path, mtime, detail=None, request_budget=None):
    prepared_file = downloads_models.PreparedFile(
        url=url, path=target_path, mtime=mtime
    )
    started = False
    transfer_committed = False

    def after_write(resp):
        nonlocal transfer_committed
        transfer_committed = True
        if downloads_runtime.runtime.image_download_callback:
            try:
                downloads_runtime.runtime.image_download_callback(prepared_file.path)
            except Exception as e:
                diagnostics_console.print(f"Image download callback failed: {e}")
        downloads_events._emit_file_download_detail(prepared_file.path, detail, resp)

    def before_get():
        nonlocal started
        diagnostics_risk._risk_api("cdn")
        if not started:
            if request_budget is not None:
                request_budget.consume()
            started = True
            downloads_events._emit_file_activity("started", prepared_file.path)

    try:
        retry_count = 0
        while True:
            try:
                _transfer_prepared_file(
                    L.context,
                    prepared_file,
                    before_get=before_get,
                    after_write=after_write,
                )
                break
            except Exception as e:
                if not transfer_committed:
                    _discard_partial_transfer(prepared_file.path)
                if (
                    transfer_committed
                    or retry_count >= _SERIAL_CDN_RETRY_COUNT
                    or not _is_retryable_cdn_transfer_error(e)
                ):
                    raise
                retry_count += 1
                diagnostics_console.print(
                    f"Transient CDN transfer failed for "
                    f"{os.path.basename(prepared_file.path)}; "
                    f"retry {retry_count}/{_SERIAL_CDN_RETRY_COUNT} after delay: "
                    f"{type(e).__name__}: {e}"
                )
                downloads_control.delay("download")
    finally:
        if started:
            downloads_events._emit_file_activity("finished", prepared_file.path)


def _log_downloaded_file(target_path, segment):
    source = str((segment or {}).get("source") or "standard").strip().lower()
    quality = "iPhone" if source == "iphone" else "normal"
    diagnostics_console.print(
        f"Downloaded {os.path.basename(target_path)} ({quality} quality)"
    )


def download_post_to_paths(
    L, post, target_paths, download_category="posts", request_budget=None
) -> bool:
    if not target_paths:
        return False
    downloads_control.wait_if_paused()
    try:
        segments = instagram_media._resolve_post_segments(L, post)
        if downloads_runtime.runtime.iphone_support_enabled:
            instagram_media._ensure_segments_match_paths(
                segments, target_paths, post.shortcode
            )
    except PostChangedException:
        diagnostics_console.print(
            f"Post changed during fetch, skipping: {post.shortcode}"
        )
        raise
    except instagram_media._URL_RESOLVE_FATAL_EXCEPTIONS:
        raise
    except (KeyError, InstaloaderException) as e:
        diagnostics_console.print(f"Failed to resolve URLs for {post.shortcode}: {e}")
        return False

    ok = True
    any_written = False
    download_phase_emitted = False
    segment_total = len(segments)
    for segment_index, (segment, path) in enumerate(
        zip(segments, target_paths), start=1
    ):
        downloads_control.wait_if_paused()
        url = segment.get("url")
        if not url:
            if segment.get("source") == "iphone":
                raise downloads_models.IPhoneQualityUnavailable(
                    f"Empty iPhone quality URL for {post.shortcode} segment {os.path.basename(path)}"
                )
            diagnostics_console.print(
                f"Empty url for {post.shortcode} segment, skipping"
            )
            ok = False
            continue
        try:
            detail = downloads_events._download_file_detail(
                path,
                segment,
                post,
                download_category,
                segment_index=segment_index,
                segment_total=segment_total,
            )
            if not download_phase_emitted:
                downloads_events.emit_content_phase_status(
                    download_category, "download"
                )
                download_phase_emitted = True
            _write_url_to_path(
                L,
                url,
                path,
                post.date_local,
                detail,
                request_budget=request_budget,
            )
            _log_downloaded_file(path, segment)
            any_written = True
        except (
            ConnectionException,
            BadResponseException,
            QueryReturnedForbiddenException,
        ) as e:
            if segment.get("source") == "iphone":
                raise downloads_models.IPhoneQualityUnavailable(
                    f"iPhone quality CDN download failed for {os.path.basename(path)}: {e}"
                ) from e
            diagnostics_console.print(
                f"CDN download failed for {os.path.basename(path)}: {e}"
            )
            ok = False
    return ok and any_written


def download_storyitem_to_paths(
    L, item, target_paths, download_category="stories", request_budget=None
) -> bool:
    if not target_paths:
        return False
    downloads_control.wait_if_paused()
    try:
        segments = instagram_media._resolve_storyitem_segments(L, item)
        if downloads_runtime.runtime.iphone_support_enabled:
            instagram_media._ensure_segments_match_paths(
                segments, target_paths, instagram_media._media_desc(item, "story item")
            )
    except instagram_media._URL_RESOLVE_FATAL_EXCEPTIONS:
        raise
    except (KeyError, InstaloaderException) as e:
        diagnostics_console.print(f"Failed to resolve URL for story item: {e}")
        return False
    segment = segments[0]
    url = segment.get("url")
    if not url:
        if segment.get("source") == "iphone":
            raise downloads_models.IPhoneQualityUnavailable(
                f"Empty iPhone quality URL for story item {os.path.basename(target_paths[0])}"
            )
        diagnostics_console.print("Empty url for story item, skipping")
        return False
    path = target_paths[0]
    try:
        detail = downloads_events._download_file_detail(
            path, segment, item, download_category
        )
        downloads_events.emit_content_phase_status(download_category, "download")
        _write_url_to_path(
            L,
            url,
            path,
            item.date_local,
            detail,
            request_budget=request_budget,
        )
        _log_downloaded_file(path, segment)
        return True
    except (
        ConnectionException,
        BadResponseException,
        QueryReturnedForbiddenException,
    ) as e:
        if segment.get("source") == "iphone":
            raise downloads_models.IPhoneQualityUnavailable(
                f"iPhone quality CDN download failed for {os.path.basename(path)}: {e}"
            ) from e
        diagnostics_console.print(
            f"CDN download failed for {os.path.basename(path)}: {e}"
        )
        return False
