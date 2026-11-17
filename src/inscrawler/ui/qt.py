"""Shared Qt imports, including optional SVG support."""

from __future__ import annotations

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QEvent,
    QModelIndex,
    QObject,
    QParallelAnimationGroup,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QRunnable,
    QSize,
    Qt,
    QThread,
    QThreadPool,
    QTimer,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QDesktopServices,
    QFont,
    QIcon,
    QImage,
    QImageReader,
    QLinearGradient,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionComboBox,
    QStyleOptionGroupBox,
    QStyleOptionViewItem,
    QSystemTrayIcon,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

try:
    from PySide6.QtSvg import QSvgRenderer

    _HAS_SVG = True
except ImportError:
    _HAS_SVG = False
