"""QThread adapters bridging core downloads and patrol to Qt signals."""

from __future__ import annotations

import os

import inscrawler.downloads.control as downloads_control
import inscrawler.downloads.errors as downloads_errors
import inscrawler.downloads.models as downloads_models
import inscrawler.downloads.service as downloads_service
import inscrawler.notifications.service as notifications_service
import inscrawler.tasks.patrol as tasks_patrol
import inscrawler.tasks.schema as tasks_schema
import inscrawler.ui.console as ui_console
import inscrawler.ui.qt as ui_qt


class DownloadThread(ui_qt.QThread):
    """下载线程 - 避免GUI阻塞"""

    message_signal = ui_qt.Signal(str)
    image_signal = ui_qt.Signal(str)  # 新下载的图片路径
    profile_picture_signal = ui_qt.Signal(str, str)  # 账号头像更新：(username, path)
    file_detail_signal = ui_qt.Signal(dict)  # 新下载文件的结构化详情
    file_activity_signal = ui_qt.Signal(dict)  # 串行路径当前文件开始/结束
    item_completed_signal = ui_qt.Signal(dict)  # 单个 post/story/highlight item 完成
    scope_completed_signal = ui_qt.Signal(dict)  # 一个 page/full scope 完整处理完成
    status_signal = ui_qt.Signal(str)  # API/准备状态信号（fetch/prepare/auth/idle）
    download_status_signal = ui_qt.Signal(str)  # 下载状态信号（右侧：CDN 下载阶段）
    auth_required_signal = ui_qt.Signal(str)
    rate_limited_signal = ui_qt.Signal(str)
    iphone_quality_unavailable_signal = ui_qt.Signal(str)
    finished_signal = ui_qt.Signal(object)
    error_signal = ui_qt.Signal(str)
    log_signal = ui_qt.Signal(str)  # 日志信号

    def __init__(
        self,
        username,
        download_media_type,
        scope_limit,
        scope_unit=None,
        media_request_cap=None,
        apply_user_switch_delay=False,
        task_id=None,
        risk_run_id=None,
        queue_completed_before=None,
    ):
        super().__init__()
        self.username = username
        self.download_media_type = download_media_type
        self.scope_limit = scope_limit
        self.scope_unit = scope_unit
        self.media_request_cap = media_request_cap
        self.apply_user_switch_delay = apply_user_switch_delay
        self.task_id = task_id
        self.risk_run_id = risk_run_id
        self.queue_completed_before = queue_completed_before
        self._gui_log_signal_connected = False

    def run(self):
        try:
            if self.apply_user_switch_delay:
                downloads_control.delay("user_switch")

            # 调用统一下载函数
            result = downloads_service.download_user_content(
                self.username,
                self.download_media_type,
                self.scope_limit,
                scope_unit=self.scope_unit,
                media_request_cap=self.media_request_cap,
                task_id_override=self.task_id,
                risk_run_id=self.risk_run_id,
                queue_completed_before=self.queue_completed_before,
            )

            status = result.get("status", "unknown")
            message = result.get("message", "Unknown error")

            if status == downloads_models.status_code.SUCCESS:
                downloaded_count = result.get("downloaded_count", 0)
                self.message_signal.emit(
                    f"@{self.username} download completed ({self.download_media_type.capitalize()})"
                )
                self.message_signal.emit(f"Downloaded {downloaded_count} items")
                self.finished_signal.emit(result)
            elif status == downloads_models.status_code.STOPPED:
                self.finished_signal.emit(result)
            elif result.get("blocking_failure"):
                self.finished_signal.emit(result)
            elif status == downloads_models.status_code.AUTHORIZATION_REQUIRED:
                self.auth_required_signal.emit(message)
            elif status == downloads_models.status_code.RATE_LIMITED:
                self.rate_limited_signal.emit(message)
            elif status == downloads_models.status_code.IPHONE_QUALITY_UNAVAILABLE:
                self.iphone_quality_unavailable_signal.emit(message)
            elif status == downloads_models.status_code.USER_NOT_FOUND:
                self.error_signal.emit(f"Account not found: {message}")
            else:
                self.error_signal.emit(f"Download failed: {message}")

        except downloads_models.DownloadStopRequested:
            self.finished_signal.emit(
                {
                    "status": downloads_models.status_code.STOPPED,
                    "message": f"Download stopped for @{self.username}",
                    "downloaded_count": 0,
                }
            )
        except Exception as e:
            self.error_signal.emit(f"Download failed: {str(e)}")


class PatrolThread(ui_qt.QThread):
    """Run one serial patrol pass over an immutable target snapshot."""

    message_signal = ui_qt.Signal(str)
    image_signal = ui_qt.Signal(str)
    profile_picture_signal = ui_qt.Signal(str, str)
    file_activity_signal = ui_qt.Signal(dict)
    status_signal = ui_qt.Signal(str)
    download_status_signal = ui_qt.Signal(str)
    target_started_signal = ui_qt.Signal(dict)
    item_completed_signal = ui_qt.Signal(dict)
    target_finished_signal = ui_qt.Signal(dict)
    finished_signal = ui_qt.Signal(object)
    log_signal = ui_qt.Signal(str)

    def __init__(
        self,
        targets,
        download_media_type,
        page_limit=None,
        delay_before_first_target=False,
        patrol_task_id=None,
        target_indexes=None,
    ):
        super().__init__()
        raw_targets = tuple(targets or ())
        raw_indexes = None if target_indexes is None else tuple(target_indexes)
        if raw_indexes is not None and len(raw_indexes) != len(raw_targets):
            raise ValueError("target_indexes must align with patrol targets")

        normalized_targets = []
        normalized_indexes = []
        for position, value in enumerate(raw_targets):
            username = str(value or "").strip().lstrip("@").strip()
            if not username:
                continue
            normalized_targets.append(username)
            if raw_indexes is not None:
                target_index = raw_indexes[position]
                if type(target_index) is not int or target_index < 0:
                    raise ValueError(
                        "patrol target indexes must be non-negative integers"
                    )
                normalized_indexes.append(target_index)

        self.targets = tuple(normalized_targets)
        self.target_indexes = (
            tuple(normalized_indexes) if raw_indexes is not None else None
        )
        self.patrol_task_id = str(patrol_task_id or "").strip() or None
        self.download_media_type = str(download_media_type or "").strip().lower()
        if self.download_media_type in tasks_schema._PAGED_PATROL_MEDIA_TYPES:
            if type(page_limit) is not int or page_limit < 1:
                raise ValueError(
                    "page_limit must be a positive integer for paged patrols"
                )
            self.page_limit = page_limit
        else:
            if page_limit is not None:
                raise ValueError("page_limit is only valid for paged patrols")
            self.page_limit = None
        self.delay_before_first_target = bool(delay_before_first_target)
        self._target_capture = None
        self._gui_log_signal_connected = False

    @staticmethod
    def _artifact_key(detail):
        if not isinstance(detail, dict):
            return None
        category = str(detail.get("download_category") or "").strip().lower()
        media_id = detail.get("media_id")
        if media_id not in (None, ""):
            return (category, "media_id", str(media_id))
        shortcode = detail.get("shortcode")
        if shortcode not in (None, ""):
            return (category, "shortcode", str(shortcode))
        return None

    def capture_download_log(self, line):
        line = str(line or "")
        if line:
            self.log_signal.emit(line)

    def capture_image(self, image_path):
        if image_path:
            self.image_signal.emit(str(image_path))

    def capture_file_detail(self, detail):
        payload = dict(detail or {})
        capture = self._target_capture
        if capture is not None:
            capture["file_details"].append(payload)

    @classmethod
    def _take_completed_media(cls, capture, completed_key):
        file_details = []
        remaining = []
        for value in capture.get("file_details", []):
            if cls._artifact_key(value) == completed_key:
                file_details.append(dict(value))
            else:
                remaining.append(value)
        capture["file_details"] = remaining
        downloaded_files = []
        seen_paths = set()
        for detail in file_details:
            path = str(detail.get("path") or "").strip()
            normalized = os.path.normcase(os.path.abspath(path)) if path else ""
            if not normalized or normalized in seen_paths:
                continue
            seen_paths.add(normalized)
            downloaded_files.append(path)
        return downloaded_files, file_details

    def capture_item_completed(self, detail):
        payload = dict(detail or {})
        capture = self._target_capture
        if capture is None:
            return
        artifact_key = self._artifact_key(payload)
        if artifact_key is None:
            return
        downloaded_files, file_details = self._take_completed_media(
            capture,
            artifact_key,
        )
        self.item_completed_signal.emit(
            {
                "event": "item_completed",
                "target_username": capture.get("target_username", ""),
                **(
                    {"target_index": capture["target_index"]}
                    if capture.get("target_index") is not None
                    else {}
                ),
                "media_type": payload.get(
                    "download_category", self.download_media_type
                ),
                "completed_item": payload,
                "downloaded_files": downloaded_files,
                "downloaded_file_details": file_details,
            }
        )

    def _handle_target_event(self, event):
        payload = dict(event or {})
        event_type = payload.get("event")
        username = str(payload.get("target_username") or "").strip().lstrip("@")
        if event_type == "target_started":
            ui_console._set_console_target_scope(self.patrol_task_id, username)
            self._target_capture = {
                "target_username": username,
                "target_index": payload.get("target_index"),
                "file_details": [],
            }
            self.target_started_signal.emit(dict(payload))
            return

        if event_type != "target_finished":
            return

        capture = self._target_capture or {
            "target_username": username,
            "target_index": payload.get("target_index"),
            "file_details": [],
        }
        result = dict(payload.get("result") or {})

        artifact = dict(result)
        artifact.update(
            {
                "target_username": username or capture["target_username"],
                "media_type": payload.get("media_type", self.download_media_type),
            }
        )
        target_index = payload.get("target_index", capture.get("target_index"))
        if target_index is not None:
            artifact["target_index"] = target_index
        self._target_capture = None
        ui_console._set_console_target_scope(self.patrol_task_id, None)
        self.target_finished_signal.emit(artifact)

    def run(self):
        final_result = {
            "status": downloads_models.status_code.STOPPED,
            "message": "Patrol stopped",
            "downloaded_count": 0,
        }
        try:
            if self.delay_before_first_target:
                downloads_control.delay("user_switch")
            if downloads_control.is_stop_requested():
                raise downloads_models.DownloadStopRequested()

            start_message = (
                f"Starting patrol: {len(self.targets)} target(s), "
                f"{self.download_media_type.capitalize()}"
            )
            if self.page_limit is not None:
                start_message += f", detection pages {self.page_limit}"
            self.message_signal.emit(start_message)
            patrol_kwargs = {
                "target_event_callback": self._handle_target_event,
            }
            if self.patrol_task_id is not None:
                patrol_kwargs["patrol_task_id"] = self.patrol_task_id
            if self.target_indexes is not None:
                patrol_kwargs["target_indexes"] = self.target_indexes
            final_result = dict(
                tasks_patrol.patrol_targets_once(
                    self.targets,
                    self.download_media_type,
                    self.page_limit,
                    **patrol_kwargs,
                )
                or {}
            )
            downloaded_count = max(
                0,
                int(final_result.get("downloaded_count", 0) or 0),
            )
            if (
                final_result.get("status") == downloads_models.status_code.SUCCESS
                and downloaded_count
            ):
                notifications_service.notify(
                    f"Patrol completed: downloaded {downloaded_count} update(s)"
                )
        except downloads_models.DownloadStopRequested:
            final_result = {
                "status": downloads_models.status_code.STOPPED,
                "message": "Patrol stopped",
                "downloaded_count": 0,
            }
        except Exception as e:
            final_result = {
                "status": downloads_errors.handle_error(e),
                "message": f"Patrol failed: {e}",
                "downloaded_count": 0,
            }
        finally:
            ui_console._set_console_target_scope(self.patrol_task_id, None)
            self._target_capture = None
            self.finished_signal.emit(final_result)
