# inscrawler

[![Python 3.13+](https://img.shields.io/badge/Python-3.13%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![Windows 10 1809+](https://img.shields.io/badge/Windows-10%201809%2B-0078D6)](#quick-start)
[![MIT License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

**English** · [简体中文](README.zh-CN.md)

inscrawler is a Windows desktop app for downloading Instagram posts, Reels, tagged posts, Stories, and Highlights. **Auto Patrol** checks selected accounts in one pass and downloads media missing from your collection.

## Quick start

On Windows x64, download and run `inscrawler-<version>-win64-setup.exe`, then open **inscrawler** from the Start menu. Setup includes Python and Qt and installs for your Windows account. [GitHub Releases](https://github.com/syy674998887/inscrawler/releases)

To run from source, open PowerShell in the project folder. These commands use Python 3.13+ and [uv](https://docs.astral.sh/uv/getting-started/installation/).

```powershell
uv sync --locked
uv run inscrawler
```

1. In **Login Configuration**, enter your Instagram username. Paste the `Cookie` header value from a signed-in browser session or the contents of a Netscape `cookies.txt` export, then click **Save Config**.
2. Under **Create Task**, enter the target account, choose a media type, and click **Add Task**.
3. Click **Start Task**. After the first download, **Local Files** opens the selected account's folder.

If you use pip, run `python -m pip install -e .` in an active Python 3.13+ virtual environment and launch with `python -m inscrawler`.

## Downloads and settings

- Pause or resume the queue and drag pending tasks to reorder them. When you switch tasks or exit, the app finishes the current file first.
- Enable **iPhone Quality** to download native iPhone media. You can preview photos in the app. For video thumbnails, add `ffmpeg` to your `PATH`. [Download FFmpeg](https://ffmpeg.org/download.html)
- Receive completion notifications on your desktop, in Discord, or via Telegram.
- If you enable **Shut down after queue**, you have 60 seconds to cancel before Windows force-closes open apps and shuts down. Unsaved changes may be lost.

The Windows installer keeps settings, sessions, tasks, and downloads in `%LOCALAPPDATA%\inscrawler`, separate from the program files. Upgrading or uninstalling preserves this data. Source checkouts use the project folder and wheel installs use the launch directory. Downloads default to `data/` under that location. Set `INSCRAWLER_HOME` before launch to choose another data directory. [Configuration template](.env.example)

## Development

Application code lives in `src/inscrawler/`, offline tests in `tests/`, and maintenance scripts in `tools/`. Windows CI runs the same checks.

```powershell
uv run ruff check --no-cache src tests tools gui.py
uv run ruff format --check --no-cache src tests tools gui.py
$env:QT_QPA_PLATFORM = 'offscreen'
uv run python -m unittest discover -s tests -v
uv build
uv run python tools/check_distribution.py
```

To publish a release, commit the project version in `pyproject.toml` and its updated `uv.lock`, then push the matching tag (for example, `git tag v1.0` followed by `git push origin v1.0`). GitHub Actions tests the app, verifies installation and removal, and publishes the setup executable to GitHub Releases. For a local build on Windows x64, run `uv run --python 3.13 --locked --group installer python tools/build_windows_installer.py`.

Built with [Instaloader](https://github.com/instaloader/instaloader) and [PySide6](https://doc.qt.io/qtforpython-6/). Released under the [MIT License](LICENSE).
