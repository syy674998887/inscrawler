"""Strict schema-v7 coverage."""

from __future__ import annotations

import os
import unittest
from copy import deepcopy
from types import MethodType, SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSpinBox,
    QWidget,
)

import inscrawler.tasks.schema as tasks_schema
import inscrawler.ui.controllers.forms as ui_controllers_forms
import inscrawler.ui.controllers.queue_view as ui_controllers_queue_view
import inscrawler.ui.controllers.targets as ui_controllers_targets
import inscrawler.ui.i18n.text as ui_i18n_text
import inscrawler.ui.layout as ui_layout
import inscrawler.ui.preferences as ui_preferences
import inscrawler.ui.widgets.combo as ui_widgets_combo
import inscrawler.ui.widgets.switch as ui_widgets_switch

DOWNLOAD_ID = "aaaaaaaaaaaa"
PATROL_ID = "bbbbbbbbbbbb"


def file_detail():
    return {
        "path": r"B:\AI\inscrawler\data\alice\posts\2026-01-01_00-00-00.jpg",
        "filename": "2026-01-01_00-00-00.jpg",
        "extension": ".jpg",
        "media_kind": "image",
        "download_category": "posts",
        "segment_index": 1,
        "segment_total": 1,
        "source": "iphone",
        "source_url_host": "scontent.example.com",
        "shortcode": "ExampleCode",
        "media_id": 123456789,
        "typename": "GraphImage",
        "media_date_utc": "2026-01-01T00:00:00",
        "media_date_local": "2025-12-31T19:00:00-05:00",
        "downloaded_at": "2026-01-01 00:01:00",
        "size_bytes": 123,
        "response_content_type": "image/jpeg",
        "response_content_length": "123",
        "response_status_code": 200,
    }


def download_record(
    status="completed",
    *,
    media_type="posts",
    scope_unit="page",
    scope_limit=1,
    scope_completed=1,
    downloaded_count=0,
    media_request_cap=None,
):
    record = {
        "schema_version": 7,
        "task": {
            "task_id": DOWNLOAD_ID,
            "task_kind": "download",
            "target_user": "alice",
            "download_media_type": media_type,
            "scope_unit": scope_unit,
            "scope_limit": scope_limit,
            "scope_completed": scope_completed,
            "downloaded_count": downloaded_count,
            "media_request_cap": media_request_cap,
            "notification": "",
            "created_at": "2026-01-01 00:00:00",
        },
        "status": status,
        "logs": [],
        "downloaded_files": [],
        "downloaded_file_details": [],
    }
    if status in ("pending", "running", "deferred", "stopped"):
        record["queue_order"] = 0
    elif status == "completed":
        record["completed_at"] = record["task"]["created_at"]
    return record


def patrol_account(username, *, downloaded=0):
    return {
        "target_user": username,
        "state": "waiting",
        "checked_count": 0,
        "updates_found_count": 0,
        "total_downloaded_count": downloaded,
        "message": "",
    }


def patrol_record(
    status="stopped",
    *,
    media_type="posts",
    page_limit=5,
):
    accounts = [
        patrol_account("alice", downloaded=2),
        patrol_account("bob", downloaded=1),
    ]
    record = {
        "schema_version": 7,
        "task": {
            "task_id": PATROL_ID,
            "task_kind": "auto_patrol",
            "target_users": ["alice", "bob"],
            "download_media_type": media_type,
            "notification": "",
            "created_at": "2026-01-01 00:00:00",
        },
        "status": status,
        "logs": [],
        "log_targets": [],
        "downloaded_files": [],
        "downloaded_file_details": [],
        "patrol_state": {
            "total_downloaded": 3,
            "accounts": accounts,
        },
    }
    if media_type in ("posts", "reels", "tagged"):
        record["task"]["page_limit"] = page_limit
    if status in ("pending", "running", "deferred", "stopped"):
        record["queue_order"] = 0
    elif status == "completed":
        record["completed_at"] = record["task"]["created_at"]
        for account in record["patrol_state"]["accounts"]:
            account["state"] = "updated"
    return record


class TaskSchemaV7Tests(unittest.TestCase):
    def assertInvalid(self, record, expected_task_id=None):
        with self.assertRaises(ValueError):
            tasks_schema._validate_saved_task_data(record, expected_task_id)

    def test_accepts_download_and_auto_patrol_records(self):
        download = download_record()
        patrol = patrol_record()

        self.assertIs(
            tasks_schema._validate_saved_task_data(download, DOWNLOAD_ID), download
        )
        self.assertIs(tasks_schema._validate_saved_task_data(patrol, PATROL_ID), patrol)

    def test_notification_accepts_system_channel_without_schema_change(self):
        for record, task_id in (
            (download_record(), DOWNLOAD_ID),
            (patrol_record(), PATROL_ID),
        ):
            with self.subTest(task_id=task_id):
                record["task"]["notification"] = "system"
                self.assertIs(
                    tasks_schema._validate_saved_task_data(record, task_id),
                    record,
                )

    def test_download_limit_is_nullable_or_a_bounded_integer(self):
        for value in (None, 1, 20, 10000):
            with self.subTest(value=value):
                record = download_record(media_request_cap=value)
                self.assertIs(
                    tasks_schema._validate_saved_task_data(record, DOWNLOAD_ID), record
                )

        for value in (True, "20", 0, -1, 10001):
            with self.subTest(value=value):
                self.assertInvalid(
                    download_record(media_request_cap=value), DOWNLOAD_ID
                )

        missing = download_record()
        missing["task"].pop("media_request_cap")
        self.assertInvalid(missing, DOWNLOAD_ID)

        for cap in (None, 1):
            with self.subTest(removed_scope="item", media_request_cap=cap):
                self.assertInvalid(
                    download_record(scope_unit="item", media_request_cap=cap),
                    DOWNLOAD_ID,
                )

    def test_downloaded_count_is_a_required_non_negative_integer(self):
        for value in (0, 1, 10000):
            with self.subTest(value=value):
                record = download_record(downloaded_count=value)
                self.assertIs(
                    tasks_schema._validate_saved_task_data(record, DOWNLOAD_ID), record
                )

        for value in (True, -1, "1", None):
            with self.subTest(value=value):
                self.assertInvalid(download_record(downloaded_count=value), DOWNLOAD_ID)

        missing = download_record()
        missing["task"].pop("downloaded_count")
        self.assertInvalid(missing, DOWNLOAD_ID)

    def test_patrol_page_limit_matches_media_response_shape(self):
        for media_type in ("posts", "reels", "tagged"):
            with self.subTest(media_type=media_type):
                record = patrol_record(media_type=media_type, page_limit=2)
                self.assertEqual(record["task"]["page_limit"], 2)
                self.assertIs(
                    tasks_schema._validate_saved_task_data(record, PATROL_ID), record
                )

        for media_type in ("stories", "highlights"):
            with self.subTest(media_type=media_type):
                record = patrol_record(media_type=media_type)
                self.assertNotIn("page_limit", record["task"])
                self.assertIs(
                    tasks_schema._validate_saved_task_data(record, PATROL_ID), record
                )

    def test_paged_patrol_requires_a_positive_integer_page_limit(self):
        for invalid in (None, 0, -1, True, "2", 101):
            with self.subTest(page_limit=invalid):
                record = patrol_record(page_limit=invalid)
                self.assertInvalid(record, PATROL_ID)

        missing = patrol_record()
        missing["task"].pop("page_limit")
        self.assertInvalid(missing, PATROL_ID)

    def test_full_patrol_media_rejects_a_page_limit_field(self):
        for media_type in ("stories", "highlights"):
            with self.subTest(media_type=media_type):
                record = patrol_record(media_type=media_type)
                record["task"]["page_limit"] = 1

                self.assertInvalid(record, PATROL_ID)

    def test_rejects_legacy_task_fields(self):
        for field in ("limit", "completed_count"):
            with self.subTest(task_kind="download", field=field):
                record = download_record()
                record["task"][field] = 5
                self.assertInvalid(record, DOWNLOAD_ID)

        for field in ("scan_limit", "legacy_scan_limit_items"):
            with self.subTest(task_kind="auto_patrol", field=field):
                record = patrol_record()
                record["task"][field] = 5
                self.assertInvalid(record, PATROL_ID)

    def test_completed_at_must_be_timestamp(self):
        for record, task_id in (
            (download_record(), DOWNLOAD_ID),
            (patrol_record("completed"), PATROL_ID),
        ):
            with self.subTest(kind=record["task"]["task_kind"]):
                self.assertIs(
                    tasks_schema._validate_saved_task_data(record, task_id), record
                )
                record["completed_at"] = None
                self.assertInvalid(record, task_id)

    def test_completed_status_requires_completed_at(self):
        for record, task_id in (
            (download_record(), DOWNLOAD_ID),
            (patrol_record("completed"), PATROL_ID),
        ):
            with self.subTest(kind=record["task"]["task_kind"]):
                record.pop("completed_at")
                self.assertInvalid(record, task_id)

    def test_auto_patrol_can_complete(self):
        record = patrol_record("completed")

        self.assertIs(tasks_schema._validate_saved_task_data(record, PATROL_ID), record)
        self.assertNotIn("queue_order", record)

    def test_completed_patrol_rejects_unfinished_account_state(self):
        for state in ("waiting", "checking", "failed", "stopped"):
            with self.subTest(state=state):
                record = patrol_record("completed")
                record["patrol_state"]["accounts"][0]["state"] = state
                self.assertInvalid(record, PATROL_ID)

    def test_rejects_wrong_schema_version(self):
        for version in (1, 2, 3, 4, 5, 6, 8, "7", None):
            with self.subTest(version=version):
                record = download_record()
                record["schema_version"] = version
                self.assertInvalid(record, DOWNLOAD_ID)

    def test_patrol_log_targets_are_required_and_align_with_logs(self):
        missing = patrol_record()
        missing.pop("log_targets")
        self.assertInvalid(missing, PATROL_ID)

        for owners in (None, "alice", [], ["alice", "bob"]):
            with self.subTest(owners=owners):
                record = patrol_record()
                record["logs"] = ["one line"]
                record["log_targets"] = owners
                self.assertInvalid(record, PATROL_ID)

    def test_patrol_log_target_owner_is_null_or_an_exact_task_target(self):
        for owner in (None, "alice", "bob"):
            with self.subTest(owner=owner):
                record = patrol_record()
                record["logs"] = ["one line"]
                record["log_targets"] = [owner]
                self.assertIs(
                    tasks_schema._validate_saved_task_data(record, PATROL_ID),
                    record,
                )

        for owner in ("carol", "Alice", "@alice", "", True, 1, {}):
            with self.subTest(owner=owner):
                record = patrol_record()
                record["logs"] = ["one line"]
                record["log_targets"] = [owner]
                self.assertInvalid(record, PATROL_ID)

    def test_download_record_rejects_patrol_only_root_fields(self):
        patrol_state = patrol_record()["patrol_state"]
        for extra_fields in (
            {"log_targets": []},
            {"patrol_state": patrol_state},
            {"log_targets": [], "patrol_state": patrol_state},
        ):
            with self.subTest(fields=tuple(extra_fields)):
                record = download_record()
                record.update(extra_fields)
                self.assertInvalid(record, DOWNLOAD_ID)

    def test_rejects_missing_and_extra_fields(self):
        cases = []

        missing_root = download_record()
        missing_root.pop("logs")
        cases.append(missing_root)

        extra_root = download_record()
        extra_root["legacy"] = True
        cases.append(extra_root)

        missing_task = download_record()
        missing_task["task"].pop("target_user")
        cases.append(missing_task)

        missing_downloaded_count = download_record()
        missing_downloaded_count["task"].pop("downloaded_count")
        cases.append(missing_downloaded_count)

        for field in (
            "limit",
            "completed_count",
            "scope_unit",
            "scope_limit",
            "scope_completed",
            "downloaded_count",
            "media_request_cap",
            "scan_limit",
            "legacy_scan_limit_items",
            "round_count",
        ):
            extra_task = patrol_record()
            extra_task["task"][field] = 5
            cases.append(extra_task)

        extra_patrol_state = patrol_record()
        extra_patrol_state["patrol_state"]["round_number"] = 1
        cases.append(extra_patrol_state)

        for index, record in enumerate(cases):
            with self.subTest(index=index):
                self.assertInvalid(record)

    def test_rejects_task_id_that_does_not_match_filename(self):
        self.assertInvalid(download_record(), "cccccccccccc")

    def test_rejects_legacy_detail_fields(self):
        record = download_record()
        detail = file_detail()
        record["downloaded_files"] = [detail["path"]]
        record["downloaded_file_details"] = [detail]

        self.assertIs(
            tasks_schema._validate_saved_task_data(record, DOWNLOAD_ID), record
        )

        for field, value in (
            ("is_video", False),
            ("iphone_quality_enabled", True),
        ):
            with self.subTest(field=field):
                legacy_record = deepcopy(record)
                legacy_record["downloaded_file_details"][0][field] = value
                self.assertInvalid(legacy_record, DOWNLOAD_ID)

    def test_active_tasks_require_only_queue_order_state_field(self):
        pending = download_record("pending")
        self.assertIs(
            tasks_schema._validate_saved_task_data(pending, DOWNLOAD_ID), pending
        )

        missing_order = deepcopy(pending)
        missing_order.pop("queue_order")
        self.assertInvalid(missing_order, DOWNLOAD_ID)

        stopped_download = download_record("stopped")
        self.assertIs(
            tasks_schema._validate_saved_task_data(stopped_download, DOWNLOAD_ID),
            stopped_download,
        )
        self.assertNotIn("completed_at", stopped_download)

        stopped_without_order = deepcopy(stopped_download)
        stopped_without_order.pop("queue_order")
        self.assertInvalid(stopped_without_order, DOWNLOAD_ID)

        completed_with_order = download_record()
        completed_with_order["queue_order"] = 0
        self.assertInvalid(completed_with_order, DOWNLOAD_ID)

        running_patrol = patrol_record("running")
        self.assertIs(
            tasks_schema._validate_saved_task_data(running_patrol, PATROL_ID),
            running_patrol,
        )
        running_patrol.pop("queue_order")
        self.assertInvalid(running_patrol, PATROL_ID)

        stopped_patrol = patrol_record("stopped")
        self.assertIs(
            tasks_schema._validate_saved_task_data(stopped_patrol, PATROL_ID),
            stopped_patrol,
        )
        stopped_patrol.pop("queue_order")
        self.assertInvalid(stopped_patrol, PATROL_ID)

        completed_patrol = patrol_record("completed")
        completed_patrol["queue_order"] = 0
        self.assertInvalid(completed_patrol, PATROL_ID)

    def test_auto_patrol_totals_and_target_order_must_match(self):
        wrong_total = patrol_record()
        wrong_total["patrol_state"]["total_downloaded"] = 4
        self.assertInvalid(wrong_total, PATROL_ID)

        wrong_order = patrol_record()
        wrong_order["patrol_state"]["accounts"].reverse()
        self.assertInvalid(wrong_order, PATROL_ID)

    def test_rejects_failed_root_status(self):
        for record in (download_record(), patrol_record()):
            with self.subTest(kind=record["task"]["task_kind"]):
                record["status"] = "failed"
                self.assertInvalid(record)

    def test_scope_unit_must_match_media_response_shape(self):
        for media_type in ("posts", "reels", "tagged"):
            with self.subTest(media_type=media_type, scope_unit="page"):
                record = download_record(media_type=media_type)
                self.assertIs(
                    tasks_schema._validate_saved_task_data(record, DOWNLOAD_ID), record
                )

        for media_type in ("stories", "highlights"):
            with self.subTest(media_type=media_type, scope_unit="full"):
                record = download_record(
                    media_type=media_type,
                    scope_unit="full",
                )
                self.assertIs(
                    tasks_schema._validate_saved_task_data(record, DOWNLOAD_ID), record
                )

        self.assertInvalid(
            download_record(media_type="stories", scope_unit="page"),
            DOWNLOAD_ID,
        )
        self.assertInvalid(
            download_record(media_type="posts", scope_unit="full"),
            DOWNLOAD_ID,
        )
        self.assertInvalid(
            download_record(
                media_type="highlights",
                scope_unit="full",
                scope_limit=2,
                scope_completed=1,
            ),
            DOWNLOAD_ID,
        )

    def test_supported_media_contract(self):
        self.assertEqual(
            tasks_schema.DOWNLOAD_MEDIA_TYPES,
            ("Posts", "Reels", "Tagged", "Stories", "Highlights"),
        )
        self.assertEqual(
            tasks_schema.PATROL_MEDIA_TYPES,
            (
                ("Posts", "posts"),
                ("Reels", "reels"),
                ("Tagged", "tagged"),
                ("Stories", "stories"),
                ("Highlights", "highlights"),
            ),
        )

    def test_scope_progress_must_not_exceed_scope_limit(self):
        record = download_record(scope_limit=2, scope_completed=3)

        self.assertInvalid(record, DOWNLOAD_ID)

    def test_download_scope_limit_must_be_positive(self):
        for status in ("pending", "running", "deferred", "stopped", "completed"):
            with self.subTest(status=status):
                self.assertInvalid(
                    download_record(
                        status=status,
                        scope_limit=0,
                        scope_completed=0,
                    ),
                    DOWNLOAD_ID,
                )

        self.assertInvalid(
            download_record(scope_limit=1001, scope_completed=0),
            DOWNLOAD_ID,
        )


class DownloadLimitTaskTests(unittest.TestCase):
    @staticmethod
    def _builder(
        *,
        enabled,
        value=20,
    ):
        empty = SimpleNamespace(text=lambda: "")
        target = SimpleNamespace(
            type=SimpleNamespace(currentText=lambda: "Posts"),
            scope_limit=SimpleNamespace(value=lambda: 2),
            media_request_cap_enabled=enabled,
            media_request_cap_spin=SimpleNamespace(value=lambda: value),
            save_path=empty,
            default_save_path="data",
            username=empty,
            notification=SimpleNamespace(currentData=lambda: ""),
            webhook=empty,
            telegram_bot_token=empty,
            telegram_chat_id=empty,
        )
        target._current_download_scope_unit = MethodType(
            ui_controllers_forms.FormController._current_download_scope_unit, target
        )
        return target

    def test_create_task_snapshots_its_own_download_limit(self):
        target = self._builder(enabled=True, value=37)

        first = ui_controllers_queue_view.QueueViewController.build_task_from_ui(
            target, "alice"
        )
        target.media_request_cap_enabled = False
        second = ui_controllers_queue_view.QueueViewController.build_task_from_ui(
            target, "alice"
        )

        self.assertEqual(first["media_request_cap"], 37)
        self.assertIsNone(second["media_request_cap"])
        self.assertEqual(first["media_request_cap"], 37)
        self.assertEqual(first["downloaded_count"], 0)
        self.assertEqual(second["downloaded_count"], 0)

    def test_home_enable_switch_updates_the_new_task_default(self):
        target = SimpleNamespace(
            set_media_request_cap_enabled=Mock(),
            media_request_cap_enabled_check=SimpleNamespace(isChecked=lambda: True),
        )
        ui_controllers_forms.FormController._on_media_request_cap_enabled_changed(
            target, None
        )
        target.set_media_request_cap_enabled.assert_called_once_with(True, persist=True)

    def test_enable_switch_persists_without_a_global_quantity(self):
        target = SimpleNamespace(
            media_request_cap_enabled=False,
            _loading_settings=False,
            _normalize_iphone_support_value=MethodType(
                ui_preferences.PreferencesController._normalize_iphone_support_value,
                SimpleNamespace(),
            ),
            _update_media_request_cap_controls=Mock(),
            save_media_request_cap_enabled_setting=Mock(),
        )

        ui_preferences.PreferencesController.set_media_request_cap_enabled(
            target, "enabled", persist=True
        )

        self.assertTrue(target.media_request_cap_enabled)
        target._update_media_request_cap_controls.assert_called_once_with()
        target.save_media_request_cap_enabled_setting.assert_called_once_with()

        persistence_target = SimpleNamespace(
            media_request_cap_enabled=True,
            _write_env_settings=Mock(),
            _show_message_box=Mock(),
            tr=lambda key, **_kwargs: key,
        )
        ui_preferences.PreferencesController.save_media_request_cap_enabled_setting(
            persistence_target
        )
        persistence_target._write_env_settings.assert_called_once_with(
            {
                "media_request_cap_enabled": "true",
            }
        )


class DownloadScopeVisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.parents = []

    def tearDown(self):
        for parent in self.parents:
            parent.close()
            parent.deleteLater()
        self.app.processEvents()

    def _scope_target(self, media_type):
        parent = QWidget()
        self.parents.append(parent)
        target = SimpleNamespace(
            type=SimpleNamespace(currentText=lambda: media_type),
            download_limit_label=QLabel(parent),
            scope_limit=QSpinBox(parent),
            tr=lambda key: ui_i18n_text.UI_TEXT["zh"].get(key, key),
        )
        target._current_download_scope_unit = MethodType(
            ui_controllers_forms.FormController._current_download_scope_unit,
            target,
        )
        return target

    def _patrol_scope_target(self, media_type):
        parent = QWidget()
        self.parents.append(parent)
        target = SimpleNamespace(
            patrol_media_type=SimpleNamespace(currentData=lambda: media_type),
            patrol_page_limit_label=QLabel(parent),
            patrol_page_limit=QSpinBox(parent),
            tr=lambda key: ui_i18n_text.UI_TEXT["zh"].get(key, key),
            _safe_exit_in_progress=False,
            _force_exit=False,
        )
        return target

    def _resizable_scope_target(self, media_type="Posts"):
        group = QGroupBox("Create Task")
        self.parents.append(group)
        layout = QGridLayout(group)
        layout.setColumnStretch(1, 1)
        target_field = ui_widgets_combo.AlignedComboBox(group)
        target_field.addItem("alice")
        type_field = ui_widgets_combo.AlignedComboBox(group)
        type_field.addItems(tasks_schema.DOWNLOAD_MEDIA_TYPES)
        target_label = QLabel("Target Account:", group)
        download_type_label = QLabel("Download Type:", group)
        layout.addWidget(target_label, 0, 0)
        layout.addWidget(target_field, 0, 1)
        layout.addWidget(download_type_label, 1, 0)
        layout.addWidget(type_field, 1, 1)
        download_limit_label = QLabel(group)
        scope_limit = QSpinBox(group)
        scope_limit.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        layout.addWidget(download_limit_label, 2, 0)
        layout.addWidget(scope_limit, 2, 1)
        media_request_cap_enabled_label = QLabel("Download Limit:", group)
        media_request_cap_control = QWidget(group)
        media_request_cap_layout = QHBoxLayout(media_request_cap_control)
        media_request_cap_layout.setContentsMargins(0, 0, 0, 0)
        media_request_cap_layout.setSpacing(8)
        media_request_cap_enabled_check = ui_widgets_switch.HomeToggleSwitch(
            parent=group
        )
        media_request_cap_layout.addWidget(
            media_request_cap_enabled_check,
            0,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )
        layout.addWidget(media_request_cap_enabled_label, 3, 0)
        media_request_cap_label = QLabel("Quantity:", group)
        media_request_cap_layout.addWidget(media_request_cap_label)
        media_request_cap_spin = QSpinBox(group)
        media_request_cap_spin.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        media_request_cap_layout.addWidget(media_request_cap_spin, 1)
        layout.addWidget(media_request_cap_control, 3, 1)

        selected_media = {"value": media_type}
        target = SimpleNamespace(
            type=SimpleNamespace(currentText=lambda: selected_media["value"]),
            download_limit_label=download_limit_label,
            scope_limit=scope_limit,
            media_request_cap_enabled_label=media_request_cap_enabled_label,
            media_request_cap_control=media_request_cap_control,
            media_request_cap_enabled_check=media_request_cap_enabled_check,
            media_request_cap_label=media_request_cap_label,
            media_request_cap_spin=media_request_cap_spin,
            media_request_cap_enabled=False,
            task_group=group,
            target_label=target_label,
            download_type_label=download_type_label,
            target_field=target_field,
            type_field=type_field,
            _syncing_minimum_window_height=False,
            _sync_minimum_window_height=Mock(),
            tr=lambda key: ui_i18n_text.UI_TEXT["en"].get(key, key),
        )
        target._current_download_scope_unit = MethodType(
            ui_controllers_forms.FormController._current_download_scope_unit,
            target,
        )
        target._set_fixed_height_from_hint = MethodType(
            ui_layout.WindowLayout._set_fixed_height_from_hint,
            target,
        )
        return target, selected_media

    def _resizable_patrol_scope_target(self):
        group = QGroupBox("Auto Patrol")
        self.parents.append(group)
        layout = QGridLayout(group)
        layout.setColumnStretch(1, 1)
        patrol_media_type = ui_widgets_combo.AlignedComboBox(group)
        for label, value in tasks_schema.PATROL_MEDIA_TYPES:
            patrol_media_type.addItem(label, value)
        patrol_page_limit_label = QLabel(group)
        patrol_page_limit = QSpinBox(group)
        patrol_page_limit.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        patrol_media_type_label = QLabel("Detection Type:", group)
        layout.addWidget(patrol_media_type_label, 0, 0)
        layout.addWidget(patrol_media_type, 0, 1)
        layout.addWidget(patrol_page_limit_label, 1, 0)
        layout.addWidget(patrol_page_limit, 1, 1)

        target = SimpleNamespace(
            patrol_media_type=patrol_media_type,
            patrol_media_type_label=patrol_media_type_label,
            patrol_page_limit_label=patrol_page_limit_label,
            patrol_page_limit=patrol_page_limit,
            patrol_group=group,
            _safe_exit_in_progress=False,
            _force_exit=False,
            _syncing_minimum_window_height=False,
            _sync_minimum_window_height=Mock(),
            tr=lambda key: ui_i18n_text.UI_TEXT["en"].get(key, key),
        )
        target._set_fixed_height_from_hint = MethodType(
            ui_layout.WindowLayout._set_fixed_height_from_hint,
            target,
        )
        return target

    def test_full_media_types_hide_the_entire_download_scope_row(self):
        for media_type in ("Stories", "Highlights"):
            with self.subTest(media_type=media_type):
                target = self._scope_target(media_type)

                ui_controllers_forms.FormController._update_download_scope_controls(
                    target
                )

                self.assertTrue(target.download_limit_label.isHidden())
                self.assertTrue(target.scope_limit.isHidden())
                self.assertFalse(target.scope_limit.isEnabled())

    def test_page_media_type_shows_the_page_scope_row(self):
        target = self._scope_target("Posts")

        ui_controllers_forms.FormController._update_download_scope_controls(target)

        self.assertFalse(target.download_limit_label.isHidden())
        self.assertFalse(target.scope_limit.isHidden())
        self.assertTrue(target.scope_limit.isEnabled())
        self.assertEqual(
            target.download_limit_label.text(),
            ui_i18n_text.UI_TEXT["zh"]["download_pages_label"],
        )

    def test_full_media_types_shrink_the_task_section_and_paged_types_restore_it(self):
        target, selected_media = self._resizable_scope_target()
        target.task_group.resize(600, target.task_group.height())
        target.task_group.show()

        ui_controllers_forms.FormController._update_download_scope_controls(target)
        self.app.processEvents()
        paged_height = target.task_group.maximumHeight()
        paged_width = target.task_group.width()
        paged_target_geometry = (
            target.target_field.x(),
            target.target_field.width(),
        )
        paged_type_geometry = (
            target.type_field.x(),
            target.type_field.width(),
        )

        for media_type in ("Stories", "Highlights"):
            with self.subTest(media_type=media_type):
                selected_media["value"] = media_type
                ui_controllers_forms.FormController._update_download_scope_controls(
                    target
                )
                self.app.processEvents()
                self.assertLess(target.task_group.maximumHeight(), paged_height)
                self.assertEqual(
                    target.task_group.minimumHeight(),
                    target.task_group.maximumHeight(),
                )
                self.assertEqual(target.task_group.width(), paged_width)
                self.assertEqual(
                    (target.target_field.x(), target.target_field.width()),
                    paged_target_geometry,
                )
                self.assertEqual(
                    (target.type_field.x(), target.type_field.width()),
                    paged_type_geometry,
                )

        full_height = target.task_group.maximumHeight()
        for media_type in ("Posts", "Reels", "Tagged"):
            with self.subTest(media_type=media_type):
                selected_media["value"] = media_type
                ui_controllers_forms.FormController._update_download_scope_controls(
                    target
                )
                self.app.processEvents()
                self.assertEqual(target.task_group.maximumHeight(), paged_height)
                self.assertGreater(target.task_group.maximumHeight(), full_height)
                self.assertEqual(target.task_group.width(), paged_width)
                self.assertEqual(
                    (target.target_field.x(), target.target_field.width()),
                    paged_target_geometry,
                )
                self.assertEqual(
                    (target.type_field.x(), target.type_field.width()),
                    paged_type_geometry,
                )

    def test_full_patrol_media_hides_and_disables_the_page_row(self):
        for media_type in ("stories", "highlights"):
            with self.subTest(media_type=media_type):
                target = self._patrol_scope_target(media_type)

                ui_controllers_forms.FormController._update_patrol_page_controls(target)

                self.assertTrue(target.patrol_page_limit_label.isHidden())
                self.assertTrue(target.patrol_page_limit.isHidden())
                self.assertFalse(target.patrol_page_limit_label.isEnabled())
                self.assertFalse(target.patrol_page_limit.isEnabled())
                self.assertEqual(target.patrol_page_limit_label.text(), "")

    def test_paged_patrol_shows_the_detection_page_row(self):
        for media_type in ("posts", "reels", "tagged"):
            with self.subTest(media_type=media_type):
                target = self._patrol_scope_target(media_type)

                ui_controllers_forms.FormController._update_patrol_page_controls(target)

                self.assertFalse(target.patrol_page_limit_label.isHidden())
                self.assertFalse(target.patrol_page_limit.isHidden())
                self.assertTrue(target.patrol_page_limit_label.isEnabled())
                self.assertTrue(target.patrol_page_limit.isEnabled())
                self.assertEqual(
                    target.patrol_page_limit_label.text(),
                    ui_i18n_text.UI_TEXT["zh"]["patrol_page_limit_label"],
                )

    def test_full_patrol_media_shrink_the_section_and_paged_types_restore_it(self):
        target = self._resizable_patrol_scope_target()
        target.patrol_group.resize(600, target.patrol_group.height())
        target.patrol_group.show()

        ui_controllers_forms.FormController._update_patrol_page_controls(target)
        self.app.processEvents()
        paged_height = target.patrol_group.maximumHeight()
        paged_width = target.patrol_group.width()
        paged_field_geometry = (
            target.patrol_media_type.x(),
            target.patrol_media_type.width(),
        )

        for media_type in ("stories", "highlights"):
            with self.subTest(media_type=media_type):
                target.patrol_media_type.setCurrentIndex(
                    target.patrol_media_type.findData(media_type)
                )
                ui_controllers_forms.FormController._update_patrol_page_controls(target)
                self.app.processEvents()
                self.assertLess(target.patrol_group.maximumHeight(), paged_height)
                self.assertEqual(
                    target.patrol_group.minimumHeight(),
                    target.patrol_group.maximumHeight(),
                )
                self.assertEqual(target.patrol_group.width(), paged_width)
                self.assertEqual(
                    (
                        target.patrol_media_type.x(),
                        target.patrol_media_type.width(),
                    ),
                    paged_field_geometry,
                )

        full_height = target.patrol_group.maximumHeight()
        for media_type in ("posts", "reels", "tagged"):
            with self.subTest(media_type=media_type):
                target.patrol_media_type.setCurrentIndex(
                    target.patrol_media_type.findData(media_type)
                )
                ui_controllers_forms.FormController._update_patrol_page_controls(target)
                self.app.processEvents()
                self.assertEqual(target.patrol_group.maximumHeight(), paged_height)
                self.assertGreater(target.patrol_group.maximumHeight(), full_height)
                self.assertEqual(target.patrol_group.width(), paged_width)
                self.assertEqual(
                    (
                        target.patrol_media_type.x(),
                        target.patrol_media_type.width(),
                    ),
                    paged_field_geometry,
                )

    def test_create_task_download_quantity_row_follows_home_switch(self):
        target, _selected_media = self._resizable_scope_target()
        target.task_group.resize(420, target.task_group.height())
        target.task_group.show()

        ui_controllers_forms.FormController._update_download_scope_controls(target)
        self.app.processEvents()

        task_layout = target.task_group.layout()
        enable_label_position = task_layout.getItemPosition(
            task_layout.indexOf(target.media_request_cap_enabled_label)
        )
        control_position = task_layout.getItemPosition(
            task_layout.indexOf(target.media_request_cap_control)
        )
        self.assertEqual(enable_label_position[:2], (3, 0))
        self.assertEqual(control_position[:2], (3, 1))
        self.assertFalse(target.media_request_cap_enabled_label.isHidden())
        self.assertFalse(target.media_request_cap_control.isHidden())
        self.assertFalse(target.media_request_cap_enabled_check.isHidden())
        self.assertTrue(target.media_request_cap_enabled_check.isEnabled())
        self.assertEqual(target.media_request_cap_enabled_check.text(), "")
        self.assertEqual(
            target.media_request_cap_enabled_check.height(),
            target.media_request_cap_spin.sizeHint().height(),
        )
        self.assertTrue(target.media_request_cap_label.isHidden())
        self.assertFalse(target.media_request_cap_label.isEnabled())
        self.assertTrue(target.media_request_cap_spin.isHidden())
        self.assertFalse(target.media_request_cap_spin.isEnabled())
        self.assertEqual(
            ui_i18n_text.UI_TEXT["zh"]["media_request_cap_enabled_label"],
            "下载限制:",
        )
        self.assertEqual(
            ui_i18n_text.UI_TEXT["zh"]["media_request_cap_enabled_tooltip"],
            "开启后，新添加的任务在下载达到指定文件数量时结束。每张图片或每段视频计为 1，相册中的每个文件分别计数。",
        )
        self.assertEqual(
            ui_i18n_text.UI_TEXT["en"]["media_request_cap_enabled_tooltip"],
            "When enabled, newly added tasks stop after downloading the specified number of media files. Each image or video counts, including every file in a carousel.",
        )
        self.assertEqual(
            ui_i18n_text.UI_TEXT["zh"]["media_request_cap_label"],
            "数量:",
        )
        toggle_x_when_off = target.media_request_cap_enabled_check.x()
        self.assertEqual(toggle_x_when_off, 0)

        target.media_request_cap_enabled = True
        ui_controllers_forms.FormController._update_media_request_cap_controls(target)
        self.app.processEvents()

        self.assertFalse(target.media_request_cap_label.isHidden())
        self.assertTrue(target.media_request_cap_label.isEnabled())
        self.assertFalse(target.media_request_cap_spin.isHidden())
        self.assertTrue(target.media_request_cap_spin.isEnabled())
        self.assertEqual(
            target.media_request_cap_enabled_check.height(),
            target.media_request_cap_spin.height(),
        )
        self.assertEqual(
            target.media_request_cap_enabled_check.x(),
            toggle_x_when_off,
        )

    def test_home_download_limit_uses_themed_animated_switch(self):
        parent = QWidget()
        self.parents.append(parent)
        colors = {
            "surface": "#f7f2eb",
            "button": "#f3ece3",
            "border": "#d4c7b7",
            "accent": "#547a86",
            "accent_hover": "#476973",
        }
        toggle = ui_widgets_switch.HomeToggleSwitch(lambda: colors, parent)
        toggle.set_control_height(32)
        toggle.resize(toggle.sizeHint())

        self.assertEqual(toggle.text(), "")
        self.assertEqual(toggle.height(), 32)
        self.assertEqual(toggle.sizeHint().height(), 32)
        self.assertEqual(
            toggle.width(),
            toggle.TRACK_WIDTH + (2 * toggle.HORIZONTAL_MARGIN),
        )
        focus_left = toggle.HORIZONTAL_MARGIN - toggle.FOCUS_OUTSET
        focus_right = (
            toggle.HORIZONTAL_MARGIN + toggle.TRACK_WIDTH + toggle.FOCUS_OUTSET
        )
        focus_pen_half = toggle.FOCUS_PEN_WIDTH / 2.0
        self.assertGreaterEqual(focus_left - focus_pen_half, 0)
        self.assertLessEqual(focus_right + focus_pen_half, toggle.width())
        self.assertEqual(
            toggle.cursor().shape(),
            Qt.CursorShape.PointingHandCursor,
        )
        self.assertEqual(toggle._thumb_animation.duration(), 140)

        off_pixmap = QPixmap(toggle.size())
        off_pixmap.fill(Qt.GlobalColor.transparent)
        toggle.render(off_pixmap)

        toggle.blockSignals(True)
        toggle.setChecked(True)
        toggle.blockSignals(False)
        self.assertEqual(toggle._thumb_position, 1.0)

        on_pixmap = QPixmap(toggle.size())
        on_pixmap.fill(Qt.GlobalColor.transparent)
        toggle.render(on_pixmap)
        sample_x = toggle.TRACK_WIDTH // 2
        sample_y = toggle.height() // 2
        self.assertNotEqual(
            off_pixmap.toImage().pixelColor(sample_x, sample_y),
            on_pixmap.toImage().pixelColor(sample_x, sample_y),
        )

    def test_download_limit_and_scope_rows_change_only_task_section_height(self):
        target, selected_media = self._resizable_scope_target()
        target.task_group.resize(420, target.task_group.height())
        target.task_group.show()

        ui_controllers_forms.FormController._update_download_scope_controls(target)
        self.app.processEvents()
        posts_off_height = target.task_group.maximumHeight()
        width = target.task_group.width()
        target_geometry = (target.target_field.x(), target.target_field.width())
        type_geometry = (target.type_field.x(), target.type_field.width())

        def assert_horizontal_geometry_is_stable():
            self.assertEqual(target.task_group.width(), width)
            self.assertEqual(
                (target.target_field.x(), target.target_field.width()),
                target_geometry,
            )
            self.assertEqual(
                (target.type_field.x(), target.type_field.width()),
                type_geometry,
            )

        target.media_request_cap_enabled = True
        ui_controllers_forms.FormController._update_media_request_cap_controls(target)
        self.app.processEvents()
        posts_on_height = target.task_group.maximumHeight()
        self.assertEqual(posts_on_height, posts_off_height)
        assert_horizontal_geometry_is_stable()

        selected_media["value"] = "Stories"
        ui_controllers_forms.FormController._update_download_scope_controls(target)
        self.app.processEvents()
        stories_on_height = target.task_group.maximumHeight()
        self.assertLess(stories_on_height, posts_on_height)
        assert_horizontal_geometry_is_stable()

        target.media_request_cap_enabled = False
        ui_controllers_forms.FormController._update_media_request_cap_controls(target)
        self.app.processEvents()
        stories_off_height = target.task_group.maximumHeight()
        self.assertEqual(stories_off_height, stories_on_height)
        assert_horizontal_geometry_is_stable()

        selected_media["value"] = "Posts"
        ui_controllers_forms.FormController._update_download_scope_controls(target)
        self.app.processEvents()
        self.assertEqual(target.task_group.maximumHeight(), posts_off_height)
        self.assertEqual(target.task_group.minimumHeight(), posts_off_height)
        assert_horizontal_geometry_is_stable()

    def test_language_change_recomputes_task_label_column_width(self):
        target, selected_media = self._resizable_scope_target()
        target.task_group.resize(420, target.task_group.height())
        target.task_group.show()

        ui_controllers_forms.FormController._update_download_scope_controls(target)
        self.app.processEvents()
        english_column_width = target.task_group.layout().columnMinimumWidth(0)
        english_field_geometry = (
            target.target_field.x(),
            target.target_field.width(),
        )

        target.target_label.setText(ui_i18n_text.UI_TEXT["zh"]["target_label"])
        target.download_type_label.setText(
            ui_i18n_text.UI_TEXT["zh"]["download_type_label"]
        )
        target.media_request_cap_enabled_label.setText(
            ui_i18n_text.UI_TEXT["zh"]["media_request_cap_enabled_label"]
        )
        target.media_request_cap_label.setText(
            ui_i18n_text.UI_TEXT["zh"]["media_request_cap_label"]
        )
        target.tr = lambda key: ui_i18n_text.UI_TEXT["zh"].get(key, key)
        ui_controllers_forms.FormController._update_download_scope_controls(target)
        self.app.processEvents()

        chinese_column_width = target.task_group.layout().columnMinimumWidth(0)
        chinese_field_geometry = (
            target.target_field.x(),
            target.target_field.width(),
        )
        self.assertLess(chinese_column_width, english_column_width)
        self.assertLess(chinese_field_geometry[0], english_field_geometry[0])
        self.assertGreater(chinese_field_geometry[1], english_field_geometry[1])

        selected_media["value"] = "Reels"
        ui_controllers_forms.FormController._update_download_scope_controls(target)
        self.app.processEvents()
        self.assertEqual(
            (target.target_field.x(), target.target_field.width()),
            chinese_field_geometry,
        )
        self.assertEqual(
            target.task_group.maximumHeight(),
            target.task_group.sizeHint().height(),
        )

        selected_media["value"] = "Stories"
        ui_controllers_forms.FormController._update_download_scope_controls(target)
        self.app.processEvents()
        self.assertEqual(
            (target.target_field.x(), target.target_field.width()),
            chinese_field_geometry,
        )

        target.media_request_cap_enabled = True
        ui_controllers_forms.FormController._update_media_request_cap_controls(target)
        self.app.processEvents()
        self.assertEqual(
            (target.target_field.x(), target.target_field.width()),
            chinese_field_geometry,
        )

    def test_ui_preferences_apply_theme_before_recomputing_layout(self):
        events = []
        target = SimpleNamespace(
            ui_theme="light",
            ui_language="en",
            _loading_settings=False,
            _normalize_language_key=lambda value: value,
            _normalize_theme_key=lambda value: value,
            apply_theme=lambda theme: events.append(("theme", theme)),
            retranslate_ui=lambda: events.append(("layout", "zh")),
            save_ui_settings=Mock(),
        )

        ui_preferences.PreferencesController.apply_ui_preferences(
            target, theme_key="dark", language_key="zh", persist=False
        )

        self.assertEqual(target.ui_theme, "dark")
        self.assertEqual(target.ui_language, "zh")
        self.assertEqual(events, [("theme", "dark"), ("layout", "zh")])
        target.save_ui_settings.assert_not_called()

    def test_loaded_target_avatar_is_laid_out_before_first_type_change(self):
        target, selected_media = self._resizable_scope_target()
        target.task_group.resize(420, target.task_group.height())
        target.task_group.show()
        ui_controllers_forms.FormController._update_download_scope_controls(target)

        avatar = QPixmap(28, 28)
        avatar.fill(QColor("#547a86"))
        target.target = target.target_field
        target._read_target_values = lambda: ["alice"]
        target._add_target_item = lambda username: target.target.addItem(
            QIcon(avatar), username
        )
        target._set_patrol_target_options = Mock()
        target._update_download_scope_controls = MethodType(
            ui_controllers_forms.FormController._update_download_scope_controls, target
        )

        ui_controllers_targets.TargetController.load_target(target)
        self.app.processEvents()
        initial_geometry = (
            target.task_group.maximumHeight(),
            target.target_field.y(),
            target.target_field.height(),
            target.type_field.y(),
            target.type_field.height(),
        )
        self.assertEqual(
            target.task_group.maximumHeight(),
            target.task_group.sizeHint().height(),
        )

        selected_media["value"] = "Reels"
        ui_controllers_forms.FormController._update_download_scope_controls(target)
        self.app.processEvents()

        self.assertEqual(
            (
                target.task_group.maximumHeight(),
                target.target_field.y(),
                target.target_field.height(),
                target.type_field.y(),
                target.type_field.height(),
            ),
            initial_geometry,
        )


if __name__ == "__main__":
    unittest.main()
