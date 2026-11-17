"""Offline contracts for atomic ordinary-download scopes.

The stable serial path consumes complete Instagram response units: one real
``NodeIterator`` page for post-like media, and the complete response set for
Stories / Highlights.  These tests deliberately use Instaloader's real
``NodeIterator`` state machine with fake response pages, while mocking all
network and filesystem integration outside a temporary directory.

Patrol entry points remain covered by the existing patrol tests.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from instaloader import (
    NodeIterator,
    Post,
    Story,
)
from instaloader.exceptions import (
    InvalidArgumentException,
)

import inscrawler.diagnostics.console as diagnostics_console
import inscrawler.downloads.avatar as downloads_avatar
import inscrawler.downloads.checkpoints as downloads_checkpoints
import inscrawler.downloads.control as downloads_control
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
import inscrawler.notifications.service as notifications_service


class FakePagedContext:
    """Minimal Instaloader context serving pages after ``first_data``."""

    def __init__(self, pages_by_cursor=None):
        self.username = "offline-session"
        self.pages_by_cursor = dict(pages_by_cursor or {})
        self.graphql_calls = []

    def graphql_query(self, query_hash, variables, referer=None):
        self.graphql_calls.append((query_hash, dict(variables), referer))
        cursor = variables.get("after")
        if cursor not in self.pages_by_cursor:
            raise AssertionError(f"unexpected page fetch for cursor {cursor!r}")
        return self.pages_by_cursor[cursor]


def _page(nodes, *, cursor=None, has_next=False):
    return {
        "edges": [{"node": node} for node in nodes],
        "page_info": {
            "has_next_page": bool(has_next),
            "end_cursor": cursor,
        },
    }


def make_node_iterator(page_nodes, wrapper=None):
    """Return a real NodeIterator plus its fake context and wrapper trace."""

    if not page_nodes:
        page_nodes = [[]]
    pages = []
    for index, nodes in enumerate(page_nodes):
        has_next = index + 1 < len(page_nodes)
        pages.append(
            _page(
                nodes,
                cursor=f"cursor-{index + 1}" if has_next else None,
                has_next=has_next,
            )
        )
    pages_by_cursor = {
        f"cursor-{index}": pages[index] for index in range(1, len(pages))
    }
    context = FakePagedContext(pages_by_cursor)
    wrapped_nodes = []

    def traced_wrapper(node):
        wrapped_nodes.append(node)
        if wrapper is not None:
            return wrapper(node)
        return node["post"]

    iterator = NodeIterator(
        context=context,
        query_hash="offline-query-hash",
        edge_extractor=lambda response: response,
        node_wrapper=traced_wrapper,
        first_data=pages[0],
    )
    return iterator, context, wrapped_nodes


class FakeStory:
    def __init__(self, items):
        self.items = list(items)
        self.get_items = Mock(side_effect=lambda: iter(self.items))


class FakeResponse:
    def __init__(self):
        self.headers = {}
        self.status_code = 200
        self.close = Mock()


def simple_post(shortcode, *, segment_count=1):
    timestamp = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    if segment_count == 1:
        return SimpleNamespace(
            shortcode=shortcode,
            typename="GraphImage",
            is_video=False,
            url=f"https://cdn.invalid/{shortcode}.jpg",
            date_utc=timestamp,
            date_local=timestamp,
        )
    nodes = [
        SimpleNamespace(
            is_video=False,
            display_url=f"https://cdn.invalid/{shortcode}-{index}.jpg",
        )
        for index in range(1, segment_count + 1)
    ]
    return SimpleNamespace(
        shortcode=shortcode,
        typename="GraphSidecar",
        is_video=False,
        date_utc=timestamp,
        date_local=timestamp,
        get_is_videos=Mock(return_value=[False] * segment_count),
        get_sidecar_nodes=Mock(return_value=nodes),
    )


def simple_story_item(shortcode):
    timestamp = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    return SimpleNamespace(
        shortcode=shortcode,
        is_video=False,
        url=f"https://cdn.invalid/{shortcode}.jpg",
        date_utc=timestamp,
        date_local=timestamp,
    )


def source_aware_story_item(shortcode, media_id, *, expires_at, iphone_media):
    timestamp = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    return SimpleNamespace(
        shortcode=shortcode,
        mediaid=media_id,
        is_video=False,
        date_utc=timestamp,
        date_local=timestamp,
        expiring_utc=expires_at,
        _iphone_struct_=iphone_media,
    )


class AtomicScopeDownloadTests(unittest.TestCase):
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
        stack.enter_context(patch.object(notifications_service, "notify"))
        return delay_mock

    def _run_post_pages(
        self,
        page_shortcodes,
        *,
        page_limit,
        already=(),
        media_request_cap=None,
        use_real_download_helper=False,
        node_wrapper=None,
        helper_side_effect=None,
        save_cursor_succeeds=True,
        event_log=None,
        phase_event_log=None,
        page_response_overrides=None,
        capture_stop=False,
    ):
        pages = [
            [
                {
                    "post": simple_post(
                        value[0],
                        segment_count=value[1],
                    )
                    if isinstance(value, tuple)
                    else simple_post(value)
                }
                for value in values
            ]
            for values in page_shortcodes
        ]
        iterator, context, wrapped_nodes = make_node_iterator(
            pages,
            wrapper=node_wrapper,
        )
        if page_response_overrides:
            context.pages_by_cursor.update(page_response_overrides)
        context.get_raw = Mock(side_effect=lambda _url: FakeResponse())
        context.write_raw = Mock()
        loader = SimpleNamespace(context=context)
        profile = SimpleNamespace(userid=101)
        already = set(already)

        with tempfile.TemporaryDirectory() as save_folder, ExitStack() as stack:
            delay_mock = self._patch_common(stack, loader, profile, save_folder)
            if capture_stop:
                stack.enter_context(
                    patch.object(
                        downloads_control,
                        "wait_if_paused",
                        side_effect=downloads_control._raise_if_stop_requested,
                    )
                )
            if phase_event_log is not None:
                stack.enter_context(
                    patch.object(
                        downloads_events,
                        "emit_content_phase_status",
                        side_effect=lambda kind, phase: phase_event_log.append(
                            (kind, phase)
                        ),
                    )
                )
            # Ordinary GUI tasks own a resumable cursor.  Give the direct
            # runner the same task identity so cap-completion assertions
            # exercise the real cursor cleanup branch.
            stack.enter_context(
                patch.object(
                    downloads_runtime.runtime, "active_task_id", "scope-test-task"
                )
            )
            stack.enter_context(
                patch.dict(
                    instagram_reels._ITER_FUNCS, {"posts": lambda _profile: iterator}
                )
            )
            stack.enter_context(
                patch.object(
                    downloads_checkpoints, "_read_resume_checkpoint", return_value=None
                )
            )

            def save_cursor_side_effect(*_args, **kwargs):
                if event_log is not None:
                    event_log.append(("cursor", kwargs.get("replay_current")))
                return save_cursor_succeeds

            save_cursor = stack.enter_context(
                patch.object(
                    downloads_checkpoints,
                    "_save_resume_checkpoint",
                    side_effect=save_cursor_side_effect,
                )
            )
            delete_cursor = stack.enter_context(
                patch.object(downloads_checkpoints, "_delete_resume_checkpoint")
            )

            def filenames(post, folder):
                count = (
                    len(post.get_is_videos()) if post.typename == "GraphSidecar" else 1
                )
                return [
                    os.path.join(folder, f"{post.shortcode}-{index}.jpg")
                    for index in range(1, count + 1)
                ]

            stack.enter_context(
                patch.object(
                    downloads_files, "generate_filenames_post", side_effect=filenames
                )
            )
            stack.enter_context(
                patch.object(
                    downloads_files,
                    "is_already_downloaded",
                    side_effect=lambda paths: (
                        os.path.basename(paths[0]).split("-", 1)[0] in already
                    ),
                )
            )
            completed_scope = stack.enter_context(
                patch.object(
                    downloads_events,
                    "_emit_scope_completed",
                    side_effect=(
                        (lambda *_args, **_kwargs: event_log.append(("scope", None)))
                        if event_log is not None
                        else None
                    ),
                )
            )
            if use_real_download_helper:
                helper = None
            else:
                helper = stack.enter_context(
                    patch.object(
                        downloads_transfer,
                        "download_post_to_paths",
                        return_value=True if helper_side_effect is None else None,
                        side_effect=helper_side_effect,
                    )
                )

            stop_error = None
            try:
                result = downloads_posts.download_post_pages(
                    "alice",
                    "posts",
                    page_limit,
                    media_request_cap=media_request_cap,
                )
            except downloads_models.DownloadStopRequested as exc:
                if not capture_stop:
                    raise
                result = None
                stop_error = exc

        return SimpleNamespace(
            result=result,
            iterator=iterator,
            context=context,
            wrapped_nodes=wrapped_nodes,
            helper=helper,
            save_cursor=save_cursor,
            delete_cursor=delete_cursor,
            completed_scope=completed_scope,
            delay=delay_mock,
            stop_error=stop_error,
        )

    def test_one_page_scope_consumes_every_item_without_prefetching_next_page(self):
        run = self._run_post_pages(
            [["p1", "p2", "p3"], ["p4", "p5"]],
            page_limit=1,
        )

        self.assertEqual(run.result["status"], downloads_models.status_code.SUCCESS)
        self.assertEqual(run.result["downloaded_count"], 3)
        self.assertEqual(run.helper.call_count, 3)
        self.assertEqual(
            [node["post"].shortcode for node in run.wrapped_nodes], ["p1", "p2", "p3"]
        )
        self.assertEqual(run.context.graphql_calls, [])
        run.completed_scope.assert_called_once()

    def test_stop_during_last_item_commits_page_without_fetching_next_page(self):
        def stop_during_transfer(*_args, **_kwargs):
            downloads_control.request_stop_downloads()
            return True

        run = self._run_post_pages(
            [["p1"], ["p2"]],
            page_limit=2,
            helper_side_effect=stop_during_transfer,
            capture_stop=True,
        )

        self.assertIsNone(run.result)
        self.assertIsInstance(run.stop_error, downloads_models.DownloadStopRequested)
        self.assertEqual(run.stop_error.downloaded_count, 1)
        self.assertEqual(run.stop_error.scope_completed_count, 1)
        run.helper.assert_called_once()
        run.completed_scope.assert_called_once()
        self.assertEqual(run.context.graphql_calls, [])
        self.assertGreaterEqual(run.save_cursor.call_count, 1)
        self.assertTrue(
            all(
                call.kwargs.get("replay_current") is False
                for call in run.save_cursor.call_args_list
            )
        )

    def test_serial_page_reports_fetch_prepare_download_in_operation_order(self):
        phases = []

        run = self._run_post_pages(
            [["p1"]],
            page_limit=1,
            use_real_download_helper=True,
            phase_event_log=phases,
        )

        self.assertEqual(run.result["status"], downloads_models.status_code.SUCCESS)
        self.assertEqual(
            phases,
            [
                ("posts", "fetch"),
                ("posts", "prepare"),
                ("posts", "download"),
            ],
        )

    def test_page_delay_is_applied_once_per_opened_page(self):
        first_page = self._run_post_pages(
            [["old1", "old2"]],
            page_limit=1,
            already={"old1", "old2"},
        )
        two_pages = self._run_post_pages(
            [["old1"], ["old2"]],
            page_limit=2,
            already={"old1", "old2"},
        )

        self.assertEqual(first_page.delay.call_count, 1)
        self.assertEqual(two_pages.delay.call_count, 2)

    def test_existing_items_still_consume_the_page_scope(self):
        run = self._run_post_pages(
            [["old1", "old2", "old3"], ["new1"]],
            page_limit=1,
            already={"old1", "old2", "old3"},
        )

        self.assertEqual(run.result["status"], downloads_models.status_code.SUCCESS)
        self.assertEqual(run.result["downloaded_count"], 0)
        self.assertEqual(run.helper.call_count, 0)
        self.assertEqual(len(run.wrapped_nodes), 3)
        self.assertEqual(run.context.graphql_calls, [])
        run.completed_scope.assert_called_once()

    def test_two_page_scopes_fetch_the_second_page_once_and_consume_it_fully(self):
        run = self._run_post_pages(
            [["p1", "p2"], ["p3", "p4", "p5"], ["p6"]],
            page_limit=2,
        )

        self.assertEqual(run.result["status"], downloads_models.status_code.SUCCESS)
        self.assertEqual(run.result["downloaded_count"], 5)
        self.assertEqual(run.helper.call_count, 5)
        self.assertEqual(len(run.context.graphql_calls), 1)
        self.assertEqual(run.context.graphql_calls[0][1]["after"], "cursor-1")
        self.assertEqual(
            [node["post"].shortcode for node in run.wrapped_nodes],
            ["p1", "p2", "p3", "p4", "p5"],
        )
        self.assertEqual(run.completed_scope.call_count, 2)

    def test_intermediate_page_cursor_is_saved_before_scope_progress(self):
        events = []
        run = self._run_post_pages(
            [["p1"], ["p2"]],
            page_limit=2,
            event_log=events,
        )

        self.assertEqual(events[:2], [("cursor", False), ("scope", None)])
        self.assertEqual(run.completed_scope.call_count, 2)

    def test_intermediate_page_is_not_committed_when_boundary_cursor_save_fails(self):
        run = self._run_post_pages(
            [["p1"], ["p2"]],
            page_limit=2,
            save_cursor_succeeds=False,
        )

        self.assertEqual(run.result["status"], downloads_models.status_code.FAILED)
        self.assertEqual(run.result["scope_completed_count"], 0)
        self.assertEqual(run.result["downloaded_count"], 1)
        self.assertEqual(run.context.graphql_calls, [])
        run.completed_scope.assert_not_called()

    def test_wrapper_exception_replays_the_advanced_unclassified_item(self):
        def wrapper(node):
            post = node["post"]
            if post.shortcode == "p2":
                raise RuntimeError("wrapper failed after NodeIterator advanced")
            return post

        run = self._run_post_pages(
            [["p1", "p2", "p3"]],
            page_limit=1,
            node_wrapper=wrapper,
        )

        self.assertEqual(run.result["status"], downloads_models.status_code.FAILED)
        self.assertEqual(run.result["downloaded_count"], 1)
        self.assertTrue(
            any(
                call.kwargs.get("replay_current") is True
                for call in run.save_cursor.call_args_list
            )
        )
        run.completed_scope.assert_not_called()

    def test_user_stop_becomes_failure_if_owned_cursor_cannot_be_saved(self):
        run = self._run_post_pages(
            [["p1", "p2"]],
            page_limit=1,
            helper_side_effect=downloads_models.DownloadStopRequested("stop"),
            save_cursor_succeeds=False,
        )

        self.assertEqual(run.result["status"], downloads_models.status_code.FAILED)
        self.assertEqual(run.result["scope_completed_count"], 0)
        self.assertIn("failed to save", run.result["message"])
        run.completed_scope.assert_not_called()

    def test_empty_first_response_with_next_cursor_fails_closed(self):
        context = FakePagedContext({"cursor-1": _page([{"post": simple_post("p1")}])})
        iterator = NodeIterator(
            context=context,
            query_hash="offline-query-hash",
            edge_extractor=lambda response: response,
            node_wrapper=lambda node: node["post"],
            first_data=_page([], cursor="cursor-1", has_next=True),
        )
        loader = SimpleNamespace(context=context)
        profile = SimpleNamespace(userid=101)

        with tempfile.TemporaryDirectory() as save_folder, ExitStack() as stack:
            self._patch_common(stack, loader, profile, save_folder)
            stack.enter_context(
                patch.object(
                    downloads_runtime.runtime, "active_task_id", "empty-page-test"
                )
            )
            stack.enter_context(
                patch.dict(
                    instagram_reels._ITER_FUNCS, {"posts": lambda _profile: iterator}
                )
            )
            stack.enter_context(
                patch.object(
                    downloads_checkpoints, "_read_resume_checkpoint", return_value=None
                )
            )
            stack.enter_context(
                patch.object(
                    downloads_checkpoints, "_save_resume_checkpoint", return_value=True
                )
            )
            completed_scope = stack.enter_context(
                patch.object(downloads_events, "_emit_scope_completed")
            )
            result = downloads_posts.download_post_pages("alice", "posts", 1)

        self.assertEqual(result["status"], downloads_models.status_code.FAILED)
        self.assertEqual(context.graphql_calls, [])
        completed_scope.assert_not_called()

    def test_empty_or_duplicate_declared_next_page_uses_upstream_end_semantics(self):
        responses = {
            "empty": _page([]),
            "duplicate": _page([{"post": simple_post("p1")}]),
        }
        for label, response in responses.items():
            with self.subTest(response=label):
                run = self._run_post_pages(
                    [["p1"], ["unused"]],
                    page_limit=2,
                    page_response_overrides={"cursor-1": response},
                )

                self.assertEqual(
                    run.result["status"], downloads_models.status_code.SUCCESS
                )
                self.assertEqual(run.result["scope_completed_count"], 1)
                self.assertEqual(run.helper.call_count, 1)
                run.delete_cursor.assert_called_once()
                self.assertEqual(run.completed_scope.call_count, 1)

    def test_falsey_non_object_page_info_fails_closed(self):
        for malformed in ([], "", 0):
            with self.subTest(page_info=malformed):
                context = FakePagedContext()
                iterator = NodeIterator(
                    context=context,
                    query_hash="offline-query-hash",
                    edge_extractor=lambda response: response,
                    node_wrapper=lambda node: node,
                    first_data={"edges": [], "page_info": malformed},
                )
                with self.assertRaises(InvalidArgumentException):
                    instagram_reels._node_page_state(iterator)

    def test_safe_page_boundary_checkpoint_keeps_next_cursor_without_replaying_page(
        self,
    ):
        page_nodes = [
            [
                {"post": simple_post("p1")},
                {"post": simple_post("p2")},
                {"post": simple_post("p3")},
            ],
            [{"post": simple_post("p4")}],
        ]
        iterator, _context, _wrapped = make_node_iterator(page_nodes)
        self.assertEqual(
            [next(iterator).shortcode for _index in range(3)],
            ["p1", "p2", "p3"],
        )

        frozen = downloads_checkpoints._freeze_iterator_for_checkpoint(
            iterator,
            replay_current=False,
        )

        self.assertEqual(frozen.total_index, 3)
        self.assertEqual(frozen.remaining_data["edges"], [])
        self.assertTrue(frozen.remaining_data["page_info"]["has_next_page"])
        self.assertEqual(
            frozen.remaining_data["page_info"]["end_cursor"],
            "cursor-1",
        )

        resumed_iterator, resumed_context, _wrapped = make_node_iterator(page_nodes)
        resumed_iterator.thaw(frozen)
        self.assertEqual(next(resumed_iterator).shortcode, "p4")
        self.assertEqual(len(resumed_context.graphql_calls), 1)
        self.assertEqual(
            resumed_context.graphql_calls[0][1]["after"],
            "cursor-1",
        )

    def test_replay_checkpoint_keeps_instaloader_at_least_once_current_item(self):
        page_nodes = [
            [
                {"post": simple_post("p1")},
                {"post": simple_post("p2")},
                {"post": simple_post("p3")},
            ]
        ]
        iterator, _context, _wrapped = make_node_iterator(page_nodes)
        self.assertEqual(next(iterator).shortcode, "p1")
        self.assertEqual(next(iterator).shortcode, "p2")

        frozen = downloads_checkpoints._freeze_iterator_for_checkpoint(
            iterator,
            replay_current=True,
        )

        self.assertEqual(frozen.total_index, 1)
        self.assertEqual(
            [edge["node"]["post"].shortcode for edge in frozen.remaining_data["edges"]],
            ["p2", "p3"],
        )

        resumed_iterator, _resumed_context, _wrapped = make_node_iterator(page_nodes)
        resumed_iterator.thaw(frozen)
        self.assertEqual(next(resumed_iterator).shortcode, "p2")

    def test_valid_resume_cursor_avoids_eager_fresh_first_page_request(self):
        page_nodes = [
            [
                {"post": simple_post("p1")},
                {"post": simple_post("p2")},
            ]
        ]
        source_iterator, source_context, _wrapped = make_node_iterator(page_nodes)
        self.assertEqual(next(source_iterator).shortcode, "p1")
        frozen = downloads_checkpoints._freeze_iterator_for_checkpoint(
            source_iterator,
            replay_current=False,
        )

        def fresh_iterator_without_first_data():
            return NodeIterator(
                context=source_context,
                query_hash="offline-query-hash",
                edge_extractor=lambda response: response,
                node_wrapper=lambda node: node["post"],
            )

        profile = SimpleNamespace(
            userid=101, get_posts=fresh_iterator_without_first_data
        )
        loader = SimpleNamespace(context=source_context)

        with tempfile.TemporaryDirectory() as save_folder, ExitStack() as stack:
            delay_mock = self._patch_common(stack, loader, profile, save_folder)
            stack.enter_context(
                patch.object(
                    downloads_runtime.runtime, "active_task_id", "lazy-resume-test"
                )
            )
            stack.enter_context(
                patch.object(
                    downloads_checkpoints,
                    "_read_resume_checkpoint",
                    return_value=frozen,
                )
            )
            stack.enter_context(
                patch.object(
                    downloads_checkpoints, "_save_resume_checkpoint", return_value=True
                )
            )
            stack.enter_context(
                patch.object(downloads_checkpoints, "_delete_resume_checkpoint")
            )
            stack.enter_context(
                patch.object(
                    downloads_files,
                    "generate_filenames_post",
                    return_value=[os.path.join(save_folder, "p2.jpg")],
                )
            )
            stack.enter_context(
                patch.object(
                    downloads_files, "is_already_downloaded", return_value=False
                )
            )
            helper = stack.enter_context(
                patch.object(
                    downloads_transfer, "download_post_to_paths", return_value=True
                )
            )

            result = downloads_posts.download_post_pages("alice", "posts", 1)

        self.assertEqual(result["status"], downloads_models.status_code.SUCCESS)
        self.assertEqual(result["downloaded_count"], 1)
        helper.assert_called_once()
        # Without deferred construction, NodeIterator.__init__ would call
        # graphql_query(after=None) before thaw and this fake context rejects it.
        self.assertEqual(source_context.graphql_calls, [])
        # The resumed item exhausts the requested page, so there is no
        # subsequent request to pace after its file has completed.
        self.assertEqual(delay_mock.call_count, 0)

    def test_invalid_thawed_cursor_is_deleted_before_one_fresh_open(self):
        source_iterator, _source_context, _wrapped = make_node_iterator(
            [[{"post": simple_post("p1")}, {"post": simple_post("p2")}]]
        )
        self.assertEqual(next(source_iterator).shortcode, "p1")
        frozen = downloads_checkpoints._freeze_iterator_for_checkpoint(
            source_iterator,
            replay_current=False,
        )._replace(
            remaining_data={
                "edges": [{"node": {"post": simple_post("p2")}}],
                "page_info": [],
            }
        )
        resume_iterator, _resume_context, _wrapped = make_node_iterator(
            [[{"post": simple_post("placeholder")}]]
        )
        fresh_iterator, fresh_context, _wrapped = make_node_iterator(
            [[{"post": simple_post("fresh")}]]
        )
        loader = SimpleNamespace(context=fresh_context)
        profile = SimpleNamespace(userid=101)

        with tempfile.TemporaryDirectory() as save_folder, ExitStack() as stack:
            self._patch_common(stack, loader, profile, save_folder)
            stack.enter_context(
                patch.object(
                    downloads_runtime.runtime, "active_task_id", "invalid-resume-test"
                )
            )
            stack.enter_context(
                patch.object(
                    downloads_checkpoints,
                    "_read_resume_checkpoint",
                    return_value=frozen,
                )
            )
            open_iterator = stack.enter_context(
                patch.object(
                    instagram_reels,
                    "_ordinary_post_iterator",
                    side_effect=[resume_iterator, fresh_iterator],
                )
            )
            delete_cursor = stack.enter_context(
                patch.object(downloads_checkpoints, "_delete_resume_checkpoint")
            )
            save_cursor = stack.enter_context(
                patch.object(downloads_checkpoints, "_save_resume_checkpoint")
            )
            stack.enter_context(
                patch.object(
                    downloads_files,
                    "generate_filenames_post",
                    return_value=[os.path.join(save_folder, "fresh.jpg")],
                )
            )
            stack.enter_context(
                patch.object(
                    downloads_files, "is_already_downloaded", return_value=True
                )
            )
            helper = stack.enter_context(
                patch.object(downloads_transfer, "download_post_to_paths")
            )
            result = downloads_posts.download_post_pages("alice", "posts", 1)

        self.assertEqual(result["status"], downloads_models.status_code.SUCCESS)
        self.assertEqual(open_iterator.call_count, 2)
        self.assertEqual(delete_cursor.call_count, 2)
        self.assertEqual(delete_cursor.call_args_list[0].args, ("posts",))
        self.assertEqual(delete_cursor.call_args_list[0].kwargs, {})
        save_cursor.assert_not_called()
        helper.assert_not_called()

    def test_media_cap_completes_without_starting_an_oversized_carousel(self):
        run = self._run_post_pages(
            [[("album", 3)]],
            page_limit=1,
            media_request_cap=2,
            use_real_download_helper=True,
        )

        self.assertEqual(run.result["status"], downloads_models.status_code.SUCCESS)
        self.assertEqual(
            run.result["completion_reason"],
            "media_request_cap",
        )
        self.assertEqual(run.result["downloaded_count"], 0)
        self.assertEqual(run.context.get_raw.call_count, 0)
        run.save_cursor.assert_not_called()
        run.delete_cursor.assert_called_once_with("posts", log=True)
        run.completed_scope.assert_not_called()

    def test_media_cap_completes_before_next_carousel_after_prior_files(self):
        run = self._run_post_pages(
            [[("album1", 2), ("album2", 2)]],
            page_limit=1,
            media_request_cap=3,
            use_real_download_helper=True,
        )

        self.assertEqual(run.result["status"], downloads_models.status_code.SUCCESS)
        self.assertEqual(
            run.result["completion_reason"],
            "media_request_cap",
        )
        self.assertEqual(run.result["downloaded_count"], 1)
        self.assertEqual(run.context.get_raw.call_count, 2)
        run.save_cursor.assert_not_called()
        run.delete_cursor.assert_called_once_with("posts", log=True)
        run.completed_scope.assert_not_called()

    def _run_reels_page(
        self,
        raw_media,
        *,
        existing_summary_file=False,
        existing_summary_category="reels",
        iphone_quality=False,
    ):
        raw_node = {"media": dict(raw_media)}
        iterator, context, _wrapped_nodes = make_node_iterator([[raw_node]])
        loader = SimpleNamespace(context=context)
        profile = SimpleNamespace(userid=404)
        detail_post = simple_post(str(raw_media.get("code") or "fallback"))

        with tempfile.TemporaryDirectory() as account_folder, ExitStack() as stack:
            save_folder = os.path.join(account_folder, "reels")
            os.makedirs(save_folder)
            delay_mock = self._patch_common(stack, loader, profile, save_folder)
            stack.enter_context(
                patch.object(
                    downloads_runtime.runtime, "iphone_support_enabled", iphone_quality
                )
            )
            stack.enter_context(
                patch.object(
                    downloads_files,
                    "build_save_path",
                    side_effect=lambda _username, category: os.path.join(
                        account_folder,
                        category,
                    ),
                )
            )
            stack.enter_context(
                patch.object(
                    downloads_runtime.runtime, "active_task_id", "reels-scope-test"
                )
            )
            stack.enter_context(
                patch.object(
                    instagram_reels,
                    "INSTALOADER_VERSION",
                    instagram_reels._RAW_REELS_INSTALOADER_VERSION,
                )
            )
            stack.enter_context(
                patch.dict(
                    instagram_reels._ITER_FUNCS, {"reels": lambda _profile: iterator}
                )
            )
            stack.enter_context(
                patch.object(
                    downloads_checkpoints, "_read_resume_checkpoint", return_value=None
                )
            )
            stack.enter_context(
                patch.object(
                    downloads_checkpoints, "_save_resume_checkpoint", return_value=True
                )
            )
            stack.enter_context(
                patch.object(downloads_checkpoints, "_delete_resume_checkpoint")
            )
            completed_scope = stack.enter_context(
                patch.object(downloads_events, "_emit_scope_completed")
            )
            detail = stack.enter_context(
                patch.object(Post, "from_shortcode", return_value=detail_post)
            )
            filename_generation = stack.enter_context(
                patch.object(
                    downloads_files,
                    "generate_filenames_post",
                    return_value=[os.path.join(save_folder, "fallback.mp4")],
                )
            )
            download_helper = stack.enter_context(
                patch.object(
                    downloads_transfer, "download_post_to_paths", return_value=True
                )
            )

            if existing_summary_file:
                timestamp = datetime.fromtimestamp(
                    raw_media["taken_at"],
                    timezone.utc,
                ).strftime("%Y-%m-%d_%H-%M-%S")
                existing_folder = os.path.join(
                    account_folder,
                    existing_summary_category,
                )
                os.makedirs(existing_folder, exist_ok=True)
                predicted_path = os.path.join(existing_folder, timestamp + ".mp4")
                with open(predicted_path, "wb") as existing_file:
                    existing_file.write(b"already-downloaded")

            result = downloads_posts.download_post_pages("alice", "reels", 1)

        return SimpleNamespace(
            result=result,
            context=context,
            detail=detail,
            filename_generation=filename_generation,
            download_helper=download_helper,
            completed_scope=completed_scope,
            delay=delay_mock,
        )

    def test_reels_valid_summary_hit_skips_shortcode_detail_request(self):
        taken_at = int(datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc).timestamp())
        for existing_category in ("posts", "reels"):
            for product_type in ("clips", "feed"):
                with self.subTest(
                    existing_category=existing_category,
                    product_type=product_type,
                ):
                    run = self._run_reels_page(
                        {
                            "code": f"local-{product_type}-reel",
                            "taken_at": taken_at,
                            "media_type": 2,
                            "product_type": product_type,
                        },
                        existing_summary_file=True,
                        existing_summary_category=existing_category,
                    )

                    self.assertEqual(
                        run.result["status"],
                        downloads_models.status_code.SUCCESS,
                    )
                    self.assertEqual(run.result["downloaded_count"], 0)
                    run.detail.assert_not_called()
                    run.filename_generation.assert_not_called()
                    run.download_helper.assert_not_called()
                    run.completed_scope.assert_called_once()

    def test_reels_posts_hit_short_circuits_reels_folder_check(self):
        with tempfile.TemporaryDirectory() as account_folder:
            posts_folder = os.path.join(account_folder, "posts")
            reels_folder = os.path.join(account_folder, "reels")
            os.makedirs(posts_folder)
            reel_path = os.path.join(reels_folder, "2026-01-02_03-04-05.mp4")
            with open(
                os.path.join(posts_folder, os.path.basename(reel_path)),
                "wb",
            ) as existing_file:
                existing_file.write(b"already-downloaded")

            with (
                patch.object(
                    downloads_files,
                    "build_save_path",
                    return_value=posts_folder,
                ),
                patch.object(downloads_files, "is_already_downloaded") as reels_check,
            ):
                already_downloaded = downloads_files._is_reel_already_downloaded(
                    "alice",
                    [reel_path],
                )

            self.assertTrue(already_downloaded)
            reels_check.assert_not_called()

    def test_reels_valid_summary_miss_fetches_shortcode_detail_exactly_once(self):
        taken_at = int(datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc).timestamp())
        run = self._run_reels_page(
            {
                "code": "missing-reel",
                "taken_at": taken_at,
                "media_type": 2,
                "product_type": "clips",
            }
        )

        self.assertEqual(run.result["status"], downloads_models.status_code.SUCCESS)
        self.assertEqual(run.result["downloaded_count"], 1)
        run.detail.assert_called_once_with(run.context, "missing-reel")
        run.download_helper.assert_called_once()

    def test_reels_iphone_media_info_is_paced_after_shortcode_detail(self):
        run = self._run_reels_page(
            {
                "code": "iphone-reel",
                "taken_at": int(
                    datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc).timestamp()
                ),
                "media_type": 2,
                "product_type": "clips",
            },
            iphone_quality=True,
        )

        self.assertEqual(run.result["status"], downloads_models.status_code.SUCCESS)
        run.detail.assert_called_once_with(run.context, "iphone-reel")
        run.download_helper.assert_called_once()
        self.assertEqual(run.delay.call_args_list, [call("request")] * 3)

    def test_reels_invalid_timestamp_falls_back_to_shortcode_detail_once(self):
        run = self._run_reels_page(
            {
                "code": "invalid-summary-reel",
                "taken_at": "not-an-integer",
                "media_type": 2,
                "product_type": "clips",
            }
        )

        self.assertEqual(run.result["status"], downloads_models.status_code.SUCCESS)
        self.assertEqual(run.result["downloaded_count"], 1)
        run.detail.assert_called_once_with(run.context, "invalid-summary-reel")
        run.download_helper.assert_called_once()

    def test_reels_mixed_page_only_fetches_details_for_summary_misses(self):
        base_time = int(datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc).timestamp())
        raw_nodes = [
            {
                "media": {
                    "code": f"reel-{index}",
                    "taken_at": base_time + index,
                    "media_type": 2,
                    "product_type": "clips",
                }
            }
            for index in range(12)
        ]
        iterator, context, _wrapped_nodes = make_node_iterator([raw_nodes])
        loader = SimpleNamespace(context=context)
        profile = SimpleNamespace(userid=405)

        with tempfile.TemporaryDirectory() as save_folder, ExitStack() as stack:
            self._patch_common(stack, loader, profile, save_folder)
            stack.enter_context(
                patch.object(
                    downloads_runtime.runtime, "active_task_id", "mixed-reels-test"
                )
            )
            stack.enter_context(
                patch.object(
                    instagram_reels,
                    "INSTALOADER_VERSION",
                    instagram_reels._RAW_REELS_INSTALOADER_VERSION,
                )
            )
            stack.enter_context(
                patch.dict(
                    instagram_reels._ITER_FUNCS, {"reels": lambda _profile: iterator}
                )
            )
            stack.enter_context(
                patch.object(
                    downloads_checkpoints, "_read_resume_checkpoint", return_value=None
                )
            )
            stack.enter_context(
                patch.object(downloads_checkpoints, "_delete_resume_checkpoint")
            )
            stack.enter_context(
                patch.object(
                    downloads_checkpoints, "_save_resume_checkpoint", return_value=True
                )
            )

            for raw_node in raw_nodes[:9]:
                existing_path = instagram_reels._raw_reel_existing_path(
                    raw_node, save_folder
                )
                if existing_path is None:
                    raise AssertionError(
                        "test fixture did not produce a valid Reel path"
                    )
                with open(existing_path, "wb") as existing_file:
                    existing_file.write(b"already-downloaded")

            def detail_for_shortcode(_context, shortcode):
                return simple_post(shortcode)

            detail = stack.enter_context(
                patch.object(
                    Post,
                    "from_shortcode",
                    side_effect=detail_for_shortcode,
                )
            )
            stack.enter_context(
                patch.object(
                    downloads_files,
                    "generate_filenames_post",
                    side_effect=lambda post, folder: [
                        os.path.join(folder, post.shortcode + ".mp4")
                    ],
                )
            )
            helper = stack.enter_context(
                patch.object(
                    downloads_transfer, "download_post_to_paths", return_value=True
                )
            )

            result = downloads_posts.download_post_pages("alice", "reels", 1)

        self.assertEqual(result["status"], downloads_models.status_code.SUCCESS)
        self.assertEqual(result["downloaded_count"], 3)
        self.assertEqual(detail.call_count, 3)
        self.assertEqual(
            [call.args[1] for call in detail.call_args_list],
            ["reel-9", "reel-10", "reel-11"],
        )
        self.assertEqual(helper.call_count, 3)

    def test_reels_exhausted_media_cap_completes_before_next_detail_request(self):
        base_time = int(datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc).timestamp())
        raw_nodes = [
            {
                "media": {
                    "code": f"reel-{index}",
                    "taken_at": base_time + index,
                    "media_type": 2,
                    "product_type": "clips",
                }
            }
            for index in range(2)
        ]
        iterator, context, _wrapped_nodes = make_node_iterator([raw_nodes])
        loader = SimpleNamespace(context=context)
        profile = SimpleNamespace(userid=406)

        with tempfile.TemporaryDirectory() as save_folder, ExitStack() as stack:
            self._patch_common(stack, loader, profile, save_folder)
            stack.enter_context(
                patch.object(
                    downloads_runtime.runtime, "active_task_id", "reels-cap-test"
                )
            )
            stack.enter_context(
                patch.object(
                    instagram_reels,
                    "INSTALOADER_VERSION",
                    instagram_reels._RAW_REELS_INSTALOADER_VERSION,
                )
            )
            stack.enter_context(
                patch.dict(
                    instagram_reels._ITER_FUNCS, {"reels": lambda _profile: iterator}
                )
            )
            stack.enter_context(
                patch.object(
                    downloads_checkpoints, "_read_resume_checkpoint", return_value=None
                )
            )
            stack.enter_context(
                patch.object(
                    downloads_checkpoints, "_save_resume_checkpoint", return_value=True
                )
            )
            delete_cursor = stack.enter_context(
                patch.object(downloads_checkpoints, "_delete_resume_checkpoint")
            )
            completed_scope = stack.enter_context(
                patch.object(downloads_events, "_emit_scope_completed")
            )
            detail = stack.enter_context(
                patch.object(
                    Post,
                    "from_shortcode",
                    side_effect=lambda _context, shortcode: simple_post(shortcode),
                )
            )
            stack.enter_context(
                patch.object(
                    downloads_files,
                    "generate_filenames_post",
                    side_effect=lambda post, folder: [
                        os.path.join(folder, post.shortcode + ".mp4")
                    ],
                )
            )

            def consume_one_request(_loader, _post, _paths, **kwargs):
                kwargs["request_budget"].consume()
                return True

            helper = stack.enter_context(
                patch.object(
                    downloads_transfer,
                    "download_post_to_paths",
                    side_effect=consume_one_request,
                )
            )

            result = downloads_posts.download_post_pages(
                "alice",
                "reels",
                1,
                media_request_cap=1,
            )

        self.assertEqual(result["status"], downloads_models.status_code.SUCCESS)
        self.assertEqual(result["completion_reason"], "media_request_cap")
        self.assertEqual(result["downloaded_count"], 1)
        self.assertEqual(result["media_request_count"], 1)
        self.assertEqual(
            [call.args[1] for call in detail.call_args_list],
            ["reel-0"],
        )
        helper.assert_called_once()
        delete_cursor.assert_called_once_with("reels", log=True)
        completed_scope.assert_not_called()

    def _run_full_scope_stop(self, media_type, item_count):
        items = [
            simple_story_item(f"{media_type}-{index}") for index in range(item_count)
        ]
        if media_type == "stories":
            source = FakeStory(items)
            loader = SimpleNamespace(
                context=SimpleNamespace(),
                get_stories=Mock(return_value=[source]),
            )
            runner = downloads_stories.download_stories
        else:
            source = SimpleNamespace(
                title="Saved",
                unique_id=303,
                get_items=Mock(return_value=iter(items)),
            )
            loader = SimpleNamespace(
                context=SimpleNamespace(),
                get_highlights=Mock(return_value=[source]),
            )
            runner = downloads_highlights.download_highlights
        profile = SimpleNamespace(userid=202)

        downloads_control.clear_stop_request()
        with tempfile.TemporaryDirectory() as save_folder, ExitStack() as stack:
            self._patch_common(stack, loader, profile, save_folder)
            stack.enter_context(
                patch.object(
                    downloads_control,
                    "wait_if_paused",
                    side_effect=downloads_control._raise_if_stop_requested,
                )
            )
            stack.enter_context(
                patch.object(
                    downloads_files,
                    "generate_filenames_storyitem",
                    side_effect=lambda item, folder: [
                        os.path.join(folder, item.shortcode + ".jpg")
                    ],
                )
            )
            stack.enter_context(
                patch.object(
                    downloads_files, "is_already_downloaded", return_value=False
                )
            )

            def stop_during_transfer(*_args, **_kwargs):
                downloads_control.request_stop_downloads()
                return True

            helper = stack.enter_context(
                patch.object(
                    downloads_transfer,
                    "download_storyitem_to_paths",
                    side_effect=stop_during_transfer,
                )
            )
            item_completed = stack.enter_context(
                patch.object(downloads_events, "_emit_item_completed")
            )
            scope_completed = stack.enter_context(
                patch.object(downloads_events, "_emit_scope_completed")
            )
            stop_error = None
            try:
                result = runner("alice", 1, scope_unit="full")
            except downloads_models.DownloadStopRequested as exc:
                result = None
                stop_error = exc
            finally:
                downloads_control.clear_stop_request()

        return SimpleNamespace(
            result=result,
            stop_error=stop_error,
            helper=helper,
            item_completed=item_completed,
            scope_completed=scope_completed,
        )

    def test_full_scopes_commit_final_item_before_honoring_stop(self):
        for media_type in ("stories", "highlights"):
            with self.subTest(media_type=media_type):
                run = self._run_full_scope_stop(media_type, item_count=1)

                self.assertIsNone(run.stop_error)
                self.assertEqual(
                    run.result["status"], downloads_models.status_code.SUCCESS
                )
                self.assertEqual(run.result["downloaded_count"], 1)
                self.assertEqual(run.result["scope_completed_count"], 1)
                run.helper.assert_called_once()
                run.item_completed.assert_called_once()
                run.scope_completed.assert_called_once()

    def test_full_scopes_stop_between_items_without_completing_scope(self):
        for media_type in ("stories", "highlights"):
            with self.subTest(media_type=media_type):
                run = self._run_full_scope_stop(media_type, item_count=2)

                self.assertIsNone(run.result)
                self.assertIsInstance(
                    run.stop_error,
                    downloads_models.DownloadStopRequested,
                )
                self.assertEqual(run.stop_error.downloaded_count, 1)
                self.assertEqual(run.stop_error.scope_completed_count, 0)
                run.helper.assert_called_once()
                run.item_completed.assert_called_once()
                run.scope_completed.assert_not_called()

    def test_full_scope_media_cap_completes_and_keeps_actual_scope_progress(self):
        for media_type in ("stories", "highlights"):
            with self.subTest(media_type=media_type):
                items = [
                    simple_story_item(f"{media_type}-{index}") for index in range(2)
                ]
                if media_type == "stories":
                    source = FakeStory(items)
                    loader = SimpleNamespace(
                        context=SimpleNamespace(),
                        get_stories=Mock(return_value=[source]),
                    )
                    runner = downloads_stories.download_stories
                else:
                    source = SimpleNamespace(
                        title="Saved",
                        unique_id=303,
                        get_items=Mock(return_value=iter(items)),
                    )
                    loader = SimpleNamespace(
                        context=SimpleNamespace(),
                        get_highlights=Mock(return_value=[source]),
                    )
                    runner = downloads_highlights.download_highlights
                profile = SimpleNamespace(userid=202)

                with tempfile.TemporaryDirectory() as save_folder, ExitStack() as stack:
                    self._patch_common(stack, loader, profile, save_folder)
                    stack.enter_context(
                        patch.object(
                            downloads_files,
                            "generate_filenames_storyitem",
                            side_effect=lambda item, folder: [
                                os.path.join(folder, item.shortcode + ".jpg")
                            ],
                        )
                    )
                    stack.enter_context(
                        patch.object(
                            downloads_files,
                            "is_already_downloaded",
                            return_value=False,
                        )
                    )

                    def consume_one_request(*_args, **kwargs):
                        kwargs["request_budget"].consume()
                        return True

                    helper = stack.enter_context(
                        patch.object(
                            downloads_transfer,
                            "download_storyitem_to_paths",
                            side_effect=consume_one_request,
                        )
                    )
                    scope_completed = stack.enter_context(
                        patch.object(downloads_events, "_emit_scope_completed")
                    )

                    result = runner(
                        "alice",
                        1,
                        scope_unit="full",
                        media_request_cap=1,
                    )

                self.assertEqual(result["status"], downloads_models.status_code.SUCCESS)
                self.assertEqual(
                    result["completion_reason"],
                    "media_request_cap",
                )
                self.assertEqual(result["downloaded_count"], 1)
                self.assertEqual(result["media_request_count"], 1)
                self.assertEqual(result["scope_completed_count"], 0)
                helper.assert_called_once()
                scope_completed.assert_not_called()

    def test_stories_full_scope_processes_every_returned_item(self):
        items = [simple_story_item(f"story-{index}") for index in range(4)]
        story = FakeStory(items)
        loader = SimpleNamespace(
            context=SimpleNamespace(),
            get_stories=Mock(return_value=[story]),
        )
        profile = SimpleNamespace(userid=202)

        with tempfile.TemporaryDirectory() as save_folder, ExitStack() as stack:
            self._patch_common(stack, loader, profile, save_folder)
            stack.enter_context(
                patch.object(
                    downloads_files,
                    "generate_filenames_storyitem",
                    side_effect=lambda item, folder: [
                        os.path.join(folder, item.shortcode + ".jpg")
                    ],
                )
            )
            stack.enter_context(
                patch.object(
                    downloads_files, "is_already_downloaded", return_value=False
                )
            )
            helper = stack.enter_context(
                patch.object(
                    downloads_transfer, "download_storyitem_to_paths", return_value=True
                )
            )
            completed_scope = stack.enter_context(
                patch.object(downloads_events, "_emit_scope_completed")
            )

            result = downloads_stories.download_stories("alice", 1, scope_unit="full")

        self.assertEqual(result["status"], downloads_models.status_code.SUCCESS)
        self.assertEqual(result["downloaded_count"], 4)
        self.assertEqual(result["download_failed_count"], 0)
        self.assertEqual(helper.call_count, 4)
        story.get_items.assert_called_once_with()
        completed_scope.assert_called_once()

    def test_story_web_request_does_not_add_a_speculative_iphone_gap(self):
        events = []
        story = FakeStory([])
        story._iphone_struct_ = {"items": []}
        story.get_items.side_effect = lambda: events.append("story_iphone") or iter(())
        loader = SimpleNamespace(
            context=SimpleNamespace(),
            get_stories=Mock(
                side_effect=lambda **_kwargs: events.append("story_web") or [story]
            ),
        )
        profile = SimpleNamespace(userid=202)

        with tempfile.TemporaryDirectory() as save_folder, ExitStack() as stack:
            delay_mock = self._patch_common(stack, loader, profile, save_folder)
            delay_mock.side_effect = lambda kind: events.append(f"delay:{kind}")
            stack.enter_context(
                patch.object(downloads_runtime.runtime, "iphone_support_enabled", True)
            )

            result = downloads_stories.download_stories("alice", 1, scope_unit="full")

        self.assertEqual(result["status"], downloads_models.status_code.SUCCESS)
        self.assertEqual(
            events[:3],
            [
                "delay:request",
                "story_web",
                "story_iphone",
            ],
        )
        self.assertEqual(delay_mock.call_args_list, [call("request")])

    def test_story_gap_is_not_used_when_iphone_quality_is_disabled(self):
        story = FakeStory([])
        loader = SimpleNamespace(
            context=SimpleNamespace(),
            get_stories=Mock(return_value=[story]),
        )
        profile = SimpleNamespace(userid=202)

        with tempfile.TemporaryDirectory() as save_folder, ExitStack() as stack:
            delay_mock = self._patch_common(stack, loader, profile, save_folder)
            stack.enter_context(
                patch.object(downloads_runtime.runtime, "iphone_support_enabled", False)
            )

            result = downloads_stories.download_stories("alice", 1, scope_unit="full")

        self.assertEqual(result["status"], downloads_models.status_code.SUCCESS)
        self.assertEqual(delay_mock.call_args_list, [call("request")])

    def test_stories_full_scope_skips_only_expired_web_only_items(self):
        matched = source_aware_story_item(
            "matched-story",
            101,
            expires_at=datetime(2999, 1, 1, tzinfo=timezone.utc),
            iphone_media={"pk": 101},
        )
        expired_web_only = source_aware_story_item(
            "expired-web-only",
            102,
            expires_at=datetime(2000, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
            iphone_media=None,
        )
        story = FakeStory([matched, expired_web_only])
        story._iphone_struct_ = {"items": [{"pk": 101}]}
        loader = SimpleNamespace(
            context=SimpleNamespace(),
            get_stories=Mock(return_value=[story]),
        )
        profile = SimpleNamespace(userid=202)

        with tempfile.TemporaryDirectory() as save_folder, ExitStack() as stack:
            self._patch_common(stack, loader, profile, save_folder)
            stack.enter_context(
                patch.object(downloads_runtime.runtime, "iphone_support_enabled", True)
            )
            filenames = stack.enter_context(
                patch.object(
                    downloads_files,
                    "generate_filenames_storyitem",
                    side_effect=lambda item, folder: [
                        os.path.join(folder, item.shortcode + ".jpg")
                    ],
                )
            )
            existing = stack.enter_context(
                patch.object(
                    downloads_files, "is_already_downloaded", return_value=False
                )
            )
            helper = stack.enter_context(
                patch.object(
                    downloads_transfer, "download_storyitem_to_paths", return_value=True
                )
            )
            completed_scope = stack.enter_context(
                patch.object(downloads_events, "_emit_scope_completed")
            )
            output = stack.enter_context(
                patch.object(diagnostics_console, "_module_print")
            )

            result = downloads_stories.download_stories("alice", 1, scope_unit="full")

        log_text = "\n".join(
            " ".join(str(arg) for arg in entry.args) for entry in output.call_args_list
        )
        self.assertEqual(result["status"], downloads_models.status_code.SUCCESS)
        self.assertEqual(result["downloaded_count"], 1)
        self.assertEqual(result["download_failed_count"], 0)
        self.assertEqual(filenames.call_count, 1)
        existing.assert_called_once()
        helper.assert_called_once()
        self.assertEqual(
            completed_scope.call_args.args[0]["item_count"],
            1,
        )
        self.assertIn("Skipping expired Web-only story expired-web-only", log_text)
        self.assertIn("media_id=102", log_text)
        self.assertIn("expired_at=2000-01-02T03:04:05Z", log_text)
        self.assertIn("Web items=2, iPhone items=1", log_text)
        self.assertIn("not counted as failures", log_text)

    def test_stories_full_scope_keeps_unexpired_web_only_failure_strict(self):
        matched = source_aware_story_item(
            "matched-story",
            201,
            expires_at=datetime(2999, 1, 1, tzinfo=timezone.utc),
            iphone_media={"pk": 201},
        )
        unexpired_web_only = source_aware_story_item(
            "unexpired-web-only",
            202,
            expires_at=datetime(2999, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
            iphone_media=None,
        )
        story = FakeStory([matched, unexpired_web_only])
        story._iphone_struct_ = {"items": [{"pk": 201}]}
        loader = SimpleNamespace(
            context=SimpleNamespace(),
            get_stories=Mock(return_value=[story]),
        )
        profile = SimpleNamespace(userid=202)

        with tempfile.TemporaryDirectory() as save_folder, ExitStack() as stack:
            self._patch_common(stack, loader, profile, save_folder)
            stack.enter_context(
                patch.object(downloads_runtime.runtime, "iphone_support_enabled", True)
            )
            stack.enter_context(
                patch.object(
                    downloads_files,
                    "generate_filenames_storyitem",
                    side_effect=lambda item, folder: [
                        os.path.join(folder, item.shortcode + ".jpg")
                    ],
                )
            )
            stack.enter_context(
                patch.object(
                    downloads_files, "is_already_downloaded", return_value=False
                )
            )
            helper = stack.enter_context(
                patch.object(
                    downloads_transfer,
                    "download_storyitem_to_paths",
                    side_effect=[
                        True,
                        downloads_models.IPhoneQualityUnavailable(
                            "Empty iPhone media info for unexpired-web-only"
                        ),
                    ],
                )
            )
            completed_scope = stack.enter_context(
                patch.object(downloads_events, "_emit_scope_completed")
            )
            output = stack.enter_context(
                patch.object(diagnostics_console, "_module_print")
            )

            result = downloads_stories.download_stories("alice", 1, scope_unit="full")

        log_text = "\n".join(
            " ".join(str(arg) for arg in entry.args) for entry in output.call_args_list
        )
        self.assertEqual(
            result["status"],
            downloads_models.status_code.IPHONE_QUALITY_UNAVAILABLE,
        )
        self.assertEqual(result["downloaded_count"], 1)
        self.assertEqual(result["download_failed_count"], 1)
        self.assertEqual(helper.call_count, 2)
        completed_scope.assert_not_called()
        self.assertIn("Story Web/iPhone mismatch for unexpired-web-only", log_text)
        self.assertIn("item is not confirmed expired", log_text)
        self.assertNotIn("Skipping expired Web-only story unexpired-web-only", log_text)

    def test_stories_full_scope_does_not_skip_without_valid_iphone_snapshot(self):
        expired = source_aware_story_item(
            "expired-with-invalid-snapshot",
            301,
            expires_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
            iphone_media=None,
        )
        story = FakeStory([expired])
        story._iphone_struct_ = {"items": "malformed"}
        loader = SimpleNamespace(
            context=SimpleNamespace(),
            get_stories=Mock(return_value=[story]),
        )
        profile = SimpleNamespace(userid=202)

        with tempfile.TemporaryDirectory() as save_folder, ExitStack() as stack:
            self._patch_common(stack, loader, profile, save_folder)
            stack.enter_context(
                patch.object(downloads_runtime.runtime, "iphone_support_enabled", True)
            )
            stack.enter_context(
                patch.object(
                    downloads_files,
                    "generate_filenames_storyitem",
                    return_value=[os.path.join(save_folder, "expired.jpg")],
                )
            )
            stack.enter_context(
                patch.object(
                    downloads_files, "is_already_downloaded", return_value=False
                )
            )
            helper = stack.enter_context(
                patch.object(
                    downloads_transfer,
                    "download_storyitem_to_paths",
                    side_effect=downloads_models.IPhoneQualityUnavailable(
                        "Malformed iPhone Story snapshot"
                    ),
                )
            )

            result = downloads_stories.download_stories("alice", 1, scope_unit="full")

        self.assertEqual(
            result["status"],
            downloads_models.status_code.IPHONE_QUALITY_UNAVAILABLE,
        )
        self.assertEqual(result["download_failed_count"], 1)
        helper.assert_called_once()

    @staticmethod
    def _story_without_iphone_reel(expiring_timestamps):
        """Real Story whose iPhone response omits the owner's reel."""
        context = Mock(iphone_support=True, is_logged_in=True)
        context.get_iphone_json.return_value = {
            "reels": {},
            "reels_media": [],
            "status": "ok",
        }
        story = Story(
            context,
            {
                "user": {"id": "202", "username": "alice"},
                "items": [
                    {
                        "id": str(500 + index),
                        "is_video": False,
                        "expiring_at_timestamp": timestamp,
                    }
                    for index, timestamp in enumerate(expiring_timestamps)
                ],
            },
        )
        loader = SimpleNamespace(
            context=context,
            get_stories=Mock(return_value=[story]),
        )
        return loader, context

    def test_stories_full_scope_without_iphone_reel_skips_expired_web_items(self):
        # 2000-01-02T03:04:05Z and one minute later: stale Web items only.
        loader, context = self._story_without_iphone_reel([946782245, 946782305])
        profile = SimpleNamespace(userid=202)

        with tempfile.TemporaryDirectory() as save_folder, ExitStack() as stack:
            self._patch_common(stack, loader, profile, save_folder)
            stack.enter_context(
                patch.object(downloads_runtime.runtime, "iphone_support_enabled", True)
            )
            filenames = stack.enter_context(
                patch.object(downloads_files, "generate_filenames_storyitem")
            )
            helper = stack.enter_context(
                patch.object(downloads_transfer, "download_storyitem_to_paths")
            )
            completed_scope = stack.enter_context(
                patch.object(downloads_events, "_emit_scope_completed")
            )
            output = stack.enter_context(
                patch.object(diagnostics_console, "_module_print")
            )

            result = downloads_stories.download_stories("alice", 1, scope_unit="full")

        log_text = "\n".join(
            " ".join(str(arg) for arg in entry.args) for entry in output.call_args_list
        )
        self.assertEqual(result["status"], downloads_models.status_code.SUCCESS)
        self.assertEqual(result["downloaded_count"], 0)
        self.assertEqual(result["download_failed_count"], 0)
        self.assertEqual(result["scope_completed_count"], 1)
        context.get_iphone_json.assert_called_once()
        filenames.assert_not_called()
        helper.assert_not_called()
        self.assertEqual(completed_scope.call_args.args[0]["item_count"], 0)
        self.assertIn(
            "No current iPhone Story reel for @alice; all 2 Web item(s) have expired",
            log_text,
        )
        self.assertIn("expired_at=2000-01-02T03:04:05Z", log_text)
        self.assertIn("Web items=2, iPhone items=0", log_text)
        self.assertIn("Skipped 2 expired Web-only story item(s)", log_text)

    def test_stories_full_scope_without_iphone_reel_fails_for_unexpired_item(self):
        unexpired = int(datetime.now(timezone.utc).timestamp()) + 86400
        loader, context = self._story_without_iphone_reel([unexpired, 946782245])
        profile = SimpleNamespace(userid=202)

        with tempfile.TemporaryDirectory() as save_folder, ExitStack() as stack:
            self._patch_common(stack, loader, profile, save_folder)
            stack.enter_context(
                patch.object(downloads_runtime.runtime, "iphone_support_enabled", True)
            )
            filenames = stack.enter_context(
                patch.object(downloads_files, "generate_filenames_storyitem")
            )
            completed_scope = stack.enter_context(
                patch.object(downloads_events, "_emit_scope_completed")
            )

            result = downloads_stories.download_stories("alice", 1, scope_unit="full")

        self.assertEqual(
            result["status"],
            downloads_models.status_code.IPHONE_QUALITY_UNAVAILABLE,
        )
        self.assertIn(
            "iPhone reels_media returned no Story reel; "
            "1 of 2 Web item(s) are not confirmed expired",
            result["message"],
        )
        self.assertEqual(result["downloaded_count"], 0)
        self.assertEqual(result["scope_completed_count"], 0)
        # Fails strictly from the one reel response: no per-item iPhone
        # lookup, Web-quality fallback or CDN request.
        context.get_iphone_json.assert_called_once()
        context.get_raw.assert_not_called()
        filenames.assert_not_called()
        completed_scope.assert_not_called()

    def test_story_source_match_is_not_skipped_even_after_expiration(self):
        expired_but_matched = source_aware_story_item(
            "expired-but-matched",
            401,
            expires_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
            iphone_media={"pk": 401},
        )

        self.assertIsNone(
            instagram_media._storyitem_web_only_info(
                expired_but_matched,
                frozenset({"401"}),
                observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        )

    def test_stories_full_scope_counts_existing_items_in_callback(self):
        items = [simple_story_item(f"story-{index}") for index in range(3)]
        story = FakeStory(items)
        loader = SimpleNamespace(
            context=SimpleNamespace(),
            get_stories=Mock(return_value=[story]),
        )
        profile = SimpleNamespace(userid=202)

        with tempfile.TemporaryDirectory() as save_folder, ExitStack() as stack:
            self._patch_common(stack, loader, profile, save_folder)
            stack.enter_context(
                patch.object(
                    downloads_files,
                    "generate_filenames_storyitem",
                    side_effect=lambda item, folder: [
                        os.path.join(folder, item.shortcode + ".jpg")
                    ],
                )
            )
            stack.enter_context(
                patch.object(
                    downloads_files,
                    "is_already_downloaded",
                    side_effect=[True, False, True],
                )
            )
            helper = stack.enter_context(
                patch.object(
                    downloads_transfer, "download_storyitem_to_paths", return_value=True
                )
            )
            completed_scope = stack.enter_context(
                patch.object(downloads_events, "_emit_scope_completed")
            )

            result = downloads_stories.download_stories("alice", 1, scope_unit="full")

        self.assertEqual(result["status"], downloads_models.status_code.SUCCESS)
        self.assertEqual(result["downloaded_count"], 1)
        helper.assert_called_once()
        self.assertEqual(completed_scope.call_args.args[0]["item_count"], 3)

    def test_stories_empty_filename_fails_full_scope_with_contract_fields(self):
        story = FakeStory([simple_story_item("broken-story")])
        loader = SimpleNamespace(
            context=SimpleNamespace(),
            get_stories=Mock(return_value=[story]),
        )
        profile = SimpleNamespace(userid=202)

        with tempfile.TemporaryDirectory() as save_folder, ExitStack() as stack:
            self._patch_common(stack, loader, profile, save_folder)
            stack.enter_context(
                patch.object(
                    downloads_files, "generate_filenames_storyitem", return_value=[]
                )
            )
            helper = stack.enter_context(
                patch.object(downloads_transfer, "download_storyitem_to_paths")
            )
            completed_scope = stack.enter_context(
                patch.object(downloads_events, "_emit_scope_completed")
            )
            result = downloads_stories.download_stories(
                "alice",
                1,
                scope_unit="full",
                media_request_cap=3,
            )

        self.assertEqual(result["status"], downloads_models.status_code.FAILED)
        self.assertEqual(result["scope_completed_count"], 0)
        self.assertEqual(result["media_request_count"], 0)
        self.assertEqual(result["download_failed_count"], 1)
        helper.assert_not_called()
        completed_scope.assert_not_called()

    def test_highlights_full_scope_processes_all_items_in_all_collections(self):
        highlights = [
            SimpleNamespace(
                title="Summer",
                unique_id=301,
                get_items=Mock(
                    return_value=iter(
                        [simple_story_item("summer-1"), simple_story_item("summer-2")]
                    )
                ),
            ),
            SimpleNamespace(
                title="Winter",
                unique_id=302,
                get_items=Mock(
                    return_value=iter(
                        [simple_story_item("winter-1"), simple_story_item("winter-2")]
                    )
                ),
            ),
        ]
        loader = SimpleNamespace(
            context=SimpleNamespace(),
            get_highlights=Mock(return_value=highlights),
        )
        profile = SimpleNamespace(userid=303)

        with tempfile.TemporaryDirectory() as save_folder, ExitStack() as stack:
            self._patch_common(stack, loader, profile, save_folder)
            stack.enter_context(
                patch.object(
                    downloads_files,
                    "generate_filenames_storyitem",
                    side_effect=lambda item, folder: [
                        os.path.join(folder, item.shortcode + ".jpg")
                    ],
                )
            )
            stack.enter_context(
                patch.object(
                    downloads_files, "is_already_downloaded", return_value=False
                )
            )
            helper = stack.enter_context(
                patch.object(
                    downloads_transfer, "download_storyitem_to_paths", return_value=True
                )
            )
            completed_scope = stack.enter_context(
                patch.object(downloads_events, "_emit_scope_completed")
            )

            result = downloads_highlights.download_highlights(
                "alice", 1, scope_unit="full"
            )

        self.assertEqual(result["status"], downloads_models.status_code.SUCCESS)
        self.assertEqual(result["downloaded_count"], 4)
        self.assertEqual(result["download_failed_count"], 0)
        self.assertEqual(helper.call_count, 4)
        for highlight in highlights:
            highlight.get_items.assert_called_once_with()
        completed_scope.assert_called_once()

    def test_highlights_failed_get_stops_immediately_and_preserves_full_contract(self):
        items = [simple_story_item(f"highlight-{index}") for index in range(3)]
        highlight = SimpleNamespace(
            title="Summer",
            unique_id=303,
            get_items=Mock(return_value=iter(items)),
        )
        loader = SimpleNamespace(
            context=SimpleNamespace(),
            get_highlights=Mock(return_value=[highlight]),
        )
        profile = SimpleNamespace(userid=303)

        with tempfile.TemporaryDirectory() as save_folder, ExitStack() as stack:
            self._patch_common(stack, loader, profile, save_folder)
            stack.enter_context(
                patch.object(
                    downloads_files,
                    "generate_filenames_storyitem",
                    side_effect=lambda item, folder: [
                        os.path.join(folder, item.shortcode + ".jpg")
                    ],
                )
            )
            stack.enter_context(
                patch.object(
                    downloads_files, "is_already_downloaded", return_value=False
                )
            )

            def failed_get(_loader, _item, _paths, **kwargs):
                kwargs["request_budget"].consume()
                raise RuntimeError("simulated CDN failure")

            helper = stack.enter_context(
                patch.object(
                    downloads_transfer,
                    "download_storyitem_to_paths",
                    side_effect=failed_get,
                )
            )
            completed_scope = stack.enter_context(
                patch.object(downloads_events, "_emit_scope_completed")
            )
            result = downloads_highlights.download_highlights(
                "alice",
                1,
                scope_unit="full",
                media_request_cap=1,
            )

        self.assertEqual(result["status"], downloads_models.status_code.FAILED)
        self.assertEqual(result["scope_completed_count"], 0)
        self.assertEqual(result["media_request_count"], 1)
        self.assertEqual(result["download_failed_count"], 1)
        helper.assert_called_once()
        completed_scope.assert_not_called()

    def test_highlights_full_callback_counts_existing_items(self):
        items = [simple_story_item(f"highlight-{index}") for index in range(3)]
        highlight = SimpleNamespace(
            title="Summer",
            unique_id=303,
            get_items=Mock(return_value=iter(items)),
        )
        loader = SimpleNamespace(
            context=SimpleNamespace(),
            get_highlights=Mock(return_value=[highlight]),
        )
        profile = SimpleNamespace(userid=303)

        with tempfile.TemporaryDirectory() as save_folder, ExitStack() as stack:
            self._patch_common(stack, loader, profile, save_folder)
            stack.enter_context(
                patch.object(
                    downloads_files,
                    "generate_filenames_storyitem",
                    side_effect=lambda item, folder: [
                        os.path.join(folder, item.shortcode + ".jpg")
                    ],
                )
            )
            stack.enter_context(
                patch.object(
                    downloads_files,
                    "is_already_downloaded",
                    side_effect=[True, False, True],
                )
            )
            helper = stack.enter_context(
                patch.object(
                    downloads_transfer, "download_storyitem_to_paths", return_value=True
                )
            )
            completed_scope = stack.enter_context(
                patch.object(downloads_events, "_emit_scope_completed")
            )
            result = downloads_highlights.download_highlights(
                "alice", 1, scope_unit="full"
            )

        self.assertEqual(result["status"], downloads_models.status_code.SUCCESS)
        self.assertEqual(result["downloaded_count"], 1)
        helper.assert_called_once()
        self.assertEqual(completed_scope.call_args.args[0]["item_count"], 3)

    def test_highlights_empty_filename_fails_full_scope(self):
        highlight = SimpleNamespace(
            title="Broken",
            unique_id=304,
            get_items=Mock(return_value=iter([simple_story_item("broken-highlight")])),
        )
        loader = SimpleNamespace(
            context=SimpleNamespace(),
            get_highlights=Mock(return_value=[highlight]),
        )
        profile = SimpleNamespace(userid=303)

        with tempfile.TemporaryDirectory() as save_folder, ExitStack() as stack:
            self._patch_common(stack, loader, profile, save_folder)
            stack.enter_context(
                patch.object(
                    downloads_files, "generate_filenames_storyitem", return_value=[]
                )
            )
            helper = stack.enter_context(
                patch.object(downloads_transfer, "download_storyitem_to_paths")
            )
            completed_scope = stack.enter_context(
                patch.object(downloads_events, "_emit_scope_completed")
            )
            result = downloads_highlights.download_highlights(
                "alice",
                1,
                scope_unit="full",
                media_request_cap=3,
            )

        self.assertEqual(result["status"], downloads_models.status_code.FAILED)
        self.assertEqual(result["scope_completed_count"], 0)
        self.assertEqual(result["media_request_count"], 0)
        self.assertEqual(result["download_failed_count"], 1)
        helper.assert_not_called()
        completed_scope.assert_not_called()

    def test_serial_dispatch_defaults_to_page_and_full_scopes(self):
        success = {
            "status": downloads_models.status_code.SUCCESS,
            "message": "ok",
            "downloaded_count": 0,
        }
        with (
            patch.object(downloads_control, "wait_if_paused"),
            patch.object(notifications_service, "notify"),
            patch.object(
                downloads_posts, "download_post_pages", return_value=success
            ) as pages,
            patch.object(downloads_posts, "download_posts") as legacy_posts,
            patch.object(
                downloads_stories, "download_stories", return_value=success
            ) as stories,
            patch.object(
                downloads_highlights, "download_highlights", return_value=success
            ) as highlights,
        ):
            for media_type in ("posts", "reels", "tagged"):
                downloads_service.download_user_content(
                    "alice",
                    media_type,
                    2,
                    scope_unit=None,
                )
            downloads_service.download_user_content(
                "alice",
                "stories",
                9,
                scope_unit=None,
            )
            downloads_service.download_user_content(
                "alice",
                "highlights",
                9,
                scope_unit=None,
            )

        self.assertEqual(
            [mock_call.args[:3] for mock_call in pages.call_args_list],
            [
                ("alice", "posts", 2),
                ("alice", "reels", 2),
                ("alice", "tagged", 2),
            ],
        )
        legacy_posts.assert_not_called()
        stories.assert_called_once()
        highlights.assert_called_once()
        self.assertEqual(stories.call_args.kwargs["scope_unit"], "full")
        self.assertEqual(highlights.call_args.kwargs["scope_unit"], "full")

    def test_dispatch_rejects_invalid_scope_before_worker(self):
        worker_names = (
            (downloads_posts, "download_post_pages"),
            (downloads_posts, "download_posts"),
        )
        with ExitStack() as stack:
            stack.enter_context(patch.object(downloads_control, "wait_if_paused"))
            workers = [
                stack.enter_context(patch.object(owner, name))
                for owner, name in worker_names
            ]
            for scope_unit in ("item", "full", "unknown"):
                with self.subTest(scope_unit=scope_unit):
                    result = downloads_service.download_user_content(
                        "alice",
                        "posts",
                        3,
                        scope_unit=scope_unit,
                    )
                    self.assertEqual(
                        result["status"], downloads_models.status_code.FAILED
                    )

        for worker in workers:
            worker.assert_not_called()


if __name__ == "__main__":
    unittest.main()
