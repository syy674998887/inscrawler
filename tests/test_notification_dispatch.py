"""Offline contracts for GUI notification dispatch and recipient snapshots."""

from __future__ import annotations

import threading
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

import inscrawler.downloads.control as downloads_control
import inscrawler.downloads.runtime as downloads_runtime
import inscrawler.notifications.service as notifications_service
import inscrawler.ui.controllers.execution as ui_execution
import tests.test_shutdown_after_queue as queue_fixture


class NotificationDispatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_queue_end_and_pause_keep_gui_responsive_during_slow_notification(self):
        for outcome in ("success", "deferred", "auth"):
            with self.subTest(outcome=outcome):
                target, active, _ = (
                    queue_fixture.ShutdownAfterQueueQueueEndTests().make_target()
                )
                target.queue_items[:] = [active]
                target.queue_paused = False
                target.queue_list = Mock()
                target.add_task_to_queue = Mock()
                target._maybe_request_shutdown_after_queue = Mock()
                release = threading.Event()
                finished = threading.Event()
                request_threads = []

                def slow_post(*args, **kwargs):
                    request_threads.append(threading.get_ident())
                    release.wait(1)
                    finished.set()
                    return Mock()

                def complete():
                    ui_execution.QueueExecutionController.handle_task_complete(
                        target,
                        success=outcome == "success",
                        auth_required=outcome == "auth",
                        error_msg="test failure",
                    )

                loop = QEventLoop()
                responsive = []

                def heartbeat():
                    responsive.append(not finished.is_set())
                    release.set()
                    loop.quit()

                with (
                    patch.object(
                        downloads_runtime.runtime, "notification_type", "discord"
                    ),
                    patch.object(
                        downloads_runtime.runtime,
                        "webhook",
                        "https://example.invalid/old",
                    ),
                    patch.object(
                        notifications_service.requests, "post", side_effect=slow_post
                    ),
                    patch.object(downloads_control, "resume_downloads"),
                ):
                    QTimer.singleShot(0, complete)
                    QTimer.singleShot(25, heartbeat)
                    try:
                        loop.exec()
                        self.assertEqual(responsive, [True])
                        self.assertTrue(finished.wait(2))
                        self.assertTrue(request_threads)
                        self.assertNotEqual(request_threads[0], threading.get_ident())
                    finally:
                        release.set()
                        finished.wait(2)

                if outcome == "auth":
                    target._maybe_request_shutdown_after_queue.assert_not_called()
                else:
                    target._maybe_request_shutdown_after_queue.assert_called_once_with()

    def test_background_delivery_keeps_dispatch_time_recipients(self):
        for channel in ("discord", "telegram", "system"):
            with self.subTest(channel=channel):
                callback = Mock()
                with (
                    patch.multiple(
                        downloads_runtime.runtime,
                        notification_type=channel,
                        webhook="https://example.invalid/old",
                        telegram_bot_token="old-token",
                        telegram_chat_id="old-chat",
                        system_notification_callback=callback,
                    ),
                    patch.object(notifications_service.threading, "Thread") as thread,
                    patch.object(notifications_service.requests, "post") as post,
                ):
                    notifications_service.notify("Queue completed", background=True)
                    send = thread.call_args.kwargs["target"]
                    self.assertTrue(thread.call_args.kwargs["daemon"])
                    thread.return_value.start.assert_called_once_with()
                    with patch.multiple(
                        downloads_runtime.runtime,
                        notification_type="",
                        webhook="https://example.invalid/new",
                        telegram_bot_token="new-token",
                        telegram_chat_id="new-chat",
                        system_notification_callback=Mock(),
                    ):
                        send()

                    if channel == "system":
                        callback.assert_called_once_with("Queue completed")
                        post.assert_not_called()
                    else:
                        post.assert_called_once()
                        if channel == "discord":
                            self.assertEqual(
                                post.call_args.args[0], "https://example.invalid/old"
                            )
                        else:
                            self.assertIn("old-token", post.call_args.args[0])
                            self.assertEqual(
                                post.call_args.kwargs["json"]["chat_id"], "old-chat"
                            )

    def test_disabled_notifications_do_not_start_a_thread(self):
        with (
            patch.object(downloads_runtime.runtime, "notification_type", ""),
            patch.object(notifications_service.threading, "Thread") as thread,
        ):
            notifications_service.notify("Queue completed", background=True)
        thread.assert_not_called()
