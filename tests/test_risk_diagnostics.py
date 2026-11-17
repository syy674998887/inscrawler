"""Offline contracts for the compact serial risk diagnostic summary."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from instaloader.exceptions import (
    ConnectionException,
)

import inscrawler.diagnostics.console as diagnostics_console
import inscrawler.diagnostics.risk as diagnostics_risk
import inscrawler.downloads.errors as downloads_errors
import inscrawler.downloads.models as downloads_models
import inscrawler.downloads.service as downloads_service
import inscrawler.instagram.session as instagram_session


class RiskDiagnosticsTests(unittest.TestCase):
    @staticmethod
    def _risk_payload(output):
        messages = [
            entry.args[1]
            for entry in output.call_args_list
            if len(entry.args) >= 2
            and isinstance(entry.args[1], str)
            and entry.args[1].startswith("[risk] ")
        ]
        if len(messages) != 1:
            raise AssertionError(f"expected one risk summary, got {messages!r}")
        return messages[0], json.loads(messages[0][len("[risk] ") :])

    def test_serial_attempt_emits_one_compact_sanitized_summary(self):
        def fake_download(*_args, **_kwargs):
            diagnostics_risk._risk_session("loader_reused")
            diagnostics_risk._risk_api("profile_page")
            diagnostics_risk._risk_api("profile")
            diagnostics_risk._risk_api("story_web")
            diagnostics_risk._risk_api("story_iphone")
            return {
                "status": downloads_models.status_code.SUCCESS,
                "message": "ok",
                "downloaded_count": 1,
            }

        with (
            patch.object(
                downloads_service,
                "_download_user_content_impl",
                side_effect=fake_download,
            ),
            patch.object(diagnostics_console, "_module_print") as output,
        ):
            result = downloads_service.download_user_content(
                "private-target-name",
                "stories",
                1,
                risk_run_id="run1234",
                queue_completed_before=3,
            )

        serialized, payload = self._risk_payload(output)
        self.assertEqual(result["status"], downloads_models.status_code.SUCCESS)
        self.assertEqual(payload["v"], "RC-16")
        self.assertEqual(payload["run"], "run1234")
        self.assertEqual(payload["media"], "stories")
        self.assertEqual(payload["status"], downloads_models.status_code.SUCCESS)
        self.assertEqual(payload["session"], "loader_reused")
        self.assertEqual(
            payload["api"],
            {
                "profile": 1,
                "story_iphone": 1,
                "story_web": 1,
                "profile_page": 1,
            },
        )
        self.assertEqual(payload["last"], "story_iphone")
        self.assertNotIn("incident", payload)
        self.assertNotIn("completed_before", payload)
        self.assertNotIn("private-target-name", serialized)
        self.assertNotIn("cookie", serialized.lower())
        self.assertNotIn("url", serialized.lower())
        self.assertLess(len(serialized), 512)

    def test_incident_adds_classification_and_prior_queue_count(self):
        failure = {
            "status": downloads_models.status_code.AUTHORIZATION_REQUIRED,
            "message": "Authorization required before accessing stories",
            "downloaded_count": 0,
        }

        def fake_download(*_args, **_kwargs):
            diagnostics_risk._risk_api("profile_page")
            diagnostics_risk._risk_api("profile")
            diagnostics_risk._risk_note_incident(
                "GraphQL response: checkpoint_required"
            )
            return failure

        with (
            patch.object(
                downloads_service,
                "_download_user_content_impl",
                side_effect=fake_download,
            ),
            patch.object(diagnostics_console, "_module_print") as output,
        ):
            result = downloads_service.download_user_content(
                "alice",
                "stories",
                1,
                queue_completed_before=6,
            )

        _serialized, payload = self._risk_payload(output)
        self.assertIs(result, failure)
        self.assertEqual(payload["incident"], "checkpoint_required")
        self.assertEqual(payload["completed_before"], 6)
        self.assertEqual(payload["last"], "profile")

    def test_session_error_log_preserves_a_swallowed_checkpoint(self):
        context = SimpleNamespace(error_log=[])

        def test_login():
            context.error_log.append(
                "GraphQL login validation returned checkpoint_required"
            )
            return None

        loader = SimpleNamespace(context=context, test_login=test_login)
        with patch.object(diagnostics_console, "_module_print") as output:
            diagnostics_risk._begin_risk_attempt("stories", run_id="session-run")
            status = instagram_session._validate_session(loader)
            result = {
                "status": status,
                "message": "Authorization required before accessing stories",
            }
            diagnostics_risk._finish_risk_attempt(result)

        _serialized, payload = self._risk_payload(output)
        self.assertEqual(status, downloads_models.status_code.AUTHORIZATION_REQUIRED)
        self.assertEqual(payload["incident"], "checkpoint_required")
        self.assertEqual(payload["api"], {"session": 1})
        self.assertEqual(payload["last"], "session")

    def test_cdn_429_is_not_labeled_as_instagram_api_incident(self):
        error = ConnectionException(
            "429 Too Many Requests when accessing https://scontent.example/media.jpg"
        )
        with patch.object(diagnostics_console, "_module_print") as output:
            diagnostics_risk._begin_risk_attempt("stories")
            status = downloads_errors.handle_error(error)
            diagnostics_risk._finish_risk_attempt(
                {
                    "status": status,
                    "message": str(error),
                }
            )

        _serialized, payload = self._risk_payload(output)
        self.assertEqual(status, downloads_models.status_code.FAILED)
        self.assertNotIn("incident", payload)

    def test_401_please_wait_keeps_its_own_incident_classification(self):
        message = (
            'JSON Query to graphql/query: 401 Unauthorized - "fail" status, '
            'message "Please wait a few minutes before you try again."'
        )

        self.assertEqual(
            diagnostics_risk._risk_incident_kind(
                {
                    "status": downloads_models.status_code.RATE_LIMITED,
                    "message": message,
                }
            ),
            "please_wait",
        )

    def test_logout_redirect_is_not_labeled_please_wait(self):
        # Instaloader's logout message also asks to "please wait some time".
        message = (
            "Redirected to login page. You've been logged out, please wait "
            "some time, recreate the session and try again"
        )

        self.assertIsNone(
            diagnostics_risk._risk_incident_kind(
                {
                    "status": downloads_models.status_code.AUTHORIZATION_REQUIRED,
                    "message": message,
                }
            )
        )

    def test_diagnostic_output_failure_does_not_change_download_result(self):
        success = {
            "status": downloads_models.status_code.SUCCESS,
            "message": "ok",
            "downloaded_count": 0,
        }
        with (
            patch.object(
                downloads_service,
                "_download_user_content_impl",
                return_value=success,
            ),
            patch.object(
                diagnostics_console,
                "_module_print",
                side_effect=RuntimeError("console unavailable"),
            ),
        ):
            result = downloads_service.download_user_content(
                "alice",
                "posts",
                1,
            )

        self.assertIs(result, success)
        self.assertIsNone(diagnostics_risk._current_risk_attempt())

    def test_diagnostic_start_failure_does_not_block_download(self):
        success = {
            "status": downloads_models.status_code.SUCCESS,
            "message": "ok",
            "downloaded_count": 0,
        }
        with (
            patch.object(
                diagnostics_risk,
                "_begin_risk_attempt",
                side_effect=RuntimeError("clock unavailable"),
            ),
            patch.object(
                downloads_service,
                "_download_user_content_impl",
                return_value=success,
            ) as worker,
        ):
            result = downloads_service.download_user_content(
                "alice",
                "posts",
                1,
            )

        self.assertIs(result, success)
        worker.assert_called_once()


if __name__ == "__main__":
    unittest.main()
