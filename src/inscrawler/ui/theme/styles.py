"""Pure Qt stylesheet builders; no QApplication or widgets are required."""

import inscrawler.ui.constants as ui_constants


def section_title_background(colors):
    return (
        "qlineargradient("
        "x1: 0, y1: 0, x2: 0, y2: 1, "
        f"stop: 0 {colors['window']}, "
        f"stop: 0.5 {colors['window']}, "
        f"stop: 0.5 {colors['surface']}, "
        f"stop: 1 {colors['surface']}"
        ")"
    )


def application_stylesheet(colors: dict[str, str]) -> str:
    title_background = section_title_background(colors)
    return f"""
        QMainWindow, QWidget#centralWidget {{
            background-color: {colors["window"]};
        }}
        QWidget#customTitleBar {{
            background-color: {colors["surface"]};
            border: 1px solid {colors["border"]};
            border-bottom: none;
            border-top-left-radius: 12px;
            border-top-right-radius: 12px;
        }}
        QLabel#titleBarTitle {{
            color: {colors["titlebar_text"]};
            font-size: 13px;
            font-weight: 600;
        }}
        QToolButton[titlebarButton="true"] {{
            border: none;
            border-radius: 0px;
            padding: 0;
            background: transparent;
        }}
        QToolButton[titlebarButton="true"]:hover {{
            background-color: {colors["alternate"]};
        }}
        QToolButton[titlebarButton="true"]:pressed {{
            background-color: {colors["button"]};
        }}
        QToolButton[titlebarRole="close"]:hover {{
            background-color: {colors["danger"]};
        }}
        QWidget {{
            color: {colors["text"]};
        }}
        QLabel {{
            color: {colors["text"]};
            background: transparent;
        }}
        QGroupBox {{
            background-color: {colors["surface"]};
            border: 1px solid {colors["border"]};
            border-radius: 14px;
            margin-top: 10px;
            padding: {ui_constants.HOME_SECTION_CONTENT_PADDING}px;
            font-weight: 600;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 12px;
            top: -1px;
            padding: 0 3px;
            color: {colors["muted"]};
            background-color: {title_background};
        }}
        QWidget#threadPipelineRoot {{
            background: transparent;
        }}
        QFrame#threadPipelineNode {{
            background-color: {colors["base"]};
            border: 1px solid {colors["border"]};
            border-radius: 10px;
        }}
        QFrame#threadPipelineNode[tone="active"],
        QFrame#threadPipelineNode[tone="download"] {{
            background-color: {colors["alternate"]};
            border-color: {colors["accent"]};
        }}
        QFrame#threadPipelineNode[tone="warning"] {{
            background-color: {colors["alternate"]};
            border-color: {colors["warning"]};
        }}
        QFrame#threadPipelineNode[tone="danger"] {{
            background-color: {colors["alternate"]};
            border-color: {colors["danger"]};
        }}
        QFrame#threadPipelineNode QLabel {{
            background: transparent;
            border: none;
        }}
        QLabel[pipelineRole="title"] {{
            color: {colors["muted"]};
            font-size: 10px;
            font-weight: 600;
        }}
        QLabel[pipelineRole="status"] {{
            color: {colors["text"]};
            font-size: 12px;
            font-weight: 600;
        }}
        QLabel[pipelineRole="detail"] {{
            color: {colors["muted"]};
            font-size: 10px;
        }}
        QLineEdit, QComboBox, QSpinBox, QListWidget {{
            background-color: {colors["base"]};
            color: {colors["text"]};
            border: 1px solid {colors["border"]};
            border-radius: 10px;
            padding: 8px 10px;
            selection-background-color: {colors["highlight"]};
            selection-color: {colors["highlight_text"]};
        }}
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
            border: 1px solid {colors["accent"]};
        }}
        QComboBox {{
            combobox-popup: 0;
            padding: 8px 34px 8px 10px;
            border-radius: 0px;
        }}
        QComboBox:on {{
            border: 1px solid {colors["accent"]};
            border-radius: 0px;
            background-color: {colors["base"]};
        }}
        QComboBox QLineEdit {{
            border: none;
            border-radius: 0px;
            background: transparent;
            padding: 0;
        }}
        QComboBox::drop-down {{
            subcontrol-origin: padding;
            subcontrol-position: top right;
            border: none;
            width: 30px;
            background: transparent;
            border-top-right-radius: 0px;
            border-bottom-right-radius: 0px;
        }}
        QComboBox::down-arrow {{
            image: none;
            width: 0px;
            height: 0px;
        }}
        QComboBox::drop-down:on {{
            background: transparent;
            border-top-right-radius: 0px;
            border-bottom-right-radius: 0px;
        }}
        QComboBoxPrivateContainer, QFrame#comboPopupContainer {{
            background-color: {colors["base"]};
            border: 1px solid {colors["accent"]};
            border-radius: 0px;
            padding: 0px;
        }}
        QComboBoxPrivateContainer QListView,
        QComboBoxPrivateContainer QAbstractItemView,
        QFrame#comboPopupContainer QListView#comboPopupView {{
            background-color: {colors["base"]};
            color: {colors["text"]};
            border: none;
            border-radius: 0px;
            padding: 0;
            outline: 0;
            selection-background-color: {colors["highlight"]};
            selection-color: {colors["highlight_text"]};
        }}
        QComboBoxPrivateContainer QListView::item,
        QComboBoxPrivateContainer QAbstractItemView::item,
        QFrame#comboPopupContainer QListView#comboPopupView::item {{
            min-height: 32px;
            margin: 0;
            padding: 6px 10px;
            border-radius: 0px;
            border: none;
            background-color: {colors["base"]};
        }}
        QComboBoxPrivateContainer QListView::item:hover,
        QComboBoxPrivateContainer QListView::item:selected,
        QComboBoxPrivateContainer QAbstractItemView::item:hover,
        QComboBoxPrivateContainer QAbstractItemView::item:selected,
        QFrame#comboPopupContainer QListView#comboPopupView::item:hover,
        QFrame#comboPopupContainer QListView#comboPopupView::item:selected {{
            background-color: {colors["highlight"]};
            color: {colors["highlight_text"]};
        }}
        QSpinBox::up-button, QSpinBox::down-button {{
            width: 20px;
            border: none;
            background: transparent;
        }}
        QPushButton {{
            background-color: {colors["button"]};
            color: {colors["text"]};
            border: 1px solid {colors["border"]};
            border-radius: 10px;
            padding: 10px 18px;
            font-size: 13px;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background-color: {colors["surface"]};
            border-color: {colors["accent"]};
        }}
        QPushButton:pressed {{
            background-color: {colors["alternate"]};
        }}
        QPushButton:disabled {{
            background-color: {colors["alternate"]};
            color: {colors["muted"]};
            border-color: {colors["border"]};
        }}
        QPushButton[variant="primary"] {{
            background-color: {colors["accent"]};
            border-color: {colors["accent"]};
            color: #ffffff;
        }}
        QPushButton[variant="primary"]:hover {{
            background-color: {colors["accent_hover"]};
            border-color: {colors["accent_hover"]};
        }}
        QPushButton[variant="primary"]:pressed {{
            background-color: {colors["accent_pressed"]};
            border-color: {colors["accent_pressed"]};
        }}
        QPushButton[variant="busy"],
        QPushButton[variant="busy"]:disabled {{
            background-color: {colors["alternate"]};
            border: 1px solid {colors["border"]};
            color: {colors["muted"]};
        }}
        QPushButton[variant="busy"]:hover {{
            background-color: {colors["button"]};
            border-color: {colors["accent"]};
            color: {colors["text"]};
        }}
        QPushButton[variant="busy"]:pressed {{
            background-color: {colors["surface"]};
            border-color: {colors["accent"]};
            color: {colors["text"]};
        }}
        QPushButton[variant="danger"] {{
            background-color: {colors["danger"]};
            border-color: {colors["danger"]};
            color: #ffffff;
        }}
        QPushButton[variant="danger"]:hover {{
            background-color: {colors["danger"]};
            border-color: {colors["danger"]};
            opacity: 0.85;
        }}
        QPushButton[variant="danger"]:pressed {{
            background-color: {colors["danger"]};
            border-color: {colors["danger"]};
        }}
        QToolButton {{
            border: none;
            border-radius: 8px;
            padding: 4px;
            background: transparent;
        }}
        QToolButton:hover {{
            background-color: {colors["alternate"]};
        }}
        QToolButton#previewVisibilityButton {{
            border: none;
            border-radius: 0px;
            padding: 3px;
            background-color: {title_background};
        }}
        QToolButton#previewVisibilityButton:hover {{
            border-radius: 6px;
            background-color: {colors["alternate"]};
        }}
        QToolButton#previewVisibilityButton:pressed {{
            border-radius: 6px;
            background-color: {colors["button"]};
        }}
        QToolButton#previewVisibilityButton:focus {{
            border: 1px solid {colors["accent"]};
            padding: 2px;
        }}
        QScrollArea {{
            border: none;
            background: transparent;
        }}
    """


def scrollbar_stylesheet(colors: dict[str, str]) -> str:
    track_color = colors["scroll_track"]
    handle_color = colors["scroll_handle"]
    handle_hover = colors["scroll_hover"]
    handle_pressed = colors["scroll_pressed"]
    return f"""
        QScrollBar:vertical {{
            background: {track_color};
            width: 12px;
            margin: 0px;
            border: none;
        }}
        QScrollBar::handle:vertical {{
            background: {handle_color};
            min-height: 28px;
            margin: 2px;
            border-radius: 4px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {handle_hover};
        }}
        QScrollBar::handle:vertical:pressed {{
            background: {handle_pressed};
        }}
        QScrollBar::add-line:vertical,
        QScrollBar::sub-line:vertical {{
            height: 0px;
            background: transparent;
            border: none;
        }}
        QScrollBar::add-page:vertical,
        QScrollBar::sub-page:vertical {{
            background: {track_color};
        }}
        QScrollBar:horizontal {{
            background: {track_color};
            height: 12px;
            margin: 0px;
            border: none;
        }}
        QScrollBar::handle:horizontal {{
            background: {handle_color};
            min-width: 28px;
            margin: 2px;
            border-radius: 4px;
        }}
        QScrollBar::handle:horizontal:hover {{
            background: {handle_hover};
        }}
        QScrollBar::handle:horizontal:pressed {{
            background: {handle_pressed};
        }}
        QScrollBar::add-line:horizontal,
        QScrollBar::sub-line:horizontal {{
            width: 0px;
            background: transparent;
            border: none;
        }}
        QScrollBar::add-page:horizontal,
        QScrollBar::sub-page:horizontal {{
            background: {track_color};
        }}
    """
