"""Bridge task persistence and restored records to queue widgets."""

from __future__ import annotations

import os

import inscrawler.downloads.checkpoints as downloads_checkpoints
import inscrawler.paths as app_paths
import inscrawler.tasks.repository as tasks_repository
import inscrawler.tasks.schema as tasks_schema
import inscrawler.ui.console as ui_console


class TaskStoreController:
    """Qt view concern composed explicitly by the main window."""

    def _task_file_path(self, task_id):
        """返回单个任务文件的路径"""
        return os.path.join(app_paths.TASKS_DIR, f"{task_id}.json")

    def _next_queue_order(self):
        return tasks_repository.next_queue_order(self.queue_items)

    def save_task(self, entry):
        """保存单个任务到 tasks/ 文件夹"""
        try:
            if tasks_schema._uses_queue_order(entry["task"], entry.get("status")):
                order = entry.get("queue_order")
                if type(order) is not int or order < 0:
                    entry["queue_order"] = self._next_queue_order()
            tasks_repository.write_record(
                self._task_file_path(entry["task"]["task_id"]),
                tasks_repository.task_record(entry),
            )
        except Exception as e:
            ui_console.print(f"Failed to save task: {e}")

    def save_tasks(self):
        """保存所有任务到 tasks/ 文件夹（每任务一个文件）"""
        try:
            os.makedirs(app_paths.TASKS_DIR, exist_ok=True)
            for entry in self.queue_items:
                self.save_task(entry)
            ui_console.print(f"Task list saved ({len(self.queue_items)} items)")
        except Exception as e:
            ui_console.print(f"Failed to save task list: {e}")

    def delete_task_file(self, entry):
        """删除单个任务文件"""
        try:
            task = entry["task"]
            filepath = self._task_file_path(task["task_id"])
            if os.path.exists(filepath):
                os.remove(filepath)
            if task.get("task_kind") == "download":
                downloads_checkpoints.delete_task_resume_checkpoint(
                    task["task_id"],
                    task["download_media_type"],
                )
            elif tasks_schema._is_auto_patrol_task(task):
                downloads_checkpoints.delete_patrol_resume_checkpoints(
                    task["task_id"],
                    task["download_media_type"],
                    len(task.get("target_users") or []),
                )
        except Exception as e:
            ui_console.print(f"Failed to delete task file: {e}")

    def _finalize_loaded_task_batch(self):
        """Refresh queue-wide UI state once after startup task hydration."""
        self._refresh_queue_item_styles()
        self._refresh_queue_reorder_state()
        self.update_clear_queue_button_state()
        self._scroll_queue_to_bottom()

    def load_saved_tasks(self):
        """从 tasks/ 文件夹加载已保存任务，并恢复显式队列顺序。"""
        try:
            items = tasks_repository.load_records(
                app_paths.TASKS_DIR,
                on_invalid=lambda filename, error: ui_console.print(
                    f"Skipping invalid saved task {filename}: {error}"
                ),
            )
            if not items:
                return

            loaded = 0
            self._loading_saved_tasks = True
            try:
                for item in items:
                    task = item["task"]
                    status = item["status"]
                    logs = item["logs"]

                    if tasks_schema._is_auto_patrol_task(task):
                        # A worker cannot survive an application restart. Restore
                        # unfinished patrols to the same pending state as downloads.
                        if status in ("running", "deferred"):
                            status = "pending"
                        patrol_state = item["patrol_state"]
                        log_targets = item["log_targets"]
                        for account in patrol_state["accounts"]:
                            if account["state"] == "checking":
                                account["state"] = "stopped"
                    else:
                        patrol_state = None
                        log_targets = None

                    # 普通进行中/延迟任务改为等待状态（下次启动时可重新执行）
                    if task["task_kind"] == "download" and status in (
                        "running",
                        "deferred",
                    ):
                        status = "pending"

                    # 运行时字段（save_task 不落盘，每次加载从当前 UI 补齐）
                    task["login_username"] = self.username.text().strip() or None
                    task["webhook"] = self.webhook.text().strip() or None
                    task["telegram_bot_token"] = (
                        self.telegram_bot_token.text().strip() or None
                    )
                    task["telegram_chat_id"] = (
                        self.telegram_chat_id.text().strip() or None
                    )
                    task["save_path"] = (
                        self.save_path.text().strip() or self.default_save_path
                    )
                    self.add_task_to_queue(
                        task,
                        status,
                        logs=logs,
                        downloaded_files=item["downloaded_files"],
                        downloaded_file_details=item["downloaded_file_details"],
                        patrol_state=patrol_state,
                        log_targets=log_targets,
                        queue_order=item.get("_runtime_queue_order"),
                        completed_at=(
                            item.get("completed_at") if status == "completed" else None
                        ),
                        save=False,
                    )
                    loaded += 1
            finally:
                self._loading_saved_tasks = False
                self._finalize_loaded_task_batch()

            ui_console.print(f"Loaded {loaded} saved task(s)")
        except Exception as e:
            ui_console.print(f"Failed to load task list: {e}")
