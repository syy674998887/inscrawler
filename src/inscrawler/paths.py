"""Application data paths, independent of source modules and the working directory.

Source checkouts keep data beside pyproject.toml, Windows executables use
Local AppData, and wheel installs use the launch directory. INSCRAWLER_HOME
overrides all three. Importing this module never creates files.
"""

import os
import sys
from dataclasses import dataclass
from pathlib import Path


def default_home() -> Path:
    override = os.environ.get("INSCRAWLER_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        local = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
        return (Path(local) / "inscrawler").resolve()
    package = Path(__file__).resolve().parent
    if package.parent.name == "src":
        checkout = package.parent.parent
        if (checkout / "pyproject.toml").is_file():
            return checkout
    return Path.cwd().resolve()


@dataclass(frozen=True)
class AppPaths:
    home: Path

    @property
    def env(self) -> Path:
        return self.home / ".env"

    @property
    def tasks(self) -> Path:
        return self.home / "tasks"

    @property
    def session(self) -> Path:
        return self.home / "session"

    @property
    def data(self) -> Path:
        return self.home / "data"

    @property
    def cache(self) -> Path:
        return self.home / "cache"


app_paths = AppPaths(default_home())
APP_DIR = PROJECT_DIR = str(app_paths.home)
ENV_FILE = str(app_paths.env)
SESSION_DIR = str(app_paths.session)
TASKS_DIR = str(app_paths.tasks)
DEFAULT_DATA_DIR = str(app_paths.data)
CACHE_DIR = str(app_paths.cache)
TARGET_FILE = str(app_paths.home / "target.json")
TASK_RESUME_DIR = str(app_paths.tasks / "resume")
PREVIEW_HISTORY_FILE = str(app_paths.cache / "preview.json")
ICON_FILE = str(Path(__file__).resolve().parent / "assets" / "logo.ico")
