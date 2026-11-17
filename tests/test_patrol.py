"""Offline contract tests for the serial patrol download path.

These tests deliberately mock profile/API/CDN access. They exercise the
detection boundary and the promoted full serial scan without an Instagram
session.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from instaloader import (
    NodeIterator,
    Post,
)
from instaloader.exceptions import (
    AbortDownloadException,
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
import inscrawler.downloads.stories as downloads_stories
import inscrawler.downloads.transfer as downloads_transfer
import inscrawler.instagram.hotfixes as instagram_hotfixes
import inscrawler.instagram.profile as instagram_profile
import inscrawler.instagram.reels as instagram_reels
import inscrawler.instagram.session as instagram_session
import inscrawler.tasks.patrol as tasks_patrol

PATROL_STAT_KEYS = (
    "checked_count",
    "already_downloaded_count",
    "updates_found_count",
    "unresolved_count",
    "downloaded_count",
    "download_failed_count",
    "no_updates_confirmed",
)


class CountingIterator:
    """Iterator that records yielded items without counting StopIteration."""

    def __init__(self, items):
        self._items = iter(items)
        self.yielded_count = 0

    def __iter__(self):
        return self

    def __next__(self):
        item = next(self._items)
        self.yielded_count += 1
        return item


class FakePagedContext:
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


def make_paged_iterator(posts, page_sizes=None, *, node_builder=None, wrapper=None):
    posts = list(posts)
    if page_sizes is None:
        page_sizes = [1] * len(posts)
    page_sizes = list(page_sizes)
    if sum(page_sizes) != len(posts):
        raise ValueError("page_sizes must cover every post exactly once")

    pages = []
    offset = 0
    for index, size in enumerate(page_sizes):
        values = posts[offset : offset + size]
        offset += size
        nodes = [
            node_builder(value) if node_builder is not None else {"post": value}
            for value in values
        ]
        has_next = index + 1 < len(page_sizes)
        pages.append(
            _page(
                nodes,
                cursor=f"cursor-{index + 1}" if has_next else None,
                has_next=has_next,
            )
        )
    if not pages:
        pages = [_page([])]

    context = FakePagedContext(
        {f"cursor-{index}": pages[index] for index in range(1, len(pages))}
    )
    iterator = NodeIterator(
        context=context,
        query_hash="offline-patrol-query",
        edge_extractor=lambda response: response,
        node_wrapper=(wrapper if wrapper is not None else lambda node: node["post"]),
        first_data=pages[0],
    )
    iterator.factory = Mock(return_value=iterator)
    return iterator, context


class FakeStory:
    def __init__(self, items):
        self.iterator = CountingIterator(items)
        self.get_items = Mock(return_value=self.iterator)


class FakeHighlight:
    def __init__(self, title, unique_id, items):
        self.title = title
        self.unique_id = unique_id
        self.iterator = CountingIterator(items)
        self.get_items = Mock(return_value=self.iterator)


def patrol_result(*, checked, already, updates, downloaded):
    return {
        "status": downloads_models.status_code.SUCCESS,
        "checked_count": checked,
        "already_downloaded_count": already,
        "updates_found_count": updates,
        "unresolved_count": 0,
        "downloaded_count": downloaded,
        "download_failed_count": 0,
        "no_updates_confirmed": updates == 0,
    }


class PatrolDownloadTests(unittest.TestCase):
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

    def assert_patrol_stats(self, result, *, checked, already, updates, downloaded):
        expected = patrol_result(
            checked=checked,
            already=already,
            updates=updates,
            downloaded=downloaded,
        )
        self.assertEqual(result.get("status"), downloads_models.status_code.SUCCESS)
        self.assertEqual(
            {key: result.get(key) for key in PATROL_STAT_KEYS},
            {key: expected[key] for key in PATROL_STAT_KEYS},
        )

    def _run_posts(
        self,
        already_flags,
        *,
        patrol_page_limit,
        patrol_resume_task_id=None,
        resume_loaded=False,
        resume_observer=None,
        save_succeeds=True,
        download_succeeds=True,
        download_exception=None,
        existing_exception_at=None,
        pathless_indexes=(),
        timeline=None,
        page_sizes=None,
        page_overrides=None,
        delay_side_effect=None,
    ):
        posts = [
            SimpleNamespace(marker=index, shortcode=f"post-{index}")
            for index in range(len(already_flags))
        ]
        iterator, context = make_paged_iterator(posts, page_sizes)
        if page_overrides:
            context.pages_by_cursor.update(page_overrides)
        loader = SimpleNamespace(context=context)
        profile = SimpleNamespace(userid=101)
        already_by_marker = {
            f"post-{index}": value for index, value in enumerate(already_flags)
        }
        save_outcomes = (
            iter(save_succeeds) if isinstance(save_succeeds, (list, tuple)) else None
        )

        def paths_for(post, folder):
            if post.marker in pathless_indexes:
                return []
            return [os.path.join(folder, post.shortcode + ".jpg")]

        def is_existing(paths):
            marker = Path(paths[0]).stem
            if timeline is not None:
                timeline.append(("checked", marker))
            if marker == existing_exception_at:
                raise downloads_models.DownloadStopRequested()
            return already_by_marker[marker]

        def download_item(_loader, post, _paths, **_kwargs):
            if timeline is not None:
                timeline.append(("download", post.shortcode))
            if download_exception is not None:
                raise download_exception
            return download_succeeds

        with tempfile.TemporaryDirectory() as save_folder, ExitStack() as stack:
            stack.enter_context(
                patch.object(instagram_session, "get_loader", return_value=loader)
            )
            stack.enter_context(
                patch.object(instagram_profile, "resolve_profile", return_value=profile)
            )
            stack.enter_context(
                patch.object(instagram_profile, "_ensure_accessible", return_value=None)
            )
            profile_sync_mock = stack.enter_context(
                patch.object(downloads_avatar, "sync_profile_picture_best_effort")
            )
            stack.enter_context(
                patch.object(
                    downloads_files, "build_save_path", return_value=save_folder
                )
            )
            stack.enter_context(
                patch.dict(instagram_reels._ITER_FUNCS, {"posts": iterator.factory})
            )
            read_resume_mock = stack.enter_context(
                patch.object(
                    downloads_checkpoints,
                    "_read_resume_checkpoint",
                    return_value=(object() if resume_loaded else None),
                )
            )
            stack.enter_context(
                patch.object(
                    downloads_checkpoints,
                    "_thaw_resume_checkpoint",
                    return_value=resume_loaded,
                )
            )

            def save_cursor(*_args, **_kwargs):
                if timeline is not None:
                    timeline.append(("cursor", "save"))
                if save_outcomes is not None:
                    return next(save_outcomes)
                return save_succeeds

            save_mock = stack.enter_context(
                patch.object(
                    downloads_checkpoints,
                    "_save_resume_checkpoint",
                    side_effect=save_cursor,
                )
            )
            delete_mock = stack.enter_context(
                patch.object(downloads_checkpoints, "_delete_resume_checkpoint")
            )
            stack.enter_context(patch.object(downloads_control, "wait_if_paused"))
            delay_mock = stack.enter_context(
                patch.object(
                    downloads_control,
                    "delay",
                    side_effect=delay_side_effect,
                )
            )
            iterator.delay_mock = delay_mock
            stack.enter_context(
                patch.object(downloads_events, "emit_content_phase_status")
            )
            stack.enter_context(patch.object(downloads_events, "emit_download_status"))
            stack.enter_context(patch.object(downloads_events, "_emit_item_completed"))
            stack.enter_context(
                patch.object(
                    downloads_files, "generate_filenames_post", side_effect=paths_for
                )
            )
            existing_mock = stack.enter_context(
                patch.object(
                    downloads_files, "is_already_downloaded", side_effect=is_existing
                )
            )
            download_mock = stack.enter_context(
                patch.object(
                    downloads_transfer,
                    "download_post_to_paths",
                    side_effect=download_item,
                )
            )

            kwargs = {"patrol_page_limit": patrol_page_limit}
            if patrol_resume_task_id is not None:
                kwargs["patrol_resume_task_id"] = patrol_resume_task_id
            result = downloads_posts.download_posts("alice", "posts", **kwargs)
            if patrol_resume_task_id is None:
                read_resume_mock.assert_not_called()
            if resume_observer is not None:
                resume_observer.update(
                    {
                        "load": read_resume_mock,
                        "save": save_mock,
                        "delete": delete_mock,
                        "profile_sync": profile_sync_mock,
                    }
                )

        return result, iterator, existing_mock, download_mock

    def _run_stories(
        self,
        already_flags,
        *,
        limit,
        patrol_mode=False,
        sync_observer=None,
        timeline=None,
    ):
        items = [
            SimpleNamespace(marker=index, shortcode=f"story-{index}")
            for index in range(len(already_flags))
        ]
        story = FakeStory(items)
        loader = SimpleNamespace(
            context=object(),
            get_stories=Mock(return_value=[story]),
        )
        profile = SimpleNamespace(userid=202)
        already_by_marker = {
            f"story-{index}": value for index, value in enumerate(already_flags)
        }

        def paths_for(item, folder):
            return [os.path.join(folder, item.shortcode + ".jpg")]

        def is_existing(paths):
            marker = Path(paths[0]).stem
            if timeline is not None:
                timeline.append(("checked", marker))
            return already_by_marker[marker]

        def download_item(_loader, item, _paths, **_kwargs):
            if timeline is not None:
                timeline.append(("download", item.shortcode))
            return True

        with tempfile.TemporaryDirectory() as save_folder, ExitStack() as stack:
            stack.enter_context(
                patch.object(instagram_session, "get_loader", return_value=loader)
            )
            stack.enter_context(
                patch.object(instagram_profile, "resolve_profile", return_value=profile)
            )
            stack.enter_context(
                patch.object(instagram_profile, "_ensure_accessible", return_value=None)
            )
            profile_sync_mock = stack.enter_context(
                patch.object(downloads_avatar, "sync_profile_picture_best_effort")
            )
            stack.enter_context(
                patch.object(
                    downloads_files, "build_save_path", return_value=save_folder
                )
            )
            stack.enter_context(patch.object(downloads_control, "wait_if_paused"))
            stack.enter_context(patch.object(downloads_control, "delay"))
            stack.enter_context(
                patch.object(downloads_events, "emit_content_phase_status")
            )
            stack.enter_context(patch.object(downloads_events, "emit_download_status"))
            stack.enter_context(patch.object(downloads_events, "_emit_item_completed"))
            stack.enter_context(
                patch.object(
                    downloads_files,
                    "generate_filenames_storyitem",
                    side_effect=paths_for,
                )
            )
            existing_mock = stack.enter_context(
                patch.object(
                    downloads_files, "is_already_downloaded", side_effect=is_existing
                )
            )
            download_mock = stack.enter_context(
                patch.object(
                    downloads_transfer,
                    "download_storyitem_to_paths",
                    side_effect=download_item,
                )
            )

            kwargs = {}
            if patrol_mode:
                kwargs["patrol_mode"] = True
            result = downloads_stories.download_stories("alice", limit, **kwargs)
            if sync_observer is not None:
                sync_observer["profile_sync"] = profile_sync_mock

        return result, loader, existing_mock, download_mock

    def _run_highlights(
        self,
        already_flags_by_highlight,
        *,
        patrol_mode=False,
        pathless_markers=(),
        download_results=None,
        download_exception_marker=None,
    ):
        highlights = []
        already_by_marker = {}
        for highlight_index, already_flags in enumerate(already_flags_by_highlight):
            items = []
            for item_index, already in enumerate(already_flags):
                marker = f"highlight-{highlight_index}-{item_index}"
                items.append(SimpleNamespace(marker=marker, shortcode=marker))
                already_by_marker[marker] = already
            highlights.append(
                FakeHighlight(
                    f"Collection {highlight_index}",
                    highlight_index,
                    items,
                )
            )

        profile = SimpleNamespace(userid=303)
        loader = SimpleNamespace(
            context=object(),
            get_highlights=Mock(return_value=highlights),
        )
        prepared_folders = []

        def paths_for(item, folder):
            prepared_folders.append(Path(folder).name)
            if item.marker in pathless_markers:
                return []
            return [os.path.join(folder, item.shortcode + ".jpg")]

        def is_existing(paths):
            return already_by_marker[Path(paths[0]).stem]

        def download_item(_loader, item, _paths, **_kwargs):
            if item.marker == download_exception_marker:
                raise RuntimeError("highlight download failed")
            return (download_results or {}).get(item.marker, True)

        with tempfile.TemporaryDirectory() as save_folder, ExitStack() as stack:
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
                patch.object(
                    downloads_files, "build_save_path", return_value=save_folder
                )
            )
            stack.enter_context(patch.object(downloads_control, "wait_if_paused"))
            stack.enter_context(patch.object(downloads_control, "delay"))
            stack.enter_context(
                patch.object(downloads_events, "emit_content_phase_status")
            )
            stack.enter_context(patch.object(downloads_events, "emit_api_status"))
            stack.enter_context(patch.object(downloads_events, "emit_download_status"))
            stack.enter_context(patch.object(downloads_events, "_emit_item_completed"))
            stack.enter_context(
                patch.object(
                    downloads_files,
                    "generate_filenames_storyitem",
                    side_effect=paths_for,
                )
            )
            existing_mock = stack.enter_context(
                patch.object(
                    downloads_files, "is_already_downloaded", side_effect=is_existing
                )
            )
            download_mock = stack.enter_context(
                patch.object(
                    downloads_transfer,
                    "download_storyitem_to_paths",
                    side_effect=download_item,
                )
            )

            kwargs = {"patrol_mode": True} if patrol_mode else {}
            result = downloads_highlights.download_highlights("alice", 1, **kwargs)

        return (
            result,
            loader,
            highlights,
            prepared_folders,
            existing_mock,
            download_mock,
        )

    def test_stop_while_fetching_next_patrol_page_remains_stopped(self):
        result, iterator, existing_mock, download_mock = self._run_posts(
            [True, True],
            patrol_page_limit=2,
            page_sizes=[1, 1],
            delay_side_effect=[None, downloads_models.DownloadStopRequested()],
        )

        self.assertEqual(result["status"], downloads_models.status_code.STOPPED)
        self.assertEqual(iterator.total_index, 1)
        existing_mock.assert_called_once()
        download_mock.assert_not_called()

    def test_posts_one_page_consumes_all_edges_without_prefetching_next_page(self):
        result, iterator, existing_mock, download_mock = self._run_posts(
            [True, True, True, False, False],
            patrol_page_limit=1,
            page_sizes=[3, 2],
        )

        self.assertEqual(iterator.total_index, 3)
        self.assertEqual(existing_mock.call_count, 3)
        self.assertEqual(iterator._context.graphql_calls, [])
        self.assertEqual(iterator.delay_mock.call_args_list, [call("request")])
        download_mock.assert_not_called()
        self.assert_patrol_stats(
            result,
            checked=3,
            already=3,
            updates=0,
            downloaded=0,
        )

    def test_posts_two_pages_consume_variable_sizes_without_fetching_page_three(self):
        result, iterator, existing_mock, download_mock = self._run_posts(
            [True] * 9,
            patrol_page_limit=2,
            page_sizes=[3, 2, 4],
        )

        self.assertEqual(iterator.total_index, 5)
        self.assertEqual(existing_mock.call_count, 5)
        self.assertEqual(len(iterator._context.graphql_calls), 1)
        self.assertEqual(
            iterator._context.graphql_calls[0][1]["after"],
            "cursor-1",
        )
        self.assertEqual(
            iterator.delay_mock.call_args_list,
            [call("request"), call("request")],
        )
        download_mock.assert_not_called()
        self.assert_patrol_stats(
            result,
            checked=5,
            already=5,
            updates=0,
            downloaded=0,
        )

    def test_posts_missing_mid_page_promotes_and_consumes_remaining_pages(self):
        result, iterator, existing_mock, download_mock = self._run_posts(
            [True, False, True, False, True],
            patrol_page_limit=1,
            page_sizes=[3, 2],
        )

        self.assertEqual(iterator.total_index, 5)
        self.assertEqual(existing_mock.call_count, 5)
        self.assertEqual(len(iterator._context.graphql_calls), 1)
        self.assertEqual(download_mock.call_count, 2)
        self.assert_patrol_stats(
            result,
            checked=5,
            already=3,
            updates=2,
            downloaded=2,
        )

    def test_reels_summary_skips_existing_and_fetches_one_missing_detail(self):
        timeline = []
        raw_reels = [
            {
                "media": {
                    "code": "reel-existing",
                    "taken_at": 1767268800,
                    "media_type": 2,
                    "product_type": "clips",
                },
            },
            {
                "media": {
                    "code": "reel-missing",
                    "taken_at": 1767268860,
                    "media_type": 2,
                    "product_type": "clips",
                },
            },
        ]
        iterator, context = make_paged_iterator(
            raw_reels,
            page_sizes=[2],
            node_builder=lambda value: value,
            wrapper=lambda value: value,
        )
        loader = SimpleNamespace(context=context)
        profile = SimpleNamespace(userid=404)
        detail_post = SimpleNamespace(shortcode="reel-missing")

        with tempfile.TemporaryDirectory() as save_folder, ExitStack() as stack:
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
                patch.object(
                    downloads_files, "build_save_path", return_value=save_folder
                )
            )
            stack.enter_context(
                patch.dict(
                    instagram_reels._ITER_FUNCS,
                    {"reels": iterator.factory},
                )
            )
            stack.enter_context(
                patch.object(
                    instagram_reels,
                    "INSTALOADER_VERSION",
                    instagram_reels._RAW_REELS_INSTALOADER_VERSION,
                )
            )
            stack.enter_context(patch.object(downloads_control, "wait_if_paused"))
            stack.enter_context(
                patch.object(
                    downloads_control,
                    "delay",
                    side_effect=lambda kind: timeline.append(("delay", kind)),
                )
            )
            stack.enter_context(
                patch.object(downloads_runtime.runtime, "iphone_support_enabled", True)
            )
            stack.enter_context(
                patch.object(downloads_events, "emit_content_phase_status")
            )
            stack.enter_context(patch.object(downloads_events, "emit_download_status"))
            stack.enter_context(patch.object(downloads_events, "_emit_item_completed"))
            stack.enter_context(
                patch.object(downloads_checkpoints, "_delete_resume_checkpoint")
            )
            stack.enter_context(
                patch.object(
                    downloads_files,
                    "generate_filenames_post",
                    return_value=[os.path.join(save_folder, "reel-missing.mp4")],
                )
            )
            existing = stack.enter_context(
                patch.object(
                    downloads_files,
                    "is_already_downloaded",
                    side_effect=[True, False, False],
                )
            )
            detail = stack.enter_context(
                patch.object(
                    Post,
                    "from_shortcode",
                    side_effect=lambda _context, shortcode: (
                        timeline.append(("detail", shortcode)) or detail_post
                    ),
                )
            )
            helper = stack.enter_context(
                patch.object(
                    downloads_transfer,
                    "download_post_to_paths",
                    side_effect=lambda *_args, **_kwargs: (
                        timeline.append(("download", detail_post.shortcode)) or True
                    ),
                )
            )

            result = downloads_posts.download_posts(
                "alice", "reels", patrol_page_limit=1
            )

        self.assert_patrol_stats(
            result,
            checked=2,
            already=1,
            updates=1,
            downloaded=1,
        )
        self.assertEqual(existing.call_count, 3)
        detail.assert_called_once_with(context, "reel-missing")
        helper.assert_called_once()
        self.assertEqual(
            timeline,
            [
                ("delay", "request"),  # Initial Reels page.
                ("delay", "request"),  # Full Reel detail.
                ("detail", "reel-missing"),
                ("delay", "request"),  # Detail -> iPhone media info boundary.
                ("download", "reel-missing"),
                ("delay", "request"),  # Next-request pacing after the item.
            ],
        )

    def test_posts_first_missing_promotes_and_continues_same_iterator(self):
        timeline = []

        result, iterator, existing_mock, download_mock = self._run_posts(
            [True, True, True, True, False, False],
            patrol_page_limit=5,
            timeline=timeline,
        )

        iterator.factory.assert_called_once()
        self.assertEqual(iterator.total_index, 6)
        self.assertEqual(existing_mock.call_count, 6)
        self.assertEqual(download_mock.call_count, 2)
        self.assertEqual(
            timeline,
            [
                ("checked", "post-0"),
                ("checked", "post-1"),
                ("checked", "post-2"),
                ("checked", "post-3"),
                ("checked", "post-4"),
                ("download", "post-4"),
                ("checked", "post-5"),
                ("download", "post-5"),
            ],
        )
        self.assert_patrol_stats(
            result,
            checked=6,
            already=4,
            updates=2,
            downloaded=2,
        )

    def test_posts_detection_with_child_owner_never_saves_cursor(self):
        observer = {}
        result, iterator, _existing_mock, download_mock = self._run_posts(
            [True, True, True, False],
            patrol_page_limit=3,
            patrol_resume_task_id="parent.patrol.0002",
            resume_loaded=False,
            resume_observer=observer,
        )

        self.assertEqual(iterator.total_index, 3)
        download_mock.assert_not_called()
        observer["load"].assert_called_once()
        self.assertEqual(
            observer["load"].call_args.kwargs.get("task_id"),
            "parent.patrol.0002",
        )
        observer["save"].assert_not_called()
        observer["profile_sync"].assert_called_once()
        self.assertEqual(result["status"], downloads_models.status_code.SUCCESS)

    def test_posts_first_missing_starts_cursor_before_formal_download(self):
        timeline = []
        observer = {}
        _result, iterator, _existing_mock, download_mock = self._run_posts(
            [True, False, True],
            patrol_page_limit=2,
            patrol_resume_task_id="parent.patrol.0001",
            resume_loaded=False,
            resume_observer=observer,
            timeline=timeline,
        )

        self.assertEqual(iterator.total_index, 3)
        self.assertEqual(download_mock.call_count, 1)
        self.assertEqual(
            timeline,
            [
                ("checked", "post-0"),
                ("checked", "post-1"),
                ("cursor", "save"),
                ("download", "post-1"),
                ("checked", "post-2"),
            ],
        )
        observer["save"].assert_called_once()
        self.assertEqual(
            observer["save"].call_args.kwargs.get("task_id"),
            "parent.patrol.0001",
        )
        self.assertIs(
            observer["save"].call_args.kwargs.get("replay_current"),
            True,
        )
        observer["delete"].assert_called_once()

    def test_posts_malformed_next_page_does_not_overwrite_formal_cursor(self):
        observer = {}
        result, iterator, _existing_mock, download_mock = self._run_posts(
            [False, True],
            patrol_page_limit=1,
            patrol_resume_task_id="parent.patrol.0001",
            resume_loaded=False,
            resume_observer=observer,
            page_sizes=[1, 1],
            page_overrides={
                "cursor-1": {
                    "edges": [
                        {
                            "node": {
                                "post": SimpleNamespace(
                                    marker=1,
                                    shortcode="post-1",
                                ),
                            },
                        }
                    ],
                    "page_info": [],
                },
            },
        )

        self.assertEqual(result["status"], downloads_models.status_code.FAILED)
        self.assertEqual(iterator.total_index, 2)
        download_mock.assert_called_once()
        # Only the first-promotion save is allowed.  The malformed response
        # must not replace that valid on-disk cursor.
        observer["save"].assert_called_once()
        self.assertIs(
            observer["save"].call_args.kwargs.get("replay_current"),
            True,
        )
        observer["delete"].assert_not_called()

    def test_posts_valid_child_cursor_ignores_detection_page_limit(self):
        observer = {}
        result, iterator, existing_mock, download_mock = self._run_posts(
            [True, True, True, True, True],
            patrol_page_limit=1,
            patrol_resume_task_id="parent.patrol.0004",
            resume_loaded=True,
            resume_observer=observer,
        )

        self.assertEqual(iterator.total_index, 5)
        self.assertEqual(existing_mock.call_count, 5)
        download_mock.assert_not_called()
        observer["save"].assert_not_called()
        observer["delete"].assert_called_once()
        self.assertEqual(result["status"], downloads_models.status_code.SUCCESS)

    def test_posts_first_missing_stops_before_download_if_cursor_save_fails(self):
        timeline = []
        observer = {}
        result, iterator, _existing_mock, download_mock = self._run_posts(
            [True, False, False],
            patrol_page_limit=2,
            patrol_resume_task_id="parent.patrol.0001",
            resume_loaded=False,
            resume_observer=observer,
            save_succeeds=False,
            timeline=timeline,
        )

        self.assertEqual(result["status"], downloads_models.status_code.STOPPED)
        self.assertEqual(result["checked_count"], 2)
        self.assertEqual(result["updates_found_count"], 1)
        self.assertEqual(result["downloaded_count"], 0)
        self.assertEqual(iterator.total_index, 2)
        download_mock.assert_not_called()
        self.assertEqual(
            timeline,
            [
                ("checked", "post-0"),
                ("checked", "post-1"),
                ("cursor", "save"),
            ],
        )
        observer["save"].assert_called_once()
        observer["delete"].assert_not_called()

    def test_posts_stop_during_detection_does_not_save_cursor(self):
        observer = {}
        result, _iterator, _existing_mock, _download_mock = self._run_posts(
            [True, True, False],
            patrol_page_limit=3,
            patrol_resume_task_id="parent.patrol.0000",
            resume_loaded=False,
            resume_observer=observer,
            existing_exception_at="post-1",
        )

        self.assertEqual(result["status"], downloads_models.status_code.STOPPED)
        observer["save"].assert_not_called()
        observer["delete"].assert_not_called()

    def test_posts_stop_during_formal_download_updates_cursor(self):
        observer = {}
        result, _iterator, _existing_mock, _download_mock = self._run_posts(
            [False],
            patrol_page_limit=1,
            patrol_resume_task_id="parent.patrol.0000",
            resume_loaded=False,
            resume_observer=observer,
            download_exception=downloads_models.DownloadStopRequested(),
        )

        self.assertEqual(result["status"], downloads_models.status_code.STOPPED)
        # One save starts formal mode; the second captures the latest Stop position.
        self.assertEqual(observer["save"].call_count, 2)
        self.assertEqual(
            [
                call.kwargs.get("replay_current")
                for call in observer["save"].call_args_list
            ],
            [True, True],
        )
        observer["delete"].assert_not_called()

    def test_posts_download_failure_updates_cursor_to_replay_current_item(self):
        observer = {}
        result, _iterator, _existing_mock, _download_mock = self._run_posts(
            [False],
            patrol_page_limit=1,
            patrol_resume_task_id="parent.patrol.0000",
            resume_loaded=False,
            resume_observer=observer,
            download_succeeds=False,
        )

        self.assertEqual(result["status"], downloads_models.status_code.FAILED)
        self.assertEqual(result["download_failed_count"], 1)
        self.assertEqual(observer["save"].call_count, 2)
        self.assertEqual(
            [
                call.kwargs.get("replay_current")
                for call in observer["save"].call_args_list
            ],
            [True, True],
        )
        observer["delete"].assert_not_called()

    def test_posts_promoted_stop_retains_earlier_cursor_if_latest_save_fails(self):
        observer = {}
        result, _iterator, _existing_mock, _download_mock = self._run_posts(
            [False],
            patrol_page_limit=1,
            patrol_resume_task_id="parent.patrol.0000",
            resume_loaded=False,
            resume_observer=observer,
            save_succeeds=[True, False],
            download_exception=downloads_models.DownloadStopRequested(),
        )

        self.assertEqual(result["status"], downloads_models.status_code.STOPPED)
        self.assertIn("earlier checkpoint was retained", result["message"])
        self.assertEqual(observer["save"].call_count, 2)
        observer["delete"].assert_not_called()

    def test_posts_patrol_short_account_checks_only_available_items(self):
        result, iterator, existing_mock, download_mock = self._run_posts(
            [True, True, True],
            patrol_page_limit=5,
        )

        self.assertEqual(iterator.total_index, 3)
        self.assertEqual(existing_mock.call_count, 3)
        download_mock.assert_not_called()
        self.assert_patrol_stats(
            result,
            checked=3,
            already=3,
            updates=0,
            downloaded=0,
        )

    def test_posts_unresolved_item_still_consumes_detection_page(self):
        result, iterator, existing_mock, download_mock = self._run_posts(
            [True, True, True, True, True, False],
            patrol_page_limit=5,
            pathless_indexes={1},
        )

        self.assertEqual(iterator.total_index, 5)
        self.assertEqual(existing_mock.call_count, 4)
        download_mock.assert_not_called()
        self.assertEqual(result.get("checked_count"), 5)
        self.assertEqual(result.get("already_downloaded_count"), 4)
        self.assertEqual(result.get("unresolved_count"), 1)
        self.assertFalse(result.get("no_updates_confirmed"))

    def test_posts_patrol_fails_immediately_if_the_write_did_not_complete(self):
        result, iterator, existing_mock, download_mock = self._run_posts(
            [False, True],
            patrol_page_limit=2,
            download_succeeds=False,
        )

        self.assertEqual(iterator.total_index, 1)
        self.assertEqual(existing_mock.call_count, 1)
        download_mock.assert_called_once()
        self.assertEqual(result.get("status"), downloads_models.status_code.FAILED)
        self.assertEqual(result.get("checked_count"), 1)
        self.assertEqual(result.get("already_downloaded_count"), 0)
        self.assertEqual(result.get("updates_found_count"), 1)
        self.assertEqual(result.get("downloaded_count"), 0)
        self.assertEqual(result.get("download_failed_count"), 1)
        self.assertFalse(result.get("no_updates_confirmed"))

    def test_stories_first_missing_promotes_and_scans_remaining_items(self):
        timeline = []

        result, loader, existing_mock, download_mock = self._run_stories(
            [True, False, True, False],
            limit=1,
            patrol_mode=True,
            timeline=timeline,
        )

        loader.get_stories.assert_called_once_with(userids=[202])
        story = loader.get_stories.return_value[0]
        story.get_items.assert_called_once()
        self.assertEqual(story.iterator.yielded_count, 4)
        self.assertEqual(existing_mock.call_count, 4)
        self.assertEqual(download_mock.call_count, 2)
        self.assertEqual(
            timeline,
            [
                ("checked", "story-0"),
                ("checked", "story-1"),
                ("download", "story-1"),
                ("checked", "story-2"),
                ("checked", "story-3"),
                ("download", "story-3"),
            ],
        )
        self.assert_patrol_stats(
            result,
            checked=4,
            already=2,
            updates=2,
            downloaded=2,
        )

    def test_stories_patrol_consumes_the_complete_current_reel(self):
        observer = {}
        result, loader, existing_mock, download_mock = self._run_stories(
            [True, True, True, False],
            limit=99,
            patrol_mode=True,
            sync_observer=observer,
        )

        story = loader.get_stories.return_value[0]
        self.assertEqual(story.iterator.yielded_count, 4)
        self.assertEqual(existing_mock.call_count, 4)
        download_mock.assert_called_once()
        observer["profile_sync"].assert_called_once()
        self.assert_patrol_stats(
            result,
            checked=4,
            already=3,
            updates=1,
            downloaded=1,
        )

    def test_highlights_patrol_consumes_all_items_across_all_collections(self):
        (
            result,
            loader,
            highlights,
            prepared_folders,
            existing_mock,
            download_mock,
        ) = self._run_highlights(
            [[True, False], [False, True, True]],
            patrol_mode=True,
        )

        loader.get_highlights.assert_called_once()
        for highlight in highlights:
            highlight.get_items.assert_called_once()
        self.assertEqual(
            [highlight.iterator.yielded_count for highlight in highlights], [2, 3]
        )
        self.assertEqual(existing_mock.call_count, 5)
        self.assertEqual(download_mock.call_count, 2)
        self.assertEqual(
            prepared_folders,
            [
                "Collection 0",
                "Collection 0",
                "Collection 1",
                "Collection 1",
                "Collection 1",
            ],
        )
        self.assert_patrol_stats(
            result,
            checked=5,
            already=3,
            updates=2,
            downloaded=2,
        )

    def test_highlights_patrol_continues_after_pathless_and_failed_items(self):
        result, _, highlights, _, existing_mock, download_mock = self._run_highlights(
            [[False, False], [True]],
            patrol_mode=True,
            pathless_markers={"highlight-0-0"},
            download_results={"highlight-0-1": False},
        )

        self.assertEqual(
            [highlight.iterator.yielded_count for highlight in highlights], [2, 1]
        )
        self.assertEqual(existing_mock.call_count, 2)
        download_mock.assert_called_once()
        self.assertEqual(result["status"], downloads_models.status_code.FAILED)
        self.assertEqual(result["checked_count"], 3)
        self.assertEqual(result["already_downloaded_count"], 1)
        self.assertEqual(result["updates_found_count"], 1)
        self.assertEqual(result["unresolved_count"], 1)
        self.assertEqual(result["download_failed_count"], 1)

    def test_highlights_patrol_download_exception_aborts_with_partial_stats(self):
        result, _, highlights, _, existing_mock, download_mock = self._run_highlights(
            [[True, False], [True]],
            patrol_mode=True,
            download_exception_marker="highlight-0-1",
        )

        self.assertEqual(
            [highlight.iterator.yielded_count for highlight in highlights], [2, 0]
        )
        self.assertEqual(existing_mock.call_count, 2)
        download_mock.assert_called_once()
        self.assertEqual(result["status"], downloads_models.status_code.FAILED)
        self.assertEqual(result["checked_count"], 2)
        self.assertEqual(result["already_downloaded_count"], 1)
        self.assertEqual(result["updates_found_count"], 1)
        self.assertEqual(result["unresolved_count"], 0)
        self.assertEqual(result["download_failed_count"], 1)

    def test_complete_sidecar_is_skipped_but_partial_sidecar_is_not(self):
        with tempfile.TemporaryDirectory() as folder:
            first = Path(folder, "item_1.jpg")
            second = Path(folder, "item_2.jpg")
            first.write_bytes(b"one")

            self.assertFalse(
                downloads_files.is_already_downloaded([str(first), str(second)])
            )

            second.write_bytes(b"two")
            self.assertTrue(
                downloads_files.is_already_downloaded([str(first), str(second)])
            )

    def test_patrol_dispatches_each_media_type_with_patrol_scope(self):
        cases = (
            (
                "posts",
                (downloads_posts, "download_posts"),
                ("alice", "posts"),
            ),
            (
                "reels",
                (downloads_posts, "download_posts"),
                ("alice", "reels"),
            ),
            (
                "tagged",
                (downloads_posts, "download_posts"),
                ("alice", "tagged"),
            ),
            (
                "stories",
                (downloads_stories, "download_stories"),
                ("alice", 1),
            ),
            (
                "highlights",
                (downloads_highlights, "download_highlights"),
                ("alice", 1),
            ),
        )
        for media_type, (serial_owner, serial_name), serial_args in cases:
            with self.subTest(media_type=media_type):
                expected = patrol_result(
                    checked=1,
                    already=1,
                    updates=0,
                    downloaded=0,
                )
                with (
                    patch.object(
                        serial_owner, serial_name, return_value=expected
                    ) as serial_mock,
                ):
                    result = tasks_patrol.patrol_user_content("alice", media_type, 1)

                if media_type in ("stories", "highlights"):
                    serial_mock.assert_called_once_with(
                        *serial_args,
                        patrol_mode=True,
                    )
                else:
                    serial_mock.assert_called_once_with(
                        *serial_args,
                        patrol_page_limit=1,
                    )
                self.assertEqual(result, expected)

    def test_stories_patrol_does_not_require_or_forward_a_page_limit(self):
        expected = patrol_result(
            checked=0,
            already=0,
            updates=0,
            downloaded=0,
        )
        with patch.object(
            downloads_stories,
            "download_stories",
            return_value=expected,
        ) as stories:
            result = tasks_patrol.patrol_user_content("alice", "stories")

        self.assertEqual(result, expected)
        stories.assert_called_once_with("alice", 1, patrol_mode=True)

        with patch.object(
            tasks_patrol,
            "patrol_user_content",
            return_value=expected,
        ) as patrol:
            result = tasks_patrol.patrol_targets_once(["alice"], "stories")

        self.assertEqual(result["status"], downloads_models.status_code.SUCCESS)
        patrol.assert_called_once_with("alice", "stories", None)

    def test_highlights_patrol_has_no_page_limit_or_resume_cursor(self):
        expected = patrol_result(
            checked=0,
            already=0,
            updates=0,
            downloaded=0,
        )
        with (
            patch.object(
                downloads_highlights, "download_highlights", return_value=expected
            ) as highlights,
            patch.object(downloads_posts, "download_posts") as posts,
        ):
            result = tasks_patrol.patrol_user_content(
                "alice",
                "highlights",
                page_limit=99,
                resume_task_id="must-not-be-forwarded",
            )

        self.assertEqual(result, expected)
        highlights.assert_called_once_with("alice", 1, patrol_mode=True)
        posts.assert_not_called()

        failed = dict(
            expected, status=downloads_models.status_code.FAILED, message="fetch failed"
        )
        with (
            patch.object(
                tasks_patrol,
                "patrol_user_content",
                return_value=failed,
            ) as patrol,
            patch.object(downloads_checkpoints, "_patrol_resume_task_id") as cursor_id,
            patch.object(
                downloads_checkpoints, "delete_task_resume_checkpoint"
            ) as delete_cursor,
        ):
            result = tasks_patrol.patrol_targets_once(
                ["alice"],
                "highlights",
                page_limit=99,
                patrol_task_id="parent-task",
            )

        self.assertEqual(result["status"], downloads_models.status_code.FAILED)
        patrol.assert_called_once_with("alice", "highlights", None)
        cursor_id.assert_not_called()
        delete_cursor.assert_not_called()

    def test_patrol_targets_once_visits_targets_in_order_and_delays_between_them(self):
        per_target = [
            patrol_result(checked=5, already=5, updates=0, downloaded=0),
            patrol_result(checked=5, already=4, updates=1, downloaded=1),
            patrol_result(checked=3, already=3, updates=0, downloaded=0),
        ]
        with (
            patch.object(
                tasks_patrol, "patrol_user_content", side_effect=per_target
            ) as patrol_mock,
            patch.object(downloads_control, "delay") as delay_mock,
        ):
            result = tasks_patrol.patrol_targets_once(
                ["alice", "bob", "carol"], "posts", 5
            )

        self.assertEqual(
            [entry.args[0] for entry in patrol_mock.call_args_list],
            ["alice", "bob", "carol"],
        )
        for entry in patrol_mock.call_args_list:
            args, kwargs = entry.args, entry.kwargs
            self.assertEqual(args[1], "posts")
            supplied_page_limit = kwargs.get(
                "page_limit", args[2] if len(args) > 2 else None
            )
            self.assertEqual(supplied_page_limit, 5)
        self.assertEqual(
            delay_mock.call_args_list, [call("user_switch"), call("user_switch")]
        )

        self.assertEqual(result["status"], downloads_models.status_code.SUCCESS)
        self.assertEqual(result["downloaded_count"], 1)
        self.assertEqual(set(result), {"status", "message", "downloaded_count"})

    def test_patrol_targets_once_emits_synchronous_target_boundaries(self):
        rows = [
            patrol_result(checked=2, already=2, updates=0, downloaded=0),
            patrol_result(checked=2, already=1, updates=1, downloaded=1),
        ]
        events = []

        def run_target(_username, _media_type, _page_limit):
            return rows.pop(0)

        with (
            patch.object(tasks_patrol, "patrol_user_content", side_effect=run_target),
            patch.object(downloads_control, "delay"),
        ):
            result = tasks_patrol.patrol_targets_once(
                ["alice", "bob"],
                "posts",
                2,
                target_event_callback=lambda event: events.append(event),
            )

        self.assertEqual(
            [(event["event"], event["target_username"]) for event in events],
            [
                ("target_started", "alice"),
                ("target_finished", "alice"),
                ("target_started", "bob"),
                ("target_finished", "bob"),
            ],
        )
        self.assertEqual(events[1]["result"]["downloaded_count"], 0)
        self.assertEqual(events[3]["result"]["downloaded_count"], 1)
        self.assertEqual(result["downloaded_count"], 1)

    def test_patrol_targets_use_parent_and_original_indexes_for_child_cursor(self):
        row = patrol_result(checked=1, already=1, updates=0, downloaded=0)
        events = []
        with (
            patch.object(
                tasks_patrol, "patrol_user_content", return_value=row
            ) as patrol_mock,
            patch.object(downloads_control, "delay"),
        ):
            result = tasks_patrol.patrol_targets_once(
                ["bob", "dave"],
                "posts",
                1,
                target_event_callback=events.append,
                patrol_task_id="0123456789ab",
                target_indexes=[1, 3],
            )

        self.assertEqual(result["status"], downloads_models.status_code.SUCCESS)
        self.assertEqual(
            [entry.kwargs["resume_task_id"] for entry in patrol_mock.call_args_list],
            [
                "0123456789ab.patrol.0001",
                "0123456789ab.patrol.0003",
            ],
        )
        self.assertEqual(
            [event["target_index"] for event in events],
            [1, 1, 3, 3],
        )

    def test_patrol_target_event_callback_failure_does_not_abort_round(self):
        row = patrol_result(checked=1, already=1, updates=0, downloaded=0)

        def broken_callback(_event):
            raise RuntimeError("observer failed")

        with patch.object(tasks_patrol, "patrol_user_content", return_value=row):
            result = tasks_patrol.patrol_targets_once(
                ["alice"],
                "stories",
                1,
                target_event_callback=broken_callback,
            )

        self.assertEqual(result["status"], downloads_models.status_code.SUCCESS)
        self.assertEqual(result["downloaded_count"], 0)

    def test_patrol_targets_once_continues_after_an_ordinary_failure(self):
        failed_result = patrol_result(checked=0, already=0, updates=0, downloaded=0)
        failed_result.update(
            {
                "status": downloads_models.status_code.FAILED,
                "message": "temporary failure",
            }
        )
        successful_result = patrol_result(checked=2, already=1, updates=1, downloaded=1)

        with (
            patch.object(
                tasks_patrol,
                "patrol_user_content",
                side_effect=[failed_result, successful_result],
            ) as patrol_mock,
            patch.object(downloads_control, "delay") as delay_mock,
        ):
            result = tasks_patrol.patrol_targets_once(["alice", "bob"], "stories", 2)

        self.assertEqual(patrol_mock.call_count, 2)
        delay_mock.assert_called_once_with("user_switch")
        self.assertEqual(result["status"], downloads_models.status_code.FAILED)
        self.assertEqual(result["downloaded_count"], 1)
        self.assertIn("failed targets 1", result["message"])

    def test_patrol_stops_after_loader_wide_failure(self):
        failed_result = patrol_result(
            checked=0,
            already=0,
            updates=0,
            downloaded=0,
        )
        failed_result.update(
            {
                "status": downloads_models.status_code.FAILED,
                "message": "Loader client not available",
                "blocking_failure": True,
            }
        )

        with (
            patch.object(
                tasks_patrol,
                "patrol_user_content",
                side_effect=[
                    failed_result,
                    patrol_result(
                        checked=1,
                        already=1,
                        updates=0,
                        downloaded=0,
                    ),
                ],
            ) as patrol_mock,
            patch.object(downloads_control, "delay") as delay_mock,
        ):
            result = tasks_patrol.patrol_targets_once(
                ["alice", "bob"],
                "stories",
                2,
            )

        self.assertEqual(patrol_mock.call_count, 1)
        delay_mock.assert_not_called()
        self.assertEqual(result["status"], downloads_models.status_code.FAILED)
        self.assertTrue(result["blocking_failure"])

    def test_patrol_targets_once_fails_for_incomplete_success_result(self):
        expected_messages = {
            "download_failed_count": "download failures 1",
            "unresolved_count": "unresolved 1",
        }
        for counter_name, expected_message in expected_messages.items():
            with self.subTest(counter_name=counter_name):
                incomplete_result = patrol_result(
                    checked=2,
                    already=0,
                    updates=2,
                    downloaded=1,
                )
                incomplete_result[counter_name] = 1

                with patch.object(
                    tasks_patrol,
                    "patrol_user_content",
                    return_value=incomplete_result,
                ):
                    result = tasks_patrol.patrol_targets_once(
                        ["alice"],
                        "stories",
                    )

                self.assertEqual(result["status"], downloads_models.status_code.FAILED)
                self.assertEqual(result["downloaded_count"], 1)
                self.assertIn(expected_message, result["message"])
                self.assertIn("failed targets 1", result["message"])

    def test_failed_child_keeps_cursor_owned_by_serial_download(self):
        failed_result = patrol_result(checked=1, already=0, updates=1, downloaded=0)
        failed_result.update(
            {
                "status": downloads_models.status_code.FAILED,
                "message": "download failed",
            }
        )
        with (
            patch.object(
                tasks_patrol, "patrol_user_content", return_value=failed_result
            ),
            patch.object(
                downloads_checkpoints, "delete_task_resume_checkpoint"
            ) as delete_cursor,
        ):
            result = tasks_patrol.patrol_targets_once(
                ["alice"],
                "posts",
                1,
                patrol_task_id="0123456789ab",
                target_indexes=[2],
            )

        self.assertEqual(result["status"], downloads_models.status_code.FAILED)
        delete_cursor.assert_not_called()

    def test_patrol_targets_once_aborts_on_terminal_statuses(self):
        terminal_statuses = (
            downloads_models.status_code.AUTHORIZATION_REQUIRED,
            downloads_models.status_code.RATE_LIMITED,
            downloads_models.status_code.IPHONE_QUALITY_UNAVAILABLE,
            downloads_models.status_code.STOPPED,
        )

        for terminal_status in terminal_statuses:
            with self.subTest(terminal_status=terminal_status):
                successful_result = patrol_result(
                    checked=2,
                    already=2,
                    updates=0,
                    downloaded=0,
                )
                terminal_result = patrol_result(
                    checked=0,
                    already=0,
                    updates=0,
                    downloaded=0,
                )
                terminal_result.update(
                    {"status": terminal_status, "message": "terminal patrol state"}
                )

                with (
                    patch.object(
                        tasks_patrol,
                        "patrol_user_content",
                        side_effect=[successful_result, terminal_result],
                    ) as patrol_mock,
                    patch.object(downloads_control, "delay") as delay_mock,
                ):
                    result = tasks_patrol.patrol_targets_once(
                        ["alice", "bob", "carol"],
                        "posts",
                        2,
                    )

                self.assertEqual(patrol_mock.call_count, 2)
                delay_mock.assert_called_once_with("user_switch")
                self.assertEqual(result.get("status"), terminal_status)
                self.assertEqual(result.get("downloaded_count"), 0)

    def test_feedback_required_maps_to_terminal_authorization_status(self):
        error = AbortDownloadException(
            '400 Bad Request - "fail" status, message "feedback_required"'
        )

        self.assertEqual(
            downloads_errors.handle_error(error),
            downloads_models.status_code.AUTHORIZATION_REQUIRED,
        )


if __name__ == "__main__":
    unittest.main()
