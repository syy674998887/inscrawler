"""Highlight titles must produce usable directories without renaming normal ones."""

import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import inscrawler.diagnostics.console as diagnostics_console
import inscrawler.downloads.avatar as downloads_avatar
import inscrawler.downloads.control as downloads_control
import inscrawler.downloads.events as downloads_events
import inscrawler.downloads.files as downloads_files
import inscrawler.downloads.highlights as downloads_highlights
import inscrawler.downloads.models as downloads_models
import inscrawler.instagram.profile as instagram_profile
import inscrawler.instagram.session as instagram_session


class HighlightDirectoryTests(unittest.TestCase):
    def _download_into(self, folder, titles):
        highlights = [
            SimpleNamespace(
                title=title, unique_id=index, get_items=Mock(return_value=[])
            )
            for index, title in enumerate(titles, start=1)
        ]
        loader = SimpleNamespace(get_highlights=Mock(return_value=highlights))
        with ExitStack() as stack:
            for module, name, return_value in (
                (instagram_session, "get_loader", loader),
                (instagram_profile, "resolve_profile", SimpleNamespace(userid=1)),
                (instagram_profile, "_ensure_accessible", None),
                (downloads_avatar, "sync_profile_picture_best_effort", None),
                (downloads_files, "build_save_path", folder),
                (downloads_control, "wait_if_paused", None),
                (downloads_control, "delay", None),
                (downloads_events, "emit_content_phase_status", None),
                (downloads_events, "emit_api_status", None),
                (downloads_events, "emit_download_status", None),
                (downloads_events, "_emit_scope_completed", None),
                (diagnostics_console, "print", None),
            ):
                stack.enter_context(
                    patch.object(module, name, return_value=return_value)
                )
            result = downloads_highlights.download_highlights(
                "offline", 1, scope_unit="full"
            )

        self.assertEqual(result["status"], downloads_models.status_code.SUCCESS)
        for highlight in highlights:
            highlight.get_items.assert_called_once_with()

    def test_expanded_emoji_title_uses_id_and_later_highlights_continue(self):
        with tempfile.TemporaryDirectory() as folder:
            self._download_into(folder, ["🏖️" * 7, "Trips"])
            self.assertEqual(set(os.listdir(folder)), {"highlight_1", "Trips"})

    def test_existing_title_names_and_empty_title_fallback_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            existing = os.path.join(folder, "[camera_with_flash]")
            os.mkdir(existing)
            marker = os.path.join(existing, "existing.jpg")
            with open(marker, "wb") as output:
                output.write(b"existing media")

            self._download_into(folder, ["📸", "日常", "   "])

            self.assertEqual(
                set(os.listdir(folder)), {"[camera_with_flash]", "日常", "highlight_3"}
            )
            with open(marker, "rb") as saved:
                self.assertEqual(saved.read(), b"existing media")

    def test_length_limit_counts_utf16_units_for_astral_letters(self):
        with tempfile.TemporaryDirectory() as folder:
            self._download_into(folder, ["\U00020000" * 128])
            self.assertEqual(os.listdir(folder), ["highlight_1"])

    @unittest.skipUnless(os.name == "nt", "Windows reserved directory names")
    def test_reserved_title_produces_a_writable_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            self._download_into(folder, ["NUL"])
            directory = Path(folder) / "highlight_1"
            media = directory / "2026-10-15_00-00-00.jpg"
            media.write_bytes(b"complete media")
            self.assertEqual(media.read_bytes(), b"complete media")
            self.assertEqual(os.listdir(folder), ["highlight_1"])
