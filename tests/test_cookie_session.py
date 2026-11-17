"""Offline regression tests for browser Cookie header handling."""

from __future__ import annotations

import os
import pickle
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import inscrawler.downloads.events as downloads_events
import inscrawler.downloads.models as downloads_models
import inscrawler.downloads.runtime as downloads_runtime
import inscrawler.instagram.session as instagram_session
import inscrawler.paths as app_paths


class CookieSessionTests(unittest.TestCase):
    def test_parse_preserves_percent_encoding_and_trims_wrappers(self):
        cookies = instagram_session._parse_cookie_header(
            ' sessionid = "123%3Atoken%25tail" ; '
            " ds_user_id = '42' ; csrftoken = csrf-token ; "
            "wd=1920x1080; invalid "
        )

        self.assertEqual(
            cookies,
            {
                "sessionid": "123%3Atoken%25tail",
                "ds_user_id": "42",
                "csrftoken": "csrf-token",
            },
        )

    def test_parse_netscape_cookies_txt_as_pasted_and_as_saved(self):
        pasted = "\n".join(
            [
                "# Netscape HTTP Cookie File",
                "# This is a generated file! Do not edit.",
                "",
                ".instagram.com\tTRUE\t/\tTRUE\t1810000000\tsessionid\t123%3Atoken",
                ".instagram.com\tTRUE\t/\tTRUE\t1790000000\tds_user_id\t42",
                ".instagram.com\tTRUE\t/\tTRUE\t1820000000\tcsrftoken\tcsrf-token",
                '.instagram.com\tTRUE\t/\tTRUE\t0\trur\t"CCO\\05442"',
                ".instagram.com\tTRUE\t/\tTRUE\t1790000000\twd\t1920x1080",
                ".example.com\tTRUE\t/\tFALSE\t1790000000\tsessionid\tother-site",
                "",
            ]
        )
        # Saving the GUI field to .env joins its lines with spaces.
        saved = " ".join(pasted.splitlines()).strip()

        for text in (pasted, saved):
            self.assertEqual(
                instagram_session._parse_cookie_header(text),
                {
                    "sessionid": "123%3Atoken",
                    "ds_user_id": "42",
                    "csrftoken": "csrf-token",
                    "rur": "CCO\\05442",
                },
            )

    def test_parse_httponly_netscape_records_filters_domains(self):
        for domain, accepted in (
            ("instagram.com", True),
            (".instagram.com", True),
            ("www.instagram.com", True),
            ("example.com", False),
            ("evilinstagram.com", False),
            ("instagram.com.example.com", False),
        ):
            expected = (
                {"sessionid": "123%3Atoken", "ds_user_id": "42", "csrftoken": "csrf"}
                if accepted
                else {}
            )
            pasted = "# Netscape HTTP Cookie File\n" + "\n".join(
                f"#HttpOnly_{domain}\tFALSE\t/\tTRUE\t0\t{name}\t{value}"
                for name, value in (
                    ("sessionid", "123%3Atoken"),
                    ("ds_user_id", "42"),
                    ("csrftoken", "csrf"),
                )
            )
            for text in (pasted, " ".join(pasted.splitlines())):
                with self.subTest(domain=domain, saved="\n" not in text):
                    self.assertEqual(
                        instagram_session._parse_cookie_header(text), expected
                    )

    def test_saved_percent_encoded_cookies_compare_stably(self):
        expected = {
            "sessionid": "123%3Atoken%25tail",
            "ds_user_id": "42",
            "csrftoken": "csrf-token",
            "mid": "mid-value",
        }
        with tempfile.TemporaryDirectory() as folder:
            session_path = os.path.join(folder, "alice")
            with open(session_path, "wb") as session_file:
                pickle.dump(expected, session_file)

            reformatted = (
                " csrftoken = 'csrf-token'; mid=mid-value; "
                'ds_user_id = "42"; sessionid = "123%3Atoken%25tail"; dpr=2 '
            )
            with patch.object(
                downloads_runtime.runtime, "session_cookie_text", reformatted
            ):
                self.assertFalse(
                    instagram_session._should_reseed_from_cookie_before_pickle_validation(
                        session_path
                    )
                )

    def test_auxiliary_cookie_change_does_not_replace_saved_auth_session(self):
        saved = {
            "sessionid": "sid",
            "ds_user_id": "42",
            "csrftoken": "csrf",
            "rur": "OLD",
        }
        with tempfile.TemporaryDirectory() as folder:
            session_path = os.path.join(folder, "alice")
            with open(session_path, "wb") as session_file:
                pickle.dump(saved, session_file)

            with patch.object(
                downloads_runtime.runtime,
                "session_cookie_text",
                "sessionid=sid; ds_user_id=42; csrftoken=csrf; rur=NEW",
            ):
                self.assertFalse(
                    instagram_session._should_reseed_from_cookie_before_pickle_validation(
                        session_path
                    )
                )

            with patch.object(
                downloads_runtime.runtime,
                "session_cookie_text",
                "sessionid=new-sid; ds_user_id=42; csrftoken=csrf; rur=NEW",
            ):
                self.assertTrue(
                    instagram_session._should_reseed_from_cookie_before_pickle_validation(
                        session_path
                    )
                )

    def test_cookie_seed_uses_public_loader_api_once(self):
        context = SimpleNamespace()
        loader = Mock(context=context)
        loader.test_login.return_value = "Alice"
        cookie_header = "sessionid=sid; ds_user_id=42; csrftoken=csrf"

        with tempfile.TemporaryDirectory() as folder:
            with (
                patch.object(app_paths, "SESSION_DIR", folder),
                patch.object(
                    downloads_runtime.runtime, "session_cookie_text", cookie_header
                ),
            ):
                ok, status, _message = instagram_session._seed_session_from_cookie_text(
                    loader, "alice"
                )

        self.assertTrue(ok)
        self.assertEqual(status, downloads_models.status_code.SUCCESS)
        loader.load_session.assert_called_once_with(
            "alice",
            {"sessionid": "sid", "ds_user_id": "42", "csrftoken": "csrf"},
        )
        loader.save_session_to_file.assert_called_once_with(
            os.path.join(folder, "alice")
        )

    def test_cookie_seed_leaves_user_id_to_instaloader(self):
        # Instaloader 4.16 derives context.user_id from the ds_user_id cookie
        # and refuses any other value, including the same ID as an int.
        loader = instagram_session.init_base_loader()
        cookie_header = "sessionid=sid; ds_user_id=42; csrftoken=csrf"
        try:
            with tempfile.TemporaryDirectory() as folder:
                with (
                    patch.object(app_paths, "SESSION_DIR", folder),
                    patch.object(
                        downloads_runtime.runtime, "session_cookie_text", cookie_header
                    ),
                    patch.object(loader, "test_login", return_value="Alice"),
                ):
                    ok, status, message = (
                        instagram_session._seed_session_from_cookie_text(
                            loader, "alice"
                        )
                    )

            self.assertTrue(ok, message)
            self.assertEqual(status, downloads_models.status_code.SUCCESS)
            self.assertEqual(loader.context.user_id, "42")
            with self.assertRaises(AttributeError):
                loader.context.user_id = 42
        finally:
            loader.close()

    def test_saved_session_loads_with_instaloader_user_id(self):
        cookies = {"sessionid": "sid", "ds_user_id": "42", "csrftoken": "csrf"}
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, "alice"), "wb") as session_file:
                pickle.dump(cookies, session_file)

            with (
                patch.object(app_paths, "SESSION_DIR", folder),
                patch.object(downloads_runtime.runtime, "login_username", "alice"),
                patch.object(downloads_runtime.runtime, "session_cookie_text", ""),
                patch.object(
                    downloads_runtime.runtime,
                    "private_client_status",
                    downloads_models.status_code.SUCCESS,
                ),
                patch.object(
                    instagram_session,
                    "_validate_session",
                    return_value=downloads_models.status_code.SUCCESS,
                ) as validate,
                patch.object(downloads_events, "emit_api_status"),
            ):
                loader = instagram_session.LoaderManager()._initialize_locked()

        try:
            self.assertIsNotNone(loader)
            validate.assert_called_once_with(loader)
            self.assertEqual(loader.context.user_id, "42")
        finally:
            if loader is not None:
                loader.close()

    def test_cookie_seed_does_not_report_service_failure_as_expired(self):
        context = SimpleNamespace(user_id=None, error_log=[])
        loader = Mock(context=context)

        def test_login():
            context.error_log.append(
                "Error when checking if logged in: JSON Query to "
                "graphql/query: 503 Service Unavailable"
            )
            return None

        loader.test_login.side_effect = test_login
        cookie_header = "sessionid=sid; ds_user_id=42; csrftoken=csrf"

        with tempfile.TemporaryDirectory() as folder:
            with (
                patch.object(app_paths, "SESSION_DIR", folder),
                patch.object(
                    downloads_runtime.runtime, "session_cookie_text", cookie_header
                ),
            ):
                ok, status, message = instagram_session._seed_session_from_cookie_text(
                    loader, "alice"
                )

        self.assertFalse(ok)
        self.assertEqual(status, downloads_models.status_code.FAILED)
        self.assertIn("network", message.lower())
        loader.save_session_to_file.assert_not_called()

    def test_cookie_seed_does_not_report_401_please_wait_as_expired(self):
        context = SimpleNamespace(user_id=None, error_log=[])
        loader = Mock(context=context)

        def test_login():
            context.error_log.append(
                "Error when checking if logged in: JSON Query to "
                'graphql/query: 401 Unauthorized - "fail" status, message '
                '"Please wait a few minutes before you try again."'
            )
            return None

        loader.test_login.side_effect = test_login
        cookie_header = "sessionid=sid; ds_user_id=42; csrftoken=csrf"

        with tempfile.TemporaryDirectory() as folder:
            with (
                patch.object(app_paths, "SESSION_DIR", folder),
                patch.object(
                    downloads_runtime.runtime, "session_cookie_text", cookie_header
                ),
            ):
                ok, status, message = instagram_session._seed_session_from_cookie_text(
                    loader, "alice"
                )

        self.assertFalse(ok)
        self.assertEqual(status, downloads_models.status_code.RATE_LIMITED)
        self.assertIn("temporarily limited", message.lower())
        self.assertNotIn("expired", message.lower())
        loader.save_session_to_file.assert_not_called()

    def test_failed_saved_session_does_not_revalidate_identical_cookie(self):
        cookies = {
            "sessionid": "sid",
            "ds_user_id": "42",
            "csrftoken": "csrf",
        }
        loader = Mock(context=SimpleNamespace())

        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, "alice"), "wb") as session_file:
                pickle.dump(cookies, session_file)

            with (
                patch.object(app_paths, "SESSION_DIR", folder),
                patch.object(downloads_runtime.runtime, "login_username", "alice"),
                patch.object(
                    downloads_runtime.runtime,
                    "session_cookie_text",
                    "sessionid=sid; ds_user_id=42; csrftoken=csrf",
                ),
                patch.object(
                    downloads_runtime.runtime,
                    "private_client_status",
                    downloads_models.status_code.SUCCESS,
                ),
                patch.object(
                    instagram_session, "init_base_loader", return_value=loader
                ) as init_loader,
                patch.object(
                    instagram_session,
                    "_validate_session",
                    return_value=downloads_models.status_code.AUTHORIZATION_REQUIRED,
                ),
                patch.object(
                    instagram_session, "_seed_session_from_cookie_text"
                ) as seed,
                patch.object(downloads_events, "emit_api_status"),
            ):
                result = instagram_session.LoaderManager()._initialize_locked()
                self.assertEqual(
                    downloads_runtime.runtime.private_client_status,
                    downloads_models.status_code.AUTHORIZATION_REQUIRED,
                )

        self.assertIsNone(result)
        init_loader.assert_called_once_with()
        seed.assert_not_called()
        loader.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
