"""Offline tests for item-level download failure propagation."""

from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime
from http.client import IncompleteRead
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from instaloader import (
    Highlight,
    NodeIterator,
    Post,
    Profile,
    Story,
    StoryItem,
)
from instaloader.exceptions import (
    AbortDownloadException,
    ConnectionException,
    LoginRequiredException,
    PostChangedException,
    QueryReturnedForbiddenException,
    TooManyRequestsException,
)
from urllib3.exceptions import (
    ProtocolError as Urllib3ProtocolError,
)

import inscrawler.downloads.avatar as downloads_avatar
import inscrawler.downloads.checkpoints as downloads_checkpoints
import inscrawler.downloads.control as downloads_control
import inscrawler.downloads.errors as downloads_errors
import inscrawler.downloads.events as downloads_events
import inscrawler.downloads.files as downloads_files
import inscrawler.downloads.highlights as downloads_highlights
import inscrawler.downloads.models as downloads_models
import inscrawler.downloads.posts as downloads_posts
import inscrawler.downloads.runtime as downloads_runtime
import inscrawler.downloads.service as downloads_service
import inscrawler.downloads.stories as downloads_stories
import inscrawler.downloads.transfer as downloads_transfer
import inscrawler.instagram.hotfixes as instagram_hotfixes
import inscrawler.instagram.media as instagram_media
import inscrawler.instagram.profile as instagram_profile
import inscrawler.instagram.reels as instagram_reels
import inscrawler.instagram.session as instagram_session
import inscrawler.paths as app_paths


class RateLimitClassificationTests(unittest.TestCase):
    def test_instaloader_api_429_is_rate_limited(self):
        error = TooManyRequestsException("429 Too Many Requests")

        self.assertEqual(
            downloads_errors.handle_error(error),
            downloads_models.status_code.RATE_LIMITED,
        )

    def test_fatal_api_429_message_is_rate_limited(self):
        error = AbortDownloadException(
            "Query to https://i.instagram.com/api/v1/feed/reels_media/ "
            'responded with "429 Too Many Requests"'
        )

        self.assertEqual(
            downloads_errors.handle_error(error),
            downloads_models.status_code.RATE_LIMITED,
        )

    def test_iphone_wrapper_preserves_nested_api_429(self):
        error = downloads_models.IPhoneQualityUnavailable("iPhone quality unavailable")
        error.__cause__ = TooManyRequestsException("429 Too Many Requests")

        self.assertEqual(
            downloads_errors.handle_error(error),
            downloads_models.status_code.RATE_LIMITED,
        )

    def test_iphone_wrapper_keeps_account_verification_as_authorization(self):
        status = downloads_models.status_code
        for name, error, expected in (
            (
                "challenge",
                AbortDownloadException(
                    "JSON Query to api/v1/media/1/info/: 400 Bad Request - "
                    '"fail" status, message "challenge_required" when accessing '
                    "https://i.instagram.com/api/v1/media/1/info/"
                ),
                status.AUTHORIZATION_REQUIRED,
            ),
            (
                "logout",
                AbortDownloadException(
                    "Redirected to login page. You've been logged out, please "
                    "wait some time, recreate the session and try again"
                ),
                status.AUTHORIZATION_REQUIRED,
            ),
            (
                "service",
                ConnectionException(
                    "JSON Query to api/v1/media/1/info/: 503 Service Unavailable"
                ),
                status.IPHONE_QUALITY_UNAVAILABLE,
            ),
        ):
            with self.subTest(name):
                # As _post_iphone_media() raises inside its except clause.
                try:
                    try:
                        raise error
                    except Exception as caught:
                        instagram_media._iphone_unavailable(
                            "Unable to fetch iPhone media info for post", caught
                        )
                except downloads_models.IPhoneQualityUnavailable as wrapped:
                    self.assertEqual(downloads_errors.handle_error(wrapped), expected)

                # As the Story and Highlights wrappers raise ... from e.
                chained = downloads_models.IPhoneQualityUnavailable("iPhone quality")
                chained.__cause__ = error
                self.assertEqual(downloads_errors.handle_error(chained), expected)

    def test_cdn_429_remains_an_ordinary_download_failure(self):
        error = ConnectionException(
            "429 Too Many Requests when accessing "
            "https://scontent-lga3-2.cdninstagram.com/media.jpg"
        )

        self.assertEqual(
            downloads_errors.handle_error(error),
            downloads_models.status_code.FAILED,
        )

    def test_instagram_page_429_is_rate_limited(self):
        # Page requests report a bare status, e.g. the logged-in start page that
        # 4.16's Posts query reads its web API tokens from.
        error = ConnectionException(
            "429 Too Many Requests when accessing https://www.instagram.com/"
        )

        self.assertEqual(
            downloads_errors.handle_error(error),
            downloads_models.status_code.RATE_LIMITED,
        )

    def test_unrelated_number_containing_429_is_not_rate_limited(self):
        error = ConnectionException("Media 1429001 could not be read")

        self.assertEqual(
            downloads_errors.handle_error(error),
            downloads_models.status_code.FAILED,
        )

    def test_loader_wide_failure_blocks_the_current_queue_run(self):
        with patch.object(
            downloads_runtime.runtime,
            "private_client_status",
            downloads_models.status_code.FAILED,
        ):
            result = downloads_errors._err_for_unavailable_client("alice", "stories")

        self.assertEqual(result["status"], downloads_models.status_code.FAILED)
        self.assertTrue(result["blocking_failure"])


class FakeStory:
    def __init__(self, items):
        self._items = list(items)

    def get_items(self):
        return iter(self._items)


class SerialFailurePropagationTests(unittest.TestCase):
    def setUp(self):
        story_source = patch.object(
            instagram_hotfixes,
            "get_target_stories",
            side_effect=lambda loader, profile: list(
                loader.get_stories(userids=[profile.userid])
            ),
        )
        story_source.start()
        self.addCleanup(story_source.stop)
        downloads_control.resume_downloads()
        downloads_control.clear_stop_request()

    def tearDown(self):
        downloads_control.resume_downloads()
        downloads_control.clear_stop_request()

    @staticmethod
    def _stop_during_rate_wait(*_args, **_kwargs):
        with patch.object(
            instagram_session.time,
            "sleep",
            side_effect=lambda _secs: downloads_control.request_stop_downloads(),
        ):
            instagram_session._CooperativeRateController(SimpleNamespace()).sleep(1)

    def test_iphone_collection_rate_wait_preserves_stop(self):
        wait_if_paused = downloads_control.wait_if_paused
        for category in ("stories", "highlights"):
            for scope in ("item", "full"):
                with self.subTest(category=category, scope=scope):
                    downloads_control.clear_stop_request()
                    context = Mock(iphone_support=True, is_logged_in=True)
                    context.get_iphone_json.side_effect = self._stop_during_rate_wait
                    context.graphql_query.return_value = {
                        "data": {"reels_media": [{"items": []}]}
                    }
                    profile = Profile(context, {"id": "101", "username": "alice"})
                    loader = SimpleNamespace(
                        get_stories=lambda **_kwargs: [
                            Story(context, {"user": profile._node, "items": []})
                        ],
                        get_highlights=lambda _profile: [
                            Highlight(context, {"id": "1", "title": "Trips"}, profile)
                        ],
                    )
                    download = (
                        downloads_stories.download_stories
                        if category == "stories"
                        else downloads_highlights.download_highlights
                    )
                    with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
                        self._patch_common(stack, loader, profile, folder)
                        stack.enter_context(
                            patch.object(
                                downloads_control, "wait_if_paused", wait_if_paused
                            )
                        )
                        stack.enter_context(
                            patch.object(
                                downloads_runtime.runtime,
                                "iphone_support_enabled",
                                True,
                            )
                        )
                        with self.assertRaises(
                            downloads_models.DownloadStopRequested
                        ) as caught:
                            download("alice", 1, scope_unit=scope)
                    self.assertEqual(caught.exception.downloaded_count, 0)
                    context.get_iphone_json.assert_called_once()

    def test_iphone_media_rate_wait_preserves_stop(self):
        for media_type, resolve in (
            (Post, instagram_media._resolve_post_segments_strict_iphone),
            (StoryItem, instagram_media._resolve_storyitem_segment_strict_iphone),
        ):
            with self.subTest(media_type=media_type.__name__):
                downloads_control.clear_stop_request()
                context = Mock(iphone_support=True, is_logged_in=True)
                context.get_iphone_json.side_effect = self._stop_during_rate_wait
                profile = Profile(context, {"id": "101", "username": "alice"})
                media = media_type(context, {"id": "202", "shortcode": "B"}, profile)
                with self.assertRaises(downloads_models.DownloadStopRequested):
                    resolve(SimpleNamespace(context=context), media)
                context.get_iphone_json.assert_called_once()

    @staticmethod
    def _patch_common(stack, loader, profile, save_folder):
        stack.enter_context(
            patch.object(instagram_session, "get_loader", return_value=loader)
        )
        stack.enter_context(
            patch.object(instagram_profile, "resolve_profile", return_value=profile)
        )
        stack.enter_context(
            patch.object(instagram_profile, "_ensure_accessible", return_value=None)
        )
        stack.enter_context(
            patch.object(downloads_avatar, "sync_profile_picture_best_effort")
        )
        stack.enter_context(
            patch.object(downloads_files, "build_save_path", return_value=save_folder)
        )
        stack.enter_context(patch.object(downloads_control, "wait_if_paused"))
        delay_mock = stack.enter_context(patch.object(downloads_control, "delay"))
        stack.enter_context(patch.object(downloads_events, "emit_content_phase_status"))
        stack.enter_context(patch.object(downloads_events, "emit_api_status"))
        stack.enter_context(patch.object(downloads_events, "emit_download_status"))
        stack.enter_context(patch.object(downloads_events, "_emit_item_completed"))
        stack.enter_context(
            patch.object(downloads_files, "is_already_downloaded", return_value=False)
        )
        return delay_mock

    def _run_posts(self, posts, helper_side_effect, *, patrol_page_limit=1):
        context = SimpleNamespace(username="offline-session")
        iterator = NodeIterator(
            context=context,
            query_hash="offline-failure-query",
            edge_extractor=lambda response: response,
            node_wrapper=lambda node: node["post"],
            first_data={
                "edges": [{"node": {"post": post}} for post in posts],
                "page_info": {
                    "has_next_page": False,
                    "end_cursor": None,
                },
            },
        )
        loader = SimpleNamespace(context=context)
        profile = SimpleNamespace(userid=101)
        with tempfile.TemporaryDirectory() as save_folder, ExitStack() as stack:
            delay_mock = self._patch_common(stack, loader, profile, save_folder)
            stack.enter_context(
                patch.dict(
                    instagram_reels._ITER_FUNCS,
                    {"posts": lambda _profile: iterator},
                )
            )
            stack.enter_context(
                patch.object(downloads_checkpoints, "_save_resume_checkpoint")
            )
            stack.enter_context(
                patch.object(downloads_checkpoints, "_delete_resume_checkpoint")
            )
            stack.enter_context(
                patch.object(
                    downloads_files,
                    "generate_filenames_post",
                    side_effect=lambda post, folder: [
                        os.path.join(folder, post.shortcode + ".jpg")
                    ],
                )
            )
            helper = stack.enter_context(
                patch.object(
                    downloads_transfer,
                    "download_post_to_paths",
                    side_effect=helper_side_effect,
                )
            )
            result = downloads_posts.download_posts(
                "alice", "posts", patrol_page_limit=patrol_page_limit
            )
        return result, helper, delay_mock

    def test_posts_report_failed_when_item_helper_returns_false(self):
        result, helper, _delay = self._run_posts(
            [SimpleNamespace(shortcode="post-1")],
            [False],
        )

        self.assertEqual(result["status"], downloads_models.status_code.FAILED)
        self.assertEqual(result["downloaded_count"], 0)
        self.assertEqual(helper.call_count, 1)

    def test_patrol_stays_promoted_after_post_changed(self):
        result, helper, _delay = self._run_posts(
            [
                SimpleNamespace(shortcode="changed"),
                SimpleNamespace(shortcode="post-2"),
            ],
            [PostChangedException("changed"), True],
            patrol_page_limit=1,
        )

        self.assertEqual(result["status"], downloads_models.status_code.SUCCESS)
        self.assertEqual(result["checked_count"], 2)
        self.assertEqual(result["updates_found_count"], 1)
        self.assertEqual(result["unresolved_count"], 1)
        self.assertEqual(result["downloaded_count"], 1)
        self.assertEqual(result["download_failed_count"], 0)
        self.assertEqual(helper.call_count, 2)

    def test_patrol_counts_download_exception_as_failed(self):
        result, _helper, delay_mock = self._run_posts(
            [SimpleNamespace(shortcode="post-1")],
            [RuntimeError("write failed")],
            patrol_page_limit=1,
        )

        self.assertEqual(result["status"], downloads_models.status_code.FAILED)
        self.assertEqual(result["updates_found_count"], 1)
        self.assertEqual(result["download_failed_count"], 1)
        self.assertEqual(
            delay_mock.call_args_list,
            [call("request"), call("request")],
        )

    def test_patrol_api_429_skips_post_failure_delay(self):
        result, _helper, delay_mock = self._run_posts(
            [SimpleNamespace(shortcode="post-1")],
            [TooManyRequestsException("429 Too Many Requests")],
            patrol_page_limit=1,
        )

        self.assertEqual(result["status"], downloads_models.status_code.RATE_LIMITED)
        self.assertEqual(delay_mock.call_args_list, [call("request")])

    def test_stories_report_failed_when_item_helper_returns_false(self):
        item = SimpleNamespace(shortcode="story-1")
        loader = SimpleNamespace(
            context=object(),
            get_stories=Mock(return_value=[FakeStory([item])]),
        )
        profile = SimpleNamespace(userid=202)
        with tempfile.TemporaryDirectory() as save_folder, ExitStack() as stack:
            self._patch_common(stack, loader, profile, save_folder)
            stack.enter_context(
                patch.object(
                    downloads_files,
                    "generate_filenames_storyitem",
                    return_value=[os.path.join(save_folder, "story-1.jpg")],
                )
            )
            stack.enter_context(
                patch.object(
                    downloads_transfer,
                    "download_storyitem_to_paths",
                    return_value=False,
                )
            )
            result = downloads_stories.download_stories("alice", 1)

        self.assertEqual(result["status"], downloads_models.status_code.FAILED)
        self.assertEqual(result["downloaded_count"], 0)

    def test_stories_api_429_skips_nonfinal_item_delay(self):
        items = [
            SimpleNamespace(shortcode="story-1"),
            SimpleNamespace(shortcode="story-2"),
        ]
        loader = SimpleNamespace(
            context=object(),
            get_stories=Mock(return_value=[FakeStory(items)]),
        )
        profile = SimpleNamespace(userid=202)
        with tempfile.TemporaryDirectory() as save_folder, ExitStack() as stack:
            delay_mock = self._patch_common(stack, loader, profile, save_folder)
            stack.enter_context(
                patch.object(
                    downloads_files,
                    "generate_filenames_storyitem",
                    return_value=[os.path.join(save_folder, "story.jpg")],
                )
            )
            stack.enter_context(
                patch.object(
                    downloads_transfer,
                    "download_storyitem_to_paths",
                    side_effect=TooManyRequestsException("429 Too Many Requests"),
                )
            )
            result = downloads_stories.download_stories("alice", 1, scope_unit="full")

        self.assertEqual(result["status"], downloads_models.status_code.RATE_LIMITED)
        self.assertEqual(delay_mock.call_args_list, [call("request")])

    def test_highlight_collection_failures_keep_their_stage_and_status(self):
        status = downloads_models.status_code
        for patrol_mode in (False, True):
            for stage, error, expected in (
                ("web", ConnectionException("503 Service Unavailable"), status.FAILED),
                ("web", KeyError("reels_media"), status.FAILED),
                (
                    "web",
                    LoginRequiredException("Login required"),
                    status.AUTHORIZATION_REQUIRED,
                ),
                (
                    "web",
                    TooManyRequestsException("429 Too Many Requests"),
                    status.RATE_LIMITED,
                ),
                (
                    "iphone",
                    ConnectionException("503 Service Unavailable"),
                    status.IPHONE_QUALITY_UNAVAILABLE,
                ),
                ("iphone", KeyError("highlight:1"), status.IPHONE_QUALITY_UNAVAILABLE),
                (
                    "iphone",
                    AbortDownloadException("checkpoint_required"),
                    status.AUTHORIZATION_REQUIRED,
                ),
                (
                    "iphone",
                    TooManyRequestsException("429 Too Many Requests"),
                    status.RATE_LIMITED,
                ),
            ):
                with self.subTest(
                    patrol=patrol_mode, stage=stage, error=type(error).__name__
                ):
                    # Use 4.16's real Highlight iterator: it fetches Web items
                    # before the iPhone reel, even when the Web list is empty.
                    context = Mock(iphone_support=True, is_logged_in=True)
                    context.graphql_query.return_value = {
                        "data": {"reels_media": [{"items": []}]}
                    }
                    request = (
                        context.graphql_query
                        if stage == "web"
                        else context.get_iphone_json
                    )
                    request.side_effect = error
                    profile = Profile(context, {"id": "101", "username": "alice"})
                    highlight = Highlight(
                        context, {"id": "1", "title": "Trips"}, profile
                    )
                    loader = SimpleNamespace(
                        context=context,
                        get_highlights=Mock(return_value=[highlight]),
                    )
                    with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
                        self._patch_common(stack, loader, profile, folder)
                        stack.enter_context(
                            patch.object(
                                downloads_runtime.runtime,
                                "iphone_support_enabled",
                                True,
                            )
                        )
                        transfer = stack.enter_context(
                            patch.object(
                                downloads_transfer, "download_storyitem_to_paths"
                            )
                        )
                        result = downloads_highlights.download_highlights(
                            "alice", 1, scope_unit="full", patrol_mode=patrol_mode
                        )
                    self.assertEqual(result["status"], expected)
                    self.assertEqual(result["downloaded_count"], 0)
                    context.graphql_query.assert_called_once()
                    self.assertEqual(
                        context.get_iphone_json.call_count, stage == "iphone"
                    )
                    transfer.assert_not_called()

    def test_highlights_report_failed_when_item_helper_returns_false(self):
        item = SimpleNamespace(shortcode="highlight-item-1")
        highlight = SimpleNamespace(
            title="Summer",
            unique_id=303,
            get_items=Mock(return_value=iter([item])),
        )
        loader = SimpleNamespace(
            context=object(),
            get_highlights=Mock(return_value=[highlight]),
        )
        profile = SimpleNamespace(userid=303)
        with tempfile.TemporaryDirectory() as save_folder, ExitStack() as stack:
            self._patch_common(stack, loader, profile, save_folder)
            stack.enter_context(
                patch.object(
                    downloads_files,
                    "generate_filenames_storyitem",
                    return_value=[os.path.join(save_folder, "highlight-1.jpg")],
                )
            )
            stack.enter_context(
                patch.object(
                    downloads_transfer,
                    "download_storyitem_to_paths",
                    return_value=False,
                )
            )
            result = downloads_highlights.download_highlights("alice", 1)

        self.assertEqual(result["status"], downloads_models.status_code.FAILED)
        self.assertEqual(result["downloaded_count"], 0)

    def test_highlights_api_429_skips_nonfinal_item_delay(self):
        items = [
            SimpleNamespace(shortcode="highlight-1"),
            SimpleNamespace(shortcode="highlight-2"),
        ]
        highlight = SimpleNamespace(
            title="Summer",
            unique_id=303,
            get_items=Mock(return_value=iter(items)),
        )
        loader = SimpleNamespace(
            context=object(),
            get_highlights=Mock(return_value=[highlight]),
        )
        profile = SimpleNamespace(userid=303)
        with tempfile.TemporaryDirectory() as save_folder, ExitStack() as stack:
            delay_mock = self._patch_common(stack, loader, profile, save_folder)
            stack.enter_context(
                patch.object(
                    downloads_files,
                    "generate_filenames_storyitem",
                    return_value=[os.path.join(save_folder, "highlight.jpg")],
                )
            )
            stack.enter_context(
                patch.object(
                    downloads_transfer,
                    "download_storyitem_to_paths",
                    side_effect=TooManyRequestsException("429 Too Many Requests"),
                )
            )
            result = downloads_highlights.download_highlights(
                "alice", 1, scope_unit="full"
            )

        self.assertEqual(result["status"], downloads_models.status_code.RATE_LIMITED)
        self.assertEqual(
            delay_mock.call_args_list,
            [call("request"), call("request")],
        )

    def test_stream_response_is_closed_when_write_fails(self):
        response = Mock()
        context = SimpleNamespace(
            get_raw=Mock(return_value=response),
            write_raw=Mock(side_effect=OSError("disk full")),
        )

        with tempfile.TemporaryDirectory() as folder:
            with (
                patch.object(downloads_control, "delay") as delay_mock,
                self.assertRaises(OSError),
            ):
                downloads_transfer._write_url_to_path(
                    SimpleNamespace(context=context),
                    "https://example.invalid/media.jpg",
                    os.path.join(folder, "media.jpg"),
                    datetime.now(),
                )

        response.close.assert_called_once_with()
        context.get_raw.assert_called_once_with("https://example.invalid/media.jpg")
        delay_mock.assert_not_called()

    @staticmethod
    def _incomplete_read_error():
        return Urllib3ProtocolError(
            "Connection broken",
            IncompleteRead(b"partial", 8),
        )

    def test_incomplete_read_retry_reuses_one_media_request_budget_slot(self):
        first_response = Mock()
        second_response = Mock(headers={}, status_code=200)
        context = SimpleNamespace(
            get_raw=Mock(side_effect=[first_response, second_response]),
        )
        attempts = []

        def write_raw(response, filename):
            attempts.append(response)
            with open(filename + ".temp", "wb") as output:
                if response is first_response:
                    output.write(b"partial")
                else:
                    output.write(b"complete")
            if response is first_response:
                raise self._incomplete_read_error()
            os.replace(filename + ".temp", filename)

        context.write_raw = Mock(side_effect=write_raw)
        activity_events = []
        image_callback = Mock()
        request_budget = downloads_models._MediaRequestBudget(1)

        with tempfile.TemporaryDirectory() as folder:
            target_path = Path(folder, "media.jpg")
            with (
                patch.object(downloads_control, "delay") as delay_mock,
                patch.object(
                    downloads_runtime.runtime, "image_download_callback", image_callback
                ),
                patch.object(downloads_runtime.runtime, "file_download_callback", None),
                patch.object(
                    downloads_runtime.runtime,
                    "file_activity_callback",
                    lambda event: activity_events.append(dict(event)),
                ),
            ):
                downloads_transfer._write_url_to_path(
                    SimpleNamespace(context=context),
                    "https://example.invalid/media.jpg",
                    str(target_path),
                    datetime.now(),
                    request_budget=request_budget,
                )

            self.assertEqual(target_path.read_bytes(), b"complete")
            self.assertFalse(Path(str(target_path) + ".temp").exists())

        self.assertEqual(attempts, [first_response, second_response])
        self.assertEqual(context.get_raw.call_count, 2)
        self.assertEqual(request_budget.used, 1)
        delay_mock.assert_called_once_with("download")
        first_response.close.assert_called_once_with()
        second_response.close.assert_called_once_with()
        image_callback.assert_called_once()
        self.assertEqual(
            [event["event"] for event in activity_events],
            ["started", "finished"],
        )

    def test_second_incomplete_read_escapes_and_removes_partial_file(self):
        responses = [Mock(), Mock()]
        context = SimpleNamespace(get_raw=Mock(side_effect=responses))

        def write_raw(_response, filename):
            with open(filename + ".temp", "wb") as output:
                output.write(b"partial")
            raise self._incomplete_read_error()

        context.write_raw = Mock(side_effect=write_raw)

        with tempfile.TemporaryDirectory() as folder:
            target_path = Path(folder, "media.jpg")
            with (
                patch.object(downloads_control, "delay") as delay_mock,
                self.assertRaises(Urllib3ProtocolError),
            ):
                downloads_transfer._write_url_to_path(
                    SimpleNamespace(context=context),
                    "https://example.invalid/media.jpg",
                    str(target_path),
                    datetime.now(),
                )

            self.assertFalse(target_path.exists())
            self.assertFalse(Path(str(target_path) + ".temp").exists())

        self.assertEqual(context.get_raw.call_count, 2)
        self.assertEqual(context.write_raw.call_count, 2)
        delay_mock.assert_called_once_with("download")
        for response in responses:
            response.close.assert_called_once_with()

    def test_transient_http_500_waits_then_retries_once(self):
        response = Mock(headers={}, status_code=200)
        context = SimpleNamespace(
            get_raw=Mock(
                side_effect=[
                    ConnectionException("500 Internal Server Error when accessing CDN"),
                    response,
                ]
            ),
        )

        def write_raw(_response, filename):
            with open(filename + ".temp", "wb") as output:
                output.write(b"complete")
            os.replace(filename + ".temp", filename)

        context.write_raw = Mock(side_effect=write_raw)

        with tempfile.TemporaryDirectory() as folder:
            target_path = Path(folder, "media.jpg")
            with patch.object(downloads_control, "delay") as delay_mock:
                downloads_transfer._write_url_to_path(
                    SimpleNamespace(context=context),
                    "https://example.invalid/media.jpg",
                    str(target_path),
                    datetime.now(),
                )
            self.assertEqual(target_path.read_bytes(), b"complete")

        self.assertEqual(context.get_raw.call_count, 2)
        context.write_raw.assert_called_once_with(response, str(target_path))
        delay_mock.assert_called_once_with("download")
        response.close.assert_called_once_with()

    def test_forbidden_cdn_response_is_not_retried(self):
        context = SimpleNamespace(
            get_raw=Mock(
                side_effect=QueryReturnedForbiddenException(
                    "403 Forbidden when accessing CDN"
                )
            ),
            write_raw=Mock(),
        )

        with tempfile.TemporaryDirectory() as folder:
            with (
                patch.object(downloads_control, "delay") as delay_mock,
                self.assertRaises(QueryReturnedForbiddenException),
            ):
                downloads_transfer._write_url_to_path(
                    SimpleNamespace(context=context),
                    "https://example.invalid/media.jpg",
                    os.path.join(folder, "media.jpg"),
                    datetime.now(),
                )

        context.get_raw.assert_called_once_with("https://example.invalid/media.jpg")
        context.write_raw.assert_not_called()
        delay_mock.assert_not_called()

    def test_stopped_result_preserves_completed_item_count(self):
        stopped = downloads_models.DownloadStopRequested()
        stopped.downloaded_count = 2
        stopped.scope_completed_count = 1
        stopped.media_request_count = 4
        with (
            patch.object(downloads_control, "wait_if_paused"),
            patch.object(downloads_posts, "download_post_pages", side_effect=stopped),
        ):
            result = downloads_service.download_user_content("alice", "posts", 10)

        self.assertEqual(result["status"], downloads_models.status_code.STOPPED)
        self.assertEqual(result["downloaded_count"], 2)
        self.assertEqual(result["scope_completed_count"], 1)
        self.assertEqual(result["media_request_count"], 4)

    def test_invalid_resume_cursor_shape_is_deleted(self):
        errors = (
            TypeError("invalid FrozenNodeIterator fields"),
            ValueError("invalid cursor timestamp"),
            OverflowError("cursor timestamp out of range"),
        )

        with (
            tempfile.TemporaryDirectory() as resume_dir,
            patch.object(
                app_paths,
                "TASK_RESUME_DIR",
                resume_dir,
            ),
            patch.object(downloads_runtime.runtime, "active_task_id", "0123456789ab"),
        ):
            cursor_path = downloads_checkpoints._resume_checkpoint_path("posts")
            for error in errors:
                with (
                    self.subTest(error=type(error).__name__),
                    patch.object(
                        downloads_checkpoints,
                        "load_structure_from_file",
                        side_effect=error,
                    ),
                ):
                    with open(cursor_path, "wb") as cursor_file:
                        cursor_file.write(b"malformed cursor")

                    self.assertIsNone(
                        downloads_checkpoints._read_resume_checkpoint(object(), "posts")
                    )
                    self.assertFalse(os.path.exists(cursor_path))

    def test_patrol_parent_cleanup_removes_all_child_cursors_and_temps(self):
        with (
            tempfile.TemporaryDirectory() as resume_dir,
            patch.object(
                app_paths,
                "TASK_RESUME_DIR",
                resume_dir,
            ),
        ):
            paths = []
            for target_index in range(3):
                child_id = f"0123456789ab.patrol.{target_index:04d}"
                path = downloads_checkpoints._resume_checkpoint_path(
                    "posts",
                    task_id=child_id,
                )
                paths.extend((path, f"{path}.tmp.xz"))
            for path in paths:
                with open(path, "wb") as cursor_file:
                    cursor_file.write(b"cursor")

            downloads_checkpoints.delete_patrol_resume_checkpoints(
                "0123456789ab",
                "posts",
                3,
            )

            self.assertTrue(all(not os.path.exists(path) for path in paths))


if __name__ == "__main__":
    unittest.main()
