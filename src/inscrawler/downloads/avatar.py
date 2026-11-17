"""Optional profile-picture synchronization for the serial path."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import inscrawler.diagnostics.console as diagnostics_console
import inscrawler.diagnostics.risk as diagnostics_risk
import inscrawler.downloads.control as downloads_control
import inscrawler.downloads.files as downloads_files
import inscrawler.downloads.models as downloads_models
import inscrawler.downloads.runtime as downloads_runtime


def _strip_ig_size_param(url):
    return re.sub(r"([?&])se=\d+&?", r"\1", str(url)).rstrip("&?")


def _resolve_profile_picture_url(profile):
    return profile.profile_pic_url_no_iphone, "metadata_no_iphone"


def _http_datetime_to_utc(value):
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _profile_picture_timestamp(headers):
    dt = _http_datetime_to_utc((headers or {}).get("Last-Modified"))
    return dt if dt is not None else datetime.now(timezone.utc)


def _profile_picture_extension(url, headers):
    content_type = (
        ((headers or {}).get("Content-Type") or "").split(";", 1)[0].strip().lower()
    )
    if content_type == "image/png":
        return ".png"
    if content_type in ("image/webp", "image/x-webp"):
        return ".webp"
    if content_type in ("image/jpeg", "image/jpg", ""):
        return ".jpg"

    ext = os.path.splitext(urlparse(url).path)[1].lower()
    if ext in downloads_models._PROFILE_PICTURE_EXTENSIONS:
        return ".jpg" if ext == ".jpeg" else ext
    return ".jpg"


def _profile_picture_filename_re():
    return re.compile(
        r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.(?:jpe?g|png|webp)$", re.IGNORECASE
    )


def _has_profile_picture_versions(profile_folder):
    if not os.path.isdir(profile_folder):
        return False
    filename_re = _profile_picture_filename_re()
    try:
        return any(filename_re.match(name) for name in os.listdir(profile_folder))
    except OSError:
        return False


def _sync_profile_picture(L, profile, target_username):
    downloads_control.wait_if_paused()
    with diagnostics_console._log_as("download"):
        url, source = _resolve_profile_picture_url(profile)
        if not url:
            raise ValueError(f"Empty profile picture URL for @{target_username}")

        profile_folder = downloads_files.build_save_path(target_username, "profile")
        target_path = None

        if _has_profile_picture_versions(profile_folder):
            with downloads_runtime.runtime.api_lock:
                diagnostics_risk._risk_api("avatar_head")
                head_resp = L.context.head(url, allow_redirects=True)
            try:
                headers = dict(head_resp.headers)
            finally:
                head_resp.close()
            if not headers.get("Last-Modified"):
                diagnostics_console.print(
                    f"Profile picture sync skipped for @{target_username}: missing Last-Modified"
                )
                return None
            ts = _profile_picture_timestamp(headers).strftime("%Y-%m-%d_%H-%M-%S")
            ext = _profile_picture_extension(url, headers)
            target_path = os.path.join(profile_folder, f"{ts}{ext}")

            if os.path.exists(target_path):
                diagnostics_console.print(
                    f"Profile picture already current for @{target_username}: {os.path.basename(target_path)}"
                )
                return target_path

        with downloads_runtime.runtime.api_lock:
            diagnostics_risk._risk_api("avatar_get")
            resp = L.context.get_raw(url)
        try:
            if target_path is None:
                ts = _profile_picture_timestamp(resp.headers).strftime(
                    "%Y-%m-%d_%H-%M-%S"
                )
                ext = _profile_picture_extension(url, resp.headers)
                target_path = os.path.join(profile_folder, f"{ts}{ext}")
                if os.path.exists(target_path):
                    diagnostics_console.print(
                        f"Profile picture already current for @{target_username}: {os.path.basename(target_path)}"
                    )
                    return target_path

            os.makedirs(profile_folder, exist_ok=True)
            L.context.write_raw(resp, target_path)
            mtime = _profile_picture_timestamp(resp.headers).timestamp()
            try:
                os.utime(target_path, (datetime.now().timestamp(), mtime))
            except Exception as e:
                diagnostics_console.print(f"Failed to set mtime on {target_path}: {e}")
            diagnostics_console.print(
                f"Profile picture updated for @{target_username}: "
                f"{os.path.basename(target_path)} ({source}, "
                f"Last-Modified: {resp.headers.get('Last-Modified') or 'missing'}, "
                f"Content-Length: {resp.headers.get('Content-Length') or 'unknown'})"
            )
            if downloads_runtime.runtime.profile_picture_callback:
                try:
                    downloads_runtime.runtime.profile_picture_callback(
                        target_username, target_path
                    )
                except Exception as e:
                    diagnostics_console.print(f"Profile picture callback failed: {e}")
            return target_path
        finally:
            resp.close()


def sync_profile_picture_best_effort(L, profile, target_username):
    if not downloads_runtime.runtime.profile_picture_sync_enabled:
        return None
    try:
        return _sync_profile_picture(L, profile, target_username)
    except downloads_models.DownloadStopRequested:
        raise
    except Exception as e:
        diagnostics_console.print(
            f"Profile picture sync skipped for @{target_username}: {e}"
        )
        return None
