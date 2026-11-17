"""Download progress, file details, and status callbacks."""

from __future__ import annotations

import os
from datetime import datetime
from urllib.parse import urlparse

import inscrawler.diagnostics.console as diagnostics_console
import inscrawler.downloads.runtime as downloads_runtime
import inscrawler.instagram.media as instagram_media


def _detail_datetime(value):
    if value is None:
        return None
    try:
        return value.isoformat()
    except Exception:
        return str(value)


def _detail_attr(obj, name):
    try:
        value = getattr(obj, name)
    except Exception:
        return None
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, datetime):
        return _detail_datetime(value)
    return str(value)


def _download_file_detail(
    target_path, segment, media_obj, download_category, segment_index=1, segment_total=1
):
    url = str((segment or {}).get("url") or "")
    parsed = urlparse(url) if url else None
    media_kind = (segment or {}).get("kind") or instagram_media._path_media_kind(
        target_path
    )

    return {
        "path": os.path.abspath(target_path),
        "filename": os.path.basename(target_path),
        "extension": os.path.splitext(target_path)[1].lower(),
        "media_kind": media_kind,
        "download_category": download_category,
        "segment_index": int(segment_index),
        "segment_total": int(segment_total),
        "source": (segment or {}).get("source") or "standard",
        "source_url_host": parsed.netloc if parsed else "",
        "shortcode": _detail_attr(media_obj, "shortcode"),
        "media_id": _detail_attr(media_obj, "mediaid"),
        "typename": _detail_attr(media_obj, "typename"),
        "media_date_utc": _detail_datetime(_detail_attr(media_obj, "date_utc")),
        "media_date_local": _detail_datetime(_detail_attr(media_obj, "date_local")),
    }


def _emit_file_download_detail(target_path, detail, resp=None):
    if downloads_runtime.runtime.file_download_callback is None:
        return
    try:
        payload = dict(detail or {})
        payload["path"] = os.path.abspath(target_path)
        payload["filename"] = os.path.basename(target_path)
        payload["extension"] = os.path.splitext(target_path)[1].lower()
        payload["downloaded_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            payload["size_bytes"] = os.path.getsize(target_path)
        except OSError:
            payload["size_bytes"] = None
        headers = getattr(resp, "headers", None)
        payload["response_content_type"] = (
            headers.get("Content-Type") if headers is not None else None
        )
        payload["response_content_length"] = (
            headers.get("Content-Length") if headers is not None else None
        )
        payload["response_status_code"] = getattr(resp, "status_code", None)
        downloads_runtime.runtime.file_download_callback(payload)
    except Exception as e:
        diagnostics_console.print(f"File detail callback failed: {e}")


def _emit_file_activity(event, target_path):
    """Report the serial path's exact current file without owning GUI state."""
    callback = downloads_runtime.runtime.file_activity_callback
    if callback is None:
        return
    try:
        callback(
            {
                "event": str(event),
                "path": os.path.abspath(target_path),
            }
        )
    except Exception as e:
        diagnostics_console.print(f"File activity callback failed: {e}")


def _item_completed_detail(media_obj, download_category):
    return {
        "download_category": download_category,
        "shortcode": _detail_attr(media_obj, "shortcode"),
        "media_id": _detail_attr(media_obj, "mediaid"),
        "typename": _detail_attr(media_obj, "typename"),
        "media_date_utc": _detail_datetime(_detail_attr(media_obj, "date_utc")),
        "media_date_local": _detail_datetime(_detail_attr(media_obj, "date_local")),
    }


def _emit_item_completed(detail=None):
    if downloads_runtime.runtime.item_completed_callback is None:
        return
    try:
        payload = dict(detail or {})
        payload["completed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        downloads_runtime.runtime.item_completed_callback(payload)
    except Exception as e:
        diagnostics_console.print(f"Item completed callback failed: {e}")


def _emit_scope_completed(detail=None):
    """Report one fully-consumed ordinary-download scope.

    A scope is one real NodeIterator response page for post-like media, or the
    complete current response set for Stories / Highlights.  Patrol does not
    use this callback.
    """
    if downloads_runtime.runtime.scope_completed_callback is None:
        return
    try:
        payload = dict(detail or {})
        payload["completed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        downloads_runtime.runtime.scope_completed_callback(payload)
    except Exception as e:
        diagnostics_console.print(f"Scope completed callback failed: {e}")


_CONTENT_STATUS_MAP = {
    "posts": {
        "fetch": "fetching_posts",
        "prepare": "preparing_posts",
        "download": "downloading_posts",
    },
    "reels": {
        "fetch": "fetching_reels",
        "prepare": "preparing_reels",
        "download": "downloading_reels",
    },
    "tagged": {
        "fetch": "fetching_tagged",
        "prepare": "preparing_tagged",
        "download": "downloading_tagged",
    },
    "stories": {
        "fetch": "fetching_stories",
        "prepare": "preparing_stories",
        "download": "downloading_stories",
    },
    "highlights": {
        "fetch": "fetching_highlights",
        "prepare": "preparing_highlights",
        "download": "downloading_highlights",
    },
}


def emit_api_status(api_type: str):
    if downloads_runtime.runtime.api_status_callback:
        try:
            downloads_runtime.runtime.api_status_callback(api_type)
        except Exception as e:
            diagnostics_console.print(f"API status callback failed: {e}")


def emit_download_status(status: str):
    if downloads_runtime.runtime.download_status_callback:
        try:
            downloads_runtime.runtime.download_status_callback(status)
        except Exception as e:
            diagnostics_console.print(f"Download status callback failed: {e}")


def emit_content_phase_status(kind: str, phase: str):
    status = _CONTENT_STATUS_MAP.get(str(kind), {}).get(phase)
    if not status:
        return
    if phase == "download":
        emit_download_status(status)
    else:
        emit_api_status(status)
