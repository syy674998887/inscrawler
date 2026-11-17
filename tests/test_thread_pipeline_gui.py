"""Headless checks for the formal thread-pipeline panel."""

import ast
import os
import unittest
from types import MethodType, SimpleNamespace

from tests.source_contracts import ui_source

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QObject, Qt, QThread, Signal, Slot
from PySide6.QtWidgets import QApplication

import inscrawler.ui.controllers.status as ui_controllers_status
import inscrawler.ui.i18n.text as ui_i18n_text
import inscrawler.ui.main_window as ui_main_window
import inscrawler.ui.preview.events as ui_preview_events
import inscrawler.ui.widgets.pipeline as ui_widgets_pipeline


class _FastFileActivityThread(QThread):
    file_activity_signal = Signal(dict)

    def __init__(self, path):
        super().__init__()
        self.path = path

    def run(self):
        self.file_activity_signal.emit(
            {
                "event": "started",
                "path": self.path,
            }
        )
        self.file_activity_signal.emit(
            {
                "event": "finished",
                "path": self.path,
            }
        )


class _LabelPaintRecorder(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.texts = []

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Paint:
            self.texts.append(watched.text().replace("\u200b", ""))
        return super().eventFilter(watched, event)


class _QueuedPipelineReceiver(QObject):
    def __init__(self, pipeline):
        super().__init__()
        self.thread_pipeline = pipeline
        self.download_thread = None
        self.patrol_thread = None
        self.queue_running = True
        self.current_queue_index = 0
        self.queue_items = [
            {
                "task": {
                    "task_kind": "download",
                    "target_user": "alice",
                    "download_media_type": "posts",
                },
                "status": "running",
            }
        ]
        self.active_patrol_entry = None
        self.queue_paused = False
        self._safe_exit_in_progress = False
        self._thread_pipeline_stop_requested = False
        self._thread_pipeline_current_file_name = ""
        self._thread_pipeline_phase = "download"
        self._thread_pipeline_last_active_phase = "download"
        self._api_status_state = "idle"
        self._download_status_state = "downloading_posts"
        self._API_STATUS_META = ui_main_window.GUI._API_STATUS_META
        self._DOWNLOAD_STATUS_META = ui_main_window.GUI._DOWNLOAD_STATUS_META

    def tr(self, key):
        return key

    @staticmethod
    def _download_thread_is_running():
        return False

    @staticmethod
    def _patrol_thread_is_running():
        return False

    @staticmethod
    def _patrol_thread_is_active():
        return False

    def _thread_pipeline_is_running(self):
        return ui_preview_events.PreviewEvents._thread_pipeline_is_running(self)

    def _refresh_thread_pipeline(self):
        return ui_controllers_status.StatusController._refresh_thread_pipeline(self)

    @Slot(dict)
    def receive_file_activity(self, event):
        ui_controllers_status.StatusController._on_thread_pipeline_file_activity(
            self, event
        )


class ThreadPipelineWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.widget = ui_widgets_pipeline.ThreadPipelineWidget()
        self.widget.resize(
            760,
            self.widget.sizeHint().height(),
        )

    def tearDown(self):
        self.widget.close()
        self.widget.deleteLater()
        self.app.processEvents()

    def _set_sample(self, **overrides):
        values = {
            "fetch_title": "FETCH",
            "fetch_status": "Task running",
            "fetch_detail": "2026-07-17_12-30-45.mp4",
            "fetch_tone": "idle",
            "prepare_title": "PREPARE",
            "prepare_status": "Status not exposed",
            "prepare_detail": "2026-07-17_12-30-45.mp4",
            "prepare_tone": "idle",
            "download_title": "DOWNLOAD",
            "download_status": "Status not exposed",
            "download_detail": "2026-07-17_12-30-45.mp4",
            "download_tone": "idle",
        }
        values.update(overrides)
        self.widget.set_pipeline(**values)

    def test_state_update_refreshes_content_properties_and_accessibility(self):
        self._set_sample(
            fetch_status="No new tasks",
            fetch_tone="warning",
            download_status="Finish started file",
            download_tone="warning",
        )

        self.assertEqual(self.widget.fetch_node.property("tone"), "warning")
        self.assertEqual(self.widget.prepare_node.property("tone"), "idle")
        self.assertEqual(self.widget.download_node.property("tone"), "warning")
        for attribute in ("fetch_dot", "prepare_dot", "download_dot"):
            self.assertFalse(hasattr(self.widget, attribute))
        self.assertEqual(self.widget.fetch_node.accessibleName(), "FETCH")
        self.assertEqual(self.widget.prepare_node.accessibleName(), "PREPARE")
        self.assertIn(
            "Status not exposed", self.widget.prepare_node.accessibleDescription()
        )
        self.assertEqual(self.widget.download_node.accessibleName(), "DOWNLOAD")
        self.assertIn("Finish started file", self.widget.accessibleDescription())
        for detail_label in (
            self.widget.fetch_detail_label,
            self.widget.prepare_detail_label,
            self.widget.download_detail_label,
        ):
            self.assertEqual(
                detail_label.text().replace("\u200b", ""),
                "2026-07-17_12-30-45.mp4",
            )
        self.assertIn(
            "2026-07-17_12-30-45.mp4",
            self.widget.accessibleDescription(),
        )

    def test_blank_file_details_stay_blank_without_accessibility_parentheses(self):
        self._set_sample(
            fetch_detail="",
            prepare_detail="",
            download_detail="",
        )

        for label in (
            self.widget.fetch_detail_label,
            self.widget.prepare_detail_label,
            self.widget.download_detail_label,
        ):
            self.assertEqual(label.text(), "")
        self.assertNotIn("()", self.widget.accessibleDescription())

    def _show_at_width(self, width):
        self.widget.resize(width, self.widget.minimumHeight())
        self.widget.show()
        self.app.processEvents()

    def _assert_stage_inside_panel(self, stage):
        geometry = stage.geometry()
        self.assertGreater(geometry.width(), 0)
        self.assertGreater(geometry.height(), 0)
        self.assertTrue(self.widget.rect().contains(geometry.topLeft()))
        self.assertTrue(self.widget.rect().contains(geometry.bottomRight()))

    def test_three_stage_layout_and_accessibility(self):
        self._set_sample()
        # Serial stays on one row regardless of the available card width.
        self._show_at_width(420)

        self.assertTrue(self.widget.fetch_prepare_rail.isVisible())
        self.assertTrue(self.widget.prepare_download_rail.isVisible())
        self.assertEqual(
            self.widget.accessibleName(),
            "FETCH → PREPARE → DOWNLOAD",
        )
        self.assertNotIn("QUEUE:", self.widget.accessibleDescription())

        fetch = self.widget.fetch_node.geometry()
        prepare = self.widget.prepare_node.geometry()
        download = self.widget.download_node.geometry()
        self.assertLessEqual(fetch.right(), prepare.left())
        self.assertLessEqual(prepare.right(), download.left())
        for stage in (
            self.widget.fetch_node,
            self.widget.prepare_node,
            self.widget.download_node,
        ):
            self._assert_stage_inside_panel(stage)

    def test_three_stages_stay_in_one_row_when_narrow(self):
        self._set_sample()
        self._show_at_width(320)

        self.assertTrue(self.widget.fetch_prepare_rail.isVisible())
        self.assertTrue(self.widget.prepare_download_rail.isVisible())
        self.assertEqual(
            self.widget.accessibleName(),
            "FETCH → PREPARE → DOWNLOAD",
        )
        self.assertNotIn("QUEUE:", self.widget.accessibleDescription())

        visible_nodes = (
            self.widget.fetch_node,
            self.widget.prepare_node,
            self.widget.download_node,
        )
        fetch = self.widget.fetch_node.geometry()
        prepare = self.widget.prepare_node.geometry()
        download = self.widget.download_node.geometry()
        self.assertEqual(fetch.top(), prepare.top())
        self.assertEqual(prepare.top(), download.top())
        self.assertLess(fetch.right(), prepare.left())
        self.assertLess(prepare.right(), download.left())
        for stage in visible_nodes:
            self._assert_stage_inside_panel(stage)
        geometries = [stage.geometry() for stage in visible_nodes]
        for index, geometry in enumerate(geometries):
            for other in geometries[index + 1 :]:
                self.assertFalse(geometry.intersects(other))

    def test_current_filename_wraps_inside_compact_cards(self):
        summary = "2026-07-17_12-30-45.mp4"
        self._set_sample(
            fetch_detail=summary,
            prepare_detail=summary,
            download_detail=summary,
        )
        self._show_at_width(320)

        for node, label in (
            (self.widget.fetch_node, self.widget.fetch_detail_label),
            (self.widget.prepare_node, self.widget.prepare_detail_label),
            (self.widget.download_node, self.widget.download_detail_label),
        ):
            with self.subTest(node=node.objectName()):
                self.assertTrue(label.wordWrap())
                self.assertEqual(label.text().replace("\u200b", ""), summary)
                self.assertGreaterEqual(
                    label.height(),
                    label.fontMetrics().lineSpacing() * 2,
                )
                self.assertTrue(node.rect().contains(label.geometry().topLeft()))
                self.assertTrue(node.rect().contains(label.geometry().bottomRight()))

    def test_compact_height_tracks_current_filename_changes(self):
        summary = "2026-07-17_12-30-45.mp4"
        self._set_sample(
            fetch_detail="",
            prepare_detail="",
            download_detail="",
        )
        self._show_at_width(320)
        blank_minimum = self.widget.minimumHeight()

        self._set_sample(
            fetch_detail=summary,
            prepare_detail=summary,
            download_detail=summary,
        )
        self.app.processEvents()
        wrapped_minimum = self.widget.minimumHeight()
        for label in (
            self.widget.fetch_detail_label,
            self.widget.prepare_detail_label,
            self.widget.download_detail_label,
        ):
            self.assertGreaterEqual(
                label.height(),
                label.fontMetrics().lineSpacing() * 2,
            )

        self._set_sample(
            fetch_detail="",
            prepare_detail="",
            download_detail="",
        )
        self.app.processEvents()
        cleared_minimum = self.widget.minimumHeight()

        self.assertGreater(wrapped_minimum, blank_minimum)
        self.assertLess(cleared_minimum, wrapped_minimum)
        self.assertGreaterEqual(cleared_minimum, self.widget._MIN_HEIGHT)


class ThreadPipelineQueuedSignalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.widget = ui_widgets_pipeline.ThreadPipelineWidget()
        self.widget.resize(760, self.widget.sizeHint().height())
        self.receiver = _QueuedPipelineReceiver(self.widget)
        self.receiver._refresh_thread_pipeline()
        self.paint_recorder = _LabelPaintRecorder(self.widget)
        self.widget.download_detail_label.installEventFilter(self.paint_recorder)
        self.worker = None

        self.widget.show()
        self.app.processEvents()
        self.paint_recorder.texts.clear()

    def tearDown(self):
        self.widget.download_detail_label.removeEventFilter(self.paint_recorder)
        if self.worker is not None:
            if self.worker.isRunning():
                self.worker.wait(2000)
            self.worker.deleteLater()
        self.receiver.download_thread = None
        self.receiver.deleteLater()
        self.widget.close()
        self.widget.deleteLater()
        self.app.processEvents()

    def test_back_to_back_queued_activity_is_painted_and_retained(self):
        filename = "fast-media.jpg"
        path = os.path.abspath(filename)
        self.worker = _FastFileActivityThread(path)
        self.receiver.download_thread = self.worker
        self.worker.file_activity_signal.connect(
            self.receiver.receive_file_activity,
            Qt.ConnectionType.QueuedConnection,
        )

        self.worker.start()
        self.assertTrue(self.worker.wait(2000))
        self.app.processEvents()
        self.widget.download_detail_label.repaint()
        self.app.processEvents()

        self.assertEqual(
            self.receiver._thread_pipeline_current_file_name,
            filename,
        )
        for detail_label in (
            self.widget.fetch_detail_label,
            self.widget.prepare_detail_label,
            self.widget.download_detail_label,
        ):
            self.assertEqual(
                detail_label.text().replace("\u200b", ""),
                filename,
            )
        self.assertIn(filename, self.paint_recorder.texts)


class ThreadPipelineStateMappingTests(unittest.TestCase):
    class PipelineSpy:
        def __init__(self):
            self.state = None

        def set_pipeline(self, **state):
            self.state = state

    def test_cards_name_download_stages_in_both_languages(self):
        expected = {
            "en": ("Fetch", "Prepare", "Download"),
            "zh": ("获取", "准备", "下载"),
        }
        for language, titles in expected.items():
            with self.subTest(language=language):
                text = ui_i18n_text.UI_TEXT[language]
                self.assertEqual(text["pipeline_fetch_title"], titles[0])
                self.assertEqual(text["pipeline_prepare_title"], titles[1])
                self.assertEqual(text["pipeline_download_title"], titles[2])

    def make_gui(self, *, phase="idle", api="idle", download="idle", running=False):
        pipeline = self.PipelineSpy()
        owner = object()
        target = SimpleNamespace(
            thread_pipeline=pipeline,
            download_thread=owner,
            patrol_thread=None,
            queue_running=running,
            current_queue_index=None,
            queue_items=[],
            active_patrol_entry=None,
            queue_paused=False,
            _safe_exit_in_progress=False,
            _thread_pipeline_stop_requested=False,
            _thread_pipeline_current_file_name="",
            _thread_pipeline_phase=phase,
            _thread_pipeline_last_active_phase=(
                phase if phase in ("fetch", "prepare", "download") else "idle"
            ),
            _api_status_state=api,
            _download_status_state=download,
            _API_STATUS_META=ui_main_window.GUI._API_STATUS_META,
            _DOWNLOAD_STATUS_META=ui_main_window.GUI._DOWNLOAD_STATUS_META,
            _PREPARE_API_STATUSES=ui_main_window.GUI._PREPARE_API_STATUSES,
            _TERMINAL_API_STATUSES=ui_main_window.GUI._TERMINAL_API_STATUSES,
        )
        target.tr = lambda key: key
        target._download_thread_is_running = lambda: False
        target._patrol_thread_is_running = lambda: False
        target._patrol_thread_is_active = lambda: False
        target.sender = lambda: owner
        target._thread_pipeline_is_running = MethodType(
            ui_preview_events.PreviewEvents._thread_pipeline_is_running, target
        )
        target._clear_thread_pipeline_current_file = MethodType(
            ui_preview_events.PreviewEvents._clear_thread_pipeline_current_file, target
        )
        target._on_thread_pipeline_file_activity = MethodType(
            ui_controllers_status.StatusController._on_thread_pipeline_file_activity,
            target,
        )
        target._refresh_thread_pipeline = MethodType(
            ui_controllers_status.StatusController._refresh_thread_pipeline, target
        )
        return target, pipeline

    @staticmethod
    def _download_task(username="alice", media_type="posts"):
        return {
            "task_kind": "download",
            "target_user": username,
            "download_media_type": media_type,
        }

    def test_current_serial_file_replaces_every_visible_card_detail(self):
        detail_keys = (
            "fetch_detail",
            "prepare_detail",
            "download_detail",
        )
        target, pipeline = self.make_gui(running=True)
        target.current_queue_index = 0
        target.queue_items = [
            {
                "task": self._download_task(),
                "status": "running",
            }
        ]
        target._thread_pipeline_current_file_name = "2026-07-17_12-30-45.mp4"

        ui_controllers_status.StatusController._refresh_thread_pipeline(target)

        for key in detail_keys:
            self.assertEqual(
                pipeline.state[key],
                "2026-07-17_12-30-45.mp4",
            )

    def test_idle_state_does_not_show_a_stale_filename(self):
        target, pipeline = self.make_gui(running=False)
        target.current_queue_index = 0
        target._thread_pipeline_current_file_name = "stale.mp4"
        target.queue_items = [
            {
                "task": self._download_task("stale", "reels"),
                "status": "running",
            }
        ]

        ui_controllers_status.StatusController._refresh_thread_pipeline(target)

        for key in (
            "fetch_detail",
            "prepare_detail",
            "download_detail",
        ):
            self.assertEqual(pipeline.state[key], "")

    def test_active_patrol_uses_the_same_filename_display(self):
        target, pipeline = self.make_gui(running=True)
        target.current_queue_index = 0
        target.queue_items = [
            {
                "task": self._download_task("stale", "posts"),
                "status": "running",
            }
        ]
        target.active_patrol_entry = {
            "status": "running",
            "task": {
                "task_kind": "auto_patrol",
                "target_users": ["alice", "bob"],
                "download_media_type": "reels",
            },
        }
        target._patrol_thread_is_active = lambda: True
        target._patrol_thread_is_running = lambda: True
        target._thread_pipeline_current_file_name = "2026-07-17_12-30-45.mp4"

        ui_controllers_status.StatusController._refresh_thread_pipeline(target)

        for key in (
            "fetch_detail",
            "prepare_detail",
            "download_detail",
        ):
            self.assertEqual(
                pipeline.state[key],
                "2026-07-17_12-30-45.mp4",
            )

    def test_file_activity_retains_latest_file_until_explicit_clear(self):
        target, pipeline = self.make_gui(running=True)
        target.current_queue_index = 0
        target.queue_items = [
            {
                "task": self._download_task(),
                "status": "running",
            }
        ]
        first_filename = "2026-07-17_12-30-45.mp4"
        first_path = os.path.abspath(first_filename)
        second_filename = "2026-07-17_12-30-46.jpg"
        second_path = os.path.abspath(second_filename)
        detail_keys = (
            "fetch_detail",
            "prepare_detail",
            "download_detail",
        )

        target._on_thread_pipeline_file_activity(
            {
                "event": "started",
                "path": first_path,
            }
        )
        for key in detail_keys:
            self.assertEqual(pipeline.state[key], first_filename)

        target._on_thread_pipeline_file_activity(
            {
                "event": "finished",
                "path": first_path,
            }
        )
        for key in detail_keys:
            self.assertEqual(pipeline.state[key], first_filename)

        target._on_thread_pipeline_file_activity(
            {
                "event": "started",
                "path": second_path,
            }
        )
        for key in detail_keys:
            self.assertEqual(pipeline.state[key], second_filename)

        target._clear_thread_pipeline_current_file()
        self.assertEqual(target._thread_pipeline_current_file_name, "")
        for key in detail_keys:
            self.assertEqual(pipeline.state[key], "")

    def test_file_activity_ignores_started_event_from_an_old_sender(self):
        target, pipeline = self.make_gui(running=True)
        target.current_queue_index = 0
        target.queue_items = [
            {
                "task": self._download_task(),
                "status": "running",
            }
        ]
        current_filename = "2026-07-17_12-30-45.mp4"
        target._on_thread_pipeline_file_activity(
            {
                "event": "started",
                "path": os.path.abspath(current_filename),
            }
        )

        target.sender = lambda: object()
        target._on_thread_pipeline_file_activity(
            {
                "event": "started",
                "path": os.path.abspath("stale-worker-file.jpg"),
            }
        )

        self.assertEqual(
            target._thread_pipeline_current_file_name,
            current_filename,
        )
        self.assertEqual(
            pipeline.state["download_detail"],
            current_filename,
        )

    def test_serial_stage_statuses_are_mutually_exclusive(self):
        cases = (
            ("fetch", "fetching_posts", "idle", "fetch", "active"),
            ("prepare", "preparing_posts", "idle", "prepare", "active"),
            ("download", "idle", "downloading_posts", "download", "download"),
        )
        for phase, api_status, download_status, active_stage, active_tone in cases:
            with self.subTest(phase=phase):
                target, pipeline = self.make_gui(
                    phase=phase,
                    api=api_status,
                    download=download_status,
                    running=True,
                )
                ui_controllers_status.StatusController._refresh_thread_pipeline(target)

                for stage in ("fetch", "prepare", "download"):
                    expected = active_tone if stage == active_stage else "idle"
                    self.assertEqual(pipeline.state[f"{stage}_tone"], expected)

    def test_status_entry_points_select_only_their_own_stage(self):
        api_cases = {
            "fetch": (
                "fetching_posts",
                "fetching_reels",
                "fetching_tagged",
                "fetching_stories",
                "fetching_highlights",
                "fetching_highlight_items",
            ),
            "prepare": (
                "preparing_posts",
                "preparing_reels",
                "preparing_tagged",
                "preparing_stories",
                "preparing_highlights",
            ),
        }
        for expected_stage, statuses in api_cases.items():
            for status in statuses:
                with self.subTest(status=status):
                    target, pipeline = self.make_gui(running=True)
                    ui_controllers_status.StatusController.update_api_status(
                        target, status
                    )
                    self.assertEqual(target._thread_pipeline_phase, expected_stage)
                    for stage in ("fetch", "prepare", "download"):
                        expected_tone = "active" if stage == expected_stage else "idle"
                        self.assertEqual(pipeline.state[f"{stage}_tone"], expected_tone)

        for status in (
            "downloading_posts",
            "downloading_reels",
            "downloading_tagged",
            "downloading_stories",
            "downloading_highlights",
        ):
            with self.subTest(status=status):
                target, pipeline = self.make_gui(running=True)
                ui_controllers_status.StatusController.update_download_status(
                    target, status
                )
                self.assertEqual(target._thread_pipeline_phase, "download")
                self.assertEqual(pipeline.state["fetch_tone"], "idle")
                self.assertEqual(pipeline.state["prepare_tone"], "idle")
                self.assertEqual(pipeline.state["download_tone"], "download")

    def test_pause_and_stop_are_known_global_states(self):
        for requested_state in ("pause", "stop"):
            with self.subTest(requested_state=requested_state):
                target, pipeline = self.make_gui(running=True)
                target.current_queue_index = 0
                target.queue_items = [
                    {
                        "task": self._download_task(),
                        "status": "running",
                    }
                ]
                target._thread_pipeline_current_file_name = "2026-07-17_12-30-45.mp4"
                if requested_state == "pause":
                    target.queue_paused = True
                else:
                    target._thread_pipeline_stop_requested = True
                ui_controllers_status.StatusController._refresh_thread_pipeline(target)

                for stage in ("fetch", "prepare", "download"):
                    self.assertEqual(pipeline.state[f"{stage}_tone"], "warning")
                for key in (
                    "fetch_detail",
                    "prepare_detail",
                    "download_detail",
                ):
                    expected = "2026-07-17_12-30-45.mp4"
                    self.assertEqual(pipeline.state[key], expected)

    def test_download_error_stays_on_last_download_phase(self):
        target, pipeline = self.make_gui(
            phase="idle", api="error", download="idle", running=True
        )
        target._thread_pipeline_last_active_phase = "download"

        ui_controllers_status.StatusController.update_api_status(target, "error")

        self.assertEqual(target._thread_pipeline_phase, "download")
        self.assertEqual(pipeline.state["download_tone"], "danger")
        self.assertEqual(pipeline.state["fetch_tone"], "idle")

    def test_prepare_error_stays_on_last_prepare_phase(self):
        target, pipeline = self.make_gui(
            phase="idle", api="error", download="idle", running=True
        )
        target._thread_pipeline_last_active_phase = "prepare"

        ui_controllers_status.StatusController.update_api_status(target, "error")

        self.assertEqual(target._thread_pipeline_phase, "prepare")
        self.assertEqual(pipeline.state["prepare_tone"], "danger")
        self.assertEqual(pipeline.state["fetch_tone"], "idle")
        self.assertEqual(pipeline.state["download_tone"], "idle")

    def test_terminal_statuses_stay_on_the_last_real_serial_stage(self):
        cases = (
            ("auth_required", "warning"),
            ("rate_limited", "danger"),
            ("iphone_quality_unavailable", "danger"),
        )
        for status, expected_tone in cases:
            for last_phase in ("fetch", "prepare", "download"):
                with self.subTest(status=status, last_phase=last_phase):
                    target, pipeline = self.make_gui(running=True)
                    target._thread_pipeline_last_active_phase = last_phase

                    ui_controllers_status.StatusController.update_api_status(
                        target, status
                    )

                    self.assertEqual(
                        target._thread_pipeline_phase,
                        last_phase,
                    )
                    for stage in ("fetch", "prepare", "download"):
                        expected = expected_tone if stage == last_phase else "idle"
                        self.assertEqual(
                            pipeline.state[f"{stage}_tone"],
                            expected,
                        )


class ThreadPipelineIntegrationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(ui_source(), filename="inscrawler/ui")

    @classmethod
    def function(cls, name):
        matches = [
            node
            for node in ast.walk(cls.tree)
            if isinstance(node, ast.FunctionDef) and node.name == name
        ]
        if len(matches) != 1:
            raise AssertionError(f"Expected one {name} function, found {len(matches)}")
        return matches[0]

    def test_status_entry_points_refresh_the_pipeline(self):
        for name in ("update_api_status", "update_download_status"):
            called = {
                ast.unparse(node.func)
                for node in ast.walk(self.function(name))
                if isinstance(node, ast.Call)
            }
            self.assertIn("self._refresh_thread_pipeline", called)

    def test_formal_group_installs_the_pipeline_widget(self):
        source = ast.unparse(self.function("init_ui"))
        self.assertIn(
            "self.thread_pipeline = ui_widgets_pipeline.ThreadPipelineWidget(self.thread_status_group)",
            source,
        )
        self.assertIn("thread_status_layout.addWidget(self.thread_pipeline)", source)

    def test_each_task_clears_the_previous_pipeline_phase(self):
        source = ast.unparse(self.function("start_task_by_index"))
        for assignment in (
            "self._api_status_state = 'idle'",
            "self._download_status_state = 'idle'",
            "self._thread_pipeline_phase = 'idle'",
            "self._thread_pipeline_last_active_phase = 'idle'",
        ):
            self.assertIn(assignment, source)


if __name__ == "__main__":
    unittest.main()
