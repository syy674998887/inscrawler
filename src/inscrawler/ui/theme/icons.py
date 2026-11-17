"""SVG icon definitions, theme-aware rendering, and pixmap caching."""

from __future__ import annotations

import inscrawler.ui.qt as ui_qt

DEFAULT_ICON_SIZE = 18

_QUEUE_SVG = {
    "pending": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M12 4V14" fill="none" stroke="#7f8c8d" stroke-width="2.4" stroke-linecap="round"/><polyline points="8,10 12,14 16,10" fill="none" stroke="#7f8c8d" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/><path d="M5 19H19" fill="none" stroke="#7f8c8d" stroke-width="2.4" stroke-linecap="round"/></svg>',
    "running": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M12 3C7.03 3 3 7.03 3 12s4.03 9 9 9 9-4.03 9-9" fill="none" stroke="#3498db" stroke-width="2.5" stroke-linecap="round"/><polygon points="21,12 17,8 17,16" fill="#3498db"/></svg>',
    "paused": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9" fill="none" stroke="#3498db" stroke-width="2"/><rect x="8" y="7" width="2.7" height="10" rx="1" fill="#3498db"/><rect x="13.3" y="7" width="2.7" height="10" rx="1" fill="#3498db"/></svg>',
    "switching": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M5 8h11" fill="none" stroke="#f39c12" stroke-width="2.3" stroke-linecap="round"/><polyline points="13,5 16,8 13,11" fill="none" stroke="#f39c12" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round"/><path d="M19 16H8" fill="none" stroke="#f39c12" stroke-width="2.3" stroke-linecap="round"/><polyline points="11,13 8,16 11,19" fill="none" stroke="#f39c12" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    "completed": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><polyline points="4,12 9.5,17.5 20,7" fill="none" stroke="#27ae60" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    "deferred": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M12 4V12L16 14" fill="none" stroke="#f39c12" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/><circle cx="12" cy="12" r="9" fill="none" stroke="#f39c12" stroke-width="2"/></svg>',
}


_QUEUE_DELETE_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><line x1="6" y1="6" x2="18" y2="18" stroke="#e74c3c" stroke-width="2.5" stroke-linecap="round"/><line x1="18" y1="6" x2="6" y2="18" stroke="#e74c3c" stroke-width="2.5" stroke-linecap="round"/></svg>'


_PREVIEW_VISIBILITY_SVG = {
    "visible": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M2.5 12C4.8 8.2 8 6.2 12 6.2s7.2 2 9.5 5.8c-2.3 3.8-5.5 5.8-9.5 5.8S4.8 15.8 2.5 12Z" fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/><circle cx="12" cy="12" r="2.8" fill="none" stroke="{color}" stroke-width="1.8"/></svg>',
    "hidden": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M2.5 12C4.8 8.2 8 6.2 12 6.2s7.2 2 9.5 5.8c-2.3 3.8-5.5 5.8-9.5 5.8S4.8 15.8 2.5 12Z" fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/><circle cx="12" cy="12" r="2.8" fill="none" stroke="{color}" stroke-width="1.8"/><line x1="4" y1="4" x2="20" y2="20" stroke="{color}" stroke-width="2" stroke-linecap="round"/></svg>',
}


_svg_icon_cache = {}


_svg_pixmap_cache = {}


_COMBO_ARROW_SVG = {
    "closed": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><polyline points="4,6 8,2.5 12,6" fill="none" stroke="{color}" stroke-opacity="0.45" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/><polyline points="4,10 8,13.5 12,10" fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    "open": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><polyline points="4,6 8,2.5 12,6" fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/><polyline points="4,10 8,13.5 12,10" fill="none" stroke="{color}" stroke-opacity="0.45" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
}


_TITLEBAR_SVG = {
    "settings": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><circle cx="12" cy="12" r="3.1" fill="none" stroke="{color}" stroke-width="1.8"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.01A1.65 1.65 0 0 0 10.09 3H10a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h.01a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v.01a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" fill="none" stroke="{color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    "minimize": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><line x1="5" y1="12" x2="19" y2="12" stroke="{color}" stroke-width="2" stroke-linecap="round"/></svg>',
    "maximize": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><rect x="6.5" y="6.5" width="11" height="11" rx="1.5" fill="none" stroke="{color}" stroke-width="1.8"/></svg>',
    "restore": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><rect x="8" y="6.5" width="9.5" height="9.5" rx="1.2" fill="none" stroke="{color}" stroke-width="1.8"/><path d="M6.5 9V17.5H15" fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    "close": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><line x1="7" y1="7" x2="17" y2="17" stroke="{color}" stroke-width="2" stroke-linecap="round"/><line x1="17" y1="7" x2="7" y2="17" stroke="{color}" stroke-width="2" stroke-linecap="round"/></svg>',
    "help": '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M8 9 c0 -3 8 -3 8 0 c0 2 -4 3.5 -4 6" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><circle cx="12" cy="19" r="1.4" fill="{color}"/></svg>',
}


def _make_svg_icon(svg_str, size=DEFAULT_ICON_SIZE):
    """从SVG字符串创建QIcon（带缓存）"""
    if not ui_qt._HAS_SVG or not svg_str:
        return None
    key = (svg_str, size)
    cached = _svg_icon_cache.get(key)
    if cached is not None:
        return cached
    renderer = ui_qt.QSvgRenderer(svg_str.encode("utf-8"))
    pixmap = ui_qt.QPixmap(size, size)
    pixmap.fill(ui_qt.Qt.GlobalColor.transparent)
    painter = ui_qt.QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    icon = ui_qt.QIcon(pixmap)
    _svg_icon_cache[key] = icon
    return icon


def _make_svg_pixmap(svg_str, size):
    if not ui_qt._HAS_SVG or not svg_str:
        return ui_qt.QPixmap()
    key = (svg_str, size.width(), size.height())
    cached = _svg_pixmap_cache.get(key)
    if cached is not None:
        return cached
    renderer = ui_qt.QSvgRenderer(svg_str.encode("utf-8"))
    pixmap = ui_qt.QPixmap(size)
    pixmap.fill(ui_qt.Qt.GlobalColor.transparent)
    painter = ui_qt.QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    _svg_pixmap_cache[key] = pixmap
    return pixmap
