"""Download outcomes, control exceptions, and immutable prepared media."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime


class status_code:
    SUCCESS = "success"
    FAILED = "failed"
    AUTHORIZATION_REQUIRED = "authorization_required"
    RATE_LIMITED = "rate_limited"
    USER_NOT_FOUND = "user_not_found"
    IPHONE_QUALITY_UNAVAILABLE = "iphone_quality_unavailable"
    STOPPED = "stopped"


class DownloadStopRequested(Exception):
    pass


class IPhoneQualityUnavailable(Exception):
    pass


_PROFILE_PICTURE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")


_RESUMABLE_MEDIA_TYPES = ("posts", "reels", "tagged")


_PATROL_MEDIA_TYPES = (
    "posts",
    "reels",
    "tagged",
    "stories",
    "highlights",
)


@dataclass(frozen=True, slots=True)
class PreparedFile:
    """Immutable input for a complete media-file transfer."""

    url: str
    path: str | os.PathLike
    mtime: datetime
    source: str = "standard"
    media_kind: str | None = None
    desc: str = "media"


class _MediaRequestBudget:
    """Per-run budget for ordinary serial media-file transfers."""

    def __init__(self, limit=None):
        try:
            normalized = int(limit) if limit is not None else None
        except (TypeError, ValueError):
            normalized = None
        self.limit = normalized if normalized is not None and normalized > 0 else None
        self.used = 0

    @property
    def enabled(self):
        return self.limit is not None

    @property
    def remaining(self):
        if self.limit is None:
            return None
        return max(0, self.limit - self.used)

    @property
    def exhausted(self):
        return self.limit is not None and self.used >= self.limit

    def can_fit(self, request_count):
        try:
            count = max(0, int(request_count))
        except (TypeError, ValueError):
            return False
        return self.limit is None or self.used + count <= self.limit

    def consume(self):
        if not self.can_fit(1):
            raise DownloadStopRequested("Download limit reached")
        self.used += 1
