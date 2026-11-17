"""Compose the desktop controllers, build the window, and own its lifecycle."""

from __future__ import annotations

import os
from collections import OrderedDict, deque

import inscrawler.config as config
import inscrawler.instagram.session as instagram_session
import inscrawler.paths as app_paths
import inscrawler.tasks.schema as tasks_schema
import inscrawler.ui.console as ui_console
import inscrawler.ui.constants as ui_constants
import inscrawler.ui.controllers.logs as ui_controllers_logs
import inscrawler.ui.i18n.options as ui_i18n_options
import inscrawler.ui.preview.thumbnails as ui_preview_thumbnails
import inscrawler.ui.qt as ui_qt
import inscrawler.ui.theme.icons as ui_theme_icons
import inscrawler.ui.widgets.combo as ui_widgets_combo
import inscrawler.ui.widgets.common as ui_widgets_common
import inscrawler.ui.widgets.pipeline as ui_widgets_pipeline
import inscrawler.ui.widgets.queue as ui_widgets_queue
import inscrawler.ui.widgets.switch as ui_widgets_switch
import inscrawler.ui.widgets.titlebar as ui_widgets_titlebar
from inscrawler.ui.controllers.desktop import DesktopController
from inscrawler.ui.controllers.execution import QueueExecutionController
from inscrawler.ui.controllers.forms import FormController
from inscrawler.ui.controllers.logs import TaskLogController
from inscrawler.ui.controllers.patrol import PatrolController
from inscrawler.ui.controllers.queue_controls import QueueControls
from inscrawler.ui.controllers.queue_view import QueueViewController
from inscrawler.ui.controllers.status import StatusController
from inscrawler.ui.controllers.targets import TargetController
from inscrawler.ui.controllers.task_store import TaskStoreController
from inscrawler.ui.layout import WindowLayout
from inscrawler.ui.preferences import PreferencesController
from inscrawler.ui.preview.controller import PreviewController
from inscrawler.ui.preview.events import PreviewEvents
from inscrawler.ui.theme.manager import ThemeController


class GUI(
    PreviewController,
    PreferencesController,
    FormController,
    DesktopController,
    WindowLayout,
    ThemeController,
    TargetController,
    TaskStoreController,
    QueueViewController,
    PatrolController,
    QueueControls,
    QueueExecutionController,
    TaskLogController,
    PreviewEvents,
    StatusController,
    ui_qt.QMainWindow,
):
    system_notification_requested = ui_qt.Signal(str)

    def __init__(self, capture_console=False):
        super().__init__()
        self.ui_theme = "light"
        self.ui_language = "en"
        self.close_behavior = "tray"
        self.iphone_support = False
        self.profile_picture_sync_enabled = False
        self.media_request_cap_enabled = False
        self.shutdown_after_queue = False
        self._shutdown_countdown_dialog = None
        self.debug_logging_enabled = False
        self.video_preview_cache_mb = ui_constants.DEFAULT_VIDEO_PREVIEW_CACHE_MB
        self._loading_settings = False
        self._loading_saved_tasks = False
        self._combo_boxes = []
        self._target_avatar_icon_cache = {}
        self._resize_cursor_edges = ui_qt.Qt.Edges()
        self._force_exit = False
        self._safe_exit_in_progress = False
        self.tray_icon = None
        self.tray_menu = None
        self.tray_open_action = None
        self.tray_exit_action = None
        self.system_notification_requested.connect(self._show_system_notification)
        self.current_preview_images = []  # 初始化累积预览列表
        self._main_preview_thumbnails_visible = True
        self.queue_items = []
        self.current_queue_index = None
        self.queue_running = False
        self.queue_paused = False
        self._download_stop_reason = None
        self._queue_handoff_target_id = None
        self.queue_started = 0
        self.queue_completed = 0
        self._risk_run_id = None
        self.patrol_thread = None
        self.patrol_enabled = False
        self.active_patrol_entry = None
        self._patrol_target_options = []
        self._patrol_selected_target_keys = set()
        self._patrol_delay_before_start = False
        self._api_status_state = "idle"
        self._download_status_state = "idle"
        self._thread_pipeline_phase = "idle"
        self._thread_pipeline_last_active_phase = "idle"
        self._thread_pipeline_stop_requested = False
        self._thread_pipeline_current_file_name = ""
        self.console_logs = deque()
        self._console_log_dialog = None
        self._console_capture_bridge = None
        self._console_capture_hub = None
        ui_console._console_log_callback = self._append_console_log
        if capture_console:
            self._console_capture_bridge = ui_console._ConsoleCaptureBridge(self)
            self._console_capture_bridge.record_ready.connect(
                self._on_console_capture_record
            )
            self._console_capture_hub = ui_console._install_console_capture(
                self._console_capture_bridge.submit
            )
        self.default_save_path = app_paths.DEFAULT_DATA_DIR
        self.max_preview_images = (
            ui_constants.MAX_PREVIEW_IMAGES
        )  # 预览数量（可在设置中修改）
        # 视频缩略图提取器（后台线程池）
        self._main_preview_thumbnail_owner = object()
        self._thumb_extractor = ui_preview_thumbnails.VideoThumbnailExtractor(self)
        self._thumb_extractor.thumbnail_ready.connect(self._on_thumbnail_ready)
        # 图片缩略图提取器（受限并发线程池 + scaled decode，主线程零阻塞）
        self._image_thumb_extractor = ui_preview_thumbnails.ImageThumbnailExtractor(
            self
        )
        self._image_thumb_extractor.thumbnail_ready.connect(
            self._on_image_thumbnail_ready
        )
        self._image_thumb_inflight = set()  # 主线程专用去重
        self._image_pixmap_cache = {}  # (path, version, size) → QPixmap
        self._video_home_pixmap_cache = {}  # current home previews; order comes from history
        self._video_detail_pixmap_cache = (
            OrderedDict()
        )  # historical/detail previews, LRU
        self._video_cache_total_bytes = 0
        # 防抖定时器：多次快速下载只触发一次刷新
        self._refresh_timer = ui_qt.QTimer()
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(100)
        self._refresh_timer.timeout.connect(self.refresh_preview_images)
        self._preview_history_save_timer = ui_qt.QTimer(self)
        self._preview_history_save_timer.setSingleShot(True)
        self._preview_history_save_timer.setInterval(250)
        self._preview_history_save_timer.timeout.connect(self.save_preview_history)
        initial_env = config._read_dotenv_file(app_paths.ENV_FILE)
        self.set_debug_logging_enabled(
            initial_env.get("debug_logging", "false"),
            persist=False,
        )
        self.setWindowFlags(
            ui_qt.Qt.WindowType.Window | ui_qt.Qt.WindowType.FramelessWindowHint
        )
        self.init_ui()
        self._init_system_tray()
        self.calculate_image_sizes()
        self._loading_settings = True
        self.load_env_settings()
        self._loading_settings = False
        self.load_target()
        self.load_saved_tasks()  # 加载已保存任务
        self.load_preview_history()

    def _apply_window_icon(self):
        """Apply the app icon to both Qt's widget and its native window."""
        if not os.path.exists(app_paths.ICON_FILE):
            return False
        icon = ui_qt.QIcon(app_paths.ICON_FILE)
        if icon.isNull():
            return False
        self.setWindowIcon(icon)
        window_handle = self.windowHandle()
        if window_handle is not None:
            window_handle.setIcon(icon)
        return True

    def init_ui(self):
        self.setWindowTitle(self.tr("app_title"))
        self.setGeometry(100, 100, 1000, 1200)
        self._apply_window_icon()

        font = ui_qt.QFont()
        font.setPointSize(12)
        self.setFont(font)

        self.central_widget = ui_qt.QWidget()
        self.central_widget.setObjectName("centralWidget")
        self.central_widget.setMouseTracking(True)
        self.central_widget.installEventFilter(self)
        self.setCentralWidget(self.central_widget)

        self.root_layout = ui_qt.QVBoxLayout(self.central_widget)
        self.root_layout.setContentsMargins(
            ui_constants.WINDOW_FRAME_MARGIN,
            ui_constants.WINDOW_FRAME_MARGIN,
            ui_constants.WINDOW_FRAME_MARGIN,
            ui_constants.WINDOW_FRAME_MARGIN,
        )
        self.root_layout.setSpacing(0)

        self.title_bar = ui_widgets_titlebar.TitleBar(self)
        self.root_layout.addWidget(self.title_bar)

        self.content_widget = ui_qt.QWidget()
        content_widget = self.content_widget
        self.content_layout = ui_qt.QHBoxLayout(content_widget)
        content_layout = self.content_layout
        content_layout.setContentsMargins(9, 9, 9, 9)
        content_layout.setSpacing(9)
        self.root_layout.addWidget(content_widget)

        self.left_layout = ui_qt.QVBoxLayout()
        left_layout = self.left_layout
        left_layout.setSpacing(9)

        self.right_layout = ui_qt.QVBoxLayout()
        right_layout = self.right_layout
        right_layout.setContentsMargins(9, 9, 9, 9)
        right_layout.setSpacing(9)

        self.login_group = ui_qt.QGroupBox()
        settings_layout = ui_qt.QGridLayout()
        settings_layout.setContentsMargins(*ui_constants.HOME_SECTION_LAYOUT_MARGINS)
        self.login_group.setLayout(settings_layout)

        self.username_label = ui_qt.QLabel()
        settings_layout.addWidget(self.username_label, 0, 0)
        self.username = ui_qt.QLineEdit()
        settings_layout.addWidget(self.username, 0, 1)

        self.agent_label = ui_qt.QLabel()
        settings_layout.addWidget(self.agent_label, 1, 0)
        self.user_agent = ui_qt.QLineEdit()
        settings_layout.addWidget(self.user_agent, 1, 1)

        self.cookie_label = ui_qt.QLabel()
        settings_layout.addWidget(self.cookie_label, 2, 0)
        self.session_cookie = ui_qt.QLineEdit()
        self.session_cookie.setObjectName("sessionCookieInput")
        settings_layout.addWidget(self.session_cookie, 2, 1)

        left_layout.addWidget(self.login_group)

        self.task_group = ui_qt.QGroupBox()
        task_layout = ui_qt.QGridLayout()
        task_layout.setContentsMargins(*ui_constants.HOME_SECTION_LAYOUT_MARGINS)
        task_layout.setColumnStretch(1, 1)
        self.task_group.setLayout(task_layout)

        self.target_label = ui_qt.QLabel()
        task_layout.addWidget(self.target_label, 0, 0)
        self.target = ui_widgets_combo.AlignedComboBox()
        self.target.setEditable(True)
        self._configure_combo_box(self.target)
        self.target.set_item_action(
            ui_theme_icons._make_svg_icon(ui_theme_icons._QUEUE_DELETE_SVG),
            self.tr("remove_target_tooltip"),
        )
        self.target.item_action_requested.connect(self.request_remove_target)
        if self.target.lineEdit() is not None:
            self.target.lineEdit().textEdited.connect(
                self._clear_target_selection_on_edit
            )
        task_layout.addWidget(self.target, 0, 1)

        self.download_type_label = ui_qt.QLabel()
        task_layout.addWidget(self.download_type_label, 1, 0)
        self.type = ui_widgets_combo.AlignedComboBox()
        self._configure_combo_box(self.type)
        self.type.addItems(tasks_schema.DOWNLOAD_MEDIA_TYPES)
        self.type.currentIndexChanged.connect(self._update_download_scope_controls)
        task_layout.addWidget(self.type, 1, 1)

        self.download_limit_label = ui_qt.QLabel()
        task_layout.addWidget(self.download_limit_label, 2, 0)
        self.scope_limit = ui_qt.QSpinBox()
        self.scope_limit.setRange(1, 1000)
        self.scope_limit.setValue(1)
        self.scope_limit.setSizePolicy(
            ui_qt.QSizePolicy.Policy.Expanding, ui_qt.QSizePolicy.Policy.Fixed
        )
        task_layout.addWidget(self.scope_limit, 2, 1)

        self.media_request_cap_enabled_label = ui_qt.QLabel()
        task_layout.addWidget(self.media_request_cap_enabled_label, 3, 0)
        self.media_request_cap_control = ui_qt.QWidget()
        media_request_cap_layout = ui_qt.QHBoxLayout(self.media_request_cap_control)
        media_request_cap_layout.setContentsMargins(0, 0, 0, 0)
        media_request_cap_layout.setSpacing(8)
        self.media_request_cap_enabled_check = ui_widgets_switch.HomeToggleSwitch(
            self._theme_tokens,
            self.task_group,
        )
        self.media_request_cap_enabled_check.stateChanged.connect(
            self._on_media_request_cap_enabled_changed
        )
        media_request_cap_layout.addWidget(
            self.media_request_cap_enabled_check,
            0,
            ui_qt.Qt.AlignmentFlag.AlignLeft | ui_qt.Qt.AlignmentFlag.AlignVCenter,
        )

        self.media_request_cap_label = ui_qt.QLabel()
        media_request_cap_layout.addWidget(self.media_request_cap_label)
        self.media_request_cap_spin = ui_qt.QSpinBox()
        self.media_request_cap_spin.setRange(1, 10000)
        self.media_request_cap_spin.setValue(ui_constants.DEFAULT_MEDIA_REQUEST_CAP)
        self.media_request_cap_spin.setSizePolicy(
            ui_qt.QSizePolicy.Policy.Expanding, ui_qt.QSizePolicy.Policy.Fixed
        )
        self.media_request_cap_label.setBuddy(self.media_request_cap_spin)
        media_request_cap_layout.addWidget(self.media_request_cap_spin, 1)
        task_layout.addWidget(self.media_request_cap_control, 3, 1)

        self.add_task_btn = ui_qt.QPushButton()
        self.add_task_btn.setProperty("variant", "primary")
        self.add_task_btn.setMinimumHeight(40)
        self.add_task_btn.setSizePolicy(
            ui_qt.QSizePolicy.Policy.Fixed, ui_qt.QSizePolicy.Policy.Fixed
        )
        self.add_task_btn.clicked.connect(self.add_task)
        self.open_profile_btn = ui_qt.QPushButton()
        self.open_profile_btn.setProperty("variant", "secondary")
        self.open_profile_btn.setMinimumHeight(40)
        self.open_profile_btn.setSizePolicy(
            ui_qt.QSizePolicy.Policy.Fixed, ui_qt.QSizePolicy.Policy.Fixed
        )
        self.open_profile_btn.clicked.connect(self.open_target_profile)
        self.open_local_folder_btn = ui_qt.QPushButton()
        self.open_local_folder_btn.setProperty("variant", "secondary")
        self.open_local_folder_btn.setMinimumHeight(40)
        self.open_local_folder_btn.setSizePolicy(
            ui_qt.QSizePolicy.Policy.Fixed, ui_qt.QSizePolicy.Policy.Fixed
        )
        self.open_local_folder_btn.clicked.connect(self.open_target_local_folder)
        left_layout.addWidget(self.task_group)
        task_button_layout = ui_qt.QHBoxLayout()
        task_button_layout.setSpacing(8)
        task_button_layout.addStretch()
        task_button_layout.addWidget(self.add_task_btn)
        task_button_layout.addWidget(self.open_profile_btn)
        task_button_layout.addWidget(self.open_local_folder_btn)
        task_button_layout.addStretch()
        left_layout.addLayout(task_button_layout)

        self.patrol_group = ui_qt.QGroupBox()
        patrol_layout = ui_qt.QGridLayout()
        patrol_layout.setContentsMargins(*ui_constants.HOME_SECTION_LAYOUT_MARGINS)
        patrol_layout.setColumnStretch(1, 1)
        self.patrol_group.setLayout(patrol_layout)

        self.patrol_media_type_label = ui_qt.QLabel()
        patrol_layout.addWidget(self.patrol_media_type_label, 0, 0)
        self.patrol_media_type = ui_widgets_combo.AlignedComboBox()
        self._configure_combo_box(self.patrol_media_type)
        for label, value in tasks_schema.PATROL_MEDIA_TYPES:
            self.patrol_media_type.addItem(label, value)
        self.patrol_media_type.currentIndexChanged.connect(
            self._update_patrol_page_controls
        )
        patrol_layout.addWidget(self.patrol_media_type, 0, 1)

        self.patrol_page_limit_label = ui_qt.QLabel()
        patrol_layout.addWidget(self.patrol_page_limit_label, 1, 0)
        self.patrol_page_limit = ui_qt.QSpinBox()
        self.patrol_page_limit.setRange(1, 100)
        self.patrol_page_limit.setValue(ui_constants.DEFAULT_PATROL_PAGE_LIMIT)
        self.patrol_page_limit.setSizePolicy(
            ui_qt.QSizePolicy.Policy.Expanding,
            ui_qt.QSizePolicy.Policy.Fixed,
        )
        patrol_layout.addWidget(self.patrol_page_limit, 1, 1)

        self.patrol_btn = ui_qt.QPushButton()
        self.patrol_btn.setProperty("variant", "primary")
        self.patrol_btn.setMinimumHeight(40)
        self.patrol_btn.setSizePolicy(
            ui_qt.QSizePolicy.Policy.Fixed, ui_qt.QSizePolicy.Policy.Fixed
        )
        self.patrol_btn.clicked.connect(self.add_patrol_task)
        self.patrol_targets_btn = ui_qt.QPushButton()
        self.patrol_targets_btn.setProperty("variant", "secondary")
        self.patrol_targets_btn.setMinimumHeight(40)
        self.patrol_targets_btn.setSizePolicy(
            ui_qt.QSizePolicy.Policy.Fixed,
            ui_qt.QSizePolicy.Policy.Fixed,
        )
        self.patrol_targets_btn.clicked.connect(self.open_patrol_target_selector)
        left_layout.addWidget(self.patrol_group)
        patrol_button_layout = ui_qt.QHBoxLayout()
        patrol_button_layout.setSpacing(8)
        patrol_button_layout.addStretch()
        patrol_button_layout.addWidget(self.patrol_targets_btn)
        patrol_button_layout.addWidget(self.patrol_btn)
        patrol_button_layout.addStretch()
        left_layout.addLayout(patrol_button_layout)

        self.download_group = ui_qt.QGroupBox()
        download_layout = ui_qt.QGridLayout()
        download_layout.setContentsMargins(*ui_constants.HOME_SECTION_LAYOUT_MARGINS)
        self.download_group.setLayout(download_layout)

        self.save_path_label = ui_qt.QLabel()
        download_layout.addWidget(self.save_path_label, 0, 0)
        path_layout = ui_qt.QHBoxLayout()
        path_layout.setContentsMargins(0, 0, 0, 0)
        path_layout.setSpacing(6)
        self.save_path = ui_qt.QLineEdit()
        path_layout.addWidget(self.save_path)
        self.browse_button = ui_qt.QPushButton()
        self.browse_button.setProperty("variant", "secondary")
        self.browse_button.clicked.connect(self.choose_save_path)
        path_layout.addWidget(self.browse_button)
        path_widget = ui_qt.QWidget()
        path_widget.setLayout(path_layout)
        download_layout.addWidget(path_widget, 0, 1)

        self.notification_label = ui_qt.QLabel()
        download_layout.addWidget(self.notification_label, 1, 0)
        self.notification = ui_widgets_combo.AlignedComboBox()
        self._configure_combo_box(self.notification)
        for notification_key, labels in ui_i18n_options.NOTIFICATION_OPTIONS:
            self.notification.addItem(
                labels.get(self.ui_language, labels["en"]), notification_key
            )
        download_layout.addWidget(self.notification, 1, 1)

        self.webhook_label = ui_qt.QLabel()
        download_layout.addWidget(self.webhook_label, 2, 0)
        self.webhook = ui_qt.QLineEdit()
        download_layout.addWidget(self.webhook, 2, 1)

        self.telegram_bot_token_label = ui_qt.QLabel()
        download_layout.addWidget(self.telegram_bot_token_label, 3, 0)
        self.telegram_bot_token = ui_qt.QLineEdit()
        download_layout.addWidget(self.telegram_bot_token, 3, 1)

        self.telegram_chat_id_label = ui_qt.QLabel()
        download_layout.addWidget(self.telegram_chat_id_label, 4, 0)
        self.telegram_chat_id = ui_qt.QLineEdit()
        download_layout.addWidget(self.telegram_chat_id, 4, 1)

        self.shutdown_after_queue_label = ui_qt.QLabel()
        download_layout.addWidget(self.shutdown_after_queue_label, 5, 0)
        self.shutdown_after_queue_check = ui_widgets_switch.HomeToggleSwitch(
            self._theme_tokens,
            self.download_group,
        )
        self.shutdown_after_queue_check.setChecked(False)
        self.shutdown_after_queue_check.stateChanged.connect(
            self._on_shutdown_after_queue_changed
        )
        download_layout.addWidget(
            self.shutdown_after_queue_check,
            5,
            1,
            ui_qt.Qt.AlignmentFlag.AlignLeft | ui_qt.Qt.AlignmentFlag.AlignVCenter,
        )

        self.notification.currentIndexChanged.connect(
            self._update_notification_fields_visibility
        )
        left_layout.addWidget(self.download_group)

        button_layout = ui_qt.QHBoxLayout()
        button_layout.addStretch()

        self.download_btn = ui_qt.QPushButton()
        self.download_btn.setProperty("variant", "primary")
        self.download_btn.setMinimumHeight(40)
        self.download_btn.clicked.connect(self.start_download)

        self.save = ui_qt.QPushButton()
        self.save.setProperty("variant", "primary")
        self.save.setMinimumHeight(40)
        self.save.clicked.connect(self.save_settings)

        self.queue_group = ui_qt.QGroupBox()
        queue_layout = ui_qt.QVBoxLayout()
        queue_layout.setContentsMargins(*ui_constants.HOME_SECTION_LAYOUT_MARGINS)
        queue_layout.setSpacing(0)
        self.queue_group.setLayout(queue_layout)
        self.queue_list = ui_widgets_queue.TaskQueueListWidget()
        self.queue_list.setObjectName("taskQueueList")
        self.queue_list.setContentsMargins(0, 0, 0, 0)
        self.queue_list.setSpacing(0)
        self.queue_list.setMinimumHeight(0)
        self.queue_list.setSizePolicy(
            ui_qt.QSizePolicy.Policy.Preferred, ui_qt.QSizePolicy.Policy.Ignored
        )
        # ScrollPerPixel ensures the scrollbar maximum is total content height
        # minus viewport height, so scrolling to the bottom puts the last item
        # flush against the viewport bottom (no leftover blank row).
        self.queue_list.setVerticalScrollMode(
            ui_qt.QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self.queue_list.setUniformItemSizes(True)
        self.queue_list.set_drop_validator(self._queue_drop_is_allowed)
        self.queue_list.order_preview_changed.connect(
            self._on_queue_order_preview_changed
        )
        self.queue_list.order_changed.connect(self._on_queue_order_changed)
        queue_layout.addWidget(self.queue_list)
        self.queue_group.setMinimumHeight(0)
        self.queue_group.setSizePolicy(
            ui_qt.QSizePolicy.Policy.Preferred, ui_qt.QSizePolicy.Policy.Ignored
        )
        left_layout.addWidget(self.queue_group, 1)

        self.clear_queue_btn = ui_qt.QPushButton()
        self.clear_queue_btn.setProperty("variant", "secondary")
        self.clear_queue_btn.setMinimumHeight(40)
        self.clear_queue_btn.clicked.connect(self.clear_queue)

        button_layout.addWidget(self.download_btn)
        button_layout.addWidget(self.save)
        button_layout.addWidget(self.clear_queue_btn)
        button_layout.addStretch()

        left_layout.addLayout(button_layout)

        # 线程预览 section — 放在右侧预览区域上方
        self.thread_status_group = ui_widgets_common.ClickableGroupBox()
        self.thread_status_group.clicked.connect(self.show_console_log_dialog)
        thread_status_layout = ui_qt.QVBoxLayout()
        thread_status_layout.setContentsMargins(
            *ui_constants.HOME_SECTION_LAYOUT_MARGINS
        )
        thread_status_layout.setSpacing(0)
        self.thread_status_group.setLayout(thread_status_layout)

        self.thread_pipeline = ui_widgets_pipeline.ThreadPipelineWidget(
            self.thread_status_group
        )
        thread_status_layout.addWidget(self.thread_pipeline)

        right_layout.addWidget(self.thread_status_group)

        self.preview_group = ui_qt.QGroupBox()
        preview_layout = ui_qt.QVBoxLayout()
        preview_layout.setContentsMargins(*ui_constants.HOME_SECTION_LAYOUT_MARGINS)
        preview_layout.setSpacing(0)
        self.preview_group.setLayout(preview_layout)

        self.preview_visibility_button = ui_qt.QToolButton(self.preview_group)
        self.preview_visibility_button.setObjectName("previewVisibilityButton")
        self.preview_visibility_button.setAutoRaise(True)
        self.preview_visibility_button.setCheckable(True)
        self.preview_visibility_button.setChecked(self._main_preview_thumbnails_visible)
        self.preview_visibility_button.setFixedSize(ui_qt.QSize(24, 24))
        self.preview_visibility_button.setIconSize(ui_qt.QSize(18, 18))
        self.preview_visibility_button.setCursor(
            ui_qt.Qt.CursorShape.PointingHandCursor
        )
        self.preview_visibility_button.toggled.connect(
            self._set_main_preview_thumbnails_visible
        )

        self.scroll = ui_qt.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setVerticalScrollBarPolicy(
            ui_qt.Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.scroll.setHorizontalScrollBarPolicy(
            ui_qt.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.container = ui_qt.QWidget()
        self.grid = ui_qt.QGridLayout()
        self.grid.setSpacing(0)
        self.grid.setContentsMargins(0, 0, 0, 0)
        for col in range(3):
            self.grid.setColumnStretch(col, 1)
        self.container.setLayout(self.grid)

        self.scroll.setWidget(self.container)
        preview_layout.addWidget(self.scroll)
        self.cells = []

        right_layout.addWidget(self.preview_group, 1)

        self.left_widget = ui_qt.QWidget()
        left_widget = self.left_widget
        left_widget.setLayout(left_layout)
        left_widget.setFixedWidth(450)
        left_widget.setSizePolicy(
            ui_qt.QSizePolicy.Policy.Fixed,
            ui_qt.QSizePolicy.Policy.Preferred,
        )

        self.right_widget = ui_qt.QWidget()
        right_widget = self.right_widget
        right_widget.setLayout(right_layout)
        # The download status panel keeps all three stages in one row.
        right_widget.setMinimumWidth(450)
        right_widget.setSizePolicy(
            ui_qt.QSizePolicy.Policy.Expanding,
            ui_qt.QSizePolicy.Policy.Preferred,
        )

        content_layout.addWidget(left_widget, 0)
        content_layout.addWidget(right_widget, 1)

        self.retranslate_ui()
        self.apply_theme(self.ui_theme)
        self.title_bar.sync_ui()
        self._lock_static_section_heights()
        self._update_notification_fields_visibility()

    def request_exit_application(self):
        self._force_exit = True
        if self.tray_icon is not None:
            self.tray_icon.hide()
        app = ui_qt.QApplication.instance()
        if app is not None:
            for widget in list(app.topLevelWidgets()):
                if widget is self:
                    continue
                widget.close()
        self.close()
        if app is not None:
            app.quit()

    def _resize_edges_for_pos(self, pos):
        if self.isMaximized():
            return ui_qt.Qt.Edges()
        edges = ui_qt.Qt.Edges()
        if pos.x() <= ui_constants.WINDOW_FRAME_MARGIN:
            edges |= ui_qt.Qt.Edge.LeftEdge
        elif pos.x() >= self.central_widget.width() - ui_constants.WINDOW_FRAME_MARGIN:
            edges |= ui_qt.Qt.Edge.RightEdge
        if pos.y() <= ui_constants.WINDOW_FRAME_MARGIN:
            edges |= ui_qt.Qt.Edge.TopEdge
        elif pos.y() >= self.central_widget.height() - ui_constants.WINDOW_FRAME_MARGIN:
            edges |= ui_qt.Qt.Edge.BottomEdge
        return edges

    def _cursor_for_edges(self, edges):
        horizontal = bool(edges & ui_qt.Qt.Edge.LeftEdge) or bool(
            edges & ui_qt.Qt.Edge.RightEdge
        )
        vertical = bool(edges & ui_qt.Qt.Edge.TopEdge) or bool(
            edges & ui_qt.Qt.Edge.BottomEdge
        )
        if horizontal and vertical:
            if bool(edges & ui_qt.Qt.Edge.LeftEdge) == bool(
                edges & ui_qt.Qt.Edge.TopEdge
            ):
                return ui_qt.Qt.CursorShape.SizeFDiagCursor
            return ui_qt.Qt.CursorShape.SizeBDiagCursor
        if horizontal:
            return ui_qt.Qt.CursorShape.SizeHorCursor
        if vertical:
            return ui_qt.Qt.CursorShape.SizeVerCursor
        return ui_qt.Qt.CursorShape.ArrowCursor

    def eventFilter(self, watched, event):
        if watched is self.central_widget:
            event_type = event.type()
            if event_type == ui_qt.QEvent.Type.MouseMove and hasattr(event, "position"):
                edges = self._resize_edges_for_pos(event.position().toPoint())
                self._resize_cursor_edges = edges
                self.central_widget.setCursor(self._cursor_for_edges(edges))
            elif event_type == ui_qt.QEvent.Type.MouseButtonPress and hasattr(
                event, "position"
            ):
                if event.button() == ui_qt.Qt.MouseButton.LeftButton:
                    edges = self._resize_edges_for_pos(event.position().toPoint())
                    if edges:
                        handle = self.windowHandle()
                        if handle is not None and handle.startSystemResize(edges):
                            return True
            elif event_type == ui_qt.QEvent.Type.Leave:
                self._resize_cursor_edges = ui_qt.Qt.Edges()
                self.central_widget.setCursor(ui_qt.Qt.CursorShape.ArrowCursor)
        return super().eventFilter(watched, event)

    def _update_window_frame(self):
        margin = 0 if self.isMaximized() else ui_constants.WINDOW_FRAME_MARGIN
        self.root_layout.setContentsMargins(margin, margin, margin, margin)
        if hasattr(self, "title_bar"):
            self.title_bar.sync_ui()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == ui_qt.QEvent.Type.WindowStateChange:
            self._update_window_frame()

    def _configure_combo_box(self, combo):
        self._combo_boxes.append(combo)
        combo.destroyed.connect(lambda: self._combo_boxes.remove(combo))
        popup_view = combo.popup_view()
        popup_view.setContentsMargins(0, 0, 0, 0)
        popup_view.viewport().setAutoFillBackground(True)
        self._apply_combo_popup_style(combo)

    def closeEvent(self, event):
        """程序关闭时保存任务；真实退出前必须先停掉下载线程"""
        if (
            not self._force_exit
            and self.close_behavior == "tray"
            and self.tray_icon is not None
        ):
            self.save_tasks()
            if self.hide_to_tray():
                event.ignore()
                return
        countdown_dialog = self._shutdown_countdown_dialog
        if countdown_dialog is not None:
            try:
                countdown_dialog.close()
            except RuntimeError:
                pass
            self._shutdown_countdown_dialog = None
        # 真实退出路径：请求下载线程在下一个安全点停止，等待其自然结束。
        self._stop_patrol_thread_for_exit()
        self._stop_download_thread_for_exit()
        # Both workers are now fully stopped.  Close any scope that did not
        # reach its normal result signal so shutdown-only console output does
        # not leak into the final task detail.
        ui_controllers_logs.TaskLogController._finish_task_console_capture(
            self,
            ui_controllers_logs.TaskLogController._active_console_task_id(self),
        )
        # 关闭 Instaloader 单例（释放 requests.Session 等资源）
        try:
            instagram_session.shutdown_loader()
        except Exception:
            pass
        # 关闭缩略图解码线程池
        try:
            self._thumb_extractor.shutdown()
        except Exception:
            pass
        try:
            self._image_thumb_extractor.shutdown()
        except Exception:
            pass
        self.flush_preview_history()
        if self._console_capture_bridge is not None:
            self._console_capture_bridge.flush()
        self.save_tasks()
        if self.tray_icon is not None:
            self.tray_icon.hide()
        ui_console._uninstall_console_capture(self._console_capture_hub)
        self._console_capture_hub = None
        ui_console._console_log_callback = None
        event.accept()

    _API_STATUS_META = {
        "idle": ("status_idle", "muted"),
        "client_init": ("status_client_init", "accent"),
        "session_validating": ("status_session_validating", "accent"),
        "login": ("status_login", "accent"),
        "checking_user": ("status_checking_user", "accent"),
        "fetching_posts": ("status_fetching_posts", "accent"),
        "fetching_reels": ("status_fetching_reels", "accent"),
        "fetching_tagged": ("status_fetching_tagged", "accent"),
        "fetching_stories": ("status_fetching_stories", "accent"),
        "fetching_highlights": ("status_fetching_highlights", "accent"),
        "fetching_highlight_items": ("status_fetching_highlight_items", "accent"),
        "preparing_posts": ("status_preparing_posts", "accent"),
        "preparing_reels": ("status_preparing_reels", "accent"),
        "preparing_tagged": ("status_preparing_tagged", "accent"),
        "preparing_stories": ("status_preparing_stories", "accent"),
        "preparing_highlights": ("status_preparing_highlights", "accent"),
        "auth_required": ("status_auth_required", "warning"),
        "rate_limited": ("status_rate_limited", "danger"),
        "iphone_quality_unavailable": ("status_iphone_quality_unavailable", "danger"),
        "error": ("status_error", "danger"),
    }

    _DOWNLOAD_STATUS_META = {
        "idle": ("status_idle", "muted"),
        "downloading_posts": ("status_downloading_posts", "accent"),
        "downloading_reels": ("status_downloading_reels", "accent"),
        "downloading_tagged": ("status_downloading_tagged", "accent"),
        "downloading_stories": ("status_downloading_stories", "accent"),
        "downloading_highlights": ("status_downloading_highlights", "accent"),
    }

    _PREPARE_API_STATUSES = frozenset(
        {
            "preparing_posts",
            "preparing_reels",
            "preparing_tagged",
            "preparing_stories",
            "preparing_highlights",
        }
    )

    _TERMINAL_API_STATUSES = frozenset(
        {
            "error",
            "auth_required",
            "rate_limited",
            "iphone_quality_unavailable",
        }
    )

    def resizeEvent(self, event):
        """窗口大小改变时的处理"""
        super().resizeEvent(event)
        self._position_main_preview_visibility_button()

        # 延迟重新计算图片尺寸 - 避免频繁更新
        if hasattr(self, "_resize_timer"):
            self._resize_timer.stop()
        else:
            self._resize_timer = ui_qt.QTimer()
            self._resize_timer.setSingleShot(True)
            self._resize_timer.timeout.connect(self._on_resize_finished)

        self._resize_timer.start(150)  # 150ms后执行

    def _on_resize_finished(self):
        """窗口大小改变完成后的处理"""
        # 重新计算图片尺寸
        self.calculate_image_sizes()

        # 刷新预览
        if hasattr(self, "current_preview_images") and self.current_preview_images:
            self.refresh_preview_images()
