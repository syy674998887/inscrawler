"""Offline identity, API-boundary, and native-quality regression tests."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests
from instaloader import InstaloaderContext, Profile
from instaloader.exceptions import (
    AbortDownloadException,
    BadResponseException,
    ConnectionException,
    InvalidArgumentException,
    LoginRequiredException,
)

import inscrawler.downloads.control as downloads_control
import inscrawler.downloads.errors as downloads_errors
import inscrawler.downloads.models as downloads_models
import inscrawler.downloads.runtime as downloads_runtime
import inscrawler.instagram.hotfixes as hotfixes
import inscrawler.instagram.media as instagram_media
import inscrawler.instagram.profile as instagram_profile
import inscrawler.instagram.session as instagram_session
import inscrawler.paths as app_paths


def _web_media(pk=101, media_type=1):
    return {
        "pk": pk,
        "media_type": media_type,
        "taken_at": 1793462400 + pk,
        "expiring_at": 1793548800 + pk,
        "caption": {"text": "Example"},
        "image_versions2": {
            "candidates": [
                {"url": "https://cdn.example/web.jpg", "width": 640, "height": 960}
            ]
        },
        "video_versions": (
            [{"url": "https://cdn.example/web.mp4", "width": 640, "height": 960}]
            if media_type == 2
            else None
        ),
    }


def _reel_response(items):
    return {
        "data": {
            "xdt_api__v1__feed__reels_media": {
                "reels_media": [
                    {
                        "id": "7",
                        "user": {"pk": "7", "username": "target"},
                        "seen": 0,
                        "latest_reel_media": 1793462502,
                        "items": items,
                    }
                ]
            }
        }
    }


def _profile_response():
    return {
        "data": {
            "user": {
                "pk": "7",
                "username": "target",
                "is_private": False,
                "media_count": 0,
                "friendship_status": {"following": False},
                "has_blocked_viewer": False,
            }
        }
    }


class InstagramHotfixTests(unittest.TestCase):
    def setUp(self):
        network = patch(
            "requests.Session.send", side_effect=AssertionError("unexpected HTTP")
        )
        network.start()
        self.addCleanup(network.stop)
        self.loader = instagram_session.init_base_loader()
        self.addCleanup(self.loader.close)
        self.loader.load_session(
            "viewer", {"ds_user_id": "42", "sessionid": "sid", "csrftoken": "csrf"}
        )
        self.context = self.loader.context
        self.context.iphone_support = True
        self.context._web_api_tokens_cache["sid"] = {
            "fb_dtsg": "dtsg",
            "lsd": "lsd",
            "av": "42",
        }
        self.profile = Profile(self.context, {"id": "7", "username": "target"})

    def test_t3_sentinel_upstream_416_still_uses_rejected_login_query(self):
        context = SimpleNamespace(
            graphql_query=Mock(return_value={"data": {"user": {"username": "v"}}})
        )
        self.assertEqual(InstaloaderContext.test_login(context), "v")
        context.graphql_query.assert_called_once_with(
            "d6f4427fbe92d846298cf93df0b937d3", {}
        )

    def test_t4_sentinel_upstream_416_still_uses_rejected_story_query(self):
        with patch.object(
            self.context, "graphql_query", return_value={"data": {"reels_media": []}}
        ) as query:
            self.assertEqual(list(self.loader.get_stories(userids=[7])), [])
        query.assert_called_once_with(
            "303a4ae99711322310f25250d988f3b7",
            {"reel_ids": [7], "precomposed_overlay": False},
        )

    def test_version_change_requires_explicit_hotfix_review(self):
        with patch.object(hotfixes, "INSTALOADER_VERSION", "4.17"):
            with self.assertRaises(InvalidArgumentException):
                hotfixes.install_login_check(self.loader)
            with self.assertRaises(InvalidArgumentException):
                hotfixes.get_target_stories(self.loader, self.profile)
            with self.assertRaises(InvalidArgumentException):
                hotfixes.query_profile_metadata(self.context, {"id": "7"})

    def test_t5_sentinel_upstream_416_still_uses_legacy_profile_endpoint(self):
        with patch.object(
            self.context, "doc_id_graphql_query", return_value=_profile_response()
        ) as query:
            self.profile._obtain_metadata()
        self.assertEqual(query.call_args.args[0], "27937681195819736")
        self.assertFalse(query.call_args.kwargs.get("web_api", False))

    def test_profile_web_request_preserves_identity_and_access_checks(self):
        response = _profile_response()
        response["data"]["user"]["is_private"] = True
        with (
            patch.object(self.context, "get_json", return_value=response) as request,
            patch.object(self.context, "graphql_query") as legacy,
            patch.object(self.context, "get_iphone_json") as iphone,
        ):
            instagram_profile._load_logged_in_metadata(self.context, self.profile)
            status = instagram_profile._ensure_accessible(self.loader, self.profile)
        request.assert_called_once()
        self.assertEqual(request.call_args.args, ("api/graphql",))
        sent = request.call_args.kwargs
        self.assertTrue(sent["use_post"])
        self.assertEqual(sent["params"]["doc_id"], "27937681195819736")
        self.assertEqual(
            {key: sent["params"][key] for key in ("fb_dtsg", "lsd", "av")},
            {"fb_dtsg": "dtsg", "lsd": "lsd", "av": "42"},
        )
        self.assertEqual(json.loads(sent["params"]["variables"])["id"], "7")
        self.assertEqual(sent["session"].headers["x-csrftoken"], "csrf")
        self.assertEqual(sent["session"].headers["x-fb-lsd"], "lsd")
        self.assertEqual(self.profile.userid, 7)
        self.assertTrue(self.profile._has_full_metadata)
        self.assertEqual(status, downloads_models.status_code.FAILED)
        legacy.assert_not_called()
        iphone.assert_not_called()

    def test_profile_cold_tokens_keep_request_gap_and_are_shared_with_story_posts(self):
        self.context._web_api_tokens_cache.clear()
        html = '"DTSGInitialData",[],{"token":"dtsg"} "LSD",[],{"token":"lsd"} "IG_USER_EIMU":"42"'
        events = []
        responses = iter(
            [
                _profile_response(),
                {"data": {"xdt_api__v1__feed__reels_media": {"reels_media": []}}},
                {
                    "data": {
                        "xdt_api__v1__feed__user_timeline_graphql_connection": {
                            "edges": [],
                            "page_info": {"has_next_page": False, "end_cursor": None},
                        }
                    }
                },
                _profile_response(),
            ]
        )

        def homepage(*_args):
            events.append("homepage")
            return html

        def query(path, **kwargs):
            self.assertEqual(path, "api/graphql")
            self.assertTrue(kwargs["use_post"])
            events.append(kwargs["params"]["doc_id"])
            return next(responses)

        with (
            patch.object(self.context, "_get_page", side_effect=homepage) as page,
            patch.object(
                self.context,
                "get_page_data",
                return_value=[
                    {"xig_user_by_username": {"pk": "7", "username": "target"}}
                ],
            ),
            patch.object(self.context, "get_json", side_effect=query) as request,
            patch.object(
                downloads_control,
                "delay",
                side_effect=lambda _kind: events.append("wait"),
            ),
        ):
            profile = instagram_profile.resolve_profile(self.loader, "target")
            self.assertEqual(hotfixes.get_target_stories(self.loader, profile), [])
            self.assertEqual(list(profile.get_posts()), [])
            instagram_profile.resolve_profile(self.loader, "target")
        page.assert_called_once()
        self.assertEqual(request.call_count, 4)
        self.assertEqual(
            events,
            [
                "wait",
                "wait",
                "homepage",
                "wait",
                "27937681195819736",
                "29170422829314651",
                "29104886249145907",
                "wait",
                "wait",
                "27937681195819736",
            ],
        )

    def test_profile_token_failure_never_sends_metadata_or_fallback_queries(self):
        status = downloads_models.status_code
        for code, error, expected in (
            (200, BadResponseException, status.FAILED),
            (302, AbortDownloadException, status.AUTHORIZATION_REQUIRED),
            (429, ConnectionException, status.RATE_LIMITED),
            (401, ConnectionException, status.RATE_LIMITED),
        ):
            with self.subTest(code=code):
                self.context._web_api_tokens_cache.clear()
                response = requests.Response()
                response.url = "https://www.instagram.com/"
                response.status_code = code
                response.reason = "OK" if code == 200 else "Error"
                response._content = b'{"status":"fail","message":"Please wait a few minutes before you try again."}'
                if code == 302:
                    response.headers["location"] = (
                        "https://www.instagram.com/accounts/login/"
                    )
                with (
                    patch.object(
                        self.context._session, "get", return_value=response
                    ) as page,
                    patch.object(self.context, "do_sleep"),
                    patch.object(self.context, "doc_id_graphql_query") as query,
                    patch.object(downloads_control, "delay") as delay,
                ):
                    with self.assertRaises(error) as caught:
                        instagram_profile._load_logged_in_metadata(
                            self.context, self.profile
                        )
                self.assertEqual(
                    downloads_errors.handle_error(caught.exception), expected
                )
                self.assertFalse(self.profile._has_full_metadata)
                self.assertEqual(self.context._web_api_tokens_cache, {})
                page.assert_called_once()
                query.assert_not_called()
                delay.assert_not_called()

    def test_profile_api_rejection_keeps_stop_classification_without_legacy_retry(self):
        for message, expected in (
            (
                "400 Bad Request: feedback_required",
                downloads_models.status_code.AUTHORIZATION_REQUIRED,
            ),
            ("429 Too Many Requests", downloads_models.status_code.RATE_LIMITED),
        ):
            error = AbortDownloadException("JSON Query to api/graphql: " + message)
            with self.subTest(message=message):
                with (
                    patch.object(self.context, "get_json", side_effect=error) as query,
                    patch.object(self.context, "graphql_query") as legacy,
                ):
                    with self.assertRaises(AbortDownloadException) as caught:
                        instagram_profile._load_logged_in_metadata(
                            self.context, self.profile
                        )
                self.assertIs(caught.exception, error)
                self.assertEqual(downloads_errors.handle_error(error), expected)
                self.assertFalse(self.profile._has_full_metadata)
                query.assert_called_once()
                legacy.assert_not_called()

    def test_profile_stop_after_token_load_prevents_metadata_request(self):
        self.context._web_api_tokens_cache.clear()
        with (
            patch.object(self.context, "_web_api_tokens") as tokens,
            patch.object(
                downloads_control,
                "delay",
                side_effect=downloads_models.DownloadStopRequested,
            ),
            patch.object(self.context, "doc_id_graphql_query") as query,
        ):
            with self.assertRaises(downloads_models.DownloadStopRequested):
                instagram_profile._load_logged_in_metadata(self.context, self.profile)
        tokens.assert_called_once()
        query.assert_not_called()
        self.assertFalse(self.profile._has_full_metadata)

    def test_profile_hotfix_rejects_anonymous_context_before_loading_tokens(self):
        self.context.username = None
        with patch.object(self.context, "_web_api_tokens") as tokens:
            with self.assertRaises(LoginRequiredException):
                hotfixes.query_profile_metadata(self.context, {"id": "7"})
        tokens.assert_not_called()

    def test_login_uses_real_identity_and_scopes_cookies_without_changing_values(self):
        cookies_before = self.context._session.cookies.get_dict()
        with (
            patch.object(
                self.context,
                "get_iphone_json",
                return_value={"status": "ok", "user": {"pk": 42, "username": "Viewer"}},
            ) as identity,
            patch.object(self.context, "graphql_query") as legacy,
        ):
            self.assertEqual(self.loader.test_login(), "Viewer")
        identity.assert_called_once_with(
            path="api/v1/accounts/current_user/", params={"edit": "true"}
        )
        legacy.assert_not_called()
        self.assertEqual(self.context._session.cookies.get_dict(), cookies_before)
        self.assertTrue(
            all(
                cookie.domain == ".instagram.com"
                for cookie in self.context._session.cookies
            )
        )

    def test_wrong_or_missing_identity_never_authenticates(self):
        for response, error in (
            (
                {"status": "ok", "user": {"pk": 43, "username": "viewer"}},
                LoginRequiredException,
            ),
            (
                {"status": "ok", "user": {"pk": True, "username": "viewer"}},
                BadResponseException,
            ),
            ({"status": "ok", "user": {"pk": 42}}, BadResponseException),
            ({"status": "ok", "user": None}, BadResponseException),
            (
                {"status": "fail", "user": {"pk": 42, "username": "viewer"}},
                BadResponseException,
            ),
        ):
            with self.subTest(response=response):
                with patch.object(
                    self.context, "get_iphone_json", return_value=response
                ):
                    with self.assertRaises(error):
                        self.loader.test_login()

    def test_login_keeps_auth_rate_limit_and_network_failures_distinct(self):
        cases = (
            (
                AbortDownloadException("400 Bad Request: feedback_required"),
                downloads_models.status_code.AUTHORIZATION_REQUIRED,
            ),
            (
                AbortDownloadException(
                    "JSON Query to api/v1/accounts/current_user/: 429 Too Many Requests"
                ),
                downloads_models.status_code.RATE_LIMITED,
            ),
            (
                ConnectionException(
                    "JSON Query to api/v1/accounts/current_user/: 503 Service Unavailable"
                ),
                downloads_models.status_code.FAILED,
            ),
        )
        for error, expected in cases:
            with self.subTest(error=type(error).__name__, expected=expected):
                with (
                    patch.object(
                        self.context, "get_iphone_json", side_effect=error
                    ) as query,
                    patch.object(self.context, "graphql_query") as legacy,
                ):
                    self.assertEqual(
                        instagram_session._validate_session(self.loader), expected
                    )
                query.assert_called_once()
                legacy.assert_not_called()

    def test_cookie_seed_saves_only_after_both_identity_checks_pass(self):
        for returned_username, expected_ok in (
            ("Viewer", True),
            ("someone_else", False),
        ):
            with (
                self.subTest(username=returned_username),
                tempfile.TemporaryDirectory() as folder,
            ):
                with (
                    patch.object(app_paths, "SESSION_DIR", folder),
                    patch.object(
                        downloads_runtime.runtime,
                        "session_cookie_text",
                        "sessionid=sid; ds_user_id=42; csrftoken=csrf",
                    ),
                    patch.object(
                        self.context,
                        "get_iphone_json",
                        return_value={
                            "status": "ok",
                            "user": {"pk": 42, "username": returned_username},
                        },
                    ),
                    patch.object(self.loader, "save_session_to_file") as save,
                ):
                    ok, _status, _message = (
                        instagram_session._seed_session_from_cookie_text(
                            self.loader, "viewer"
                        )
                    )
                self.assertEqual(ok, expected_ok)
                self.assertEqual(save.call_count, int(expected_ok))

    def test_story_new_query_preserves_order_dates_and_separate_native_quality(self):
        web_items = [_web_media(), _web_media(102, 2)]
        native_items = copy.deepcopy(web_items)
        native_items[0]["image_versions2"]["candidates"][0]["url"] = (
            "https://cdn.example/native.jpg"
        )
        native_items[1]["video_versions"] = [
            {"url": "https://cdn.example/native-low.mp4", "width": 640, "height": 960},
            {
                "url": "https://cdn.example/native-high.mp4",
                "width": 1080,
                "height": 1920,
            },
        ]
        with (
            patch.object(
                self.context,
                "doc_id_graphql_query",
                return_value=_reel_response(web_items),
            ) as web,
            patch.object(
                self.context,
                "get_iphone_json",
                return_value={"reels": {"7": {"items": native_items}}},
            ) as native,
            patch.object(self.context, "graphql_query") as legacy,
        ):
            stories = hotfixes.get_target_stories(self.loader, self.profile)
            self.assertEqual(len(stories), 1)
            self.assertIsNone(stories[0]._iphone_struct_)
            self.assertTrue(
                all("iphone_struct" not in node for node in stories[0]._node["items"])
            )
            items = list(stories[0].get_items())
            self.assertEqual([item.mediaid for item in items], [102, 101])
            self.assertEqual([item.owner_id for item in items], [7, 7])
            self.assertEqual(
                items[0]._node["expiring_at_timestamp"], web_items[1]["expiring_at"]
            )
            segments = [
                instagram_media._resolve_storyitem_segment_strict_iphone(
                    self.loader, item
                )
                for item in items
            ]
        self.assertEqual(
            [segment["source"] for segment in segments], ["iphone", "iphone"]
        )
        self.assertEqual(
            [segment["url"] for segment in segments],
            ["https://cdn.example/native-high.mp4", "https://cdn.example/native.jpg"],
        )
        web.assert_called_once_with(
            "29170422829314651",
            {
                "reel_ids_arr": ["7"],
                "__relay_internal__pv__PolarisCommunityNoteStoriesLabelEnabledrelayprovider": True,
            },
            web_api=True,
        )
        native.assert_called_once_with(
            path="api/v1/feed/reels_media/?reel_ids=7", params={}
        )
        legacy.assert_not_called()

    def test_story_cold_tokens_wait_once_and_reuse_posts_cache(self):
        self.context._web_api_tokens_cache.clear()
        html = '"DTSGInitialData",[],{"token":"dtsg"} "LSD",[],{"token":"lsd"} "IG_USER_EIMU":"42"'
        with (
            patch.object(self.context, "_get_page", return_value=html) as page,
            patch.object(
                self.context, "doc_id_graphql_query", return_value=_reel_response([])
            ) as query,
            patch.object(downloads_control, "delay") as delay,
        ):
            hotfixes.get_target_stories(self.loader, self.profile)
            hotfixes.get_target_stories(self.loader, self.profile)
        page.assert_called_once()
        delay.assert_called_once_with("request")
        self.assertEqual(query.call_count, 2)

    def test_story_token_failure_does_not_query_or_fallback(self):
        self.context._web_api_tokens_cache.clear()
        with (
            patch.object(
                self.context,
                "_get_page",
                side_effect=BadResponseException("missing tokens"),
            ),
            patch.object(self.context, "doc_id_graphql_query") as query,
            patch.object(self.context, "graphql_query") as legacy,
        ):
            with self.assertRaises(BadResponseException):
                hotfixes.get_target_stories(self.loader, self.profile)
        query.assert_not_called()
        legacy.assert_not_called()

    def test_story_native_failure_never_uses_web_media_as_iphone(self):
        with (
            patch.object(
                self.context,
                "doc_id_graphql_query",
                return_value=_reel_response([_web_media()]),
            ),
            patch.object(
                self.context,
                "get_iphone_json",
                side_effect=AbortDownloadException("feedback_required"),
            ) as native,
        ):
            story = hotfixes.get_target_stories(self.loader, self.profile)[0]
            with self.assertRaises(AbortDownloadException):
                list(story.get_items())
        native.assert_called_once()
        self.assertIsNone(story._iphone_struct_)

    def test_story_empty_and_malformed_reels_are_distinguished(self):
        empty = {"data": {"xdt_api__v1__feed__reels_media": {"reels_media": []}}}
        with patch.object(self.context, "doc_id_graphql_query", return_value=empty):
            self.assertEqual(hotfixes.get_target_stories(self.loader, self.profile), [])
        wrong_owner = _reel_response([_web_media()])
        wrong_owner["data"]["xdt_api__v1__feed__reels_media"]["reels_media"][0][
            "id"
        ] = "8"
        bad_item = _reel_response([{"pk": True}])
        for response in (
            {},
            {"data": {"xdt_api__v1__feed__reels_media": None}},
            wrong_owner,
            bad_item,
        ):
            with self.subTest(response=response):
                with patch.object(
                    self.context, "doc_id_graphql_query", return_value=response
                ):
                    with self.assertRaises(BadResponseException):
                        hotfixes.get_target_stories(self.loader, self.profile)

    def test_story_web_rejection_does_not_retry_legacy_query(self):
        with (
            patch.object(
                self.context,
                "doc_id_graphql_query",
                side_effect=AbortDownloadException("feedback_required"),
            ) as web,
            patch.object(self.context, "graphql_query") as legacy,
            patch.object(self.context, "get_iphone_json") as native,
        ):
            with self.assertRaises(AbortDownloadException):
                hotfixes.get_target_stories(self.loader, self.profile)
        web.assert_called_once()
        legacy.assert_not_called()
        native.assert_not_called()


if __name__ == "__main__":
    unittest.main()
