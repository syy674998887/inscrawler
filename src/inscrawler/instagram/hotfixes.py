"""Temporary, version-bound fixes awaiting an Instaloader release.

Upstream sources and removal conditions are recorded beside each fix.
Never change the installed package or treat a failed identity check as success.
"""

from __future__ import annotations

from types import MethodType

from instaloader import Story
from instaloader import __version__ as INSTALOADER_VERSION
from instaloader.exceptions import (
    BadResponseException,
    InvalidArgumentException,
    LoginRequiredException,
)
from requests.cookies import RequestsCookieJar

import inscrawler.downloads.control as downloads_control

_SUPPORTED_VERSION = "4.16"
_STORIES_DOC_ID = "29170422829314651"


def _require_supported_version():
    if INSTALOADER_VERSION != _SUPPORTED_VERSION:
        raise InvalidArgumentException(
            "Review and remove temporary Instagram fixes before changing "
            f"Instaloader {_SUPPORTED_VERSION} (found {INSTALOADER_VERSION})."
        )


def _user_id(value):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise BadResponseException("Invalid user or media ID in Instagram response.")
    value = str(value)
    if not value.isdecimal() or int(value) <= 0:
        raise BadResponseException("Invalid user or media ID in Instagram response.")
    return value


def _test_login_current_user(context):
    """TEMPORARY(T3): authenticate, without the rejected legacy login query.

    Issue #2792: https://github.com/instaloader/instaloader/issues/2792
    The endpoint is also used by instagrapi's account_info(). Verified against
    a real cookie session on 2026-10-31. Remove when upstream test_login() has
    a working authenticated identity check; never return the cached username.
    """
    if not context.is_logged_in:
        return None
    expected_id = context.user_id
    if not expected_id:
        raise LoginRequiredException("Cookie session has no authenticated user ID.")
    data = context.get_iphone_json(
        path="api/v1/accounts/current_user/", params={"edit": "true"}
    )
    user = data.get("user") if isinstance(data, dict) else None
    if not isinstance(user, dict) or data.get("status") != "ok":
        raise BadResponseException("Instagram did not return a current user.")
    username = user.get("username")
    if not isinstance(username, str) or not username.strip():
        raise BadResponseException("Instagram did not return a current username.")
    if _user_id(user.get("pk")) != str(expected_id):
        raise LoginRequiredException(
            "Authenticated user ID does not match the Cookie session."
        )

    # The following authenticated homepage may refresh cookies. Give existing
    # cookies the same domain as Set-Cookie, avoiding duplicate unscoped names.
    # This is PR #2793's jar normalization, locally verified with T4. Session
    # pickle contents remain the same name/value mapping.
    cookies = RequestsCookieJar()
    for name, value in context._session.cookies.get_dict().items():
        cookies.set(name, value, domain=".instagram.com", path="/")
    context._session.cookies = cookies
    return username


def install_login_check(loader):
    """Install T3 on this loader only; keep the public test_login() contract."""
    _require_supported_version()
    loader.context.test_login = MethodType(_test_login_current_user, loader.context)


def _ensure_web_api_tokens(context):
    """Reuse the session's tokens and keep the gap after a cold homepage load."""
    sessionid = context._session.cookies.get_dict().get("sessionid", "")
    if sessionid not in context._web_api_tokens_cache:
        # The caller already waited before this first HTTP request.
        context._web_api_tokens()
        downloads_control.delay("request")


def query_profile_metadata(context, variables):
    """TEMPORARY(T5): send the logged-in Profile query to the web API.

    https://github.com/instaloader/instaloader/pull/2795 at
    a62f608c621d47f2af9ec069295f5633549a1120 migrates this query from the
    rejected graphql/query endpoint. Remove after a formal release includes
    the change, retaining the homepage/request gap and no-TopSearch behavior.
    The caller owns the API lock, initial delay, and profile access checks.
    """
    _require_supported_version()
    if not context.is_logged_in:
        raise LoginRequiredException("Login required to read Profile metadata.")
    _ensure_web_api_tokens(context)
    return context.doc_id_graphql_query("27937681195819736", variables, web_api=True)


def _story_node(media, owner_id):
    """Adapt new Web metadata without populating the native iPhone cache."""
    try:
        media_id = _user_id(media["pk"])
        media_type = media["media_type"]
        if type(media_type) is not int or media_type not in (1, 2):
            raise ValueError("unsupported media type")
        taken_at = media["taken_at"]
        expires_at = media.get("expiring_at")
        if type(taken_at) not in (int, float) or (
            expires_at is not None and type(expires_at) not in (int, float)
        ):
            raise ValueError("invalid timestamps")
        candidates = media["image_versions2"]["candidates"]
        if not isinstance(candidates, list) or not candidates:
            raise ValueError("missing image candidates")
        node = {
            "id": media_id,
            "__typename": "GraphStoryVideo" if media_type == 2 else "GraphStoryImage",
            "is_video": media_type == 2,
            "taken_at_timestamp": taken_at,
            "expiring_at_timestamp": expires_at,
            "caption": (media.get("caption") or {}).get("text"),
            "owner": {"id": owner_id},
            "display_resources": [
                {
                    "src": candidate["url"],
                    "config_width": candidate.get("width"),
                    "config_height": candidate.get("height"),
                }
                for candidate in reversed(candidates)
            ],
        }
        if media_type == 2:
            node["video_resources"] = [
                {"src": version["url"]}
                for version in reversed(media.get("video_versions") or [])
            ]
        return node
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise BadResponseException("Malformed Web Story metadata.") from error


def get_target_stories(loader, profile):
    """TEMPORARY(T4): the serial path's current Story reel for one profile.

    Query from https://github.com/instaloader/instaloader/pull/2793 at
    32389cc92c5969fbdfb5a3f4dff38ae6a1c2c54b. Remove when a formal release
    supplies this query, after checking its Story/iPhone cache contract.

    Keep 4.16 Story.get_items() and the project's strict iPhone reel handling:
    Web data having an iPhone-like shape does not make it native iPhone data.
    The caller must resolve the profile and check access before calling here.
    """
    _require_supported_version()
    context = loader.context
    if not context.is_logged_in:
        raise LoginRequiredException("Login required to read Stories.")
    owner_id = _user_id(profile.userid)
    _ensure_web_api_tokens(context)
    data = context.doc_id_graphql_query(
        _STORIES_DOC_ID,
        {
            "reel_ids_arr": [owner_id],
            "__relay_internal__pv__PolarisCommunityNoteStoriesLabelEnabledrelayprovider": True,
        },
        web_api=True,
    )
    try:
        reels = data["data"]["xdt_api__v1__feed__reels_media"]["reels_media"]
        if not isinstance(reels, list) or len(reels) > 1:
            raise ValueError("unexpected reel count")
        stories = []
        for reel in reels:
            if _user_id(reel["id"]) != owner_id:
                raise ValueError("unexpected reel owner")
            reel_user_id = (reel.get("user") or {}).get("pk")
            if reel_user_id is not None and _user_id(reel_user_id) != owner_id:
                raise ValueError("unexpected reel user")
            if not isinstance(reel["items"], list):
                raise ValueError("invalid reel items")
            story = Story(
                context,
                {
                    **reel,
                    "items": [_story_node(media, owner_id) for media in reel["items"]],
                },
            )
            story._owner_profile = profile
            stories.append(story)
        return stories
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise BadResponseException("Malformed Story reel response.") from error
