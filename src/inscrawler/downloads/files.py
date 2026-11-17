"""Media directories, canonical filenames, and existing-file checks."""

from __future__ import annotations

import os
import unicodedata

import inscrawler.diagnostics.console as diagnostics_console
import inscrawler.downloads.runtime as downloads_runtime
import inscrawler.paths as app_paths


def set_download_base_dir(path):
    if path:
        downloads_runtime.runtime.download_base_dir = os.path.abspath(
            os.path.expanduser(path)
        )
    else:
        downloads_runtime.runtime.download_base_dir = app_paths.DEFAULT_DATA_DIR


def build_save_path(target_username: str, category: str):
    return os.path.join(
        downloads_runtime.runtime.download_base_dir, target_username, category
    )


def clean_filename(name):
    """Windows 友好的字符串清洗，用于 highlight title / 用户子目录名。
    Instaloader sanitize_paths=True 只作用于库内部 formatter，
    对我们用 os.path.join 自拼的外部路径无效。"""
    result = []
    for char in name:
        if char.isalnum() or char in " _-":
            result.append(char)
        else:
            try:
                description = unicodedata.name(char).lower().replace(" ", "_")
                result.append(f"[{description}]")
            except ValueError:
                result.append(f"[U+{ord(char):04X}]")
    return "".join(result).strip()


def generate_filenames_post(post, folder):
    """文件名统一按 post.date_utc 格式化，避免本机时区改变影响去重。"""
    try:
        ts = post.date_utc.strftime("%Y-%m-%d_%H-%M-%S")
        if post.typename == "GraphSidecar":
            return [
                os.path.join(folder, f"{ts}_{idx}{'.mp4' if iv else '.jpg'}")
                for idx, iv in enumerate(post.get_is_videos(), start=1)
            ]
        ext = ".mp4" if post.is_video else ".jpg"
        return [os.path.join(folder, f"{ts}{ext}")]
    except Exception as e:
        diagnostics_console.print(f"Error generating filenames for post: {e}")
        return []


def generate_filenames_storyitem(item, folder):
    try:
        ts = item.date_utc.strftime("%Y-%m-%d_%H-%M-%S")
        ext = ".mp4" if item.is_video else ".jpg"
        return [os.path.join(folder, f"{ts}{ext}")]
    except Exception as e:
        diagnostics_console.print(f"Error generating filenames for story item: {e}")
        return []


def is_already_downloaded(filenames):
    """根据文件名列表检查是否已下载。
    - 全部存在 → True（跳过）
    - 全部不存在 → False（正常新下载）
    - 部分存在（album 上次下到一半最常见）→ False，并打印警告告知用户
      这次会整包重下、本地已有文件会被覆盖
    """
    if not filenames:
        return False

    total = len(filenames)
    existing = 0
    for filename in filenames:
        if os.path.exists(filename):
            existing += 1

    if existing == total:
        first_name = os.path.basename(filenames[0])
        if total > 1:
            stem = first_name.rsplit("_", 1)[0]
            diagnostics_console.print(
                f"Skipping already downloaded set: {stem} ({total} files)"
            )
        else:
            diagnostics_console.print(f"Skipping already downloaded file: {first_name}")
        return True

    if existing > 0:
        first_name = os.path.basename(filenames[0])
        stem = first_name.rsplit("_", 1)[0] if total > 1 else first_name
        diagnostics_console.print(
            f"Partial download detected for {stem}: "
            f"{existing}/{total} files already on disk, "
            f"re-downloading whole set (existing files will be overwritten)"
        )

    return False


def _is_reel_already_downloaded(target_username, reel_filenames):
    """Check the matching Posts files before the normal Reels folder."""
    reel_filenames = list(reel_filenames or ())
    if not reel_filenames:
        return False

    posts_folder = build_save_path(target_username, "posts")
    posts_filenames = [
        os.path.join(posts_folder, os.path.basename(filename))
        for filename in reel_filenames
    ]
    if all(os.path.exists(filename) for filename in posts_filenames):
        first_name = os.path.basename(posts_filenames[0])
        if len(posts_filenames) > 1:
            stem = first_name.rsplit("_", 1)[0]
            diagnostics_console.print(
                f"Skipping Reel set already downloaded in Posts: "
                f"{stem} ({len(posts_filenames)} files)"
            )
        else:
            diagnostics_console.print(
                f"Skipping Reel already downloaded in Posts: {first_name}"
            )
        return True

    return is_already_downloaded(reel_filenames)
