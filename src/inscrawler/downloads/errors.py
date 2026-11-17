"""Map loader and network failures to application outcomes."""

from __future__ import annotations

from instaloader.exceptions import (
    AbortDownloadException,
    BadCredentialsException,
    LoginException,
    LoginRequiredException,
    TooManyRequestsException,
    TwoFactorAuthRequiredException,
)

import inscrawler.diagnostics.classification as diagnostics_classification
import inscrawler.diagnostics.risk as diagnostics_risk
import inscrawler.downloads.models as downloads_models
import inscrawler.downloads.runtime as downloads_runtime


def _is_instagram_api_rate_limit(error):
    """Identify explicit Instagram API limits while excluding raw CDN errors."""
    errors = tuple(diagnostics_classification._exception_tree(error))
    if any(isinstance(current, TooManyRequestsException) for current in errors):
        return True

    messages = errors or (error,)
    return any(
        diagnostics_classification._looks_like_instagram_api_rate_limit(current)
        for current in messages
    )


def handle_error(e):
    diagnostics_risk._risk_note_incident(e)
    cached = getattr(e, "_inscrawler_status", None)
    if cached is not None:
        return cached
    msg = str(e)

    auth = (
        BadCredentialsException,
        TwoFactorAuthRequiredException,
        LoginException,
        LoginRequiredException,
    )
    if _is_instagram_api_rate_limit(e):
        status = downloads_models.status_code.RATE_LIMITED
    elif isinstance(e, downloads_models.IPhoneQualityUnavailable):
        # The strict iPhone wrappers also catch Instaloader's abort for a
        # checkpoint, challenge or logout; API 429 was classified above.
        if isinstance(e.__cause__ or e.__context__, AbortDownloadException):
            status = downloads_models.status_code.AUTHORIZATION_REQUIRED
        else:
            status = downloads_models.status_code.IPHONE_QUALITY_UNAVAILABLE
    elif (
        isinstance(e, auth)
        or "checkpoint" in msg.lower()
        or "challenge_required" in msg.lower()
        or "feedback_required" in msg.lower()
        or (isinstance(e, AbortDownloadException) and "login page" in msg.lower())
    ):
        status = downloads_models.status_code.AUTHORIZATION_REQUIRED
    else:
        # 含 PrivateProfileNotFollowedException：已登录用户对私密账号无权限——
        # 这是 permission denied，不是 auth 失败
        status = downloads_models.status_code.FAILED
    try:
        setattr(e, "_inscrawler_status", status)
    except Exception:
        pass
    return status


def _err_for_unavailable_client(target_username, kind):
    status = (
        downloads_runtime.runtime.private_client_status
        if downloads_runtime.runtime.private_client_status
        != downloads_models.status_code.SUCCESS
        else downloads_models.status_code.FAILED
    )
    if status == downloads_models.status_code.AUTHORIZATION_REQUIRED:
        message = (
            f"Authorization required before accessing {kind} for {target_username}"
        )
    elif status == downloads_models.status_code.RATE_LIMITED:
        message = f"Instagram temporarily limited API access before accessing {kind} for {target_username}"
    else:
        message = f"Loader client not available for {kind} for {target_username}"
    result = {"status": status, "message": message}
    if status == downloads_models.status_code.FAILED:
        result["blocking_failure"] = True
    return result
