"""Offline checks that notification failures do not disclose credentials."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import requests

import inscrawler.downloads.runtime as downloads_runtime
import inscrawler.notifications.service as notifications_service


class NotificationErrorTests(unittest.TestCase):
    def setUp(self):
        self.secret = "DUMMY_NOTIFICATION_SECRET"
        self.discord_url = f"https://discord.com/api/webhooks/123/{self.secret}"
        self.telegram_url = f"https://api.telegram.org/bot{self.secret}/sendMessage"
        self.enterContext(
            patch.object(downloads_runtime.runtime, "webhook", self.discord_url)
        )
        self.enterContext(
            patch.object(downloads_runtime.runtime, "telegram_bot_token", self.secret)
        )
        self.enterContext(
            patch.object(downloads_runtime.runtime, "telegram_chat_id", "dummy-chat")
        )
        self.log = self.enterContext(
            patch.object(notifications_service.diagnostics_console, "print")
        )

    def test_http_failures_report_status_without_url_or_credentials(self):
        for channel, url in (
            ("discord", self.discord_url),
            ("telegram", self.telegram_url),
        ):
            with self.subTest(channel=channel):
                self.log.reset_mock()
                response = requests.Response()
                response.status_code = 400
                response.reason = "Bad Request"
                response.url = url
                with (
                    patch.object(
                        downloads_runtime.runtime, "notification_type", channel
                    ),
                    patch.object(
                        notifications_service.requests, "post", return_value=response
                    ),
                ):
                    notifications_service.notify("Task completed")

                self.log.assert_called_once_with(
                    f"Failed to send {channel.capitalize()} notification: HTTPError (HTTP 400)"
                )

    def test_network_failures_do_not_log_exception_urls_or_credentials(self):
        for channel, url in (
            ("discord", self.discord_url),
            ("telegram", self.telegram_url),
        ):
            for exception_type in (requests.ConnectionError, requests.Timeout):
                with self.subTest(channel=channel, exception=exception_type.__name__):
                    self.log.reset_mock()
                    with (
                        patch.object(
                            downloads_runtime.runtime, "notification_type", channel
                        ),
                        patch.object(
                            notifications_service.requests,
                            "post",
                            side_effect=exception_type(f"Request failed for {url}"),
                        ),
                    ):
                        notifications_service.notify("Task completed")

                    self.log.assert_called_once_with(
                        f"Failed to send {channel.capitalize()} notification: {exception_type.__name__}"
                    )

    def test_success_preserves_channel_message_payloads(self):
        message = "Task completed"
        for channel, url, payload in (
            (
                "discord",
                self.discord_url,
                {"username": "inscrawler", "content": message},
            ),
            (
                "telegram",
                self.telegram_url,
                {"chat_id": "dummy-chat", "text": f"[inscrawler] {message}"},
            ),
        ):
            with self.subTest(channel=channel):
                self.log.reset_mock()
                response = requests.Response()
                response.status_code = 200
                with (
                    patch.object(
                        downloads_runtime.runtime, "notification_type", channel
                    ),
                    patch.object(
                        notifications_service.requests, "post", return_value=response
                    ) as post,
                ):
                    notifications_service.notify(message)

                post.assert_called_once_with(url, json=payload, timeout=10)
                self.log.assert_called_once_with(
                    f"{channel.capitalize()} notification sent successfully"
                )


if __name__ == "__main__":
    unittest.main()
