"""Resolve profiles through public pages, then check logged-in metadata."""

from __future__ import annotations

from instaloader import (
    Instaloader,
    InstaloaderContext,
    Profile,
)
from instaloader.exceptions import (
    AbortDownloadException,
    ConnectionException,
    ProfileNotExistsException,
    QueryReturnedNotFoundException,
)

import inscrawler.diagnostics.risk as diagnostics_risk
import inscrawler.downloads.control as downloads_control
import inscrawler.downloads.events as downloads_events
import inscrawler.downloads.models as downloads_models
import inscrawler.downloads.runtime as downloads_runtime
import inscrawler.instagram.hotfixes as instagram_hotfixes


def _load_logged_in_metadata(context: InstaloaderContext, profile: Profile) -> None:
    """Use 4.16's Profile data contract through T5's corrected endpoint.

    Called under the API lock after validating the public page. Keep the query,
    normalization and private state aligned with the pinned upstream version.
    """
    user_id = profile._node.get("id") or profile._node.get("pk")
    variables = {
        "id": str(user_id),
        "render_surface": "PROFILE",
        "__relay_internal__pv__PolarisCannesGuardianExperienceEnabledrelayprovider": True,
        "__relay_internal__pv__PolarisCASB976ProfileEnabledrelayprovider": False,
        "__relay_internal__pv__PolarisRepostsConsumptionEnabledrelayprovider": False,
        "__relay_internal__pv__PolarisWebSchoolsEnabledrelayprovider": False,
        "enable_integrity_filters": True,
    }
    data = instagram_hotfixes.query_profile_metadata(context, variables)
    if data is None:
        raise QueryReturnedNotFoundException("GraphQL query returned None")
    user_data = data.get("data", {}).get("user")
    if user_data is None:
        raise ProfileNotExistsException(f"Profile {profile.username} does not exist.")
    profile._node = Profile._normalize_profile_data(user_data)
    profile._has_full_metadata = True


def resolve_profile(L: Instaloader, username: str):
    """每次都重新拉 Profile —— Profile 实例包含时间点快照元数据。"""
    normalized_username = username.lower()
    try:
        downloads_control.wait_if_paused()
        downloads_events.emit_api_status("checking_user")
        downloads_control.delay("request")
        with downloads_runtime.runtime.api_lock:
            context = L.context
            diagnostics_risk._risk_api("profile_page")
            try:
                # In pinned 4.16 this reads the public page's embedded user,
                # without TopSearch or web_profile_info. Page errors end with
                # "when accessing https://www.instagram.com/...", so 429 and
                # Please wait still stop the queue as API limits.
                profile = Profile.from_username(context, normalized_username)
            except AbortDownloadException as error:
                # The public page is requested anonymously, yet 4.16 reports
                # its redirect to the login page as the logged-in session
                # having been logged out. Keep it an ordinary page failure, as
                # the bare redirect status was in 4.15.4, so it is neither
                # taken for an expired session nor tagged as "please wait".
                if "login page" not in str(error).lower():
                    raise
                raise ConnectionException(
                    f"Query to https://www.instagram.com/{normalized_username}/: "
                    "redirected to the login page"
                ) from error
            if profile.username != normalized_username:
                raise ConnectionException(
                    f"Could not resolve @{normalized_username} through "
                    "the profile page: returned a different username."
                )
            if not context.is_logged_in:
                return profile

            # Public page data does not establish the logged-in viewer's
            # relationship. Fetch it before private-profile access checks.
            downloads_control.delay("request")
            diagnostics_risk._risk_api("profile")
            _load_logged_in_metadata(context, profile)
            return profile
    except ProfileNotExistsException:
        return None


def _ensure_accessible(L: Instaloader, profile) -> str | None:
    """登录态 + 私密/拉黑/未关注 → 拒绝抓取。

    解析器已加载登录态 metadata；其他 Profile 实例的权限属性仍可能触发
    上游懒加载，所以全过程保持在 API 锁内。"""
    downloads_control.wait_if_paused()
    with downloads_runtime.runtime.api_lock:
        if L.context.is_logged_in and profile.has_blocked_viewer:
            return downloads_models.status_code.FAILED
        if not profile.is_private:
            return None
        if not L.context.is_logged_in:
            return downloads_models.status_code.AUTHORIZATION_REQUIRED
        if profile.username == (L.context.username or "").lower():
            return None
        if profile.followed_by_viewer:
            return None
    return downloads_models.status_code.FAILED
