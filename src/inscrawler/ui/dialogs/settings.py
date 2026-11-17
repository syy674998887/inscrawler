"""Immediate-apply application settings dialog."""

from __future__ import annotations

import inscrawler.ui.constants as ui_constants
import inscrawler.ui.i18n.options as ui_i18n_options
import inscrawler.ui.i18n.text as ui_i18n_text
import inscrawler.ui.platform as ui_platform
import inscrawler.ui.qt as ui_qt
import inscrawler.ui.widgets.combo as ui_widgets_combo


class SettingsDialog(ui_qt.QDialog):
    def __init__(self, window):
        super().__init__(window)
        self._window = window
        self._syncing = False
        self.setModal(True)
        self.setMinimumWidth(360)
        self.setWindowFlags(
            ui_qt.Qt.WindowType.Dialog
            | ui_qt.Qt.WindowType.CustomizeWindowHint
            | ui_qt.Qt.WindowType.WindowTitleHint
            | ui_qt.Qt.WindowType.WindowCloseButtonHint
            | ui_qt.Qt.WindowType.MSWindowsFixedSizeDialogHint
        )
        ui_platform._suppress_secondary_window_icon(self)

        layout = ui_qt.QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        form_layout = ui_qt.QGridLayout()
        form_layout.setHorizontalSpacing(12)
        form_layout.setVerticalSpacing(10)
        layout.addLayout(form_layout)

        self.theme_label = ui_qt.QLabel()
        self.theme_combo = ui_widgets_combo.AlignedComboBox()
        self.theme_combo.setSizePolicy(
            ui_qt.QSizePolicy.Policy.Expanding, ui_qt.QSizePolicy.Policy.Fixed
        )
        self._window._configure_combo_box(self.theme_combo)
        form_layout.addWidget(self.theme_label, 0, 0)
        form_layout.addWidget(self.theme_combo, 0, 1)

        self.language_label = ui_qt.QLabel()
        self.language_combo = ui_widgets_combo.AlignedComboBox()
        self.language_combo.setSizePolicy(
            ui_qt.QSizePolicy.Policy.Expanding, ui_qt.QSizePolicy.Policy.Fixed
        )
        self._window._configure_combo_box(self.language_combo)
        form_layout.addWidget(self.language_label, 1, 0)
        form_layout.addWidget(self.language_combo, 1, 1)

        self.close_behavior_label = ui_qt.QLabel()
        self.close_behavior_combo = ui_widgets_combo.AlignedComboBox()
        self.close_behavior_combo.setSizePolicy(
            ui_qt.QSizePolicy.Policy.Expanding, ui_qt.QSizePolicy.Policy.Fixed
        )
        self._window._configure_combo_box(self.close_behavior_combo)
        form_layout.addWidget(self.close_behavior_label, 2, 0)
        form_layout.addWidget(self.close_behavior_combo, 2, 1)

        self.iphone_support_label = ui_qt.QLabel()
        self.iphone_support_combo = ui_widgets_combo.AlignedComboBox()
        self.iphone_support_combo.setSizePolicy(
            ui_qt.QSizePolicy.Policy.Expanding, ui_qt.QSizePolicy.Policy.Fixed
        )
        self._window._configure_combo_box(self.iphone_support_combo)
        form_layout.addWidget(self.iphone_support_label, 3, 0)
        form_layout.addWidget(self.iphone_support_combo, 3, 1)

        self.profile_picture_sync_label = ui_qt.QLabel()
        self.profile_picture_sync_check = ui_qt.QCheckBox()
        form_layout.addWidget(self.profile_picture_sync_label, 4, 0)
        form_layout.addWidget(self.profile_picture_sync_check, 4, 1)

        self.preview_count_label = ui_qt.QLabel()
        self.preview_count_spin = ui_qt.QSpinBox()
        self.preview_count_spin.setRange(15, 99)
        self.preview_count_spin.setValue(self._window.max_preview_images)
        self.preview_count_spin.setSizePolicy(
            ui_qt.QSizePolicy.Policy.Expanding, ui_qt.QSizePolicy.Policy.Fixed
        )
        form_layout.addWidget(self.preview_count_label, 5, 0)
        form_layout.addWidget(self.preview_count_spin, 5, 1)

        self.debug_logging_label = ui_qt.QLabel()
        self.debug_logging_check = ui_qt.QCheckBox()
        form_layout.addWidget(self.debug_logging_label, 6, 0)
        form_layout.addWidget(self.debug_logging_check, 6, 1)

        self.video_preview_cache_label = ui_qt.QLabel()
        cache_control = ui_qt.QWidget()
        cache_layout = ui_qt.QHBoxLayout(cache_control)
        cache_layout.setContentsMargins(0, 0, 0, 0)
        cache_layout.setSpacing(8)
        self.video_preview_cache_slider = ui_qt.QSlider(ui_qt.Qt.Orientation.Horizontal)
        self.video_preview_cache_slider.setRange(
            0,
            len(ui_constants.VIDEO_PREVIEW_CACHE_MB_OPTIONS) - 1,
        )
        self.video_preview_cache_slider.setSingleStep(1)
        self.video_preview_cache_slider.setPageStep(1)
        self.video_preview_cache_slider.setTracking(False)
        self.video_preview_cache_slider.setMinimumWidth(160)
        self.video_preview_cache_slider.setSizePolicy(
            ui_qt.QSizePolicy.Policy.Expanding,
            ui_qt.QSizePolicy.Policy.Fixed,
        )
        self.video_preview_cache_value_label = ui_qt.QLabel()
        self.video_preview_cache_value_label.setMinimumWidth(58)
        self.video_preview_cache_value_label.setAlignment(
            ui_qt.Qt.AlignmentFlag.AlignRight | ui_qt.Qt.AlignmentFlag.AlignVCenter
        )
        cache_layout.addWidget(self.video_preview_cache_slider, 1)
        cache_layout.addWidget(self.video_preview_cache_value_label)
        form_layout.addWidget(self.video_preview_cache_label, 7, 0)
        form_layout.addWidget(cache_control, 7, 1)

        self.detail_video_cache_usage_label = ui_qt.QLabel()
        form_layout.addWidget(self.detail_video_cache_usage_label, 8, 0)
        self.clear_detail_video_cache_button = ui_qt.QPushButton()
        self.clear_detail_video_cache_button.setSizePolicy(
            ui_qt.QSizePolicy.Policy.Fixed,
            ui_qt.QSizePolicy.Policy.Fixed,
        )
        form_layout.addWidget(
            self.clear_detail_video_cache_button,
            8,
            1,
            ui_qt.Qt.AlignmentFlag.AlignRight,
        )

        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        self.language_combo.currentIndexChanged.connect(self._on_language_changed)
        self.close_behavior_combo.currentIndexChanged.connect(
            self._on_close_behavior_changed
        )
        self.iphone_support_combo.currentIndexChanged.connect(
            self._on_iphone_support_changed
        )
        self.profile_picture_sync_check.stateChanged.connect(
            self._on_profile_picture_sync_changed
        )
        self.preview_count_spin.valueChanged.connect(self._on_preview_count_changed)
        self.debug_logging_check.stateChanged.connect(self._on_debug_logging_changed)
        self.video_preview_cache_slider.sliderMoved.connect(
            self._on_video_preview_cache_slider_moved
        )
        self.video_preview_cache_slider.valueChanged.connect(
            self._on_video_preview_cache_changed
        )
        self.clear_detail_video_cache_button.clicked.connect(
            self._on_clear_detail_video_cache
        )
        self._populate_options()
        self.retranslate_ui()

    def _populate_options(self):
        self._syncing = True
        current_theme = self._window.ui_theme
        current_language = self._window.ui_language

        self.theme_combo.clear()
        for theme_key, labels in ui_i18n_options.THEME_OPTIONS:
            self.theme_combo.addItem(
                labels.get(self._window.ui_language, labels["en"]), theme_key
            )
        theme_index = self.theme_combo.findData(current_theme)
        if theme_index >= 0:
            self.theme_combo.setCurrentIndex(theme_index)

        self.language_combo.clear()
        for language_key, label in ui_i18n_options.LANGUAGE_OPTIONS:
            self.language_combo.addItem(label, language_key)
        language_index = self.language_combo.findData(current_language)
        if language_index >= 0:
            self.language_combo.setCurrentIndex(language_index)

        self.close_behavior_combo.clear()
        for behavior_key, labels in ui_i18n_options.CLOSE_BEHAVIOR_OPTIONS:
            self.close_behavior_combo.addItem(
                labels.get(self._window.ui_language, labels["en"]), behavior_key
            )
        behavior_index = self.close_behavior_combo.findData(self._window.close_behavior)
        if behavior_index >= 0:
            self.close_behavior_combo.setCurrentIndex(behavior_index)

        self.iphone_support_combo.clear()
        self.iphone_support_combo.addItem(self._window.tr("iphone_support_off"), False)
        self.iphone_support_combo.addItem(self._window.tr("iphone_support_on"), True)
        self._sync_iphone_support_combo_to_window()
        self.iphone_support_combo.setEnabled(not self._window.is_downloading())

        self.profile_picture_sync_check.blockSignals(True)
        self.profile_picture_sync_check.setChecked(
            bool(self._window.profile_picture_sync_enabled)
        )
        self.profile_picture_sync_check.blockSignals(False)

        self.preview_count_spin.blockSignals(True)
        self.preview_count_spin.setValue(self._window.max_preview_images)
        self.preview_count_spin.blockSignals(False)

        self.debug_logging_check.setChecked(bool(self._window.debug_logging_enabled))

        cache_position = self._video_preview_cache_slider_position(
            self._window.video_preview_cache_mb
        )
        self.video_preview_cache_slider.blockSignals(True)
        self.video_preview_cache_slider.setValue(cache_position)
        self.video_preview_cache_slider.blockSignals(False)
        self._update_video_preview_cache_value_label(cache_position)

        self._syncing = False

    def _on_theme_changed(self):
        if self._syncing:
            return
        selected_theme = self.theme_combo.currentData()
        self._window.apply_ui_preferences(selected_theme, None, persist=True)

    def _on_language_changed(self):
        if self._syncing:
            return
        selected_language = self.language_combo.currentData()
        self._window.apply_ui_preferences(None, selected_language, persist=True)
        self._populate_options()
        self.retranslate_ui()

    def _on_close_behavior_changed(self):
        if self._syncing:
            return
        selected_behavior = self.close_behavior_combo.currentData()
        self._window.set_close_behavior(selected_behavior, persist=True)

    def _on_iphone_support_changed(self):
        if self._syncing:
            return
        if self._window.is_downloading():
            self._sync_iphone_support_combo_to_window()
            return
        selected = bool(self.iphone_support_combo.currentData())
        self._window.set_iphone_support(selected, persist=True)

    def _on_profile_picture_sync_changed(self, _state):
        if self._syncing:
            return
        self._window.set_profile_picture_sync_enabled(
            self.profile_picture_sync_check.isChecked(),
            persist=True,
        )

    def _on_preview_count_changed(self, value):
        if self._syncing:
            return
        self._window.set_max_preview_images(value, persist=True)

    def _on_debug_logging_changed(self, _state):
        if self._syncing:
            return
        self._window.set_debug_logging_enabled(
            self.debug_logging_check.isChecked(),
            persist=True,
        )

    @staticmethod
    def _video_preview_cache_slider_position(value_mb):
        try:
            value_mb = int(value_mb)
        except (TypeError, ValueError):
            value_mb = ui_constants.DEFAULT_VIDEO_PREVIEW_CACHE_MB
        return min(
            range(len(ui_constants.VIDEO_PREVIEW_CACHE_MB_OPTIONS)),
            key=lambda index: (
                abs(ui_constants.VIDEO_PREVIEW_CACHE_MB_OPTIONS[index] - value_mb),
                index,
            ),
        )

    @staticmethod
    def _video_preview_cache_mb_at(position):
        position = max(
            0, min(len(ui_constants.VIDEO_PREVIEW_CACHE_MB_OPTIONS) - 1, int(position))
        )
        return ui_constants.VIDEO_PREVIEW_CACHE_MB_OPTIONS[position]

    def _update_video_preview_cache_value_label(self, position):
        value_mb = self._video_preview_cache_mb_at(position)
        self.video_preview_cache_value_label.setText(f"{value_mb} MB")

    def _update_detail_video_cache_usage_label(self):
        try:
            byte_count = max(
                0,
                int(self._window.detail_video_thumbnail_cache_bytes()),
            )
        except (AttributeError, TypeError, ValueError):
            byte_count = 0
        size_mb = byte_count / (1024 * 1024)
        self.detail_video_cache_usage_label.setText(
            self._window.tr(
                "detail_video_cache_usage",
                size=f"{size_mb:.2f}",
            )
        )

    def _on_video_preview_cache_slider_moved(self, position):
        self._update_video_preview_cache_value_label(position)

    def _on_video_preview_cache_changed(self, position):
        self._update_video_preview_cache_value_label(position)
        if self._syncing:
            return
        self._window.set_video_preview_cache_mb(
            self._video_preview_cache_mb_at(position),
            persist=True,
        )
        self._update_detail_video_cache_usage_label()

    def _on_clear_detail_video_cache(self):
        self._window.clear_detail_video_thumbnail_cache()
        self._update_detail_video_cache_usage_label()

    def _sync_iphone_support_combo_to_window(self):
        index = self.iphone_support_combo.findData(bool(self._window.iphone_support))
        if index < 0:
            index = self.iphone_support_combo.findData(False)
        if index >= 0:
            self.iphone_support_combo.blockSignals(True)
            self.iphone_support_combo.setCurrentIndex(index)
            self.iphone_support_combo.blockSignals(False)

    def retranslate_ui(self):
        self.setWindowTitle(self._window.tr("settings_title"))
        self.theme_label.setText(self._window.tr("theme_label"))
        # Language label intentionally shows the OPPOSITE language so users who
        # don't read the current UI language can still locate the switcher.
        other_lang = "en" if self._window.ui_language == "zh" else "zh"
        self.language_label.setText(ui_i18n_text.UI_TEXT[other_lang]["language_label"])
        self.close_behavior_label.setText(self._window.tr("close_behavior_label"))
        self.iphone_support_label.setText(self._window.tr("iphone_support_label"))
        self.profile_picture_sync_label.setText(
            self._window.tr("profile_picture_sync_label")
        )
        self.profile_picture_sync_check.setText(
            self._window.tr("profile_picture_sync_on")
        )
        profile_picture_tooltip = self._window.tr("profile_picture_sync_tooltip")
        self.profile_picture_sync_label.setToolTip(profile_picture_tooltip)
        self.profile_picture_sync_check.setToolTip(profile_picture_tooltip)
        self.profile_picture_sync_check.setAccessibleName(
            self._window.tr("profile_picture_sync_label").rstrip(":：")
        )
        self.profile_picture_sync_check.setAccessibleDescription(
            profile_picture_tooltip
        )
        self.preview_count_label.setText(self._window.tr("preview_count_label"))
        self.debug_logging_label.setText(self._window.tr("debug_logging_label"))
        self.debug_logging_check.setText(self._window.tr("debug_logging_on"))
        self.video_preview_cache_label.setText(
            self._window.tr("video_preview_cache_label")
        )
        cache_tooltip = self._window.tr("video_preview_cache_tooltip")
        self.video_preview_cache_label.setToolTip(cache_tooltip)
        self.video_preview_cache_slider.setToolTip(cache_tooltip)
        self.video_preview_cache_value_label.setToolTip(cache_tooltip)
        self.clear_detail_video_cache_button.setText(
            self._window.tr("clear_detail_video_cache")
        )
        self.clear_detail_video_cache_button.setToolTip(
            self._window.tr("clear_detail_video_cache_tooltip")
        )
        self.detail_video_cache_usage_label.setToolTip(
            self._window.tr("clear_detail_video_cache_tooltip")
        )
        self._update_detail_video_cache_usage_label()
        self.video_preview_cache_slider.setAccessibleName(
            self._window.tr("video_preview_cache_label").rstrip(":：")
        )
        self.iphone_support_combo.setEnabled(not self._window.is_downloading())
        # Refresh combo item labels for the new language without re-triggering save
        self._syncing = True
        for idx in range(self.iphone_support_combo.count()):
            data = self.iphone_support_combo.itemData(idx)
            label_key = "iphone_support_on" if bool(data) else "iphone_support_off"
            self.iphone_support_combo.setItemText(idx, self._window.tr(label_key))
        self._syncing = False
