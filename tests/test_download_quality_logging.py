"""Per-file quality log contracts for the supported serial download path."""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from instaloader.exceptions import (
    ConnectionException,
)

import inscrawler.diagnostics.console as diagnostics_console
import inscrawler.downloads.events as downloads_events
import inscrawler.downloads.runtime as downloads_runtime
import inscrawler.downloads.transfer as downloads_transfer
import inscrawler.instagram.media as instagram_media


class DownloadQualityLoggingTests(unittest.TestCase):
    @staticmethod
    def _post():
        return SimpleNamespace(
            shortcode="shortcode",
            date_local=SimpleNamespace(),
        )

    def test_post_logs_filename_and_selected_quality_after_write(self):
        for source, expected_quality in (
            ("iphone", "iPhone"),
            ("standard", "normal"),
        ):
            with self.subTest(source=source):
                path = os.path.join("data", "2026-07-20_10-20-30.mp4")
                segment = {
                    "url": "https://cdn.example/media",
                    "kind": "video",
                    "source": source,
                }
                events = []

                with (
                    patch.object(
                        instagram_media,
                        "_resolve_post_segments",
                        return_value=[segment],
                    ),
                    patch.object(
                        downloads_runtime.runtime,
                        "iphone_support_enabled",
                        source == "iphone",
                    ),
                    patch.object(instagram_media, "_ensure_segments_match_paths"),
                    patch.object(
                        downloads_events, "_download_file_detail", return_value={}
                    ),
                    patch.object(downloads_events, "emit_content_phase_status"),
                    patch.object(
                        downloads_transfer,
                        "_write_url_to_path",
                        side_effect=lambda *_args, **_kwargs: events.append("written"),
                    ),
                    patch.object(
                        diagnostics_console,
                        "print",
                        side_effect=lambda message: events.append(message),
                    ),
                ):
                    result = downloads_transfer.download_post_to_paths(
                        SimpleNamespace(),
                        self._post(),
                        [path],
                    )

                self.assertTrue(result)
                self.assertEqual(
                    events,
                    [
                        "written",
                        f"Downloaded {os.path.basename(path)} ({expected_quality} quality)",
                    ],
                )

    def test_story_logs_normal_quality_after_write(self):
        path = os.path.join("data", "2026-07-20_11-22-33.jpg")
        segment = {
            "url": "https://cdn.example/story",
            "kind": "image",
            "source": "standard",
        }
        output = Mock()

        with (
            patch.object(
                instagram_media,
                "_resolve_storyitem_segments",
                return_value=[segment],
            ),
            patch.object(downloads_runtime.runtime, "iphone_support_enabled", False),
            patch.object(downloads_events, "_download_file_detail", return_value={}),
            patch.object(downloads_events, "emit_content_phase_status"),
            patch.object(downloads_transfer, "_write_url_to_path"),
            patch.object(diagnostics_console, "print", output),
        ):
            result = downloads_transfer.download_storyitem_to_paths(
                SimpleNamespace(),
                SimpleNamespace(date_local=SimpleNamespace()),
                [path],
            )

        self.assertTrue(result)
        output.assert_called_once_with(
            f"Downloaded {os.path.basename(path)} (normal quality)"
        )

    def test_failed_transfer_does_not_log_downloaded(self):
        path = os.path.join("data", "2026-07-20_12-23-34.jpg")
        segment = {
            "url": "https://cdn.example/media",
            "kind": "image",
            "source": "standard",
        }
        output = Mock()

        with (
            patch.object(
                instagram_media,
                "_resolve_post_segments",
                return_value=[segment],
            ),
            patch.object(downloads_runtime.runtime, "iphone_support_enabled", False),
            patch.object(downloads_events, "_download_file_detail", return_value={}),
            patch.object(downloads_events, "emit_content_phase_status"),
            patch.object(
                downloads_transfer,
                "_write_url_to_path",
                side_effect=ConnectionException("transfer failed"),
            ),
            patch.object(diagnostics_console, "print", output),
        ):
            result = downloads_transfer.download_post_to_paths(
                SimpleNamespace(),
                self._post(),
                [path],
            )

        self.assertFalse(result)
        self.assertFalse(
            any(
                str(call.args[0]).startswith("Downloaded ")
                for call in output.call_args_list
            )
        )


if __name__ == "__main__":
    unittest.main()
