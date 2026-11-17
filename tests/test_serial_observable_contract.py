"""Deterministic observable contracts for the stable serial download path.

These tests intentionally exercise the real Instaloader ``write_raw`` method
against temporary files.  Network access is replaced by a fake context, and
threading events (rather than timing sleeps) control the Stop boundary.
"""

from __future__ import annotations

import io
import os
import tempfile
import threading
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests
from instaloader.instaloadercontext import InstaloaderContext

import inscrawler.downloads.avatar as downloads_avatar
import inscrawler.downloads.checkpoints as downloads_checkpoints
import inscrawler.downloads.control as downloads_control
import inscrawler.downloads.events as downloads_events
import inscrawler.downloads.files as downloads_files
import inscrawler.downloads.models as downloads_models
import inscrawler.downloads.posts as downloads_posts
import inscrawler.downloads.runtime as downloads_runtime
import inscrawler.downloads.transfer as downloads_transfer
import inscrawler.instagram.media as instagram_media
import inscrawler.instagram.profile as instagram_profile
import inscrawler.instagram.reels as instagram_reels
import inscrawler.instagram.session as instagram_session
from tests.test_download_scopes import make_node_iterator, simple_post


class _ObservedRaw(io.BytesIO):
    """Record the filesystem state when Instaloader starts reading bytes."""

    def __init__(self, payload, target_path, observations):
        super().__init__(payload)
        self._target_path = str(target_path)
        self._observations = observations
        self._observed = False

    def read(self, size=-1):
        if not self._observed:
            self._observed = True
            self._observations.append(
                {
                    "temp_exists": os.path.exists(self._target_path + ".temp"),
                    "final_exists": os.path.exists(self._target_path),
                }
            )
        return super().read(size)


class _BlockingRaw(io.BytesIO):
    """Block the first stream read until the test releases the writer."""

    def __init__(self, payload, write_started, release_write):
        super().__init__(payload)
        self._write_started = write_started
        self._release_write = release_write
        self._blocked_once = False

    def read(self, size=-1):
        if not self._blocked_once:
            self._blocked_once = True
            self._write_started.set()
            if not self._release_write.wait(timeout=5):
                raise TimeoutError("test did not release the blocked media write")
        return super().read(size)


class _TrackedResponse(requests.Response):
    def __init__(self, raw, payload_size, close_events=None):
        super().__init__()
        self.status_code = 200
        self.raw = raw
        self.headers = {
            "Content-Type": "image/jpeg",
            "Content-Length": str(payload_size),
        }
        self._close_events = close_events

    def close(self):
        if self._close_events is not None:
            self._close_events.append(("response_close", None))
        super().close()


class _RealWriteContext:
    """Fake network context that delegates file output to real Instaloader."""

    def __init__(self, responses):
        self._responses = iter(responses)
        self.requested_urls = []

    def log(self, *_args, **_kwargs):
        pass

    def get_raw(self, url):
        self.requested_urls.append(url)
        try:
            return next(self._responses)
        except StopIteration as exc:
            raise AssertionError(f"unexpected additional media GET: {url}") from exc

    def write_raw(self, response, filename):
        return InstaloaderContext.write_raw(self, response, filename)


class SerialObservableContractTests(unittest.TestCase):
    def setUp(self):
        downloads_control.resume_downloads()
        downloads_control.clear_stop_request()

    def tearDown(self):
        downloads_control.resume_downloads()
        downloads_control.clear_stop_request()

    def test_real_writer_replaces_temp_before_callbacks_and_closes_response(self):
        payload = b"complete-media-payload"
        media_mtime = datetime(2025, 6, 7, 8, 9, 10, tzinfo=timezone.utc)
        callback_events = []
        stream_observations = []

        with tempfile.TemporaryDirectory() as folder:
            target_path = Path(folder, "nested", "media.jpg")
            raw = _ObservedRaw(payload, target_path, stream_observations)
            response = _TrackedResponse(raw, len(payload), callback_events)
            context = _RealWriteContext([response])
            loader = SimpleNamespace(context=context)

            def image_callback(path):
                self.assertEqual(Path(path), target_path)
                self.assertTrue(target_path.is_file())
                self.assertFalse(Path(str(target_path) + ".temp").exists())
                self.assertEqual(target_path.read_bytes(), payload)
                callback_events.append(("image", path))

            def file_callback(detail):
                self.assertTrue(target_path.is_file())
                self.assertFalse(Path(str(target_path) + ".temp").exists())
                callback_events.append(("file", dict(detail)))

            def activity_callback(event):
                callback_events.append(
                    (
                        f"activity_{event['event']}",
                        dict(event),
                    )
                )

            with (
                patch.object(
                    downloads_runtime.runtime, "image_download_callback", image_callback
                ),
                patch.object(
                    downloads_runtime.runtime, "file_download_callback", file_callback
                ),
                patch.object(
                    downloads_runtime.runtime,
                    "file_activity_callback",
                    activity_callback,
                ),
            ):
                downloads_transfer._write_url_to_path(
                    loader,
                    "https://cdn.invalid/media.jpg",
                    str(target_path),
                    media_mtime,
                    detail={"download_category": "posts", "source": "standard"},
                )

            self.assertEqual(
                stream_observations,
                [{"temp_exists": True, "final_exists": False}],
            )
            self.assertTrue(target_path.is_file())
            self.assertFalse(Path(str(target_path) + ".temp").exists())
            self.assertEqual(target_path.read_bytes(), payload)
            self.assertAlmostEqual(
                target_path.stat().st_mtime,
                media_mtime.timestamp(),
                delta=1.0,
            )
            self.assertEqual(
                [name for name, _payload in callback_events],
                [
                    "activity_started",
                    "image",
                    "file",
                    "response_close",
                    "activity_finished",
                ],
            )
            started_detail = callback_events[0][1]
            self.assertEqual(started_detail["path"], os.path.abspath(target_path))
            file_detail = callback_events[2][1]
            self.assertEqual(file_detail["path"], os.path.abspath(target_path))
            self.assertEqual(file_detail["size_bytes"], len(payload))
            self.assertEqual(file_detail["response_content_type"], "image/jpeg")
            self.assertEqual(
                file_detail["response_content_length"],
                str(len(payload)),
            )
            self.assertEqual(file_detail["response_status_code"], 200)
            self.assertTrue(raw.closed)
            self.assertEqual(
                context.requested_urls,
                ["https://cdn.invalid/media.jpg"],
            )

    def test_stop_during_started_write_finishes_current_file_only(self):
        first_payload = b"first-file-completes-after-stop"
        second_payload = b"second-file-must-not-start"
        media_mtime = datetime(2025, 6, 7, 8, 9, 10, tzinfo=timezone.utc)
        write_started = threading.Event()
        release_write = threading.Event()
        callback_events = []
        errors = []
        results = []

        with tempfile.TemporaryDirectory() as folder:
            first_path = Path(folder, "album_1.jpg")
            second_path = Path(folder, "album_2.jpg")
            first_raw = _BlockingRaw(
                first_payload,
                write_started,
                release_write,
            )
            first_response = _TrackedResponse(
                first_raw,
                len(first_payload),
                callback_events,
            )
            second_response = _TrackedResponse(
                io.BytesIO(second_payload),
                len(second_payload),
                callback_events,
            )
            context = _RealWriteContext([first_response, second_response])
            loader = SimpleNamespace(context=context)
            post = SimpleNamespace(
                shortcode="album",
                date_local=media_mtime,
            )
            segments = [
                {
                    "url": "https://cdn.invalid/album_1.jpg",
                    "source": "standard",
                    "kind": "image",
                },
                {
                    "url": "https://cdn.invalid/album_2.jpg",
                    "source": "standard",
                    "kind": "image",
                },
            ]

            def run_download():
                try:
                    results.append(
                        downloads_transfer.download_post_to_paths(
                            loader,
                            post,
                            [str(first_path), str(second_path)],
                            download_category="posts",
                        )
                    )
                except Exception as exc:  # The expected Stop is asserted below.
                    errors.append(exc)

            with (
                patch.object(
                    instagram_media, "_resolve_post_segments", return_value=segments
                ),
                patch.object(
                    downloads_runtime.runtime, "iphone_support_enabled", False
                ),
                patch.object(
                    downloads_runtime.runtime,
                    "image_download_callback",
                    lambda path: callback_events.append(("image", path)),
                ),
                patch.object(
                    downloads_runtime.runtime,
                    "file_download_callback",
                    lambda detail: callback_events.append(("file", dict(detail))),
                ),
            ):
                worker = threading.Thread(target=run_download, daemon=True)
                worker.start()
                try:
                    self.assertTrue(
                        write_started.wait(timeout=5),
                        "the first file write never started",
                    )
                    self.assertTrue(Path(str(first_path) + ".temp").exists())
                    self.assertFalse(first_path.exists())
                    downloads_control.request_stop_downloads()
                finally:
                    release_write.set()
                worker.join(timeout=5)

            self.assertFalse(worker.is_alive(), "serial download did not stop")
            self.assertEqual(results, [])
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], downloads_models.DownloadStopRequested)
            self.assertEqual(first_path.read_bytes(), first_payload)
            self.assertFalse(Path(str(first_path) + ".temp").exists())
            self.assertFalse(second_path.exists())
            self.assertFalse(Path(str(second_path) + ".temp").exists())
            self.assertEqual(
                context.requested_urls,
                ["https://cdn.invalid/album_1.jpg"],
            )
            self.assertEqual(
                [name for name, _payload in callback_events],
                ["image", "file", "response_close"],
            )
            self.assertTrue(first_raw.closed)
            self.assertFalse(second_response.raw.closed)

    def test_file_activity_finishes_on_error_and_does_not_start_before_budget(self):
        events = []
        context = SimpleNamespace(
            get_raw=Mock(side_effect=OSError("network failed")),
            write_raw=Mock(),
        )
        loader = SimpleNamespace(context=context)

        with tempfile.TemporaryDirectory() as folder:
            target_path = os.path.join(folder, "media.jpg")
            with patch.object(
                downloads_runtime.runtime,
                "file_activity_callback",
                lambda event: events.append(dict(event)),
            ):
                with self.assertRaisesRegex(OSError, "network failed"):
                    downloads_transfer._write_url_to_path(
                        loader,
                        "https://cdn.invalid/media.jpg",
                        target_path,
                        datetime.now(),
                    )

                self.assertEqual(
                    [event["event"] for event in events],
                    ["started", "finished"],
                )

                events.clear()
                context.get_raw.reset_mock()
                budget = SimpleNamespace(
                    consume=Mock(side_effect=downloads_models.DownloadStopRequested()),
                )
                with self.assertRaises(downloads_models.DownloadStopRequested):
                    downloads_transfer._write_url_to_path(
                        loader,
                        "https://cdn.invalid/media.jpg",
                        target_path,
                        datetime.now(),
                        request_budget=budget,
                    )

            self.assertEqual(events, [])
            context.get_raw.assert_not_called()

    def test_later_empty_iphone_segment_does_not_undo_earlier_transfer(self):
        """Serial carousel preparation stays interleaved with each transfer."""

        media_mtime = datetime(2025, 6, 7, 8, 9, 10, tzinfo=timezone.utc)
        loader = SimpleNamespace(context=SimpleNamespace())
        post = SimpleNamespace(shortcode="album", date_local=media_mtime)
        paths = ["album_1.jpg", "album_2.jpg"]
        segments = [
            {
                "url": "https://cdn.invalid/album_1.jpg",
                "source": "standard",
                "kind": "image",
            },
            {
                "url": "",
                "source": "iphone",
                "kind": "image",
            },
        ]
        transfers = []

        with (
            patch.object(
                instagram_media, "_resolve_post_segments", return_value=segments
            ),
            patch.object(downloads_runtime.runtime, "iphone_support_enabled", True),
            patch.object(instagram_media, "_ensure_segments_match_paths"),
            patch.object(downloads_events, "_download_file_detail", return_value={}),
            patch.object(downloads_events, "emit_content_phase_status"),
            patch.object(
                downloads_transfer,
                "_write_url_to_path",
                side_effect=lambda _loader, url, path, *_args, **_kwargs: (
                    transfers.append((url, path))
                ),
            ),
        ):
            with self.assertRaises(downloads_models.IPhoneQualityUnavailable):
                downloads_transfer.download_post_to_paths(loader, post, paths)

        self.assertEqual(
            transfers,
            [("https://cdn.invalid/album_1.jpg", "album_1.jpg")],
        )

    def test_page_runner_stop_after_write_commits_item_without_page_progress(self):
        """A completed file commits its item before Stop leaves the page open."""

        first_payload = b"first-page-file-completes-before-stop-is-observed"
        second_payload = b"second-page-file-must-not-start"
        write_started = threading.Event()
        release_write = threading.Event()
        stop_seen_in_delay = threading.Event()
        observable_events = []
        errors = []
        results = []

        first_post = simple_post("first")
        second_post = simple_post("second")
        iterator, page_context, _wrapped_nodes = make_node_iterator(
            [
                [
                    {
                        "post": first_post,
                    },
                    {
                        "post": second_post,
                    },
                ]
            ]
        )

        with tempfile.TemporaryDirectory() as folder:
            first_path = Path(folder, "first.jpg")
            second_path = Path(folder, "second.jpg")
            first_raw = _BlockingRaw(
                first_payload,
                write_started,
                release_write,
            )
            first_response = _TrackedResponse(
                first_raw,
                len(first_payload),
                observable_events,
            )
            second_response = _TrackedResponse(
                io.BytesIO(second_payload),
                len(second_payload),
                observable_events,
            )
            write_context = _RealWriteContext(
                [
                    first_response,
                    second_response,
                ]
            )
            page_context.get_raw = write_context.get_raw
            page_context.write_raw = write_context.write_raw
            loader = SimpleNamespace(context=page_context)
            profile = SimpleNamespace(userid=101)

            def deterministic_delay(_kind="request"):
                try:
                    downloads_control.wait_if_paused()
                except downloads_models.DownloadStopRequested:
                    observable_events.append(("delay_stop", None))
                    stop_seen_in_delay.set()
                    raise

            def save_cursor(*_args, **kwargs):
                observable_events.append(
                    (
                        "cursor_save",
                        kwargs.get("replay_current"),
                    )
                )
                return True

            save_cursor_mock = Mock(side_effect=save_cursor)
            item_completed = Mock()
            scope_completed = Mock()

            def filenames(post, _save_folder):
                return [str(first_path if post.shortcode == "first" else second_path)]

            def run_page():
                try:
                    results.append(
                        downloads_posts.download_post_pages(
                            "alice",
                            "posts",
                            1,
                        )
                    )
                except Exception as exc:  # Expected Stop asserted below.
                    errors.append(exc)

            with ExitStack() as stack:
                stack.enter_context(
                    patch.object(instagram_session, "get_loader", return_value=loader)
                )
                stack.enter_context(
                    patch.object(
                        instagram_profile, "resolve_profile", return_value=profile
                    )
                )
                stack.enter_context(
                    patch.object(
                        instagram_profile, "_ensure_accessible", return_value=None
                    )
                )
                stack.enter_context(
                    patch.object(downloads_avatar, "sync_profile_picture_best_effort")
                )
                stack.enter_context(
                    patch.object(
                        downloads_files, "build_save_path", return_value=folder
                    )
                )
                stack.enter_context(
                    patch.object(
                        downloads_runtime.runtime,
                        "active_task_id",
                        "serial-stop-contract",
                    )
                )
                stack.enter_context(
                    patch.object(
                        downloads_checkpoints,
                        "_read_resume_checkpoint",
                        return_value=None,
                    )
                )
                stack.enter_context(
                    patch.object(
                        downloads_checkpoints,
                        "_save_resume_checkpoint",
                        save_cursor_mock,
                    )
                )
                stack.enter_context(
                    patch.object(downloads_checkpoints, "_delete_resume_checkpoint")
                )
                stack.enter_context(
                    patch.dict(
                        instagram_reels._ITER_FUNCS,
                        {"posts": lambda _profile: iterator},
                    )
                )
                stack.enter_context(
                    patch.object(
                        downloads_files,
                        "generate_filenames_post",
                        side_effect=filenames,
                    )
                )
                stack.enter_context(
                    patch.object(
                        downloads_files,
                        "is_already_downloaded",
                        return_value=False,
                    )
                )
                stack.enter_context(
                    patch.object(
                        downloads_runtime.runtime, "iphone_support_enabled", False
                    )
                )
                stack.enter_context(
                    patch.object(
                        downloads_control,
                        "delay",
                        side_effect=deterministic_delay,
                    )
                )
                stack.enter_context(
                    patch.object(downloads_events, "emit_content_phase_status")
                )
                stack.enter_context(
                    patch.object(downloads_events, "emit_download_status")
                )
                stack.enter_context(
                    patch.object(
                        downloads_events,
                        "_emit_item_completed",
                        item_completed,
                    )
                )
                stack.enter_context(
                    patch.object(
                        downloads_events,
                        "_emit_scope_completed",
                        scope_completed,
                    )
                )
                stack.enter_context(
                    patch.object(
                        downloads_runtime.runtime,
                        "image_download_callback",
                        lambda path: observable_events.append(("image", path)),
                    )
                )
                stack.enter_context(
                    patch.object(
                        downloads_runtime.runtime,
                        "file_download_callback",
                        lambda detail: observable_events.append(
                            (
                                "file",
                                dict(detail),
                            )
                        ),
                    )
                )
                worker = threading.Thread(target=run_page, daemon=True)
                worker.start()
                try:
                    self.assertTrue(
                        write_started.wait(timeout=5),
                        "the page runner never started its first physical write",
                    )
                    self.assertTrue(Path(str(first_path) + ".temp").exists())
                    self.assertFalse(first_path.exists())
                    downloads_control.request_stop_downloads()
                finally:
                    release_write.set()
                worker.join(timeout=5)

            self.assertFalse(worker.is_alive(), "page runner did not stop")
            self.assertTrue(stop_seen_in_delay.is_set())
            self.assertEqual(results, [])
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], downloads_models.DownloadStopRequested)
            self.assertEqual(errors[0].downloaded_count, 1)
            self.assertEqual(errors[0].scope_completed_count, 0)

            self.assertEqual(first_path.read_bytes(), first_payload)
            self.assertFalse(Path(str(first_path) + ".temp").exists())
            self.assertFalse(second_path.exists())
            self.assertFalse(Path(str(second_path) + ".temp").exists())
            self.assertEqual(
                write_context.requested_urls,
                ["https://cdn.invalid/first.jpg"],
            )
            self.assertEqual(
                [name for name, _payload in observable_events],
                [
                    "image",
                    "file",
                    "response_close",
                    "delay_stop",
                    "cursor_save",
                ],
            )
            self.assertTrue(first_raw.closed)
            self.assertFalse(second_response.raw.closed)
            item_completed.assert_called_once()
            scope_completed.assert_not_called()
            save_cursor_mock.assert_called_once()
            self.assertIs(
                save_cursor_mock.call_args.kwargs["replay_current"],
                False,
            )

    def test_writer_callback_exceptions_are_nonfatal(self):
        payload = b"callback-errors-do-not-roll-back-the-file"
        media_mtime = datetime(2025, 6, 7, 8, 9, 10, tzinfo=timezone.utc)
        callback_calls = []
        close_events = []

        with tempfile.TemporaryDirectory() as folder:
            target_path = Path(folder, "media.jpg")
            raw = io.BytesIO(payload)
            response = _TrackedResponse(raw, len(payload), close_events)
            context = _RealWriteContext([response])
            loader = SimpleNamespace(context=context)

            def failing_image_callback(path):
                callback_calls.append(("image", path))
                raise RuntimeError("preview consumer failed")

            def failing_file_callback(detail):
                callback_calls.append(("file", dict(detail)))
                raise RuntimeError("file-detail consumer failed")

            def failing_activity_callback(event):
                callback_calls.append(("activity", dict(event)))
                raise RuntimeError("file-activity consumer failed")

            with (
                patch.object(
                    downloads_runtime.runtime,
                    "image_download_callback",
                    failing_image_callback,
                ),
                patch.object(
                    downloads_runtime.runtime,
                    "file_download_callback",
                    failing_file_callback,
                ),
                patch.object(
                    downloads_runtime.runtime,
                    "file_activity_callback",
                    failing_activity_callback,
                ),
            ):
                downloads_transfer._write_url_to_path(
                    loader,
                    "https://cdn.invalid/media.jpg",
                    str(target_path),
                    media_mtime,
                    detail={"download_category": "posts"},
                )

            self.assertEqual(target_path.read_bytes(), payload)
            self.assertFalse(Path(str(target_path) + ".temp").exists())
            self.assertEqual(
                [name for name, _payload in callback_calls],
                ["activity", "image", "file", "activity"],
            )
            self.assertEqual(close_events, [("response_close", None)])
            self.assertTrue(raw.closed)

    def test_status_and_progress_callback_exceptions_are_nonfatal(self):
        api_callback = Mock(side_effect=RuntimeError("api status failed"))
        download_callback = Mock(side_effect=RuntimeError("download status failed"))
        item_callback = Mock(side_effect=RuntimeError("item progress failed"))
        scope_callback = Mock(side_effect=RuntimeError("scope progress failed"))

        with (
            patch.object(
                downloads_runtime.runtime, "api_status_callback", api_callback
            ),
            patch.object(
                downloads_runtime.runtime, "download_status_callback", download_callback
            ),
            patch.object(
                downloads_runtime.runtime, "item_completed_callback", item_callback
            ),
            patch.object(
                downloads_runtime.runtime, "scope_completed_callback", scope_callback
            ),
        ):
            downloads_events.emit_api_status("fetching_posts")
            downloads_events.emit_download_status("downloading_posts")
            downloads_events._emit_item_completed({"download_category": "posts"})
            downloads_events._emit_scope_completed(
                {
                    "scope_unit": "page",
                    "download_category": "posts",
                }
            )

        api_callback.assert_called_once_with("fetching_posts")
        download_callback.assert_called_once_with("downloading_posts")
        item_callback.assert_called_once()
        scope_callback.assert_called_once()

    def test_prepare_phase_uses_the_serial_api_status_channel(self):
        api_callback = Mock()
        download_callback = Mock()

        with (
            patch.object(
                downloads_runtime.runtime, "api_status_callback", api_callback
            ),
            patch.object(
                downloads_runtime.runtime,
                "download_status_callback",
                download_callback,
            ),
        ):
            downloads_events.emit_content_phase_status("posts", "prepare")

        api_callback.assert_called_once_with("preparing_posts")
        download_callback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
