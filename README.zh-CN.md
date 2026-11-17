# inscrawler

[![Python 3.13+](https://img.shields.io/badge/Python-3.13%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![Windows 10 1809+](https://img.shields.io/badge/Windows-10%201809%2B-0078D6)](#快速开始)
[![MIT 许可证](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

[English](README.md) · **简体中文**

inscrawler 是一款 Windows 桌面应用，可下载 Instagram 帖子、Reels 短视频、标记了目标账号的帖子、快拍（Stories）和精选快拍（Highlights）。「自动巡逻」可一次检查多个账号，下载本地缺少的照片和视频。

## 快速开始

在 x64 版 Windows 上，下载并运行 `inscrawler-<版本>-win64-setup.exe`，再从开始菜单打开 **inscrawler**。安装程序已包含 Python 和 Qt，仅为当前 Windows 用户安装。[GitHub Releases](https://github.com/syy674998887/inscrawler/releases)

从源码运行时，在项目目录打开 PowerShell。以下命令使用 Python 3.13+ 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)。

```powershell
uv sync --locked
uv run inscrawler
```

1. 在「登录配置」中输入你的 Instagram 用户名。将已登录浏览器会话的 `Cookie` 请求头值或 Netscape 格式 `cookies.txt` 的内容粘贴到 Cookie 栏，然后点击「保存配置」。
2. 在「创建任务」中输入目标账号，选择媒体类型，然后点击「添加任务」。
3. 点击「开始任务」。下载首个文件后，点击「本地文件」即可打开该账号的下载目录。

使用 pip 时，可在已激活的 Python 3.13+ 虚拟环境中运行 `python -m pip install -e .`，再用 `python -m inscrawler` 启动。

## 下载与设置

- 队列可以暂停和继续，拖动待执行的任务即可调整顺序。切换任务或退出应用时，当前文件会先下载完毕。
- 启用「iPhone 画质」可下载原生 iPhone 画质的媒体。照片可直接预览，视频缩略图需要将 `ffmpeg` 加入 `PATH`。[下载 FFmpeg](https://ffmpeg.org/download.html)
- 任务完成后，可通过桌面通知、Discord 或 Telegram 接收提醒。
- 启用「完成后关机」后，倒计时的 60 秒内可以取消。倒计时结束时，Windows 会强制关闭应用并关机，未保存的更改可能丢失。

Windows 安装版将配置、登录会话、任务和下载保存在 `%LOCALAPPDATA%\inscrawler`，升级或卸载程序时会保留这些数据。源码版使用项目目录，wheel 安装版使用启动目录。下载文件默认位于数据目录下的 `data/`。启动前设置 `INSCRAWLER_HOME` 可指定其他数据目录。[配置示例](.env.example)

## 开发

应用源码位于 `src/inscrawler/`，离线测试位于 `tests/`，维护脚本位于 `tools/`。Windows CI 也会运行以下检查。

```powershell
uv run ruff check --no-cache src tests tools gui.py
uv run ruff format --check --no-cache src tests tools gui.py
$env:QT_QPA_PLATFORM = 'offscreen'
uv run python -m unittest discover -s tests -v
uv build
uv run python tools/check_distribution.py
```

发布时，提交 `pyproject.toml` 中的版本号和更新后的 `uv.lock`，再推送对应标签（例如依次运行 `git tag v1.0` 和 `git push origin v1.0`）。GitHub Actions 会测试应用、验证安装和卸载，再将 EXE 安装程序发布到 GitHub Releases。在本地 Windows x64 上构建可运行 `uv run --python 3.13 --locked --group installer python tools/build_windows_installer.py`。

本项目使用 [Instaloader](https://github.com/instaloader/instaloader) 和 [PySide6](https://doc.qt.io/qtforpython-6/)，采用 [MIT 许可证](LICENSE)。
