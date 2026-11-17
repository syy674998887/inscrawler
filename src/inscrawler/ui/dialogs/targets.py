"""Target-account selection and management dialog."""

from __future__ import annotations

import inscrawler.ui.platform as ui_platform
import inscrawler.ui.qt as ui_qt


class PatrolTargetItemDelegate(ui_qt.QStyledItemDelegate):
    """Use the original Qt checkbox while omitting the item focus frame."""

    def check_indicator_rect(self, option, index):
        check_option = ui_qt.QStyleOptionViewItem(option)
        self.initStyleOption(check_option, index)
        style = (
            check_option.widget.style()
            if check_option.widget is not None
            else ui_qt.QApplication.style()
        )
        return style.subElementRect(
            ui_qt.QStyle.SubElement.SE_ItemViewItemCheckIndicator,
            check_option,
            check_option.widget,
        )

    def paint(self, painter, option, index):
        clean_option = ui_qt.QStyleOptionViewItem(option)
        clean_option.state &= ~ui_qt.QStyle.StateFlag.State_HasFocus
        super().paint(painter, clean_option, index)


class PatrolTargetListWidget(ui_qt.QListWidget):
    """Target list with selection on rows and checking on the leading toggle."""

    check_states_changed = ui_qt.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setItemDelegate(PatrolTargetItemDelegate(self))
        self.setSelectionMode(ui_qt.QAbstractItemView.SelectionMode.ExtendedSelection)

    def _set_items_checked(self, items, state):
        if not items:
            return
        for item in items:
            item.setCheckState(state)
        self.check_states_changed.emit()

    def _toggle_items_from(self, source_item, items):
        state = (
            ui_qt.Qt.CheckState.Unchecked
            if source_item.checkState() == ui_qt.Qt.CheckState.Checked
            else ui_qt.Qt.CheckState.Checked
        )
        self._set_items_checked(items, state)

    def _toggle_rect(self, item):
        option = ui_qt.QStyleOptionViewItem()
        self.initViewItemOption(option)
        option.rect = self.visualItemRect(item)
        return self.itemDelegate().check_indicator_rect(
            option,
            self.indexFromItem(item),
        )

    def mousePressEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        selection_modifiers = (
            ui_qt.Qt.KeyboardModifier.ControlModifier
            | ui_qt.Qt.KeyboardModifier.ShiftModifier
        )
        if (
            event.button() != ui_qt.Qt.MouseButton.LeftButton
            or item is None
            or event.modifiers() & selection_modifiers
            or not self._toggle_rect(item).contains(event.position().toPoint())
        ):
            # Username/avatar clicks and Ctrl/Shift clicks only edit the row
            # selection. Check state changes are limited to the leading toggle.
            super().mousePressEvent(event)
            return

        self.setFocus(ui_qt.Qt.FocusReason.MouseFocusReason)
        selected_items = self.selectedItems()
        if item.isSelected() and len(selected_items) > 1:
            toggle_items = selected_items
        else:
            self.clearSelection()
            item.setSelected(True)
            self.setCurrentItem(item)
            toggle_items = [item]
        self._toggle_items_from(item, toggle_items)
        event.accept()

    def keyPressEvent(self, event):
        if event.key() == ui_qt.Qt.Key.Key_Space:
            selected_items = self.selectedItems()
            source_item = self.currentItem()
            if source_item is None and selected_items:
                source_item = selected_items[0]
            if source_item is not None:
                if source_item not in selected_items:
                    selected_items = [source_item]
                self._toggle_items_from(source_item, selected_items)
                event.accept()
                return
        super().keyPressEvent(event)


class PatrolTargetSelectionDialog(ui_qt.QDialog):
    """Modal editor for the target selection used by the next patrol task."""

    def __init__(self, window, targets, selected_targets):
        super().__init__(window)
        self._window = window
        self._targets = list(targets or [])
        selected_keys = {
            str(username or "").strip().lstrip("@").casefold()
            for username in selected_targets or []
        }
        self.setModal(True)
        self.setMinimumSize(460, 520)
        self.resize(500, 680)
        self.setWindowFlags(
            ui_qt.Qt.WindowType.Dialog
            | ui_qt.Qt.WindowType.CustomizeWindowHint
            | ui_qt.Qt.WindowType.WindowTitleHint
            | ui_qt.Qt.WindowType.WindowCloseButtonHint
        )
        ui_platform._suppress_secondary_window_icon(self)

        layout = ui_qt.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self._search = ui_qt.QLineEdit()
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filter)
        layout.addWidget(self._search)

        self._target_list = PatrolTargetListWidget()
        self._target_list.setObjectName("patrolTargetSelectorList")
        self._target_list.setFont(self._window.target.font())
        self._target_list.setIconSize(ui_qt.QSize(28, 28))
        self._target_list.setVerticalScrollMode(
            ui_qt.QAbstractItemView.ScrollMode.ScrollPerPixel
        )
        self._target_list.setHorizontalScrollBarPolicy(
            ui_qt.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._target_list.setUniformItemSizes(True)
        self._target_list.check_states_changed.connect(self._update_selected_count)
        layout.addWidget(self._target_list, 1)

        for username in self._targets:
            item = ui_qt.QListWidgetItem(
                self._window._target_profile_picture_icon(username),
                str(username),
            )
            item.setData(ui_qt.Qt.ItemDataRole.UserRole, str(username))
            item.setFlags(
                (
                    item.flags()
                    | ui_qt.Qt.ItemFlag.ItemIsSelectable
                    | ui_qt.Qt.ItemFlag.ItemIsEnabled
                )
                & ~ui_qt.Qt.ItemFlag.ItemIsUserCheckable
            )
            item.setCheckState(
                ui_qt.Qt.CheckState.Checked
                if str(username).casefold() in selected_keys
                else ui_qt.Qt.CheckState.Unchecked
            )
            item.setSizeHint(ui_qt.QSize(0, self._window.target.POPUP_ITEM_HEIGHT))
            self._target_list.addItem(item)

        footer = ui_qt.QHBoxLayout()
        footer.setSpacing(8)
        self._selected_count = ui_qt.QLabel()
        footer.addWidget(self._selected_count)
        footer.addStretch()
        self._cancel_btn = ui_qt.QPushButton()
        self._cancel_btn.setProperty("variant", "secondary")
        self._cancel_btn.clicked.connect(self.reject)
        footer.addWidget(self._cancel_btn)
        self._apply_btn = ui_qt.QPushButton()
        self._apply_btn.setProperty("variant", "primary")
        self._apply_btn.setDefault(True)
        self._apply_btn.clicked.connect(self.accept)
        footer.addWidget(self._apply_btn)
        layout.addLayout(footer)

        self._refresh_theme()
        self._retranslate()
        self._update_selected_count()
        self._target_list.setFocus(ui_qt.Qt.FocusReason.OtherFocusReason)

    def selected_targets(self):
        targets = []
        for row in range(self._target_list.count()):
            item = self._target_list.item(row)
            if item.checkState() != ui_qt.Qt.CheckState.Checked:
                continue
            username = str(
                item.data(ui_qt.Qt.ItemDataRole.UserRole) or item.text()
            ).strip()
            if username:
                targets.append(username)
        return targets

    def _apply_filter(self, text):
        query = str(text or "").strip().casefold()
        for row in range(self._target_list.count()):
            item = self._target_list.item(row)
            username = str(item.data(ui_qt.Qt.ItemDataRole.UserRole) or item.text())
            item.setHidden(bool(query and query not in username.casefold()))

    def _update_selected_count(self):
        selected = sum(
            1
            for row in range(self._target_list.count())
            if self._target_list.item(row).checkState() == ui_qt.Qt.CheckState.Checked
        )
        self._selected_count.setText(
            self._window.tr(
                "patrol_selected_count",
                selected=selected,
                total=self._target_list.count(),
            )
        )

    def _retranslate(self):
        self.setWindowTitle(self._window.tr("patrol_target_selector_title"))
        self._search.setPlaceholderText(
            self._window.tr("patrol_target_search_placeholder")
        )
        self._cancel_btn.setText(self._window.tr("cancel"))
        self._apply_btn.setText(self._window.tr("patrol_apply_selection"))

    def _refresh_theme(self):
        colors = self._window._theme_tokens()
        scrollbar_ss = self._window._scrollbar_stylesheet()
        self.setStyleSheet(f"QDialog {{ background-color: {colors['surface']}; }}")
        self._selected_count.setStyleSheet(
            f"color: {colors['muted']}; font-size: 12px;"
        )
        self._target_list.setStyleSheet(
            f"QListWidget#patrolTargetSelectorList {{ background-color: {colors['base']}; "
            f"color: {colors['text']}; border: 1px solid {colors['border']}; "
            f"border-radius: 5px; padding: 2px; }} "
            f"QListWidget#patrolTargetSelectorList::item {{ padding: 3px 5px; }} "
            + scrollbar_ss
        )
