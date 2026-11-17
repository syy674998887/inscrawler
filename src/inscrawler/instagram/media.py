"""Resolve Web and strict iPhone media URLs and segment metadata."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from instaloader.exceptions import (
    AbortDownloadException,
    BadCredentialsException,
    BadResponseException,
    ConnectionException,
    LoginException,
    LoginRequiredException,
    QueryReturnedBadRequestException,
    QueryReturnedForbiddenException,
    TwoFactorAuthRequiredException,
)

import inscrawler.diagnostics.risk as diagnostics_risk
import inscrawler.downloads.avatar as downloads_avatar
import inscrawler.downloads.models as downloads_models
import inscrawler.downloads.runtime as downloads_runtime


def _resolve_post_urls(post):
    if post.typename == "GraphSidecar":
        nodes = list(post.get_sidecar_nodes())
        return [(n.video_url if n.is_video else n.display_url) for n in nodes]
    return [post.video_url if post.is_video else post.url]


def _resolve_storyitem_url(item):
    return item.video_url if item.is_video else item.url


def _iphone_unavailable(message, cause=None):
    if cause is not None:
        message = f"{message}: {cause}"
    raise downloads_models.IPhoneQualityUnavailable(message)


def _ensure_iphone_context(L):
    if not getattr(L.context, "iphone_support", False):
        _iphone_unavailable(
            "iPhone quality is enabled in the app, but the loader was created without iPhone support"
        )
    if not L.context.is_logged_in:
        _iphone_unavailable(
            "iPhone quality requires a valid logged-in Instagram session"
        )


def _media_desc(media_obj, fallback):
    try:
        shortcode = getattr(media_obj, "shortcode")
        if shortcode:
            return str(shortcode)
    except Exception:
        pass
    try:
        mediaid = getattr(media_obj, "mediaid")
        if mediaid:
            return str(mediaid)
    except Exception:
        pass
    return fallback


def _iphone_media_kind(media, fallback_is_video, desc):
    if not isinstance(media, dict):
        _iphone_unavailable(f"iPhone media info for {desc} is not a JSON object")
    media_type = media.get("media_type")
    if media_type == 1:
        return "image"
    if media_type == 2:
        return "video"
    if fallback_is_video is not None:
        return "video" if fallback_is_video else "image"
    _iphone_unavailable(
        f"iPhone media info for {desc} has unsupported media_type={media_type!r}"
    )


def _iphone_image_url(media, desc):
    try:
        candidates = media["image_versions2"]["candidates"]
    except (KeyError, TypeError) as e:
        _iphone_unavailable(f"Missing iPhone image_versions2 for {desc}", e)
    if not candidates:
        _iphone_unavailable(f"Empty iPhone image candidates for {desc}")
    try:
        url = candidates[0]["url"]
    except (KeyError, TypeError) as e:
        _iphone_unavailable(f"Missing iPhone image URL for {desc}", e)
    if not url:
        _iphone_unavailable(f"Empty iPhone image URL for {desc}")
    return downloads_avatar._strip_ig_size_param(url)


def _iphone_video_url(media, desc):
    versions = media.get("video_versions") if isinstance(media, dict) else None
    if not versions:
        _iphone_unavailable(f"Missing iPhone video_versions for {desc}")
    candidates = []
    for idx, version in enumerate(versions):
        if not isinstance(version, dict):
            continue
        url = version.get("url")
        if not url:
            continue
        try:
            width = int(version.get("width") or 0)
            height = int(version.get("height") or 0)
        except (TypeError, ValueError):
            width = height = 0
        # Prefer the largest explicit resolution; keep the first candidate on ties.
        candidates.append((width * height, width, height, -idx, url))
    if not candidates:
        _iphone_unavailable(f"No usable iPhone video URL for {desc}")
    return downloads_avatar._strip_ig_size_param(max(candidates)[4])


def _post_iphone_media(post):
    desc = _media_desc(post, "post")
    try:
        if not getattr(post, "_iphone_struct_", None):
            diagnostics_risk._risk_api("post_iphone")
        media = post._iphone_struct
    except downloads_models.DownloadStopRequested:
        raise
    except Exception as e:
        _iphone_unavailable(f"Unable to fetch iPhone media info for {desc}", e)
    if not media:
        _iphone_unavailable(f"Empty iPhone media info for {desc}")
    return media


def _storyitem_iphone_media(item):
    desc = _media_desc(item, "story item")
    try:
        media = item._iphone_struct
    except downloads_models.DownloadStopRequested:
        raise
    except Exception as e:
        _iphone_unavailable(f"Unable to fetch iPhone media info for {desc}", e)
    if not media:
        _iphone_unavailable(f"Empty iPhone media info for {desc}")
    return media


def _load_story_iphone_reel(story):
    """Load a Story's iPhone reel; return ``True`` when the owner has none.

    Mirrors Instaloader 4.16 ``Story._fetch_iphone_struct()``: the same
    request and the same private cache, so ``Story.get_items()`` reuses this
    response. Upstream raises ``KeyError`` when a well-formed response omits
    the owner's reel. That is expected when no Story is current although the
    Web reel still lists expired items. Upstream PR #2771 also reported it for
    current reels of cookie sessions without a bearer token, which 4.16 now
    derives from the session cookies. Cache an empty reel and let the caller
    decide from the Web items' expiry.
    """
    if story._iphone_struct_:
        return False
    context = story._context
    if not (context.iphone_support and context.is_logged_in):
        return False
    owner_id = str(story.owner_id)
    data = context.get_iphone_json(
        path=f"api/v1/feed/reels_media/?reel_ids={owner_id}", params={}
    )
    reels = data["reels"]
    if isinstance(reels, dict) and owner_id not in reels:
        story._iphone_struct_ = {"items": []}
        return True
    reel = reels[owner_id]
    if not isinstance(reel, dict) or not reel:
        _iphone_unavailable(f"Malformed iPhone Story reel for owner {owner_id}")
    story._iphone_struct_ = reel
    return False


def _story_iphone_media_ids(story):
    """Return a validated snapshot of iPhone Story media IDs, or ``None``.

    ``Story.get_items()`` fetches the iPhone reel before yielding its Web
    items. Keep this helper read-only: an absent or malformed snapshot must
    retain the existing strict iPhone failure behavior rather than being
    mistaken for an empty current reel.
    """
    try:
        iphone_reel = getattr(story, "_iphone_struct_")
    except Exception:
        return None
    if not isinstance(iphone_reel, dict):
        return None
    iphone_items = iphone_reel.get("items")
    if not isinstance(iphone_items, list):
        return None

    media_ids = set()
    for media in iphone_items:
        if not isinstance(media, dict):
            return None
        media_id = media.get("pk")
        if isinstance(media_id, bool) or not isinstance(media_id, (int, str)):
            return None
        media_id = str(media_id).strip()
        if not media_id:
            return None
        media_ids.add(media_id)
    return frozenset(media_ids)


def _storyitem_expiring_utc(item):
    """Return a Story item's expiration as an aware UTC datetime."""
    try:
        expires_at = getattr(item, "expiring_utc")
    except Exception:
        expires_at = None
    if isinstance(expires_at, datetime):
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return expires_at.astimezone(timezone.utc)

    try:
        raw_node = getattr(item, "_node")
    except Exception:
        return None
    if not isinstance(raw_node, dict):
        return None
    timestamp = raw_node.get("expiring_at_timestamp")
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(timestamp, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _story_items_not_confirmed_expired(items, observed_at=None):
    """Count Web Story items whose expiration is unknown or still ahead."""
    observed_at = observed_at or datetime.now(timezone.utc)
    unexpired = 0
    for item in items:
        expires_at = _storyitem_expiring_utc(item)
        if expires_at is None or expires_at > observed_at:
            unexpired += 1
    return unexpired


def _storyitem_web_only_info(item, iphone_media_ids, observed_at=None):
    """Describe a Web Story item absent from a valid iPhone reel snapshot."""
    if iphone_media_ids is None:
        return None
    try:
        cached_iphone_media = getattr(item, "_iphone_struct_", None)
    except Exception:
        return None
    if cached_iphone_media:
        return None
    try:
        media_id = str(getattr(item, "mediaid")).strip()
    except Exception:
        return None
    if not media_id or media_id in iphone_media_ids:
        return None

    observed_at = observed_at or datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    else:
        observed_at = observed_at.astimezone(timezone.utc)
    expires_at = _storyitem_expiring_utc(item)
    return {
        "media_id": media_id,
        "expires_at": expires_at,
        "observed_at": observed_at,
        "is_expired": expires_at is not None and expires_at <= observed_at,
    }


def _utc_log_timestamp(value):
    if value is None:
        return "unknown"
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _segment_from_iphone_media(media, expected_is_video, desc):
    kind = _iphone_media_kind(media, expected_is_video, desc)
    expected_kind = "video" if expected_is_video else "image"
    if kind != expected_kind:
        _iphone_unavailable(
            f"iPhone media type mismatch for {desc}: expected {expected_kind}, got {kind}"
        )
    url = (
        _iphone_video_url(media, desc)
        if kind == "video"
        else _iphone_image_url(media, desc)
    )
    return {"url": url, "kind": kind, "source": "iphone"}


def _resolve_post_segments_strict_iphone(L, post):
    _ensure_iphone_context(L)
    desc = _media_desc(post, "post")
    media = _post_iphone_media(post)
    if post.typename == "GraphSidecar":
        carousel = media.get("carousel_media") if isinstance(media, dict) else None
        if not isinstance(carousel, list) or not carousel:
            _iphone_unavailable(f"Missing iPhone carousel_media for sidecar {desc}")
        expected_is_videos = list(post.get_is_videos())
        if len(carousel) != len(expected_is_videos):
            _iphone_unavailable(
                f"iPhone carousel_media count mismatch for {desc}: "
                f"expected {len(expected_is_videos)}, got {len(carousel)}"
            )
        return [
            _segment_from_iphone_media(
                node, expected_is_videos[idx], f"{desc} sidecar item {idx + 1}"
            )
            for idx, node in enumerate(carousel)
        ]
    return [_segment_from_iphone_media(media, bool(post.is_video), desc)]


def _resolve_storyitem_segment_strict_iphone(L, item):
    _ensure_iphone_context(L)
    desc = _media_desc(item, "story item")
    media = _storyitem_iphone_media(item)
    return _segment_from_iphone_media(media, bool(item.is_video), desc)


def _path_media_kind(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".mp4":
        return "video"
    if ext in (".jpg", ".jpeg", ".png"):
        return "image"
    return None


def _ensure_segments_match_paths(segments, target_paths, desc):
    if len(segments) != len(target_paths):
        _iphone_unavailable(
            f"Resolved iPhone segment count mismatch for {desc}: "
            f"expected {len(target_paths)}, got {len(segments)}"
        )
    for segment, path in zip(segments, target_paths):
        expected = _path_media_kind(path)
        if expected and segment.get("kind") != expected:
            _iphone_unavailable(
                f"iPhone media type does not match target filename for {os.path.basename(path)}: "
                f"expected {expected}, got {segment.get('kind')}"
            )


def _resolve_post_segments(L, post):
    if downloads_runtime.runtime.iphone_support_enabled:
        return _resolve_post_segments_strict_iphone(L, post)
    return [
        {"url": url, "kind": None, "source": "standard"}
        for url in _resolve_post_urls(post)
    ]


def _resolve_storyitem_segments(L, item):
    if downloads_runtime.runtime.iphone_support_enabled:
        return [_resolve_storyitem_segment_strict_iphone(L, item)]
    return [{"url": _resolve_storyitem_url(item), "kind": None, "source": "standard"}]


_URL_RESOLVE_FATAL_EXCEPTIONS = (
    downloads_models.IPhoneQualityUnavailable,
    BadCredentialsException,
    TwoFactorAuthRequiredException,
    LoginException,
    LoginRequiredException,
    ConnectionException,
    BadResponseException,
    QueryReturnedBadRequestException,
    QueryReturnedForbiddenException,
    AbortDownloadException,
)
