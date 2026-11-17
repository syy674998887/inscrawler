"""Strict serial iterator checkpoints and task cursor cleanup."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from lzma import LZMAError

from instaloader import (
    FrozenNodeIterator,
    NodeIterator,
)
from instaloader.exceptions import (
    InvalidArgumentException,
)
from instaloader.structures import load_structure_from_file, save_structure_to_file

import inscrawler.diagnostics.console as diagnostics_console
import inscrawler.downloads.models as downloads_models
import inscrawler.downloads.runtime as downloads_runtime
import inscrawler.paths as app_paths

_resume_checkpoint_warning_paths = set()


def _safe_checkpoint_part(value):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()) or "unknown"


def _resume_checkpoint_path(kind, *, task_id=None):
    checkpoint_task_id = (
        downloads_runtime.runtime.active_task_id if task_id is None else task_id
    )
    if kind not in downloads_models._RESUMABLE_MEDIA_TYPES or not checkpoint_task_id:
        return None
    filename = f"{_safe_checkpoint_part(checkpoint_task_id)}_{_safe_checkpoint_part(kind)}.json.xz"
    return os.path.join(app_paths.TASK_RESUME_DIR, filename)


def _warn_resume_checkpoint_once(path, message):
    key = path or message
    if key in _resume_checkpoint_warning_paths:
        return
    _resume_checkpoint_warning_paths.add(key)
    diagnostics_console.print(message)


def _delete_resume_checkpoint(kind, *, task_id=None, log=False):
    checkpoint_task_id = (
        downloads_runtime.runtime.active_task_id if task_id is None else task_id
    )
    path = _resume_checkpoint_path(kind, task_id=checkpoint_task_id)
    if not path:
        return
    removed = False
    tmp_path = f"{path}.tmp.xz"
    for candidate in (path, tmp_path):
        if not os.path.exists(candidate):
            continue
        try:
            os.unlink(candidate)
            removed = True
        except OSError as e:
            _warn_resume_checkpoint_once(
                candidate,
                f"Warning: failed to delete {kind} resume cursor for task "
                f"{checkpoint_task_id}: {e}",
            )
    if removed and log:
        diagnostics_console.print(
            f"Deleted {kind} resume cursor for task {checkpoint_task_id}."
        )


def _patrol_resume_task_id(parent_task_id, target_index):
    parent = str(parent_task_id or "").strip()
    if not parent:
        return None
    try:
        index = int(target_index)
    except (TypeError, ValueError):
        return None
    if index < 0:
        return None
    return f"{parent}.patrol.{index:04d}"


def delete_task_resume_checkpoint(task_id, kind):
    """Delete the serial resume cursor owned by one persisted download task."""
    if not str(task_id or "").strip():
        return
    _delete_resume_checkpoint(kind, task_id=task_id)


def delete_patrol_resume_checkpoints(parent_task_id, kind, target_count):
    """Delete every serial child cursor owned by one persisted patrol task."""
    try:
        count = max(0, int(target_count))
    except (TypeError, ValueError):
        return
    for target_index in range(count):
        child_task_id = _patrol_resume_task_id(parent_task_id, target_index)
        if child_task_id:
            _delete_resume_checkpoint(kind, task_id=child_task_id)


def _read_resume_checkpoint(context, kind, *, task_id=None):
    checkpoint_task_id = (
        downloads_runtime.runtime.active_task_id if task_id is None else task_id
    )
    path = _resume_checkpoint_path(kind, task_id=checkpoint_task_id)
    if not path or not os.path.isfile(path):
        return None

    try:
        frozen = load_structure_from_file(context, path)
        if not isinstance(frozen, FrozenNodeIterator):
            raise InvalidArgumentException("Invalid resume cursor type.")
        if (
            frozen.best_before
            and datetime.fromtimestamp(frozen.best_before) < datetime.now()
        ):
            raise InvalidArgumentException("Resume cursor expired.")
        return frozen
    except (
        InvalidArgumentException,
        LZMAError,
        json.JSONDecodeError,
        EOFError,
        OSError,
        TypeError,
        ValueError,
        OverflowError,
    ) as e:
        diagnostics_console.print(
            f"Warning: not resuming {kind} cursor for task {checkpoint_task_id}: {e}"
        )
        _delete_resume_checkpoint(kind, task_id=checkpoint_task_id)
        return None


def _thaw_resume_checkpoint(iterator, frozen, kind, *, task_id=None):
    checkpoint_task_id = (
        downloads_runtime.runtime.active_task_id if task_id is None else task_id
    )
    if not isinstance(iterator, NodeIterator) or not isinstance(
        frozen, FrozenNodeIterator
    ):
        return False

    try:
        iterator.thaw(frozen)
        diagnostics_console.print(
            f"Resuming {kind} iterator from saved cursor for task "
            f"{checkpoint_task_id} at index {iterator.total_index}."
        )
        return True
    except (InvalidArgumentException, TypeError, ValueError, OverflowError) as e:
        diagnostics_console.print(
            f"Warning: not resuming {kind} cursor for task {checkpoint_task_id}: {e}"
        )
        _delete_resume_checkpoint(kind, task_id=checkpoint_task_id)
        return False


def _freeze_iterator_for_checkpoint(iterator, replay_current=True):
    """Freeze a NodeIterator at an item-safe position.

    Instaloader's public freeze() deliberately replays the last yielded item.
    That is required when the current logical item has not completed.  At a
    known-safe boundary we instead retain only edges after ``_page_index`` so a
    completed page is not counted again after resume.
    """
    frozen = iterator.freeze()
    if replay_current:
        return frozen

    data = getattr(iterator, "_data", None)
    page_index = getattr(iterator, "_page_index", None)
    if not isinstance(data, dict) or not isinstance(page_index, int):
        raise InvalidArgumentException("NodeIterator page state is unavailable.")
    edges = data.get("edges")
    if not isinstance(edges, list) or page_index < 0 or page_index > len(edges):
        raise InvalidArgumentException("NodeIterator edge state is invalid.")

    remaining_data = {**data, "edges": edges[page_index:]}
    return frozen._replace(
        total_index=max(0, iterator.total_index),
        remaining_data=remaining_data,
    )


def _save_resume_checkpoint(iterator, kind, *, task_id=None, replay_current=True):
    checkpoint_task_id = (
        downloads_runtime.runtime.active_task_id if task_id is None else task_id
    )
    path = _resume_checkpoint_path(kind, task_id=checkpoint_task_id)
    if not path or not isinstance(iterator, NodeIterator):
        return False

    tmp_path = f"{path}.tmp.xz"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        save_structure_to_file(
            _freeze_iterator_for_checkpoint(iterator, replay_current=replay_current),
            tmp_path,
        )
        os.replace(tmp_path, path)
        return True
    except (InvalidArgumentException, LZMAError, OSError, TypeError, ValueError) as e:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass
        _warn_resume_checkpoint_once(
            path,
            f"Warning: failed to save {kind} resume cursor for task "
            f"{checkpoint_task_id}: {e}",
        )
        return False
