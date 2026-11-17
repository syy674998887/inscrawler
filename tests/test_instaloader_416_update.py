"""Offline regression coverage for the Instaloader 4.16 update.

4.16 ships the two former temporary fixes: the iPhone API bearer token derived
from the session cookies (PR #2771, T1) and the logged-in Posts query on the
web app's ``api/graphql`` endpoint (PR #2785, T2). Their tests now pin the
upstream behavior that the app relies on in their place.
"""

from __future__ import annotations

import base64
import contextlib
import importlib.metadata
import io
import json
import time
import unittest
import urllib.parse
from datetime import timezone
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import instaloader
import requests
from instaloader import (
    FrozenNodeIterator,
    InstaloaderContext,
    Post,
    Profile,
    Story,
)
from instaloader.exceptions import (
    AbortDownloadException,
    BadResponseException,
    ConnectionException,
    InvalidArgumentException,
    QueryReturnedNotFoundException,
)

import inscrawler.diagnostics.risk as diagnostics_risk
import inscrawler.downloads.control as downloads_control
import inscrawler.downloads.errors as downloads_errors
import inscrawler.downloads.models as downloads_models
import inscrawler.instagram.media as instagram_media
import inscrawler.instagram.profile as instagram_profile
import inscrawler.instagram.reels as instagram_reels
import inscrawler.instagram.session as instagram_session

_COOKIES = {
    "sessionid": "12345%3AsessionAbc%3A8%3AsigXyz",
    "ds_user_id": "12345",
    "csrftoken": "csrf-token",
    "mid": "mid-value",
}


# The token PR #2771 derives from _COOKIES, spelled out independently.
_PR_2771_TOKEN = (
    "Bearer IGT:2:"
    + base64.b64encode(
        b'{"ds_user_id":"12345","sessionid":"12345%3AsessionAbc%3A8%3AsigXyz"}'
    ).decode()
)


_FB_DTSG = "NAcMz9:17:1761100000"
_LSD = "AVqLsd_x"


_START_PAGE = (
    '<script type="application/json">{"require":[['
    f'["DTSGInitialData",[],{{"token":"{_FB_DTSG}"}},258],'
    f'["LSD",[],{{"token":"{_LSD}"}},323],'
    '["CurrentUserInitialData",[],{"IG_USER_EIMU":"17841400000000000"},270]'
    "]]}</script>"
)


# Profile.get_posts() logged-in variables of 4.16, copied from its source. The
# former T2 sent the same ones, so its saved cursors still resume.
_POSTS_VARIABLES = {
    "data": {
        "count": 12,
        "include_reel_media_seen_timestamp": True,
        "include_relationship_info": True,
        "latest_besties_reel_media": True,
        "latest_reel_media": True,
    },
    "first": 12,
    "include_multi_captions": True,
    "username": "alice",
    "__relay_internal__pv__PolarisMultiCaptionCarouselEnabledrelayprovider": True,
    "__relay_internal__pv__PolarisShortDramaEnabledrelayprovider": True,
    "__relay_internal__pv__PolarisSEOIsCrawlerrelayprovider": False,
    "__relay_internal__pv__PolarisThreadsStyleCommentsEnabledrelayprovider": False,
    "__relay_internal__pv__PolarisReelsRecoDebugOverlayEnabledrelayprovider": False,
}


_TIMELINE = "xdt_api__v1__feed__user_timeline_graphql_connection"


def _logged_in_context(cookies=None):
    context = InstaloaderContext(sleep=False, quiet=True)
    context.load_session("viewer", dict(cookies or _COOKIES))
    return context


def _logged_in_profile(context):
    profile = Profile(
        context, {"id": "12345", "username": "alice", "is_private": False}
    )
    profile._has_full_metadata = True
    return profile


def _response(status_code=200, text="", location=None):
    response = requests.Response()
    response.status_code = status_code
    response.reason = "OK" if status_code == 200 else "Error"
    response.url = "https://www.instagram.com/"
    response.encoding = "utf-8"
    response._content = text.encode()
    if location is not None:
        response.headers["location"] = location
    return response


def _media(code, pk, taken_at):
    return {
        "media_type": 2,
        "code": code,
        "pk": pk,
        "taken_at": taken_at,
        "has_liked": False,
        "like_count": 0,
        "caption": None,
        "image_versions2": {"candidates": [{"url": "https://cdn.example/cover.jpg"}]},
        "video_versions": [
            {"width": 640, "height": 360, "url": "https://cdn.example/low.mp4"},
            {"width": 1920, "height": 1080, "url": "https://cdn.example/high.mp4"},
        ],
    }


def _timeline_page(nodes, cursor=None):
    return {
        "data": {
            _TIMELINE: {
                "edges": [{"node": node} for node in nodes],
                "page_info": {
                    "has_next_page": cursor is not None,
                    "end_cursor": cursor,
                },
            }
        }
    }


class _RecordingGetJson:
    """Stand-in for ``InstaloaderContext.get_json`` that records each request."""

    def __init__(self, responses=(), response_headers=None):
        self.responses = list(responses)
        self.response_headers = dict(response_headers or {})
        self.calls = []

    def __call__(
        self,
        path,
        params,
        host="www.instagram.com",
        session=None,
        response_headers=None,
        use_post=False,
    ):
        self.calls.append(
            SimpleNamespace(
                path=path,
                params=dict(params),
                host=host,
                headers=session.headers.copy(),
                cookies=session.cookies.get_dict(),
                use_post=use_post,
            )
        )
        if response_headers is not None:
            response_headers.clear()
            response_headers.update(self.response_headers)
        response = self.responses.pop(0) if self.responses else {"status": "ok"}
        if isinstance(response, Exception):
            raise response
        return response


class Instaloader416UpdateTests(unittest.TestCase):
    def test_runtime_dependency_is_416(self):
        self.assertEqual(instaloader.__version__, "4.16")
        self.assertEqual(importlib.metadata.version("instaloader"), "4.16")
        self.assertEqual(instagram_reels.INSTALOADER_VERSION, "4.16")
        self.assertEqual(instagram_reels._RAW_REELS_INSTALOADER_VERSION, "4.16")

    @staticmethod
    def _page(connection, nodes, cursor=None):
        return {
            "data": {
                connection: {
                    "edges": [{"node": node} for node in nodes],
                    "page_info": {
                        "has_next_page": cursor is not None,
                        "end_cursor": cursor,
                    },
                }
            }
        }

    @staticmethod
    def _profile_page_context():
        context = Mock(is_logged_in=True, username="viewer")
        context._session.cookies.get_dict.return_value = {"sessionid": "sid"}
        context._web_api_tokens_cache = {"sid": {}}
        context.get_page_data.return_value = [
            {"xig_user_by_username": {"pk": "12345", "username": "alice"}}
        ]
        context.get_json.side_effect = AssertionError("unexpected legacy lookup")
        return context

    def test_upstream_logged_in_posts_query_pages_and_keeps_iphone_media(self):
        # Instagram answers the query that 4.15.4 sent, 28975909992013618, with
        # 400 "feedback_required"; 4.16 sends the web app's posts tab query to
        # api/graphql instead.
        context = Mock(is_logged_in=True, iphone_support=True, username="viewer")
        media = _media("video1", "101", 1784776879)
        second = _media("video2", "102", 1784776800)
        context.doc_id_graphql_query.side_effect = [
            _timeline_page([media], "page-2"),
            _timeline_page([second]),
        ]

        posts = list(
            instagram_reels._ordinary_post_iterator(
                _logged_in_profile(context), "posts"
            )
        )

        self.assertEqual([post.shortcode for post in posts], ["video1", "video2"])
        self.assertEqual(
            posts[0].date_utc.replace(tzinfo=timezone.utc).timestamp(),
            media["taken_at"],
        )
        self.assertEqual(
            [
                query_call.args
                for query_call in context.doc_id_graphql_query.call_args_list
            ],
            [
                (
                    "29104886249145907",
                    _POSTS_VARIABLES,
                    "https://www.instagram.com/alice/",
                    True,
                ),
                (
                    "29104886249145907",
                    {
                        **_POSTS_VARIABLES,
                        "after": "page-2",
                        "before": None,
                        "first": 12,
                        "last": None,
                    },
                    "https://www.instagram.com/alice/",
                    True,
                ),
            ],
        )
        self.assertEqual(
            instagram_media._resolve_post_segments_strict_iphone(
                SimpleNamespace(context=context), posts[0]
            ),
            [
                {
                    "url": "https://cdn.example/high.mp4",
                    "kind": "video",
                    "source": "iphone",
                }
            ],
        )
        context.get_iphone_json.assert_not_called()
        context.get_json.assert_not_called()

    def test_raw_reels_use_real_upstream_iterator_without_detail_requests(self):
        context = Mock(is_logged_in=True, username="viewer")
        nodes = [
            {"media": {"code": "reel1", "taken_at": 1784776879, "media_type": 2}},
            {"media": {"code": "reel2", "taken_at": 1784776800, "media_type": 2}},
        ]
        connection = "xdt_api__v1__clips__user__connection_v2"
        context.doc_id_graphql_query.side_effect = [
            self._page(connection, nodes[:1], "page-2"),
            self._page(connection, nodes[1:]),
        ]
        profile = _logged_in_profile(context)
        with patch.object(
            Post,
            "from_shortcode",
            side_effect=AssertionError("unexpected detail query"),
        ):
            iterator = instagram_reels._ordinary_post_iterator(profile, "reels")
            self.assertIs(next(iterator), nodes[0])
            frozen = iterator.freeze()
            resumed = instagram_reels._ordinary_post_iterator(
                profile, "reels", defer_initial_query=True
            )
            resumed.thaw(frozen)
            self.assertEqual(list(resumed), nodes)
            self.assertEqual(resumed.first_item, nodes[0])

        self.assertEqual(context.doc_id_graphql_query.call_count, 2)
        self.assertTrue(
            all(
                call.args[0] == "7845543455542541"
                for call in context.doc_id_graphql_query.call_args_list
            )
        )
        self.assertEqual(
            context.doc_id_graphql_query.call_args.args[1]["after"], "page-2"
        )

    def test_logged_in_profile_metadata_uses_web_api_and_normalizes_access(self):
        for following, blocked in ((False, False), (True, False), (True, True)):
            with self.subTest(following=following, blocked=blocked):
                context = self._profile_page_context()
                context.get_json.side_effect = AssertionError(
                    "unexpected legacy lookup"
                )
                context.get_page_data.return_value = [
                    {
                        "xig_user_by_username": {
                            "pk": "12345",
                            "id": "page-id-not-user-id",
                            "username": "alice",
                            "is_private": True,
                            "followed_by_viewer": not following,
                            "has_blocked_viewer": not blocked,
                        }
                    }
                ]
                context.doc_id_graphql_query.return_value = {
                    "data": {
                        "user": {
                            "pk": "12345",
                            "username": "alice",
                            "is_private": True,
                            "media_count": 7,
                            "friendship_status": {"following": following},
                            "has_blocked_viewer": blocked,
                        }
                    }
                }
                loader = SimpleNamespace(context=context)
                with (
                    patch.object(downloads_control, "delay") as delay,
                    patch.object(diagnostics_risk, "_risk_api") as risk_api,
                ):
                    profile = instagram_profile.resolve_profile(loader, "Alice")
                    status = instagram_profile._ensure_accessible(loader, profile)
                context.get_page_data.assert_called_once_with("alice/")
                self.assertEqual(
                    delay.call_args_list, [call("request"), call("request")]
                )
                self.assertEqual(
                    risk_api.call_args_list, [call("profile_page"), call("profile")]
                )
                self.assertEqual(
                    status,
                    None
                    if following and not blocked
                    else downloads_models.status_code.FAILED,
                )
                self.assertEqual(profile.mediacount, 7)
                context.doc_id_graphql_query.assert_called_once()
                self.assertEqual(
                    context.doc_id_graphql_query.call_args.args[0], "27937681195819736"
                )
                self.assertEqual(
                    context.doc_id_graphql_query.call_args.args[1]["id"], "12345"
                )
                # Keep the query variables and normalized state aligned with
                # 4.16; T5 changes only the endpoint and supplies web tokens.
                upstream_context = Mock(is_logged_in=True)
                upstream_context.doc_id_graphql_query.return_value = (
                    context.doc_id_graphql_query.return_value
                )
                upstream_profile = Profile(
                    upstream_context, {"id": "12345", "username": "alice"}
                )
                upstream_profile._obtain_metadata()
                self.assertEqual(
                    context.doc_id_graphql_query.call_args.args,
                    upstream_context.doc_id_graphql_query.call_args.args,
                )
                self.assertEqual(
                    context.doc_id_graphql_query.call_args.kwargs, {"web_api": True}
                )
                self.assertEqual(profile._node, upstream_profile._node)
                self.assertTrue(profile._has_full_metadata)
                context.get_json.assert_not_called()
                context.get_iphone_json.assert_not_called()

    def test_own_private_profile_is_accessible_whatever_the_login_case(self):
        # Profile.username is lowercase; the configured login name may not be.
        profile = SimpleNamespace(
            has_blocked_viewer=False,
            is_private=True,
            username="alice",
            followed_by_viewer=False,
        )
        for login in ("alice", "Alice"):
            with self.subTest(login=login):
                loader = SimpleNamespace(
                    context=SimpleNamespace(is_logged_in=True, username=login)
                )
                self.assertIsNone(instagram_profile._ensure_accessible(loader, profile))

    def test_profile_metadata_failures_keep_original_error_without_legacy_lookup(self):
        for error, expected in (
            (
                QueryReturnedNotFoundException(
                    "JSON Query to graphql/query: 404 Not Found"
                ),
                downloads_models.status_code.FAILED,
            ),
            (KeyError("user"), downloads_models.status_code.FAILED),
            (
                ConnectionException(
                    "JSON Query to graphql/query: 503 Service Unavailable"
                ),
                downloads_models.status_code.FAILED,
            ),
            (
                ConnectionException(
                    "JSON Query to graphql/query: 429 Too Many Requests"
                ),
                downloads_models.status_code.RATE_LIMITED,
            ),
            (
                ConnectionException(
                    "JSON Query to graphql/query: 401 Unauthorized - "
                    '"Please wait a few minutes before you try again."'
                ),
                downloads_models.status_code.RATE_LIMITED,
            ),
        ):
            with self.subTest(error=error):
                context = self._profile_page_context()
                context.doc_id_graphql_query.side_effect = error
                with patch.object(downloads_control, "delay"):
                    with self.assertRaises(type(error)) as caught:
                        instagram_profile.resolve_profile(
                            SimpleNamespace(context=context), "alice"
                        )
                self.assertIs(caught.exception, error)
                self.assertEqual(downloads_errors.handle_error(error), expected)
                context.get_page_data.assert_called_once_with("alice/")
                context.doc_id_graphql_query.assert_called_once()
                context.get_json.assert_not_called()
                context.get_iphone_json.assert_not_called()

    def test_empty_profile_metadata_does_not_retry_topsearch(self):
        context = self._profile_page_context()
        context.doc_id_graphql_query.return_value = None
        with patch.object(downloads_control, "delay"):
            with self.assertRaisesRegex(
                QueryReturnedNotFoundException, "GraphQL query returned None"
            ):
                instagram_profile.resolve_profile(
                    SimpleNamespace(context=context), "alice"
                )
        context.doc_id_graphql_query.assert_called_once()
        context.get_json.assert_not_called()
        context.get_iphone_json.assert_not_called()

    def test_profile_metadata_user_not_found_still_returns_none(self):
        for response in ({}, {"data": {}}, {"data": {"user": None}}):
            with self.subTest(response=response):
                context = self._profile_page_context()
                context.doc_id_graphql_query.return_value = response
                with patch.object(downloads_control, "delay"):
                    self.assertIsNone(
                        instagram_profile.resolve_profile(
                            SimpleNamespace(context=context), "alice"
                        )
                    )
                context.doc_id_graphql_query.assert_called_once()
                context.get_json.assert_not_called()
                context.get_iphone_json.assert_not_called()

    def test_profile_metadata_normalization_errors_do_not_retry_topsearch(self):
        context = self._profile_page_context()
        context.doc_id_graphql_query.return_value = {
            "data": {"user": {"pk": "12345", "username": "alice"}}
        }
        error = KeyError("friendship_status")
        with (
            patch.object(downloads_control, "delay"),
            patch.object(Profile, "_normalize_profile_data", side_effect=error),
        ):
            with self.assertRaises(KeyError) as caught:
                instagram_profile.resolve_profile(
                    SimpleNamespace(context=context), "alice"
                )
        self.assertIs(caught.exception, error)
        context.doc_id_graphql_query.assert_called_once()
        context.get_json.assert_not_called()
        context.get_iphone_json.assert_not_called()

    def test_post_detail_keeps_polaris_query_without_play_count_request(self):
        media = {
            "media_type": 2,
            "code": "HW80V",
            "pk": "123456789",
            "taken_at": 1_700_000_000,
            "user": {
                "pk": "12345",
                "username": "example_user",
                "full_name": "Example User",
            },
            "image_versions2": {
                "candidates": [{"url": "https://cdn.example/cover.jpg"}],
            },
            "video_versions": [{"url": "https://cdn.example/reel.mp4"}],
            "video_duration": 12.5,
            "view_count": 50,
            "play_count": 60,
            "caption": {"text": "caption"},
            "like_count": 7,
            "comment_count": 2,
            "has_liked": False,
        }
        # 4.15.4 also queried the clips connection for the play count of a
        # video without view count; 4.16 only does so when video_play_count is
        # read, which the app never does.
        for view_count in (50, None):
            with self.subTest(view_count=view_count):
                context = Mock(is_logged_in=True)
                context.doc_id_graphql_query.return_value = {
                    "data": {
                        "xdt_api__v1__media__shortcode__web_info": {
                            "items": [{**media, "view_count": view_count}],
                        },
                    },
                }

                post = Post.from_shortcode(context, "HW80V")

                # Logged in, the detail query stays on graphql/query.
                context.doc_id_graphql_query.assert_called_once_with(
                    "27128499623469141",
                    {
                        "shortcode": "HW80V",
                        "__relay_internal__pv__PolarisAIGMMediaWebLabelEnabledrelayprovider": False,
                    },
                    web_api=False,
                )
                self.assertEqual(post._node["__typename"], "GraphVideo")
                self.assertEqual(post._node["taken_at_timestamp"], 1_700_000_000)
                self.assertEqual(
                    post._node["display_url"], "https://cdn.example/cover.jpg"
                )
                self.assertEqual(
                    post._node["video_url"], "https://cdn.example/reel.mp4"
                )

    @staticmethod
    def _story_context(response=None, *, iphone_support=True, is_logged_in=True):
        context = Mock(iphone_support=iphone_support, is_logged_in=is_logged_in)
        context.get_iphone_json.return_value = response
        return context

    @staticmethod
    def _story(context, item_ids=("101",)):
        return Story(
            context,
            {
                "user": {"id": "12345", "username": "alice"},
                "items": [{"id": item_id} for item_id in item_ids],
            },
        )

    def test_story_iphone_reel_loader_matches_upstream_request_and_cache(self):
        media = {"pk": 101, "media_type": 1}
        response = {"reels": {"12345": {"items": [media]}}, "status": "ok"}
        context = self._story_context(response)
        story = self._story(context)

        self.assertFalse(instagram_media._load_story_iphone_reel(story))
        items = list(story.get_items())

        upstream_context = self._story_context(response)
        upstream_story = self._story(upstream_context)
        upstream_story._fetch_iphone_struct()
        self.assertEqual(
            context.get_iphone_json.call_args_list,
            upstream_context.get_iphone_json.call_args_list,
        )
        self.assertEqual(story._iphone_struct_, upstream_story._iphone_struct_)
        # Story.get_items() reuses the loaded reel instead of requesting again.
        context.get_iphone_json.assert_called_once()
        self.assertEqual(items[0]._iphone_struct_, media)

    def test_story_iphone_reel_loader_caches_absent_owner_as_empty_reel(self):
        response = {"reels": {}, "reels_media": [], "status": "ok"}
        upstream_context = self._story_context(response)
        # Unmerged PR #2789 would let upstream fall back to the lower-quality
        # Web items here; review the strict handling below if this changes.
        with self.assertRaises(KeyError) as caught:
            list(self._story(upstream_context).get_items())
        self.assertEqual(caught.exception.args, ("12345",))

        context = self._story_context(response)
        story = self._story(context, ("101", "102"))

        self.assertTrue(instagram_media._load_story_iphone_reel(story))
        items = list(story.get_items())

        self.assertEqual([item.mediaid for item in items], [102, 101])
        self.assertTrue(all(item._iphone_struct_ is None for item in items))
        self.assertEqual(instagram_media._story_iphone_media_ids(story), frozenset())
        self.assertEqual(
            context.get_iphone_json.call_args_list,
            upstream_context.get_iphone_json.call_args_list,
        )

    def test_story_iphone_reel_loader_keeps_malformed_reels_strict(self):
        for response, error in (
            ({"status": "ok"}, KeyError),
            ({"reels": []}, TypeError),
            ({"reels": {"12345": None}}, downloads_models.IPhoneQualityUnavailable),
            ({"reels": {"12345": {}}}, downloads_models.IPhoneQualityUnavailable),
        ):
            with self.subTest(response=response):
                context = self._story_context(response)
                story = self._story(context)
                with self.assertRaises(error):
                    instagram_media._load_story_iphone_reel(story)
                self.assertIsNone(story._iphone_struct_)
                context.get_iphone_json.assert_called_once()

        for iphone_support, is_logged_in in ((False, True), (True, False)):
            with self.subTest(iphone_support=iphone_support, is_logged_in=is_logged_in):
                context = self._story_context(
                    iphone_support=iphone_support, is_logged_in=is_logged_in
                )
                story = self._story(context)
                self.assertFalse(instagram_media._load_story_iphone_reel(story))
                self.assertIsNone(story._iphone_struct_)
                context.get_iphone_json.assert_not_called()

    def test_anonymous_profile_resolution_uses_public_page_without_metadata_query(self):
        context = Mock(is_logged_in=False)
        context.get_page_data.return_value = [
            {"xig_user_by_username": {"pk": "12345", "username": "alice"}}
        ]
        with patch.object(downloads_control, "delay") as delay:
            profile = instagram_profile.resolve_profile(
                SimpleNamespace(context=context), "Alice"
            )
        self.assertEqual(profile.userid, 12345)
        context.get_page_data.assert_called_once_with("alice/")
        delay.assert_called_once_with("request")
        context.doc_id_graphql_query.assert_not_called()
        context.get_json.assert_not_called()

    def test_profile_page_not_found_does_not_retry_topsearch(self):
        context = Mock(is_logged_in=True)
        context.get_page_data.side_effect = QueryReturnedNotFoundException(
            "404 Not Found"
        )
        with patch.object(downloads_control, "delay"):
            self.assertIsNone(
                instagram_profile.resolve_profile(
                    SimpleNamespace(context=context), "alice"
                )
            )
        context.get_page_data.assert_called_once_with("alice/")
        context.get_json.assert_not_called()
        context.doc_id_graphql_query.assert_not_called()

    def test_profile_page_must_match_requested_username(self):
        context = Mock(is_logged_in=True)
        context.get_page_data.return_value = [
            {"xig_user_by_username": {"pk": "12345", "username": "someone_else"}}
        ]
        with patch.object(downloads_control, "delay"):
            with self.assertRaises(ConnectionException):
                instagram_profile.resolve_profile(
                    SimpleNamespace(context=context), "alice"
                )
        context.get_json.assert_not_called()
        context.doc_id_graphql_query.assert_not_called()

    def test_profile_page_errors_preserve_limits_without_legacy_fallback(self):
        please_wait = json.dumps(
            {
                "message": "Please wait a few minutes before you try again.",
                "status": "fail",
            }
        )
        status = downloads_models.status_code
        for code, body, expected in (
            (429, "", status.RATE_LIMITED),
            (401, please_wait, status.RATE_LIMITED),
            (503, "", status.FAILED),
        ):
            with self.subTest(code=code):
                # 4.16 reports the page error as "... when accessing
                # https://www.instagram.com/...", which the classifier reads.
                context = _logged_in_context()
                page_get = Mock(return_value=_response(code, text=body))
                with (
                    patch.object(requests.Session, "get", page_get),
                    patch.object(downloads_control, "delay"),
                    patch.object(context, "doc_id_graphql_query") as metadata_query,
                    patch.object(context, "get_json") as get_json,
                ):
                    with self.assertRaises(ConnectionException) as caught:
                        instagram_profile.resolve_profile(
                            SimpleNamespace(context=context), "alice"
                        )
                self.assertIn(
                    "when accessing https://www.instagram.com/", str(caught.exception)
                )
                self.assertEqual(
                    downloads_errors.handle_error(caught.exception), expected
                )
                page_get.assert_called_once()
                metadata_query.assert_not_called()
                get_json.assert_not_called()

    def test_profile_page_login_redirect_stays_an_ordinary_page_failure(self):
        # The public page is requested anonymously, but 4.16 reports its
        # redirect to the login page as the logged-in session being logged out.
        context = _logged_in_context()
        login = "https://www.instagram.com/accounts/login/?next=%2Falice%2F"
        page_get = Mock(return_value=_response(302, location=login))
        with (
            patch.object(requests.Session, "get", page_get),
            patch.object(downloads_control, "delay"),
            patch.object(context, "doc_id_graphql_query") as metadata_query,
        ):
            with self.assertRaises(ConnectionException) as caught:
                instagram_profile.resolve_profile(
                    SimpleNamespace(context=context), "alice"
                )

        upstream_error = caught.exception.__cause__
        self.assertIsInstance(upstream_error, AbortDownloadException)
        self.assertIn("logged out", str(upstream_error))
        self.assertEqual(
            downloads_errors.handle_error(upstream_error),
            downloads_models.status_code.AUTHORIZATION_REQUIRED,
        )
        self.assertEqual(
            downloads_errors.handle_error(caught.exception),
            downloads_models.status_code.FAILED,
        )
        self.assertIsNone(diagnostics_risk._risk_incident_kind(None, caught.exception))
        page_get.assert_called_once()
        metadata_query.assert_not_called()

    def test_session_validation_preserves_swallowed_api_429(self):
        context = SimpleNamespace(error_log=[])

        def test_login():
            context.error_log.append(
                "Error when checking if logged in: Query to "
                "https://www.instagram.com/graphql/query responded with "
                '"429 Too Many Requests"'
            )
            return None

        loader = SimpleNamespace(context=context, test_login=test_login)

        self.assertEqual(
            instagram_session._validate_session(loader),
            downloads_models.status_code.RATE_LIMITED,
        )

    def test_session_validation_does_not_report_service_failure_as_auth(self):
        context = SimpleNamespace(error_log=[])

        def test_login():
            context.error_log.append(
                "Error when checking if logged in: JSON Query to "
                "graphql/query: 503 Service Unavailable"
            )
            return None

        loader = SimpleNamespace(context=context, test_login=test_login)

        self.assertEqual(
            instagram_session._validate_session(loader),
            downloads_models.status_code.FAILED,
        )

    def test_session_validation_preserves_swallowed_api_401_as_auth(self):
        context = SimpleNamespace(error_log=[])

        def test_login():
            context.error_log.append(
                "Error when checking if logged in: JSON Query to "
                "graphql/query: 401 Unauthorized"
            )
            return None

        loader = SimpleNamespace(context=context, test_login=test_login)

        self.assertEqual(
            instagram_session._validate_session(loader),
            downloads_models.status_code.AUTHORIZATION_REQUIRED,
        )

    def test_session_validation_treats_401_please_wait_as_rate_limit(self):
        context = SimpleNamespace(error_log=[])

        def test_login():
            context.error_log.append(
                "Error when checking if logged in: JSON Query to "
                'graphql/query: 401 Unauthorized - "fail" status, message '
                '"Please wait a few minutes before you try again."'
            )
            return None

        loader = SimpleNamespace(context=context, test_login=test_login)

        self.assertEqual(
            instagram_session._validate_session(loader),
            downloads_models.status_code.RATE_LIMITED,
        )

    def test_session_validation_does_not_treat_api_401_server_error_as_auth(self):
        context = SimpleNamespace(error_log=[])

        def test_login():
            context.error_log.append(
                "Error when checking if logged in: JSON Query to "
                "graphql/query: 401 Server Error"
            )
            return None

        loader = SimpleNamespace(context=context, test_login=test_login)

        self.assertEqual(
            instagram_session._validate_session(loader),
            downloads_models.status_code.FAILED,
        )

    def test_rate_controller_sleep_observes_app_stop(self):
        controller = instagram_session._CooperativeRateController(Mock())
        with patch.object(
            downloads_control,
            "_raise_if_stop_requested",
            side_effect=downloads_models.DownloadStopRequested,
        ):
            with self.assertRaises(downloads_models.DownloadStopRequested):
                controller.sleep(666)

    def test_loader_uses_cooperative_rate_controller(self):
        loader = instagram_session.init_base_loader()
        try:
            self.assertIsInstance(
                loader.context._rate_controller,
                instagram_session._CooperativeRateController,
            )
            self.assertEqual(loader.context.fatal_status_codes, [429])
        finally:
            loader.close()


class IPhoneBearerTokenTests(unittest.TestCase):
    """4.16 derives the iPhone API bearer token from the session cookies."""

    _PATH = "api/v1/feed/reels_media/?reel_ids=12345"

    def test_app_loader_sends_the_derived_token_instead_of_cookies(self):
        loader = instagram_session.init_base_loader()
        try:
            loader.context.load_session("viewer", dict(_COOKIES))
            get_json = _RecordingGetJson([{"reels": {}, "status": "ok"}])
            with patch.object(loader.context, "get_json", get_json):
                response = loader.context.get_iphone_json(path=self._PATH, params={})

            self.assertEqual(response, {"reels": {}, "status": "ok"})
            sent = get_json.calls[0]
            self.assertEqual((sent.path, sent.host), (self._PATH, "i.instagram.com"))
            self.assertEqual(sent.headers["authorization"], _PR_2771_TOKEN)
            self.assertEqual(sent.headers["ig-intended-user-id"], "12345")
            # A bearer token replaces the cookies, and the derived value is
            # never stored.
            self.assertEqual(sent.cookies, {})
            self.assertNotIn("authorization", loader.context.iphone_headers)
            # Derived from the ds_user_id cookie; the app does not assign it.
            self.assertEqual(loader.context.user_id, "12345")
        finally:
            loader.close()

    def test_server_issued_token_takes_precedence_and_is_kept(self):
        context = _logged_in_context()
        issued = "Bearer IGT:2:server-issued"
        get_json = _RecordingGetJson(response_headers={"ig-set-authorization": issued})

        with patch.object(context, "get_json", get_json):
            context.get_iphone_json(path=self._PATH, params={})
            context.get_iphone_json(path=self._PATH, params={})

        self.assertEqual(
            [sent.headers["authorization"] for sent in get_json.calls],
            [_PR_2771_TOKEN, issued],
        )
        self.assertEqual(context.iphone_headers["authorization"], issued)


class WebApiPostsTests(unittest.TestCase):
    """4.16 ``Profile.get_posts()`` when logged in, as the app iterates it."""

    def test_logged_in_posts_use_web_api_query_with_start_page_tokens(self):
        profile = _logged_in_profile(_logged_in_context())
        context = profile._context
        first = _media("video1", "101", 1784776879)
        second = _media("video2", "102", 1784776800)
        get_json = _RecordingGetJson(
            [_timeline_page([first], "page-2"), _timeline_page([second])]
        )
        start_page = Mock(return_value=_response(text=_START_PAGE))
        get_iphone_json = Mock(side_effect=AssertionError("unexpected iPhone query"))

        with (
            patch.object(context._session, "get", start_page),
            patch.object(context, "get_json", get_json),
            patch.object(context, "get_iphone_json", get_iphone_json),
        ):
            posts = list(instagram_reels._ordinary_post_iterator(profile, "posts"))
            segments = instagram_media._resolve_post_segments_strict_iphone(
                SimpleNamespace(context=context), posts[0]
            )

        self.assertEqual([post.shortcode for post in posts], ["video1", "video2"])
        start_page.assert_called_once_with(
            "https://www.instagram.com/",
            headers={
                "Accept": "text/html",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
            },
            allow_redirects=False,
        )
        self.assertEqual(len(get_json.calls), 2)
        for sent, extra in zip(
            get_json.calls,
            ({}, {"after": "page-2", "before": None, "first": 12, "last": None}),
        ):
            self.assertEqual(
                (sent.path, sent.host, sent.use_post),
                ("api/graphql", "www.instagram.com", True),
            )
            self.assertEqual(
                json.loads(sent.params.pop("variables")),
                {**_POSTS_VARIABLES, **extra},
            )
            self.assertEqual(
                sent.params,
                {
                    "doc_id": "29104886249145907",
                    "server_timestamps": "true",
                    "fb_dtsg": _FB_DTSG,
                    "lsd": _LSD,
                    "av": "17841400000000000",
                },
            )
            self.assertEqual(sent.headers["x-csrftoken"], "csrf-token")
            self.assertEqual(sent.headers["x-fb-lsd"], _LSD)
            self.assertEqual(
                sent.headers["referer"], "https%3A//www.instagram.com/alice/"
            )
            self.assertEqual(
                (
                    sent.headers["accept"],
                    sent.headers["sec-fetch-dest"],
                    sent.headers["sec-fetch-mode"],
                    sent.headers["sec-fetch-site"],
                ),
                ("*/*", "empty", "cors", "same-origin"),
            )
        self.assertEqual(
            segments,
            [
                {
                    "url": "https://cdn.example/high.mp4",
                    "kind": "video",
                    "source": "iphone",
                }
            ],
        )

    def test_failed_posts_queries_retry_as_post_without_tokens_in_urls(self):
        # 4.15.4's get_json() re-sent the POST form as GET query arguments when
        # retrying or following a redirect, which is why the former T2 sent
        # only once. 4.16 retries the POST itself and follows redirects without
        # the form, so the start page tokens never reach a URL or an error.
        challenge = "https://www.instagram.com/challenge/"

        def follow(url, params=None, allow_redirects=True):
            response = _response(403)
            response.url = requests.Request("GET", url, params=params).prepare().url
            return response

        for name, posted, follows in (
            ("server_error", _response(500), 0),
            ("redirect", _response(302, location=challenge), 3),
        ):
            with self.subTest(name):
                profile = _logged_in_profile(_logged_in_context())
                post = Mock(return_value=posted)
                get = Mock(side_effect=follow)
                with (
                    patch.object(
                        profile._context._session,
                        "get",
                        Mock(return_value=_response(text=_START_PAGE)),
                    ),
                    patch.object(requests.Session, "post", post),
                    patch.object(requests.Session, "get", get),
                    contextlib.redirect_stderr(io.StringIO()),
                ):
                    with self.assertRaises(ConnectionException) as caught:
                        instagram_reels._ordinary_post_iterator(profile, "posts")

                # Up to max_connection_attempts, as the app's loader also uses.
                self.assertEqual(post.call_count, 3)
                self.assertEqual(get.call_count, follows)
                for sent in get.call_args_list:
                    self.assertIsNone(sent.kwargs.get("params"))
                message = str(caught.exception)
                for token in (_FB_DTSG, _LSD):
                    self.assertNotIn(token, message)
                    self.assertNotIn(urllib.parse.quote_plus(token), message)

    def test_start_page_tokens_are_reused_while_the_session_stays(self):
        # The former T2 read the start page again after an hour; 4.16 keeps its
        # tokens for as long as the sessionid cookie is unchanged.
        context = _logged_in_context()
        start_page = Mock(return_value=_response(text=_START_PAGE))

        def open_first_page():
            with patch.object(
                context, "get_json", _RecordingGetJson([_timeline_page([])])
            ):
                instagram_reels._ordinary_post_iterator(
                    _logged_in_profile(context), "posts"
                )

        with patch.object(context._session, "get", start_page):
            open_first_page()
            open_first_page()
            self.assertEqual(start_page.call_count, 1)

            context._session.cookies.set("sessionid", "67890%3Aother")
            open_first_page()
            self.assertEqual(start_page.call_count, 2)

    def test_start_page_failures_raise_before_querying(self):
        login = "https://www.instagram.com/accounts/login/?next=%2F"
        please_wait = json.dumps(
            {
                "message": "Please wait a few minutes before you try again.",
                "status": "fail",
            }
        )
        status = downloads_models.status_code
        for name, outcome, error, expected in (
            (
                "missing_token",
                _response(text=_START_PAGE.replace("LSD", "X")),
                BadResponseException,
                status.FAILED,
            ),
            (
                "login_redirect",
                _response(302, location=login),
                AbortDownloadException,
                status.AUTHORIZATION_REQUIRED,
            ),
            (
                "not_found",
                _response(404),
                QueryReturnedNotFoundException,
                status.FAILED,
            ),
            ("server_error", _response(500), ConnectionException, status.FAILED),
            (
                "too_many_requests",
                _response(429),
                ConnectionException,
                status.RATE_LIMITED,
            ),
            (
                "please_wait",
                _response(401, text=please_wait),
                ConnectionException,
                status.RATE_LIMITED,
            ),
            (
                "network",
                requests.exceptions.ConnectionError("offline"),
                ConnectionException,
                status.FAILED,
            ),
        ):
            with self.subTest(name):
                context = _logged_in_context()
                start_page = Mock(
                    side_effect=outcome if isinstance(outcome, Exception) else None,
                    return_value=outcome,
                )
                get_json = _RecordingGetJson()
                with (
                    patch.object(context._session, "get", start_page),
                    patch.object(context, "get_json", get_json),
                ):
                    with self.assertRaises(error) as caught:
                        instagram_reels._ordinary_post_iterator(
                            _logged_in_profile(context), "posts"
                        )
                    # Nothing is cached: the next attempt reads the page again.
                    with self.assertRaises(error):
                        instagram_reels._ordinary_post_iterator(
                            _logged_in_profile(context), "posts"
                        )
                self.assertEqual(start_page.call_count, 2)
                self.assertEqual(get_json.calls, [])
                self.assertEqual(
                    downloads_errors.handle_error(caught.exception), expected
                )
                if name == "missing_token":
                    self.assertIn("token lsd", str(caught.exception))
                elif name == "network":
                    self.assertIn(
                        "Page request to https://www.instagram.com/",
                        str(caught.exception),
                    )

    def test_deferred_open_resumes_cursors_of_the_same_query(self):
        profile = _logged_in_profile(_logged_in_context())
        context = profile._context
        get_json = _RecordingGetJson(
            [
                _timeline_page(
                    [
                        _media("video1", "101", 1784776879),
                        _media("video2", "102", 1784776800),
                    ],
                    "page-2",
                )
            ]
        )
        with (
            patch.object(
                context._session, "get", Mock(return_value=_response(text=_START_PAGE))
            ) as start_page,
            patch.object(context, "get_json", get_json),
        ):
            source = instagram_reels._ordinary_post_iterator(profile, "posts")
            self.assertEqual(next(source).shortcode, "video1")
            frozen = source.freeze()

            resumed = instagram_reels._ordinary_post_iterator(
                profile, "posts", defer_initial_query=True
            )
            resumed.thaw(frozen)

            # Deferred construction sends neither the page nor the posts query.
            self.assertEqual(start_page.call_count, 1)
            self.assertEqual(len(get_json.calls), 1)
            self.assertEqual(next(resumed).shortcode, "video1")
        self.assertEqual(frozen.doc_id, "29104886249145907")
        self.assertEqual(frozen.query_variables, _POSTS_VARIABLES)

        # A cursor saved while the former T2 sent this query still resumes. One
        # of the query that 4.15.4 sent no longer matches; checkpoints.py then
        # discards it and opens a fresh first page.
        saved_under_t2 = FrozenNodeIterator(
            query_hash=None,
            query_variables=dict(_POSTS_VARIABLES),
            query_referer="https://www.instagram.com/alice/",
            context_username="viewer",
            total_index=1,
            best_before=time.time() + 3600,
            remaining_data=_timeline_page([_media("video2", "102", 1784776800)])[
                "data"
            ][_TIMELINE],
            first_node=_media("video1", "101", 1784776879),
            doc_id="29104886249145907",
        )
        with patch.object(
            context, "get_json", Mock(side_effect=AssertionError("unexpected query"))
        ):
            resumed = instagram_reels._ordinary_post_iterator(
                _logged_in_profile(context), "posts", defer_initial_query=True
            )
            resumed.thaw(saved_under_t2)
            self.assertEqual([post.shortcode for post in resumed], ["video2"])

            fresh = instagram_reels._ordinary_post_iterator(
                _logged_in_profile(context), "posts", defer_initial_query=True
            )
            with self.assertRaisesRegex(InvalidArgumentException, "Mismatching"):
                fresh.thaw(saved_under_t2._replace(doc_id="28975909992013618"))


if __name__ == "__main__":
    unittest.main()
