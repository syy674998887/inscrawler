"""Fixed download topology and supported task-state visualization."""

from __future__ import annotations

import math
import os

import inscrawler.ui.qt as ui_qt

_PIPELINE_ANIM_INTERVAL_MS = 40


_PIPELINE_ANIM_CYCLE_MS = 88000  # 流动/呼吸/慢闪周期的公倍数,循环无缝衔接


class _PipelineFlowRail(ui_qt.QWidget):
    """节点之间的箭头轨道:数据流动时 chevron 依次点亮成波。"""

    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self._owner = owner
        self._state = "idle"
        self.setMinimumSize(24, 18)
        self.setMaximumSize(46, 18)
        self.setSizePolicy(
            ui_qt.QSizePolicy.Policy.Expanding,
            ui_qt.QSizePolicy.Policy.Fixed,
        )

    def sizeHint(self):
        return ui_qt.QSize(34, 18)

    def set_state(self, state):
        if state != self._state:
            self._state = state
            self.update()

    def paintEvent(self, event):
        extent = float(self.width())
        chevron_w, gap, arm = 7.0, 4.0, 4.2
        count = 3 if extent >= chevron_w * 3 + gap * 2 + 2 else 2
        color, alphas = self._owner.rail_visuals(self._state, count)
        painter = ui_qt.QPainter(self)
        painter.setRenderHint(ui_qt.QPainter.RenderHint.Antialiasing)
        total = chevron_w * count + gap * (count - 1)
        offset = (extent - total) / 2.0
        for alpha in alphas:
            if alpha > 0.02:
                c = ui_qt.QColor(color)
                c.setAlphaF(min(1.0, alpha))
                pen = ui_qt.QPen(c, 2.0)
                pen.setCapStyle(ui_qt.Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(ui_qt.Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                cy = self.height() / 2.0
                start_x = offset
                tip_x = offset + chevron_w - 1.5
                painter.drawLine(
                    ui_qt.QPointF(start_x, cy - arm), ui_qt.QPointF(tip_x, cy)
                )
                painter.drawLine(
                    ui_qt.QPointF(tip_x, cy), ui_qt.QPointF(start_x, cy + arm)
                )
            offset += chevron_w + gap


class _PipelineNodeFrame(ui_qt.QFrame):
    """QSS 负责底色和边框;活跃时在其上叠一圈呼吸光晕。"""

    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self._owner = owner

    def paintEvent(self, event):
        super().paintEvent(event)
        glow = self._owner.node_glow(self.property("tone"))
        if glow is None:
            return
        color, strength = glow
        if strength <= 0.02:
            return
        painter = ui_qt.QPainter(self)
        painter.setRenderHint(ui_qt.QPainter.RenderHint.Antialiasing)
        painter.setBrush(ui_qt.Qt.BrushStyle.NoBrush)
        rect = ui_qt.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        for stroke_width, alpha in ((4.6, 0.10), (2.8, 0.18), (1.4, 0.50)):
            c = ui_qt.QColor(color)
            c.setAlphaF(min(1.0, alpha * strength))
            painter.setPen(ui_qt.QPen(c, stroke_width))
            inset = stroke_width / 2.0 + 0.5
            painter.drawRoundedRect(
                rect.adjusted(inset, inset, -inset, -inset), 8.0, 8.0
            )


def _pipeline_api_display(api_status, api_status_meta, translate):
    text_key, color_key = api_status_meta.get(api_status, ("status_idle", "muted"))
    tone = {
        "accent": "active",
        "warning": "warning",
        "danger": "danger",
    }.get(color_key, "idle")
    return translate(text_key), tone, color_key


def _thread_pipeline_accessible_value(primary, detail):
    """Omit empty detail parentheses from screen-reader descriptions."""
    primary = str(primary or "")
    detail = str(detail or "").strip()
    return f"{primary} ({detail})" if detail else primary


def _thread_pipeline_wrappable_filename(text):
    """Add invisible wrap opportunities without changing the visible filename."""
    return "".join(
        f"{character}\u200b" if character in "-_." else character
        for character in str(text or "")
    )


def _build_thread_pipeline_state(
    *,
    running,
    paused,
    stopping,
    phase,
    api_status,
    download_status,
    translate,
    api_status_meta,
    download_status_meta,
    current_file=None,
):
    """Map serial download phases and task controls to the status panel."""
    idle_text = translate("status_idle")
    file_summary = os.path.basename(str(current_file or "").strip())
    state = {
        "fetch_title": translate("pipeline_fetch_title"),
        "fetch_status": idle_text,
        "fetch_detail": file_summary,
        "fetch_tone": "idle",
        "prepare_title": translate("pipeline_prepare_title"),
        "prepare_status": idle_text,
        "prepare_detail": file_summary,
        "prepare_tone": "idle",
        "download_title": translate("pipeline_download_title"),
        "download_status": idle_text,
        "download_detail": file_summary,
        "download_tone": "idle",
    }

    if stopping:
        state.update(
            {
                "fetch_status": translate("pipeline_state_stop_fetch"),
                "prepare_status": translate("pipeline_state_stop_prepare"),
                "download_status": translate("pipeline_state_stop_download"),
                "fetch_tone": "warning",
                "prepare_tone": "warning",
                "download_tone": "warning",
            }
        )
        return state
    if paused:
        pause_text = translate("pipeline_state_pause_requested")
        state.update(
            {
                "fetch_status": pause_text,
                "prepare_status": pause_text,
                "download_status": pause_text,
                "fetch_tone": "warning",
                "prepare_tone": "warning",
                "download_tone": "warning",
            }
        )
        return state

    api_text, api_tone, api_color_key = _pipeline_api_display(
        api_status, api_status_meta, translate
    )
    if phase == "download":
        if api_status != "idle" and api_color_key in ("warning", "danger"):
            state["download_status"] = api_text
            state["download_tone"] = api_tone
        elif download_status != "idle":
            text_key, _ = download_status_meta.get(
                download_status, ("status_idle", "muted")
            )
            state["download_status"] = translate(text_key)
            state["download_tone"] = "download"
    elif phase == "prepare" and api_status != "idle":
        state["prepare_status"] = api_text
        state["prepare_tone"] = api_tone
    elif phase == "fetch" and api_status != "idle":
        state["fetch_status"] = api_text
        state["fetch_tone"] = api_tone
    elif running:
        state["fetch_status"] = translate("pipeline_state_waiting_phase")
    return state


class ThreadPipelineWidget(ui_qt.QWidget):
    """串行下载状态面板：获取、准备、下载三个阶段。

    - 活跃节点以卡片边框呼吸光晕表示;暂停/停止慢闪;错误常亮
    - 阶段之间的 chevron 轨道流动,表示媒体从获取流向下载
    - 单个 QTimer 驱动,无动画需求或面板隐藏时自动停止
    """

    _DEFAULT_COLORS = {
        "accent": "#547a86",
        "warning": "#b8742a",
        "danger": "#c2584d",
        "muted": "#74695c",
    }
    _MIN_HEIGHT = 74

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("threadPipelineRoot")
        self.setAttribute(ui_qt.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setSizePolicy(
            ui_qt.QSizePolicy.Policy.Expanding, ui_qt.QSizePolicy.Policy.Fixed
        )
        self.setMinimumWidth(280)
        self.setMinimumHeight(self._MIN_HEIGHT)

        self._colors = {
            key: ui_qt.QColor(value) for key, value in self._DEFAULT_COLORS.items()
        }
        self._anim_ms = 0
        self._anim_timer = ui_qt.QTimer(self)
        self._anim_timer.setInterval(_PIPELINE_ANIM_INTERVAL_MS)
        self._anim_timer.timeout.connect(self._on_anim_tick)

        self.stage_layout = ui_qt.QGridLayout(self)
        self.stage_layout.setContentsMargins(0, 0, 0, 0)
        self.stage_layout.setHorizontalSpacing(3)
        self.stage_layout.setVerticalSpacing(3)

        (
            self.fetch_node,
            self.fetch_title_label,
            self.fetch_status_label,
            self.fetch_detail_label,
        ) = self._create_node()
        (
            self.prepare_node,
            self.prepare_title_label,
            self.prepare_status_label,
            self.prepare_detail_label,
        ) = self._create_node()
        (
            self.download_node,
            self.download_title_label,
            self.download_status_label,
            self.download_detail_label,
        ) = self._create_node()

        self.fetch_prepare_rail = _PipelineFlowRail(self, self)
        self.prepare_download_rail = _PipelineFlowRail(self, self)

        self._stage_widgets = (
            self.fetch_node,
            self.fetch_prepare_rail,
            self.prepare_node,
            self.prepare_download_rail,
            self.download_node,
        )
        for column, widget in enumerate(self._stage_widgets):
            alignment = (
                ui_qt.Qt.AlignmentFlag.AlignVCenter
                if isinstance(widget, _PipelineFlowRail)
                else ui_qt.Qt.AlignmentFlag(0)
            )
            self.stage_layout.addWidget(widget, 0, column, alignment)
        for column in (0, 2, 4):
            self.stage_layout.setColumnStretch(column, 1)

        self._animated_widgets = (
            self.fetch_node,
            self.prepare_node,
            self.download_node,
            self.fetch_prepare_rail,
            self.prepare_download_rail,
        )
        self._tones = {
            "fetch": "idle",
            "prepare": "idle",
            "download": "idle",
        }

    def resizeEvent(self, event):
        super().resizeEvent(event)
        width = self.contentsRect().width()
        self._sync_content_minimum_height(width)

    def _sync_content_minimum_height(self, width=None):
        """Keep wrapped task summaries inside their cards at the current width."""
        if width is None:
            width = self.contentsRect().width()
        width = max(1, int(width))
        content_height = self.stage_layout.heightForWidth(width)
        minimum_height = max(self._MIN_HEIGHT, content_height)
        if minimum_height != self.minimumHeight():
            self.setMinimumHeight(minimum_height)
            self.updateGeometry()

    def _create_node(self):
        node = _PipelineNodeFrame(self, self)
        node.setObjectName("threadPipelineNode")
        node.setProperty("tone", "idle")
        node.setMinimumWidth(0)
        node.setMinimumHeight(74)
        node.setSizePolicy(
            ui_qt.QSizePolicy.Policy.Expanding,
            ui_qt.QSizePolicy.Policy.Preferred,
        )
        layout = ui_qt.QVBoxLayout(node)
        layout.setContentsMargins(6, 7, 6, 7)
        layout.setSpacing(1)

        title = ui_qt.QLabel(node)
        title.setProperty("pipelineRole", "title")
        title.setAlignment(ui_qt.Qt.AlignmentFlag.AlignCenter)
        title.setMinimumWidth(0)
        title.setSizePolicy(
            ui_qt.QSizePolicy.Policy.Ignored, ui_qt.QSizePolicy.Policy.Preferred
        )

        status = ui_qt.QLabel(node)
        status.setProperty("pipelineRole", "status")
        status.setMinimumWidth(0)
        status.setSizePolicy(
            ui_qt.QSizePolicy.Policy.Ignored, ui_qt.QSizePolicy.Policy.Preferred
        )
        status.setWordWrap(True)
        detail = ui_qt.QLabel(node)
        detail.setProperty("pipelineRole", "detail")
        detail.setMinimumWidth(0)
        detail.setSizePolicy(
            ui_qt.QSizePolicy.Policy.Ignored, ui_qt.QSizePolicy.Policy.Preferred
        )
        detail.setWordWrap(True)

        layout.addWidget(title)
        layout.addWidget(status, 1)
        layout.addWidget(detail)
        return node, title, status, detail

    # ---- 动画时钟与视觉参数(子控件在 paintEvent 里取用) ----

    def _phase(self, period_ms):
        return (self._anim_ms % period_ms) / period_ms

    def _pulse(self):
        return 0.5 - 0.5 * math.cos(2 * math.pi * self._phase(2000))

    def _blink(self):
        return 0.5 - 0.5 * math.cos(2 * math.pi * self._phase(1600))

    def rail_visuals(self, state, count):
        colors = self._colors
        if state == "flow":
            phase = self._phase(1100)
            alphas = []
            for i in range(count):
                distance = (phase - i / count) % 1.0
                alphas.append(0.22 + 0.78 * max(0.0, 1.0 - distance * 2.6))
            return colors["accent"], alphas
        if state == "warning":
            return colors["warning"], [0.55] * count
        return colors["muted"], [0.4] * count

    def node_glow(self, tone):
        if tone in ("active", "download"):
            return self._colors["accent"], 0.35 + 0.65 * self._pulse()
        if tone == "warning":
            return self._colors["warning"], 0.25 + 0.6 * self._blink()
        if tone == "danger":
            return self._colors["danger"], 0.85
        return None

    def apply_theme_colors(self, colors):
        for key in self._DEFAULT_COLORS:
            value = colors.get(key)
            if value:
                self._colors[key] = ui_qt.QColor(value)
        for widget in self._animated_widgets:
            widget.update()

    def _needs_animation(self):
        visible_stages = ("fetch", "prepare", "download")
        return any(
            self._tones[stage] in ("active", "download", "warning")
            for stage in visible_stages
        )

    def _sync_anim_timer(self):
        if self._needs_animation() and self.isVisible():
            if not self._anim_timer.isActive():
                self._anim_timer.start()
        else:
            self._anim_timer.stop()

    def _on_anim_tick(self):
        self._anim_ms = (
            self._anim_ms + _PIPELINE_ANIM_INTERVAL_MS
        ) % _PIPELINE_ANIM_CYCLE_MS
        for widget in self._animated_widgets:
            widget.update()

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_anim_timer()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._anim_timer.stop()

    @staticmethod
    def _set_dynamic_property(widget, name, value):
        if widget.property(name) == value:
            return
        widget.setProperty(name, value)
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()

    def set_pipeline(
        self,
        *,
        fetch_title,
        fetch_status,
        fetch_detail,
        fetch_tone,
        prepare_title,
        prepare_status,
        prepare_detail,
        prepare_tone,
        download_title,
        download_status,
        download_detail,
        download_tone,
    ):
        self.fetch_title_label.setText(fetch_title)
        self.fetch_status_label.setText(fetch_status)
        self.fetch_detail_label.setText(
            _thread_pipeline_wrappable_filename(fetch_detail)
        )
        self.prepare_title_label.setText(prepare_title)
        self.prepare_status_label.setText(prepare_status)
        self.prepare_detail_label.setText(
            _thread_pipeline_wrappable_filename(prepare_detail)
        )
        self.download_title_label.setText(download_title)
        self.download_status_label.setText(download_status)
        self.download_detail_label.setText(
            _thread_pipeline_wrappable_filename(download_detail)
        )
        for node in (
            self.fetch_node,
            self.prepare_node,
            self.download_node,
        ):
            node.updateGeometry()
        self.stage_layout.invalidate()
        self._sync_content_minimum_height()

        self._set_dynamic_property(self.fetch_node, "tone", fetch_tone)
        self._set_dynamic_property(self.prepare_node, "tone", prepare_tone)
        self._set_dynamic_property(self.download_node, "tone", download_tone)
        self._tones = {
            "fetch": fetch_tone,
            "prepare": prepare_tone,
            "download": download_tone,
        }

        # Each rail follows its adjacent stage; Pause/Stop colors the full path.
        visible_tones = [fetch_tone, prepare_tone, download_tone]
        if "warning" in visible_tones:
            fetch_prepare_state = prepare_download_state = "warning"
        else:
            fetch_prepare_state = "flow" if fetch_tone == "active" else "idle"
            prepare_download_active = prepare_tone == "active" or download_tone in (
                "active",
                "download",
            )
            prepare_download_state = "flow" if prepare_download_active else "idle"
        self.fetch_prepare_rail.set_state(fetch_prepare_state)
        self.prepare_download_rail.set_state(prepare_download_state)
        self._sync_anim_timer()
        for widget in self._animated_widgets:
            widget.update()

        fetch_accessible = _thread_pipeline_accessible_value(fetch_status, fetch_detail)
        prepare_accessible = _thread_pipeline_accessible_value(
            prepare_status, prepare_detail
        )
        download_accessible = _thread_pipeline_accessible_value(
            download_status, download_detail
        )
        node_accessibility = [
            (self.fetch_node, fetch_title, fetch_accessible),
            (self.prepare_node, prepare_title, prepare_accessible),
        ]
        node_accessibility.append(
            (self.download_node, download_title, download_accessible)
        )
        for node, title, accessible_value in node_accessibility:
            node.setAccessibleName(title)
            node.setAccessibleDescription(accessible_value)
        self.setAccessibleName(
            " → ".join(title for _node, title, _value in node_accessibility)
        )
        self.setAccessibleDescription(
            "; ".join(f"{title}: {value}" for _node, title, value in node_accessibility)
        )
