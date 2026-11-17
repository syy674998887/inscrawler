"""Instaloader 4.16 raw-Reels adapter and real-page iteration."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

from instaloader import (
    NodeIterator,
)
from instaloader import (
    __version__ as INSTALOADER_VERSION,
)
from instaloader.exceptions import (
    InvalidArgumentException,
)

_ITER_FUNCS = {
    "posts": lambda p: p.get_posts(),
    "reels": lambda p: p.get_reels(),
    "tagged": lambda p: p.get_tagged_posts(),
}


_RAW_REELS_INSTALOADER_VERSION = "4.16"


_NO_PAGE_ITEM = object()


def _ordinary_post_iterator(profile, kind, *, defer_initial_query=False):
    """Create the ordinary post-like iterator.

    Reels are intercepted before Instaloader's node wrapper.  In 4.16 that
    wrapper performs one shortcode detail query for every item, and after a
    thaw ``first_item`` wraps the saved first node again for the ``is_first``
    callback.  Replacing both private callbacks here keeps the page raw and
    lets local timestamp dedup run before any detail request.
    """
    if defer_initial_query:
        # NodeIterator eagerly queries its first page in __init__.  A valid
        # frozen cursor already contains the page data to resume, so issuing
        # that request only to overwrite it in thaw() is pure risk and waste.
        # The app runs iterator creation under _api_lock; keep this tiny class
        # hook scoped to construction and restore it even if the profile
        # method fails.
        original_query = NodeIterator._query

        def deferred_query(instance, after=None):
            instance._best_before = None
            return {
                "edges": [],
                "page_info": {"has_next_page": False, "end_cursor": None},
            }

        NodeIterator._query = deferred_query
        try:
            iterator = iter(_ITER_FUNCS[kind](profile))
        finally:
            NodeIterator._query = original_query
    else:
        iterator = iter(_ITER_FUNCS[kind](profile))
    if not isinstance(iterator, NodeIterator):
        raise InvalidArgumentException(f"{kind} page source is not a NodeIterator.")
    if kind != "reels":
        return iterator
    if INSTALOADER_VERSION != _RAW_REELS_INSTALOADER_VERSION:
        raise InvalidArgumentException(
            "Raw Reels adapter requires Instaloader "
            f"{_RAW_REELS_INSTALOADER_VERSION}; found {INSTALOADER_VERSION}."
        )
    for attribute in ("_node_wrapper", "_is_first", "_data", "_page_index"):
        if not hasattr(iterator, attribute):
            raise InvalidArgumentException(
                f"Reels iterator does not expose required state {attribute}."
            )
    iterator._node_wrapper = lambda node: node
    iterator._is_first = None
    return iterator


def _node_page_state(iterator):
    """Return the current real response page state, failing closed on drift."""
    if not isinstance(iterator, NodeIterator):
        raise InvalidArgumentException("Page mode requires a NodeIterator.")
    data = getattr(iterator, "_data", None)
    page_index = getattr(iterator, "_page_index", None)
    if not isinstance(data, dict) or not isinstance(page_index, int):
        raise InvalidArgumentException("NodeIterator page state is unavailable.")
    edges = data.get("edges", [])
    if not isinstance(edges, list) or page_index < 0 or page_index > len(edges):
        raise InvalidArgumentException("NodeIterator edge state is invalid.")
    page_info = data.get("page_info")
    if page_info is None:
        page_info = {}
    elif not isinstance(page_info, dict):
        raise InvalidArgumentException("NodeIterator page_info is invalid.")
    has_next_page = page_info.get("has_next_page", False)
    if type(has_next_page) is not bool:
        raise InvalidArgumentException("NodeIterator has_next_page must be a boolean.")
    if has_next_page:
        end_cursor = page_info.get("end_cursor")
        if not isinstance(end_cursor, str) or not end_cursor:
            raise InvalidArgumentException(
                "NodeIterator page has_next_page without a valid end_cursor."
            )
        # A safe-boundary checkpoint intentionally thaws as an empty remainder
        # with a positive total index.  A genuinely empty first response that
        # claims another page is response drift; do not silently count the
        # following response as this page.
        if not edges and not (
            page_index == 0 and getattr(iterator, "total_index", 0) > 0
        ):
            raise InvalidArgumentException(
                "NodeIterator returned an empty page with has_next_page."
            )
    return data, edges, page_index, page_info


def _node_advanced_during_next(iterator, previous_total_index):
    """Whether next() advanced to an item before its wrapper raised."""
    try:
        return int(iterator.total_index) > int(previous_total_index)
    except (AttributeError, TypeError, ValueError):
        # Fail closed: replaying can duplicate a local check; skipping can lose
        # an unclassified remote item.
        return True


def _node_page_has_unconsumed_item(iterator):
    _data, edges, page_index, _page_info = _node_page_state(iterator)
    return page_index < len(edges)


def _node_page_has_next_response(iterator):
    _data, _edges, _page_index, page_info = _node_page_state(iterator)
    return bool(page_info.get("has_next_page"))


def _next_real_page_first_item(iterator):
    """Yield the first item of exactly one real page, without crossing it."""
    _data, edges, page_index, page_info = _node_page_state(iterator)
    if page_index < len(edges):
        return next(iterator)
    if not page_info.get("has_next_page"):
        return _NO_PAGE_ITEM
    try:
        return next(iterator)
    except StopIteration:
        return _NO_PAGE_ITEM


def _raw_reel_media(raw_node):
    if not isinstance(raw_node, dict):
        return None
    media = raw_node.get("media")
    return media if isinstance(media, dict) else None


def _raw_reel_shortcode(raw_node):
    media = _raw_reel_media(raw_node)
    code = media.get("code") if media is not None else None
    return code.strip() if isinstance(code, str) and code.strip() else None


def _raw_reel_existing_path(raw_node, folder):
    """Return a validated simple-video path, or None for detail fallback."""
    media = _raw_reel_media(raw_node)
    if media is None or _raw_reel_shortcode(raw_node) is None:
        return None
    taken_at = media.get("taken_at")
    media_type = media.get("media_type")
    if type(taken_at) is not int or type(media_type) is not int or media_type != 2:
        return None
    if media.get("carousel_media"):
        return None
    if taken_at < 1262304000 or taken_at > int(time.time()) + 86400:
        return None
    try:
        timestamp = datetime.fromtimestamp(taken_at, timezone.utc).strftime(
            "%Y-%m-%d_%H-%M-%S"
        )
    except (OverflowError, OSError, ValueError):
        return None
    return os.path.join(folder, f"{timestamp}.mp4")
