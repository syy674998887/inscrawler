import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import inscrawler.diagnostics.console as diagnostics_console
import inscrawler.downloads.control as downloads_control
import inscrawler.downloads.runtime as downloads_runtime
import inscrawler.ui.console as ui_console
import inscrawler.ui.controllers.execution as ui_controllers_execution
import inscrawler.ui.controllers.patrol as ui_controllers_patrol

DOWNLOAD_SIGNAL_NAMES = (
    "log_signal",
    "message_signal",
    "image_signal",
    "profile_picture_signal",
    "file_detail_signal",
    "file_activity_signal",
    "item_completed_signal",
    "scope_completed_signal",
    "status_signal",
    "download_status_signal",
    "auth_required_signal",
    "rate_limited_signal",
    "iphone_quality_unavailable_signal",
    "finished_signal",
    "error_signal",
)

PATROL_SIGNAL_NAMES = (
    "message_signal",
    "image_signal",
    "profile_picture_signal",
    "file_activity_signal",
    "status_signal",
    "download_status_signal",
    "target_started_signal",
    "item_completed_signal",
    "target_finished_signal",
    "finished_signal",
    "log_signal",
)


def _fake_thread(signal_names, *, log_connected):
    thread = SimpleNamespace(
        _gui_log_signal_connected=log_connected,
        isRunning=Mock(return_value=False),
        wait=Mock(),
        deleteLater=Mock(),
    )
    for signal_name in signal_names:
        setattr(thread, signal_name, Mock())
    return thread


class ThreadSignalCleanupTests(unittest.TestCase):
    def test_download_cleanup_skips_log_signal_that_was_never_connected(self):
        thread = _fake_thread(DOWNLOAD_SIGNAL_NAMES, log_connected=False)
        target = SimpleNamespace(
            download_thread=thread,
            _clear_thread_pipeline_current_file=Mock(),
        )

        with (
            patch.object(ui_console, "_gui_log_callback", object()),
            patch.object(downloads_control, "resume_downloads"),
            patch.object(diagnostics_console, "log_callback", object()),
            patch.object(downloads_runtime.runtime, "file_activity_callback", object()),
        ):
            ui_controllers_execution.QueueExecutionController._shutdown_download_thread(
                target
            )

        thread.log_signal.disconnect.assert_not_called()
        thread.message_signal.disconnect.assert_called_once_with()
        self.assertIsNone(target.download_thread)

    def test_download_cleanup_disconnects_connected_fallback_log_signal(self):
        thread = _fake_thread(DOWNLOAD_SIGNAL_NAMES, log_connected=True)
        target = SimpleNamespace(
            download_thread=thread,
            _clear_thread_pipeline_current_file=Mock(),
        )

        with (
            patch.object(ui_console, "_gui_log_callback", object()),
            patch.object(downloads_control, "resume_downloads"),
            patch.object(diagnostics_console, "log_callback", object()),
            patch.object(downloads_runtime.runtime, "file_activity_callback", object()),
        ):
            ui_controllers_execution.QueueExecutionController._shutdown_download_thread(
                target
            )

        thread.log_signal.disconnect.assert_called_once_with()
        self.assertFalse(thread._gui_log_signal_connected)

    def test_patrol_cleanup_skips_log_signal_that_was_never_connected(self):
        thread = _fake_thread(PATROL_SIGNAL_NAMES, log_connected=False)
        target = SimpleNamespace(
            patrol_thread=thread,
            _clear_thread_pipeline_current_file=Mock(),
            _thread_pipeline_stop_requested=True,
            _refresh_thread_pipeline=Mock(),
        )

        with (
            patch.object(ui_console, "_gui_log_callback", object()),
            patch.object(diagnostics_console, "log_callback", object()),
            patch.object(downloads_runtime.runtime, "file_activity_callback", object()),
            patch.object(downloads_control, "clear_stop_request"),
        ):
            ui_controllers_patrol.PatrolController._cleanup_patrol_thread(target)

        thread.log_signal.disconnect.assert_not_called()
        thread.message_signal.disconnect.assert_called_once_with()
        self.assertIsNone(target.patrol_thread)

    def test_patrol_cleanup_disconnects_connected_fallback_log_signal(self):
        thread = _fake_thread(PATROL_SIGNAL_NAMES, log_connected=True)
        target = SimpleNamespace(
            patrol_thread=thread,
            _clear_thread_pipeline_current_file=Mock(),
            _thread_pipeline_stop_requested=True,
            _refresh_thread_pipeline=Mock(),
        )

        with (
            patch.object(ui_console, "_gui_log_callback", object()),
            patch.object(diagnostics_console, "log_callback", object()),
            patch.object(downloads_runtime.runtime, "file_activity_callback", object()),
            patch.object(downloads_control, "clear_stop_request"),
        ):
            ui_controllers_patrol.PatrolController._cleanup_patrol_thread(target)

        thread.log_signal.disconnect.assert_called_once_with()
        self.assertFalse(thread._gui_log_signal_connected)


if __name__ == "__main__":
    unittest.main()
