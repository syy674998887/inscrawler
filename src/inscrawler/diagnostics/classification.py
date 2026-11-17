"""Classify Instagram status messages and nested network exceptions."""

from __future__ import annotations

import re


def _looks_like_instagram_api_status(message, status):
    text = str(message).lower()
    if re.search(rf"(?<!\d){int(status)}(?!\d)", text) is None:
        return False
    return any(
        marker in text
        for marker in (
            "json query to",
            "query to https://www.instagram.com/",
            "query to https://i.instagram.com/",
            "when accessing https://www.instagram.com/",
            "/graphql/query",
            "/api/v1/",
        )
    )


def _looks_like_instagram_api_rate_limit(message):
    text = str(message).lower()
    return _looks_like_instagram_api_status(text, 429) or (
        "please wait a few minutes" in text
        and _looks_like_instagram_api_status(text, 401)
    )


def _exception_tree(error):
    """Yield an exception plus nested causes and exception-valued arguments."""
    pending = [error]
    seen = set()
    while pending:
        current = pending.pop()
        if not isinstance(current, BaseException) or id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        pending.extend(
            nested
            for nested in (
                current.__cause__,
                current.__context__,
                *current.args,
            )
            if isinstance(nested, BaseException)
        )
